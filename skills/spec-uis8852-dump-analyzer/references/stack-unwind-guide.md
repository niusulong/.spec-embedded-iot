# RISC-V 帧感知栈回溯（UIS8852）

UIS8852 的 AP 核（RV32 + Zc 压缩指令）默认**不使用 frame pointer**（`-fno-omit-frame-pointer` 未开）。这导致栈回溯极容易出错——这是本平台 dump 分析的最大坑。本文档给出可靠的回溯方法。

## 为什么"扫描栈找代码地址"会误判

最朴素的回溯：从 SP 向上扫描，把栈上所有"看起来像代码地址（落在 .text 区）"的值当返回地址。**这在 RISC-V 上不可靠**，因为：

- 栈上大量**数据**（局部变量、保存的寄存器、结构体成员）恰好落在函数地址范围内
- 这些数据值的前一条"指令"可能恰好是 `jal`/`jalr` 操作码字节，通过 call-site 检查也无法排除

### 本次实战的误判案例

堆耗尽死机的栈扫描同时出现：`osiWorkEnqueue`、`SHA224_256ProcessMessageBlock`（×3）、`DMA_Irq`、`Ps_LpmCallback`（×2）。其中：

- `osiWorkEnqueue+0x3a` 被误判为 `osMallocTrace` 的调用者。但反汇编 `osiWorkEnqueue` 显示它只调 `osiSemaphoreRelease`/`osiExitCritical`，**根本不调 osMalloc**。`+0x3a` 是 `beq` 跳转目标，栈上值是残留数据
- `DMA_Irq` 是之前某次 DMA 中断的栈残留，`g_osInterruptNest=1` 证明当前只有一层中断
- 真正的调用者是 `Ps_LpmCallback`（反汇编显示 `jal osMallocTrace`）

**结论**：启发式扫描只能给"候选"，必须用反汇编验证每一条。

## 正确方法 1：读 g_osIrqNo 确定中断（最权威）

如果 `g_osInterruptNest ≥ 1`（ISR 上下文），当前中断由 `g_osIrqNo`（DTCM，**uint8_t**）直接给出。`osInterruptDispatch`(cpuport.c) 入口：

```c
void osInterruptDispatch(uint32_t csr) {
    uint32_t irq = (csr & 0x00000FFF);   // 从 CSR 提取 IRQ 号
    g_osIrqNo = irq;                      // ★ 写入全局
    ...
    irq_func = g_irq_table[irq].handler;  // 查表
    (*irq_func)(irq, param);              // 调用 ISR
}
```

- 读 `g_osIrqNo` 得**内部 IRQ 号**（`csr & 0xFFF`）
- IRQ → 中断源：`g_osIrqNo` 是**内部号**，外部/源号 = `irq - 19`（`OS_EXT_IRQ_TO_IRQ(ext) = ext + 19`，os_hw.h）。用 `ext = irq - 19` 查 `idh.code/components/driver/include/chip/8852/chip_int_num.h` 的 `AP_INT_NUM_<ext>`（注释含中断名）。**不要**直接拿 `irq` 去匹配 `AP_INT_NUM_*`（数值不同的两套编号）
- IRQ → ISR：源码里 `grep "osInterruptInstall.*<中断源常量>"` 或读 `g_irq_table[irq].handler`

> 例：`g_osIrqNo=0x27(39)`（内部）→ `ext = 39 - 19 = 20` → `AP_INT_NUM_20` → `LTE_LPM5_INT` → "LTE lpm timer5 子帧中断" → `LPM_Isr`。

## 正确方法 2：帧感知回溯（prologue 解析）

RISC-V 函数 prologue 有固定模式，可从中提取**帧大小**和 **ra 保存偏移**：

```asm
func:
    addi sp, sp, -N        ; 或 c.addi16sp -N（大帧）/ cm.push {ra,...}, -N（Zcm）
    sw   ra, k(sp)         ; 或 c.swsp ra, k（Zcm；RV32 用 sw/c.swsp，无 sd/c.sdsp）
    sw   s0, ...
```

回溯算法（`unwind.py` 实现）：

1. 从 trap SP（`g_osException->trace + 128`）出发
2. 当前函数的 ra 保存在 `sp + ra_offset`
3. 读 `ra` → 得调用者函数（resolve 符号）
4. `sp += frame_size` → 移到调用者帧
5. 重复，直到 `osInterruptDispatch` 和被中断任务

### Zcm（Zc push/pop）prologue 解析

UIS8852 用 Zc 压缩指令，常见 prologue：

| 指令 | 含义 | ra 位置 |
|------|------|--------|
| `cm.push {ra, s0-sN}, -F` | 分配 F 字节，保存 ra + s0..sN | `sp + F - 4`（帧顶） |
| `c.addi16sp sp, -F` | 分配 F 字节（F≥16，大帧） | 配合 `sw ra, k(sp)` |
| `addi sp, sp, -F` | 标准 prologue | 配合 `sw ra, k(sp)` |
| `c.swsp ra, k` | 压缩 store ra（RV32） | `sp + k` |

`unwind.py` 的 `parse_prologue()` 用正则解析 objdump 输出提取这些。`cm.push` 没有显式 `sw ra` 时，ra 在 `sp + frame - 4`（Zcm 规范：寄存器存在帧高地址端）。

### 帧语义（解读栈值）

**栈地址 A 处的值 V**：拥有该栈帧的函数将**返回到 V**。即 V 的函数**调用了**该帧所有者。

读栈时按栈地址**从低（内层/当前）到高（外层/调用者）**，每条 ra 值指向它的调用者。从外到内的链：`osInterruptDispatch ← ISR ← ISR的callee ← ... ← assert点函数`。

## 正确方法 3：call-site 反汇编验证（必做）

对每个候选返回地址 V，反汇编 `V-4` / `V-2` 处，确认是一条调用指令：

| 指令 | 编码特征 |
|------|---------|
| `jal` | 低字节 = `0xef` |
| `jalr` | 低字节 = `0xe7` |
| `jr`（用于尾调用） | 低字节 = `0x67` |
| `c.jal` | 半字 `op=1`（`h&3==1`），`(h & 0xE000)==0x2000` |
| `c.jalr` | 半字 `op=2`（`h&3==2`），`(h & 0xE000)==0x8000` |

通过此检查可过滤大部分噪声，但**仍不够**（数据值恰好匹配）。最终确认：

```bash
riscv64-unknown-elf-objdump -d -C \
  --start-address=0x<V-0x10> --stop-address=0x<V+4> <elf>
```

看 `V` 前一条是否真是 `jal/jalr <被调用函数>`。本次案例中 `osiWorkEnqueue+0x3a` 前一条是 `beq`（跳转），不是 call → 排除。

## 致命陷阱

### 陷阱 1：trap 帧的 ra 被覆盖

ASSERT 场景下 `rt_hw_stack_frame.ra` **不是** assert 点的调用者。`osAssertHandler` 在 ecall 前会改写 ra（常等于 epc，或被改成数据指针如 `g_osApSystemMem` 地址）。

- **不要**从 `rt_hw_stack_frame.ra` 直接当调用者
- 从栈上 `do_check_*`/`dlmallocPrint` 等真实函数帧的 ra 找调用者

### 陷阱 2：malloc trace ring 最后一条 ≠ 崩溃时刻的 alloc

`gOsiMemRecords` 在 `osMallocTrace` 内 `dlMalloc` 返回**后**才记录。崩溃发生在 `dlMalloc` 内部（未返回），**记录未写入**。ring 末尾是崩溃前**上一次成功**的 alloc。

- 判断崩溃时刻的 osMalloc 调用者：看栈（帧感知回溯），**不是**看 ring 末尾
- 本次案例：ring 末尾全是 `SLOG_GetCommBuffer`，但崩溃时刻真正执行 osMalloc 的是栈上的 `Ps_LpmCallback(osMalloc 801)`

### 陷阱 3：ISR 上下文的栈混杂

ISR 上下文时，栈上同时有：
- 中断派发链（`osInterruptDispatch → ISR → ISR的callee → ...`）
- 被中断任务的链（`... → NAS_TASK 当前函数`）

两者在栈上交错，扫描会把被中断任务的函数误当 ISR 链一环。用 `g_osIrqNo` 锁定 ISR，用帧感知步进区分。

## 推荐工作流

1. `python unwind.py <dump> <elf>` → 拿到 `g_osIrqNo`（中断身份）+ prologue 表 + call-site 验证扫描结果
2. 对可疑的相邻链，反汇编确认 call site（见方法 3）
3. 结合源码调用图（`grep` 函数调用关系）双向核对
4. 输出真实调用链，标注每步的验证方式
