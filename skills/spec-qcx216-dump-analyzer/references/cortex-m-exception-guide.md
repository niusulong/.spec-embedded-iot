# Cortex-M3 异常与 Fault 解码指南

QCX216 是 ARM Cortex-M3 (ARMv7-M)。本文档覆盖 HardFault 场景下的异常类型识别、
Fault Status 寄存器解码、异常栈帧格式。**ASSERT 场景不涉及本指南**（ASSERT 有明确文本），
本指南用于 `Exception Type = HardFault` 时的深入分析。

## Cortex-M3 异常类型

| 异常 | 向量号 | 处理函数 | 典型原因 |
|------|--------|---------|---------|
| Reset | 1 | Mcu_Reset_Handler | 正常启动 |
| NMI | 2 | Mcu_NMI_Handler | NMI 信号 |
| **HardFault** | 3 | Mcu_HardFault_Handler | MemManage/BusFault/UsageFault 升级，或不可恢复错误 |
| MemManage | 4 | Mcu_MemManage_Handler | MPU 违例、不可执行地址（XN） |
| BusFault | 5 | Mcu_BusFault_Handler | 总线错误（非法地址访问、预取失败） |
| UsageFault | 6 | Mcu_UsageFault_Handler | 未定义指令、除零、未对齐访问 |

> 若 MemManage/BusFault/UsageFault 未启用（默认禁用），所有 fault 升级为 **HardFault**。
> QCX216 的向量表里这些 handler 常指向同一桩地址（连续 2 字节 `B .`），意味着实际都进 HardFault。

## 异常栈帧（Cortex-M3 自动压栈）

进入异常时硬件自动压入 **当前栈**（MSP 或 PSP，由 EXC_RETURN 决定）8 个字：

```
SP + 0x00  xPSR
SP + 0x04  PC      <- 异常发生时将要执行的指令（崩溃点）
SP + 0x08  LR      <- 返回地址（调用者）
SP + 0x0C  R12
SP + 0x10  R3
SP + 0x14  R2
SP + 0x18  R1
SP + 0x1C  R0      <- 函数参数（assert Val 常在此）
```
压栈顺序是 R0 在低地址、xPSR 在高地址（满递减栈）。`PC` 是最关键的崩溃定位地址。

## EXC_RETURN（判断 MSP/PSP/模式）

异常返回时 LR 保存 EXC_RETURN：
- `0xFFFFFFF1` = 返回 Handler 模式，用 MSP（异常嵌套）
- `0xFFFFFFF9` = 返回 Thread 模式，用 MSP（main 栈 / 中断里触发的 fault）
- `0xFFFFFFFD` = 返回 Thread 模式，用 PSP（任务栈 / 任务里触发的 fault）

若 `context=interrupt`（ASSERT 在中断里），fault 栈帧在 MSP；若在任务里，在 PSP（即当前任务 TCB 的 `pxTopOfStack`）。

## Fault Status 寄存器解码

| 寄存器 | 地址 | 含义 |
|--------|------|------|
| CFSR | `0xE000ED28` | = MFSR(B0) | BFSR(B1) | UFSR(H2) |
| HFSR | `0xE000ED2C` | HardFault Status |
| DFSR | `0xE000ED30` | Debug Fault Status |
| MMFAR | `0xE000ED34` | MemManage 故障地址 |
| BFAR | `0xE000ED38` | BusFault 故障地址 |

### HFSR（HardFault）
- `bit31 FORCED=1`：由 MemManage/BusFault/UsageFault 升级而来 → 看下层 status
- `bit30 VECTTBL=1`：读向量表失败

### MFSR（MemManage，CFSR 低字节）
- `bit0 IACCVIOL`：指令访问违例（常为 XN 区执行 / 函数指针错误）
- `bit1 DACCVIOL`：数据访问违例（MPU）
- `bit3 MUNSTKERR`/`bit4 MSTKERR`：出/入栈错误
- `bit7 MMARVALID`：MMFAR 有效 → 看 MMFAR 故障地址

### BFSR（BusFault，CFSR 次字节）
- `bit0 IBUSERR`：指令预取错误（常见：跳转到非法地址/Flash 代码损坏）
- `bit1 PRECISERR`：精确数据错误 → BFAR 有效
- `bit2 IMPRECISERR`：非精确数据错误（BFAR 无效，难定位）
- `bit7 BFARVALID`：BFAR 有效 → 看 BFAR 故障地址

### UFSR（UsageFault，CFSR 高半字）
- `bit0 UNDEFINSTR`：未定义指令（代码损坏/跳转到数据区）
- `bit1 INVSTATE`：无效状态（如 Thumb 位错误，跳转到偶数地址执行 Thumb）
- `bit2 INVPC`：无效 PC 加载（EXC_RETURN 非法）
- `bit3 NOCP`：协处理器访问（FPU 不可用）
- `bit8 UNALIGNED`：未对齐访问
- `bit9 DIVBYZERO`：除零

## 在 QCX216 dump 中如何获取

当前核心版脚本对 HardFault 用「寄存器快照区代码地址扫描」定位 PC/LR 候选
（excepInfoStore 头部捕获的 Flash 代码地址）。精确的栈帧/fault 寄存器偏移
随固件版本变化，需要 **HardFault 样本**进一步逆向 excepInfoStore 中
`CFSR/HFSR/MMFAR/BFAR` 与栈帧 `{R0..R3,R12,LR,PC,xPSR}` 的确切位置。

**临时手段**：拿到 HardFault dump 后，可在 excepInfoStore 区域人工查找
- `0xE000ED2C` 附近的值（HFSR，若被转储）
- `0xE000ED28` 附近的值（CFSR）
- 栈帧特征：连续 8 个字，其中 PC/LR 落在 ELF 代码区，xPSR 高字节常为 `0x21`/`0x?`

## 常见 HardFault 模式 → 根因

| 现象 | 可能根因 |
|------|---------|
| PC 指向 RAM 数据区 / `UNDEFINSTR` | 函数指针损坏 / 栈被踩后返回到非法地址 |
| `IBUSERR` + PC 在 Flash 代码区 | Flash 代码损坏（见 [[7040012382-fsrf-read-limit]] 类完整性问题） |
| `DACCVIOL`/`PRECISERR` + BFAR 指向非法地址 | 空指针/野指针写、数组越界 |
| `UNALIGNED` | 强制类型转换导致未对齐访问 |
| `DIVBYZERO` | 整数除零 |
| 当前任务栈 `OVERFLOW` + HardFault | 栈溢出踩坏返回地址 |
