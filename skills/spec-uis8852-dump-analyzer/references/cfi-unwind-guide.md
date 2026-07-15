# UIS8852 DWARF CFI 栈回溯指南

`scripts/unwind_cfi.py` + `scripts/cfi.py` 用 ELF 的 `.debug_frame`（DWARF Call Frame Information）做**确定性逐帧回溯**，与 TRACE32/GDB 同机制。相比 `unwind.py` 的 prologue 启发式 + 栈上扫代码地址，CFI 逐帧精确、无噪声（无重复帧、无残留帧）。

## 何时用 CFI vs 启发式

| 条件 | 用什么 |
|------|--------|
| ELF 含 `.debug_frame`（`readelf -S` 可见） | **首选 `unwind_cfi.py`** |
| ELF 无 `.debug_frame`（被 strip / 仅 release 无调试） | 回退 `unwind.py`（启发式） |
| 只想确认当前中断 IRQ 号 | 两者都先读 `g_osIrqNo`（CFI 不依赖 IRQ） |

检查 ELF 是否有 CFI：`riscv64-unknown-elf-readelf -S <elf> | grep debug_frame`。

## 两条回溯路径（关键区分）

CFI 回溯的"起点"决定看到什么。UIS8852 有两种保存现场，对应两种种子：

### A. 崩溃/异常上下文（正在执行的线程）
- 现场：`g_osException->trace` 指向 `rt_hw_stack_frame`（32 个寄存器，cpuport.c:109）。
- 种子：`pc = frame.epc`（= mepc），`sp = trace + 128`（sp 不在帧里，=帧顶），其余寄存器从帧读。
- **ASSERT 陷阱**：trap 帧的 `ra` 被 `osAssertHandler` 的 `ecall` 覆盖（常等于 epc）。**不要用 trap 帧 ra**。CFI 从 `osAssertHandler`(mepc) 出发，用其 FDE 规则重算真实返回链（→ `_osSchedulerStackCheck` → `osSchedule` → …）。
- 对应 API：`cfi.unwind_exception(uw, mem, trace_addr)`。

### B. 挂起线程（每个任务的执行链）
- 现场：每个任务 TCB 的 `sp` 字段指向它被切出时保存的切换帧（同样是 `rt_hw_stack_frame` 布局）。
- 种子：`pc = mem[tcb.sp]`（resume 点 = epc 槽），`sp = tcb.sp + 128`。
- **这是根因常藏的地方**：崩溃点链只显示"谁触发了 assert"，而**真正溢出/阻塞的线程**的执行链要从它自己的 `tcb.sp` 回溯。经典案例：scheduler 栈检查 assert 在 tcpip 线程触发，但溢出的是 timer 任务——timer 的 keepalive 回调链只在 B 路径出现。
- 当前正在执行的线程（崩溃线程）的 `tcb.sp` resume_pc=0、无保存切换帧 → B 路径返回空（它的上下文在 A 路径的异常帧里）。`unwind_cfi.py` 自动标注"崩溃线程(见[1])"。
- 对应 API：`cfi.unwind_thread(uw, mem, tcb_sp)`。

## 终止条件（防误回溯）

`cfi.CFIUnwinder.unwind()` 逐帧上溯，遇到以下任一即停：
- 返回地址 `ra` 不在任何 FDE 覆盖范围（不是真实调用边界）
- `ra == 0`
- 下一帧 sp == 当前 sp（无增长，碰到 `osThreadExit` 等线程入口蹦床，其 CFA=自身 sp 会无限循环）
- sp 越出 `[stackAddr, stackAddr+stackSize]`（传入任务栈边界）
- 达到 `max_frames`（默认 48）

## CFI 局限（已知）

1. **已弹出的深栈帧回溯不到**：CFI 从当前 sp 向上（调用者方向）走。若某线程曾深入后又返回，那些已弹出帧（在当前 sp 之下）只剩栈残留，CFI 无法确定性还原——仍由 `unwind.py` 的栈扫描或人工看残留补充。例：timer 溢出时深至 `dns_send→PDCP`（已返回），但 B 路径从挂起点向上已能拿到 `keepalive 回调→阻塞 DNS` 根因链，足够。
2. **不解析局部变量值**：CFI 只给调用链（函数+偏移），不给 `to_thread`/`from_thread`/dns 字段等局部变量值（那是 TRACE32 用 location lists 才有的富度）。需要时可用已就位的 `riscv64-unknown-elf-gdb.exe` 做单帧深挖。
3. **依赖 `.debug_frame` 质量**：个别 `__attribute__((naked))`/纯汇编函数可能无 FDE，链到那里会断（属正常，保守停止优于误报）。

## 库 API（供其它脚本/技能复用）

```python
from common import Mem, Symbols
from cfi import CFIUnwinder, NoCFIError, unwind_exception, unwind_thread, fmt_chain

mem = Mem(dump_dir); syms = Symbols(elf)
try:
    uw = CFIUnwinder(syms)            # 解析全部 FDE，建 PC 索引；无 .debug_frame 抛 NoCFIError
except NoCFIError:
    ... # 回退启发式

# 崩溃上下文链
chain = unwind_exception(uw, mem, trace_addr)
# 某挂起线程链
chain = unwind_thread(uw, mem, tcb_sp, stack_lo=sa, stack_hi=sa+ss)
# 渲染
print(fmt_chain(chain, syms))
```

`CFIUnwinder.unwind(mem, start_pc, start_sp, registers=None, stack_lo=None, stack_hi=None, max_frames=48)` 是底层接口：`registers` 传种子帧的全部寄存器（regnum->val）可让 FP 相对 CFA 正确解析；只给 sp 也能跑（FP 帧可能早停）。

## 实现要点（cfi.py）

- FDE 经 `Symbols(...).dwarf.CFI_entries()` 取（CIE/FDE 类在 `elftools.dwarf.callframe`，指令属性是 `.opcode`/`.args`，**不是** `.op_name`/`.get_instructions`）。
- 全寄存器规则表跨帧携带：每帧用上一帧规则表算出调用者寄存器值，使 FP 相对（s0/fp）的 CFA 也能解析，不只 sp/ra。
- **offset 类指令是 factored offset**（DW_CFA_offset/offset_extended/val_offset），必须 `× data_alignment_factor`（本平台 -4）；def_cfa/def_cfa_offset 是 raw offset 不缩放。CFA = reg[base] + offset。
- 内存读全部走 `common.Mem`（PSRAM 0x80000000↔0x40000000、IRAM 0xc0200000↔0x10200000 别名透明，无需地址转换）。
