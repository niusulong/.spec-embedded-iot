# EC Platform Cortex-M Exception Reference

## Table of Contents

1. [Exception Types and Priority](#1-exception-types-and-priority)
2. [Fault Status Register Decoding](#2-fault-status-register-decoding) — HFSR, MFSR, BFSR, UFSR
3. [Common Fault Scenarios](#3-common-fault-scenarios) — DACCVIOL, PRECISERR, UNDEFINSTR, INVSTATE, STKERR
4. [EXC_RETURN Decoding](#4-exc_return-decoding)
5. [xPSR Decoding](#5-xpsr-decoding)
6. [Crash Instruction Pattern Analysis](#6-crash-instruction-pattern-analysis) — 矛盾分析法、栈溢出检测
7. [Thumb Addressing](#7-thumb-addressing)

## 1. Exception Types and Priority

| Exception | Vector | Priority | Notes |
|-----------|--------|----------|-------|
| Reset | 1 | -3 (highest) | |
| NMI | 2 | -2 | |
| HardFault | 3 | -1 | Catch-all for disabled/configurable faults |
| MemManage | 4 | Configurable | MPU violation |
| BusFault | 5 | Configurable | Bus error |
| UsageFault | 6 | Configurable | Undefined instruction, div-by-zero, etc. |
| SVCall | 11 | Configurable | |
| PendSV | 14 | Configurable | |
| SysTick | 15 | Configurable | |

## 2. Fault Status Register Decoding

### HFSR (0xE000ED2C)
| Bit | Name | Meaning |
|-----|------|---------|
| 1 | VECTTBL | Vector table bus fault |
| 30 | FORCED | Escalated from configurable fault (MemManage/BusFault/UsageFault) |
| 31 | DEBUGEVT | Debug event |

**Most common**: FORCED bit set → check MFSR/BFSR/UFSR for root cause.

### MFSR (0xE000ED28, uint8)
| Bit | Name | Meaning |
|-----|------|---------|
| 0 | IACCVIOL | Instruction access violation (MPU/XN) |
| 1 | DACCVIOL | Data access violation (MPU) |
| 3 | MUNSTKERR | MemManage unstacking error on exception return |
| 4 | MSTKERR | MemManage stacking error on exception entry |
| 7 | MMARVALID | MMFAR register contains valid fault address |

### BFSR (0xE000ED29, uint8)
| Bit | Name | Meaning |
|-----|------|---------|
| 0 | IBUSERR | Instruction bus error |
| 1 | PRECISERR | Precise data bus error (fault address valid) |
| 2 | IMPREISERR | Imprecise data bus error |
| 3 | UNSTKERR | BusFault on exception return unstacking |
| 4 | STKERR | BusFault on exception entry stacking |
| 7 | BFARVALID | BFAR register contains valid fault address |

### UFSR (0xE000ED2A, uint16)
| Bit | Name | Meaning |
|-----|------|---------|
| 0 | UNDEFINSTR | Undefined instruction executed |
| 1 | INVSTATE | Invalid state (ARM mode in Thumb-only CPU) |
| 2 | INVPC | Invalid EXC_RETURN value |
| 3 | NOCP | Coprocessor instruction without coprocessor |
| 8 | UNALIGNED | Unaligned access |
| 9 | DIVBYZERO | Divide by zero |

## 3. Common Fault Scenarios

### 3.1 HardFault with FORCED + DACCVIOL + MMARVALID
- **Root cause**: NULL pointer write or array out-of-bounds
- **Check**: MMFAR = target address, PC = faulting instruction
- If MMFAR ≈ 0 → NULL pointer dereference
- If MMFAR in stack range → stack pointer corruption

### 3.2 HardFault with FORCED + PRECISERR + BFARVALID
- **Root cause**: Bus access to invalid/peripheral address
- **Check**: BFAR = faulting address, PC = faulting instruction

### 3.3 HardFault with FORCED + UNDEFINSTR
- **Root cause**: Jumped to data area / corrupted code / wrong function pointer
- **Check**: PC = address of undefined instruction

### 3.4 HardFault with FORCED + INVSTATE
- **Root cause**: Attempted ARM mode execution on Thumb-only CPU
- Usually: corrupted function pointer with bit0=0

### 3.5 HardFault with STKERR/UNSTKERR
- **Root cause**: Stack pointer points to invalid memory
- Usually: SP corrupted by earlier bug, or stack overflow into protected region

## 4. EXC_RETURN Decoding

| Bit | Value | Meaning |
|-----|-------|---------|
| [2] | 0 | MSP used on return |
| [2] | 1 | PSP used on return (task context) |
| [3] | 0 | Return to Handler mode |
| [3] | 1 | Return to Thread mode |
| [4] | 0 | Floating-point context on stack (FPU) |
| [4] | 1 | No floating-point context |

Common values:
- `0xFFFFFFF9`: Return to Thread mode, MSP (main stack)
- `0xFFFFFFFD`: Return to Thread mode, PSP (process/task stack)
- `0xFFFFFFF1`: Return to Handler mode, MSP

## 5. xPSR Decoding

| Bits | Name | Meaning |
|------|------|---------|
| [31:24] | N/Z/C/V/Q | Condition flags |
| [8:0] | IPSR | Exception number being processed |
| 9 | I | Interrupt mask (1=IRQ disabled) |

IPSR values: 0=Thread, 2=NMI, 3=HardFault, 4=MemManage, 5=BusFault, 6=UsageFault, etc.

## 6. Crash Instruction Pattern Analysis

### 6.1 Data Abort (DACCVIOL / PRECISERR)
- **str [Rn, #offset]** → Writing to invalid address → check Rn value
- **ldr [Rn, #offset]** → Reading from invalid address → check Rn value
- **push {Rn, ...}** → Stack overflow (SP below stack limit) → check SP vs stack range
- **pop {Rn, ...}** → Stack corruption during return → check stack integrity

### 6.2 Contradiction Analysis
If push succeeded but str[sp] failed → SP was valid for push but corrupted before str → investigate what modified SP between push and str.

### 6.3 Stack Overflow Detection
- **Direct**: Stack bottom guard (0xA5A5A5A5 in FreeRTOS) overwritten
- **Indirect**: SP below stack allocation range, or high water mark > allocation
- **Subtle**: SP appears OK at crash time, but overflow occurred earlier and corrupted adjacent memory

## 7. Thumb Addressing
- Cortex-M executes only Thumb instructions
- Function addresses in LR have bit0=1 (Thumb indicator)
- Actual code address = addr & ~1
- When looking up symbols: use (addr & ~1) for MAP lookup
- PC saved in exception frame: bit0=0 (already stripped by hardware)
