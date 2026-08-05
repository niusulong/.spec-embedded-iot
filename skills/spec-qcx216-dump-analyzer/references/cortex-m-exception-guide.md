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

进入异常时硬件自动压入 **当前栈**（MSP 或 PSP，由 EXC_RETURN 决定）8 个字。
压栈顺序：硬件依次压 R0,R1,R2,R3,R12,LR,PC,xPSR，**R0 在最低地址、xPSR 在最高地址**：

```
SP + 0x00  R0      <- 函数参数（assert Val 常在此；HardFault 的 fault 信息也在此）
SP + 0x04  R1
SP + 0x08  R2      <- 常是崩溃指令的访存地址/指针
SP + 0x0C  R3
SP + 0x10  R12
SP + 0x14  LR      <- 返回地址（调用者，崩溃函数的 caller）
SP + 0x18  PC      <- 异常发生时将要执行的指令（崩溃点）★ 最关键
SP + 0x1C  xPSR    <- bit24 Thumb 位应置位（合法帧的必要条件，0x21xxxxxx）
```

> ⚠️ 注意是 **R0 在低地址、xPSR 在高地址**（曾画反过，导致帧还原错误）。验证：PC 落在 ELF
> 代码段 + xPSR bit24=1 + LR 是合法调用返回点，三者同时成立才确认帧正确。

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

## 在 QCX216 dump 中还原 HardFault 现场

excepInfoStore 头部除 magic 外，散落异常时刻的关键寄存器/状态。HardFault 场景关键字段：

| 字段 | 含义 | 识别方法 |
|------|------|---------|
| magic1 | 有效异常转储 | store 首字 = `0xEC112013` |
| EXC_RETURN | 异常返回码（定 MSP/PSP/模式） | 扫 store 头部找值 ∈ {`0xFFFFFFF1`, `0xFFFFFFF9`, `0xFFFFFFFD`} |
| 异常帧 SP | 崩溃任务/主栈的 SP | EXC_RETURN=FD→当前任务栈范围；F9/F1→MSP 范围；用帧校验定位 |

**还原流程**（已自动化：`frame` / `full-analyze` 子命令，无需人工脚本）：
1. 扫 store 头部找 EXC_RETURN → 判定模式（`0xFFFFFFFD`=Thread/PSP 任务里崩，帧在该任务栈；
   `0xFFFFFFF9`=Thread/MSP；`0x...1`=Handler 异常嵌套）。
2. 按模式定 SP 候选：PSP → 当前任务栈（`pxCurrentTCB` 的 `pxStack`~`pxTopOfStack`）；
   MSP → `__StackLimit`~`__StackTop`。store 头部落该范围的栈值 + `pxTopOfStack` 作候选。
3. 对每个候选 SP 读 8 字帧 `{R0,R1,R2,R3,R12,LR,PC,xPSR}`，三重校验：PC∈代码段(`elf.is_code`)
   + xPSR bit24=1 + LR 是合法函数。三重通过才确认帧（避免把栈上巧合数据当帧）。
4. `PC` → objdump 反汇编确认崩溃指令类型（`LDR/STR` 访存、`BL` 跳转、未定义指令…）；
   `R0..R3` 看函数参数 / fault 访存地址；`LR` 给调用者。

> ⚠️ **偏移随固件版本变**（某 dump 实测 EXC_RETURN@store+0x50、PSP@+0x58，但**勿硬编码**——
> 脚本用扫描 + 帧校验定位）。CFSR/HFSR/MMFAR/BFAR（`0xE000ED28~38`）**不在 dump**（核内寄存器，
> DTools 未抓）。故 QCX216 HardFault 靠"异常帧 PC/LR + 反汇编指令语义"定位，而非 fault status。
> 例：`STR R1,[R2,#8]` 且 R2=0 → 向地址 0x8 写 → 空指针/链表损坏（无需 CFSR 即可判定）。

## 常见 HardFault 模式 → 根因

| 现象 | 可能根因 |
|------|---------|
| PC 指向 RAM 数据区 / `UNDEFINSTR` | 函数指针损坏 / 栈被踩后返回到非法地址 |
| `IBUSERR` + PC 在 Flash 代码区 | Flash 代码损坏（取指错误；用 `code-compare` 子命令核对 ELF 代码段 vs dump） |
| `DACCVIOL`/`PRECISERR` + BFAR 指向非法地址 | 空指针/野指针写、数组越界 |
| `UNALIGNED` | 强制类型转换导致未对齐访问 |
| `DIVBYZERO` | 整数除零 |
| 当前任务栈 `OVERFLOW` + HardFault | 栈溢出踩坏返回地址 |
