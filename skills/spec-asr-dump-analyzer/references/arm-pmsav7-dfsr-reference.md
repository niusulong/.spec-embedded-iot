# ARM Cortex-R5 PMSAv7 DFSR/FSC 官方编码参考

> 来源：ARM Architecture Reference Manual (DDI 0406C) + SEGGER Cortex-A/R Fault Implementation
> 生成日期：2026-05-26
> 适用平台：ASR1603 (Cortex-R5, PMSAv7 MPU)

---

## 1. PMSAv7 DFSR 寄存器位域布局

### 1.1 Data Fault Status Register (DFSR) - CP15 c5

读取方式：`MRC p15, 0, <Rd>, c5, c0, 0`

```
bit[3:0]    = Fault status bits[3:0] (FS[3:0])
bit[9:4]    = UNK/SBZP (未知/应为零)
bit[10]     = Fault status bit[4] (FS[4])
bit[11]     = WnR (Write not Read bit)
bit[12]     = ExT (External abort type)
bit[31:13]  = UNK/SBZP (未知/应为零)
```

**关键点**：
- WnR 位于 **bit[11]**，而非 bit[9]
- bit[10] 是 FS[4]（Fault Status 的高位），不是 "Software Step"
- PMSAv7 格式**没有** Domain 字段（bit[7:4] 为 UNK/SBZP）
- FSC 完整值 = `((DFSR >> 6) & 0x10) + (DFSR & 0x0F)`，即 FS[4] 拼接 FS[3:0]

### 1.2 Instruction Fault Status Register (IFSR) - CP15 c5

读取方式：`MRC p15, 0, <Rd>, c5, c0, 1`

```
bit[3:0]    = Fault status bits[3:0] (FS[3:0])
bit[9:4]    = UNK/SBZP
bit[10]     = Fault status bit[4] (FS[4])
bit[11]     = UNK/SBZP (IFSR 没有 WnR)
bit[12]     = ExT (External abort type)
bit[31:13]  = UNK/SBZP
```

### 1.3 Data Fault Address Register (DFAR) - CP15 c6

读取方式：`MRC p15, 0, <Rd>, c6, c0, 0`

DFAR 保存触发 Data Abort 的内存地址，但**并非所有 fault 类型都保证 DFAR 有效**。

---

## 2. PMSAv7 FSC (Fault Status Code) 编码表

### 2.1 Data Fault Sources (DFSR)

| FSC 值 | 枚举名 | 描述 | DFAR 有效? |
|--------|--------|------|-----------|
| 0x00 | BACKGROUND_FAULT | Background fault (MPU fault) | 是 |
| 0x01 | ALIGNMENT_FAULT | Alignment fault | 是 |
| 0x02 | DEBUG_EVENT | (A)synchronous Watchpoint debug event | - |
| 0x03 | - | Reserved | - |
| 0x04 | INSTRUCTION_CACHE_MAINT | Instruction cache maintenance fault | - |
| 0x05 | - | Reserved | - |
| 0x06 | TRANSLATION_FAULT_FIRST | Translation fault, first level (MPU fault) | 是 |
| 0x07 | TRANSLATION_FAULT_SECOND | Translation fault, second level (MPU fault) | 是 |
| 0x08 | SYNC_EXTERNAL_ABORT | Synchronous external abort | 是 |
| 0x09 | - | Reserved | - |
| 0x0A | - | Reserved | - |
| 0x0B | - | Reserved | - |
| 0x0C | SYNC_EXTERNAL_ABORT_L1 | Synchronous external abort, first level (translation) | 是 |
| 0x0D | PERMISSION_FAULT | **Permission fault (MPU fault)** | **是** |
| 0x0E | ALIGNMENT_FAULT_FIRST | Alignment fault, first level | 是 |
| 0x0F | - | Reserved | - |
| 0x10 | - | Reserved | - |
| 0x14 | LOCKDOWN | Implementation defined (Lock-down) | - |
| 0x16 | ASYNC_EXTERNAL_ABORT | **Asynchronous external abort** | **否** |
| 0x18 | ASYNC_PARITY_ERROR | Asynchronous parity error on memory access | 否 |
| 0x19 | SYNC_PARITY_ERROR | Synchronous parity error on memory access | 是 |
| 0x1A | COPROCESSOR_ABORT | Implementation defined (Co-processor abort) | - |

### 2.2 Instruction Fault Sources (IFSR)

| FSC 值 | 描述 |
|--------|------|
| 0x00 | Background fault (MPU fault) |
| 0x02 | (A)synchronous Watchpoint debug event |
| 0x04 | Instruction cache maintenance fault |
| 0x06 | Translation fault, first level (MPU fault) |
| 0x07 | Translation fault, second level (MPU fault) |
| 0x08 | Synchronous external abort |
| 0x0D | Permission fault (MPU fault) |
| 0x16 | Asynchronous external abort |
| 0x18 | Asynchronous parity error |
| 0x19 | Synchronous parity error |

---

## 3. FSC 解码方法

### 3.1 SEGGER 官方解码代码

```c
//
// PMSAv7 Data fault sources
//
typedef enum {
    BACKGROUND_FAULT        = 0x00u,  // Background fault (MPU fault).
    ALIGNMENT_FAULT         = 0x01u,  // Alignment fault.
    DEBUG_EVENT             = 0x02u,  // (A)synchronous Watchpoint debug event.
    SYNC_EXTERNAL_ABORT     = 0x08u,  // Synchronous external abort.
    PERMISSION_FAULT        = 0x0Du,  // Permission fault (MPU fault).
    LOCKDOWN                = 0x14u,  // Implementation defined (Lock-down).
    ASYNC_EXTERNAL_ABORT    = 0x16u,  // Asynchronous external abort.
    ASYNC_PARITY_ERROR      = 0x18u,  // Asynchronous parity error on memory access.
    SYNC_PARITY_ERROR       = 0x19u,  // Synchronous parity error on memory access.
    COPROCESSOR_ABORT       = 0x1Au,  // Implementation defined (Co-processor abort).
} PMSAv7_DATA_FAULT_SOURCE;

//
// 从 DFSR 寄存器值提取 FSC
// PMSAv7 格式: FS[4] = bit[10], FS[3:0] = bit[3:0]
//
PMSAv7_DATA_FAULT_SOURCE DecodeDFSR(unsigned int dfsr) {
    return (PMSAv7_DATA_FAULT_SOURCE)(((dfsr >> 6) & 0x10u) + (dfsr & 0x0Fu));
}

//
// 从 DFSR 寄存器值提取 WnR
// WnR = bit[11]
//
int DecodeWnR(unsigned int dfsr) {
    return (dfsr >> 11) & 1;
}
```

### 3.2 实际解码示例

**DFSR = 0x80D 的解码过程**：

```
DFSR = 0x0000080D = 0b 0000_0000_0000_0000_0000_1000_0000_1101

bit[11] = 1  →  WnR = 1 (写操作触发)
bit[10] = 0  →  FS[4] = 0
bit[3:0] = 0xD = 13  →  FS[3:0] = 0x0D

FSC = (0 << 4) + 0x0D = 0x0D = PERMISSION_FAULT
结论: MPU 权限错误（写操作）
DFAR 有效: 是，保存了触发错误的内存地址
```

---

## 4. DFAR 有效性规则

| Fault 类型 | FSC | DFAR 有效 | 说明 |
|------------|-----|----------|------|
| Background fault | 0x00 | 是 | MPU 未命中 |
| Alignment fault | 0x01 | 是 | 对齐错误 |
| Translation fault | 0x06/0x07 | 是 | 地址翻译失败 |
| Synchronous external abort | 0x08/0x0C | 是 | 同步外部中止 |
| **Permission fault** | **0x0D** | **是** | **MPU 权限违规** |
| Asynchronous external abort | 0x16 | **否** | 异步外部中止 |
| Asynchronous parity error | 0x18 | **否** | 异步奇偶校验错误 |
| Synchronous parity error | 0x19 | 是 | 同步奇偶校验错误 |

**关键区别**：
- **同步错误** (Permission fault, Sync external abort)：PC 指向触发异常的指令，DFAR 有效
- **异步错误** (Async external abort, Async parity error)：PC 不一定指向触发异常的指令，DFAR 无效

---

## 5. 与 VMSAv7 Short-descriptor 格式对比

### 5.1 VMSAv7 Short-descriptor DFSR 位域

```
bit[3:0]    = Fault status bits[3:0] (FS[3:0])
bit[7:4]    = Domain                ← PMSAv7 没有，为 UNK/SBZP
bit[8]      = UNK/SBZP              ← PMSAv7 同样为 UNK/SBZP
bit[9]      = LPAE indicator        ← PMSAv7 为 UNK/SBZP，不是 WnR
bit[10]     = Fault status bit[4] (FS[4])
bit[11]     = WnR (Write not Read bit)  ← 两种格式都在 bit[11]
bit[12]     = ExT (External abort type)
bit[13]     = Cache maintenance fault (LPAE only)
```

### 5.2 VMSAv7 Short-descriptor FSC 编码

| FSC 值 | 描述 | 级别 |
|--------|------|------|
| 0x00 | Background fault (MMU fault) | - |
| 0x01 | Alignment fault | - |
| 0x02 | Debug event | - |
| 0x06 | Translation fault | First level |
| 0x07 | Translation fault | Second level |
| 0x08 | Synchronous external abort | - |
| 0x0B | Synchronous external abort | First level (translation) |
| 0x0C | Synchronous external abort | Second level (translation) |
| 0x0D | Permission fault (MMU fault) | First level |
| 0x0F | Permission fault (MMU fault) | Second level |
| 0x16 | Asynchronous external abort | - |
| 0x18 | Asynchronous parity error | - |
| 0x19 | Synchronous parity error | - |

### 5.3 两种格式共同点

- **WnR 均在 bit[11]**（不是 bit[9]）
- **FS[4] 均在 bit[10]**
- **FSC=0x0D 均为 Permission fault**（不是异步外部中止）
- **FSC=0x16 均为 Asynchronous external abort**
- FSC 解码公式相同：`((DFSR >> 6) & 0x10) + (DFSR & 0x0F)`

---

## 6. 常见错误辨析

| 错误描述 | 正确值 | 来源 |
|----------|--------|------|
| FSC=0x0D 是异步外部中止 | FSC=0x0D 是 **Permission fault (MPU/MMU)** | DDI 0406C Table B3-11 |
| FSC=0x16 是 Permission fault | FSC=0x16 是 **Asynchronous external abort** | DDI 0406C Table B3-11 |
| WnR 在 bit[9] | WnR 在 **bit[11]** | DDI 0406C Table B3-9 |
| bit[10] 是 Software Step | bit[10] 是 **FS[4]** (Fault Status bit[4]) | DDI 0406C Table B3-9 |
| PMSAv7 DFSR 有 Domain 字段 | PMSAv7 **没有** Domain 字段，bit[7:4] 为 UNK/SBZP | DDI 0406C Table B3-9 |
| 0x80D 中 bit[8]=1 | 0x80D = 0b100000001101，**bit[8]=0，bit[11]=1** | 二进制计算 |

---

## 7. 参考来源

| 文档 | 编号 | 说明 |
|------|------|------|
| ARM Architecture Reference Manual | DDI 0406C | ARMv7-R 架构参考手册，Cortex-R5 权威规范 |
| SEGGER Cortex-A/R Fault | https://kb.segger.com/Cortex-A/R_Fault | SEGGER 官方实现，直接引用 DDI 0406C |
| ARM Cortex-R5 Technical Reference Manual | DDI 0460C | Cortex-R5 处理器技术参考手册 |
