# RISC-V 异常与 abort 解码（UIS8852）

UIS8852 AP 核为 RISC-V RV32。异常通过 `mcause`/`mepc`/`mtval` CSR 保存到 `g_osException` 结构。本平台的 ASSERT 机制特殊：`osAssertHandler` 主动执行 `ecall` 陷入来保存蓝屏现场。

## abort 类型路由（gBlueScreenAbortType）

| 值 | 类型 | 含义 |
|----|------|------|
| `0xFE` | **ASSERT** | 软件断言失败（`OS_ASSERT` 宏）。`g_osErrorLog` 形如 `AP Assert. File: xxx.c, Line: N, PC: 0x...`。PC = assert 宏调用点的返回地址 |
| 其他 | **EXCEPTION** | 硬件异常，值 = `mcause & 0x1f`（异常码） |

## mcause 解码

`mcause`（32 位）：

- **bit31 = 1** → interrupt（异步）
- **bit31 = 0** → exception（同步），低 5 位 = 异常码

异常码（code = mcause & 0x1f）：

| code | 名称 | 含义 | UIS8852 典型场景 |
|------|------|------|-----------------|
| 0 | Instruction address misaligned | 指令地址未对齐 | 跳转到非对齐地址 |
| 1 | Instruction access fault | 取指访问错误 | XIP/PSRAM 代码损坏、非法跳转 |
| 2 | Illegal instruction | 非法指令 | 跳转到数据区、指令损坏、栈溢出覆盖返回地址 |
| 3 | Breakpoint | 断点（ebreak） | 调试断点 |
| 4 | Load address misaligned | load 未对齐 | 异常指针解引用 |
| 5 | Load access fault | load 访问错误 | **空指针/野指针读**、PMP 违例 |
| 6 | Store/AMO address misaligned | store 未对齐 | 异常指针写 |
| 7 | Store/AMO access fault | store 访问错误 | **空指针/野指针写**、只读区写、PMP 违例 |
| 8/9/11 | ECALL from U/S/M mode | 环境调用（系统调用） | **code=11 = `osAssertHandler` 主动 ecall**（见下） |
| 12+ | 更高级异常 | — | 较少见 |

> **关键**：`code=11`（Machine ECALL）在本平台**几乎都是 ASSERT**——`osAssertHandler` 用 `ecall` 指令主动陷入，让 bluescreen handler 保存全部寄存器到 `rt_hw_stack_frame`。看到 `mcause=0x3000000b` 不要当硬件错误，去看 `g_osAssert`/`g_osErrorLog`。

## mdcause 解码（平台扩展损坏原因）

`mdcause & 0x3`：

| 值 | 含义 |
|----|------|
| 0 | 未明确 |
| 1 | PMP（物理内存保护）违例 |
| 2 | Bus 错误 |
| 3 | NICE 长指令 |

访问错误（code 5/7）时，mdcause 区分是 PMP 拦截还是总线错误。

## mepc / mtval

- `mepc` = 触发异常的指令地址（EXCEPTION 场景）/ ecall 指令地址（ASSERT 场景，指向 `osAssertHandler`）
- `mtval` = 异常附加信息：访问错误的地址、非法指令编码等。ASSERT 场景常为 0

## ASSERT 机制详解

`OS_ASSERT(cond)` 宏：`if (!(cond)) osAssert(__FILE__, __LINE__)`。`osAssert` → `osAssertHandler(file, line)`：

1. 把 `file`/`line` 存入 `g_osAssert` 指向的结构
2. 把当前寄存器现场保存到 `g_osException` 指向的结构（`mcause=0x0B`，`mepc`=ecall 地址）
3. 执行 `ecall` 指令陷入 bluescreen handler
4. bluescreen handler 蓝屏、抓 dump

所以 ASSERT 场景：
- `g_osAssert.line` = 源码行号（`__LINE__`）
- `g_osAssert.pc` = assert 宏调用点的返回地址（指向 assert 所在函数，如 `do_check_chunk+0x3c`）
- `g_osException.mepc` = `osAssertHandler` 内 ecall 指令地址
- `g_osException.trace` → `rt_hw_stack_frame`（osAssertHandler ecall 时的寄存器现场）

## 反汇编确认 assert 分支

同一函数常有多个 `OS_ASSERT`，`g_osAssert.line` 已给出行号，但反汇编可**双重确认**：

```bash
riscv64-unknown-elf-objdump -d -C --start-address=<func_base> --stop-address=<func_end> <elf>
```

找 `li a1, <line>` 紧跟 `jalr osAssertHandler` 的代码块——`a1` = `__LINE__`。例：

```
c026cb80: lui a0, 0x80107        ; a0 = __FILE__ 指针
c026cb84: li  a1, 539            ; a1 = 539  (= __LINE__)
c026cb8c: auipc ra, ...
c026cb90: jalr osAssertHandler
c026cb94: ...                    ; g_osAssert.pc 指向这里（jalr 的返回地址）
```

`g_osAssert.pc` 应落在某个 `jalr osAssertHandler` 之后的地址，对照 `li a1, <line>` 即可确认是哪条 assert。

## 判断崩溃上下文：ISR vs 任务

`g_osInterruptNest`（DTCM，u8）：

- `0` → 任务上下文，`g_osCurrentThread` 是当前任务，栈是任务栈
- `≥1` → **ISR 上下文**，崩溃在中断里，`g_osCurrentThread` 是**被中断的任务**（其栈上有被中断的任务链，与中断链共存，需区分）

ISR 上下文时栈回溯要特别处理：栈上同时有"中断派发链"（osInterruptDispatch → ISR → ...）和"被中断任务链"。用 `g_osIrqNo` 确定具体中断（见 `stack-unwind-guide.md`）。
