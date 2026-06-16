# CeuTask ASSERT in PsifSuspendInd — Dump 分析报告

## 0. 结构化摘要

> 以下信息供知识库检索使用，需完整准确填写。

| 字段 | 内容 |
|------|------|
| **工作项 ID** | NA |
| **平台** | EC626 |
| **模块** | PS/LWIP |
| **问题分类** | 状态机异常 |
| **症状关键词** | CeuTask ASSERT, PsifSuspendInd, Bearer Resume, EC_ASSERT, 协议栈崩溃 |
| **根因概述** | PS协议栈Bearer Resume流程中CedrProcBearerResumeIndSig调用PsifSuspendInd时内部断言条件不满足触发EC_ASSERT |
| **调用链摘要** | CeUpTaskEntry → CedrProcBearerResumeIndSig → PsifSuspendInd → EC_ASSERT |
| **检索关键词** | CeuTask, PsifSuspendInd, ASSERT, Bearer Resume, PS协议栈, dump分析, EC_ASSERT, libpsif |

---

## 基本信息

| 项目 | 值 |
|------|------|
| 平台 | EC626 (ARM Cortex-M, FreeRTOS) |
| Dump 文件 | RamDumpData_20260417_100823.bin (272KB) |
| MAP 文件 | app-demo-flash.map |
| ELF 文件 | app-demo-flash.elf |
| 分析日期 | 2026-06-04 |

## 异常类型

**EC_ASSERT（软件断言），非 HardFault**

| 字段 | 值 | 说明 |
|------|------|------|
| ec_start_flag | 0xA2A0A1A3 | EC_EXCEP_START_FLAG ✓ |
| ec_assert_flag | 0xB2B0B1B3 | EC_EXCEP_ASSERT_FLAG ✓ |
| ec_hardfault_flag | 0x00000000 | 未设置（非 HardFault） |
| ec_exception_count | 1 | 首次异常 |
| ec_end_flag | 0xA0A1A2A9 | EC_EXCEP_END_FLAG ✓ |
| 所有 Fault Status 寄存器 | 0x00000000 | 无硬件异常 |

**验证**：EC_EXCEPTION_MAGIC_ADDR (0x43FE8) = 0x00EC00EC ✓，EC_EXCEP_STORE_RAM_ADDR (0x43FF0) = 0x00010F24 ✓

## ASSERT 定位

### 崩溃函数

| 项目 | 值 |
|------|------|
| EC_ASSERT_PC_ADDR (0x43FE0) | 0x0095F3C9 |
| excep_store.PC | 0x0095F3C4 (0x0095F3C9 - 5) |
| **崩溃函数** | **PsifSuspendInd** (0x0095F394, size 0x64) |
| 函数内偏移 | +0x35 |
| 所属模块 | libpsif.a (psifadpt.o) — **预编译库，无源码** |
| 函数签名 | `void PsifSuspendInd(BOOL suspend)` |

### 调用者

| 项目 | 值 |
|------|------|
| EC_ASSERT_LR_ADDR (0x43FE4) | 0x00919ED7 |
| excep_store.LR | 0x00919ED7 |
| **调用者函数** | **CedrProcBearerResumeIndSig** (0x00919EC8, size 0x130) |
| 函数内偏移 | +0x0F |
| 所属模块 | libps.a (cedrbearer.o) — **预编译库，无源码** |

### 完整调用链

```
CeUpTaskEntry (0x009058F4, size 0x75C, R7=0x00905BC9)
  └→ CedrProcBearerResumeIndSig (0x00919EC8, size 0x130, LR=0x00919ED7)
       └→ PsifSuspendInd (0x0095F394, size 0x64, PC=0x0095F3C4)
            └→ EC_ASSERT triggered at offset +0x35
```

## 任务上下文

| 项目 | 值 |
|------|------|
| 任务名 | CeuTask |
| pxCurrentTCB | 0x0002DBB8 |
| 栈范围 | 0x0002CFF0 - 0x0002D5EC (1532 bytes) |
| SP (assert时) | 0x0002D564 |
| 栈高水位 | 384 bytes (25% 使用率) |
| 栈底 guard | 0xA5A5A5A5 ✓ (未溢出) |

## ec_assert_regs 保存的寄存器

```
R0   = 0x0001E03D   (RAM 数据指针)
R1   = 0x40000008   (外设地址: APB 0x40000000+)
R2   = 0x00000800   (值: 2048)
R3   = 0xF000D020   (可能是 XIP/Flash 地址)
R4   = 0x00000000
R5   = 0x00919ED7   (= LR, CedrProcBearerResumeIndSig+0x0F)
R6   = 0x00012344   (RAM 数据)
R7   = 0x00905BC9   (CeUpTaskEntry+0x2D5)
R8   = 0x00038950   (RAM 数据)
R9   = 0x00000000
R10  = 0x00038950   (RAM 数据, = R8)
R11  = 0x00038964   (RAM 数据, R8+0x14)
R12  = 0x00000000
SP   = 0x0002D564   (CeuTask 栈内, 有效)
LR   = 0x00919ED7   (CedrProcBearerResumeIndSig+0x0F)
PC   = 0x0095F3C4   (PsifSuspendInd+0x30)
xPSR = 0x20000000
MSP  = 0x00031980
PSP  = 0x0002D564
CONTROL = 0x00000002 (PSP 模式)
```

## 栈溢出分析

**全任务栈扫描结果：所有 23 个 FreeRTOS 任务栈的底部 0xA5A5A5A5 标记均完好，无栈溢出。**

CeuTask 栈使用率仅 25%，不可能溢出。

## 内存布局

| 区域 | 起始 | 大小 |
|------|------|------|
| RAM16_AREA | 0x00000000 | 16KB |
| RAM256_AREA | 0x00004000 | 256KB |
| FLASH_APP | 0x0081F000 | ~1.6MB |
| MCU Stack | 0x0002F62C - 0x0003062C | 4KB |
| Main Stack | 0x000309A0 - 0x000319A0 | 4KB |
| Heap | 0x000319A0 - 0x00042000 | ~58KB |

## 根因分析

### 结论：PS 协议栈 bearer resume 流程中 PsifSuspendInd 触发 ASSERT

**根因推断**：

1. **直接原因**：`PsifSuspendInd` 函数在处理 PS 挂起/恢复指示时触发了 `EC_ASSERT`

2. **触发场景**：`CedrProcBearerResumeIndSig`（Bearer Resume 指示信号处理函数）调用了 `PsifSuspendInd`，参数为 `BOOL suspend`。这表明在 Bearer Resume 过程中，PS 协议栈尝试通知 LWIP 层 PS 状态变化（suspend/resume），但 `PsifSuspendInd` 内部的某个断言条件不满足

3. **可能原因**：
   - **Bearer 状态不一致**：Resume 信号处理中调用了 SuspendInd，可能 suspend 参数值不符合预期（如 Resume 时传入了 suspend=TRUE，或网络接口状态与 suspend 操作不匹配）
   - **网络接口未初始化**：`PsifSuspendInd` 内部可能 ASSERT 了 netif 指针非空，但实际为空
   - **重复 suspend/resume**：可能存在重复的 suspend/resume 操作导致内部状态异常

4. **非根因**：栈溢出、堆损坏、HardFault 均已排除

### 建议排查方向

1. **查看 EPAT/UART 日志**：ASSERT 前 `PsifSuspendInd` 的两条 UNILOG 日志（`UNILOG_PSIF_PsifSuspendInd_1` / `_2`）会输出关键信息
2. **联系 EigenComm PS 团队**：`PsifSuspendInd` 和 `CedrProcBearerResumeIndSig` 均在预编译库 `libpsif.a` / `libps.a` 中，需要源码确认 ASSERT 条件
3. **复现条件**：关注 Bearer Resume 场景（如从 OOS 恢复、小区重选后的 bearer 恢复等）
4. **检查 PS 状态**：R3 = 0xF000D020 可能是 XIP 地址，可能与 Flash 上的配置有关

## 附录：栈回溯（从 SP=0x2D564 扫描代码地址）

| 栈偏移 | 地址 | 函数 |
|--------|------|------|
| SP+0x14 | 0x00919ED7 | CedrProcBearerResumeIndSig+0x0F |
| SP+0x3C | 0x00905BC9 | CeUpTaskEntry+0x2D5 |
| SP+0x54 | 0x0087CF05 | port.o (无名函数, 0x0087CF04+0x1) |