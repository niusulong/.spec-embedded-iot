# EC Platform Dump Analysis Reference

> **Auto-detection**: 所有 EC 芯片（EC626/EC626E/EC616）的关键 RAM 地址通过 `RAM_END - 固定偏移` 计算。
> 脚本从 MAP 文件 `Memory Configuration` 段自动提取 RAM_END 和 Flash 范围，无需手动指定。
> 固定偏移：reset_reason=-0x24, assert_pc=-0x20, assert_lr=-0x1C, magic=-0x18, store_ptr=-0x10。

## Table of Contents

1. [Memory Layout](#1-memory-layout) — RAM/Flash 区域、关键地址、符号地址
2. [Reset Reason Register](#2-reset-reason-register) — ResetReason_e 和 LastResetState_e
3. [ec_exception_store Structure](#3-ec_exception_store-structure) — 结构体布局、寄存器偏移、异常类型识别、FS Assert 检测、HardFault vs ASSERT 差异
4. [HardFault Register Source](#4-hardfault-register-source) — 硬件压栈 vs 软件保存
5. [ASSERT PC Calculation](#5-assert-pc-calculation) — GCC vs ARM CC 路径
6. [FreeRTOS TCB Layout](#6-freertos-tcb-layout) — TCB 偏移、栈填充模式
7. [Dump File Format](#7-dump-file-format) — RAM dump 格式、地址映射
8. [Exception Handler Architecture](#8-exception-handler-architecture) — 向量表、WDT 流程、ExcepConfigOp

## 1. Memory Layout

### 1.1 EC626 (2MB Flash)

| Region | Origin | Length | Attributes |
|--------|--------|--------|------------|
| RAM16_AREA | 0x00000000 | 0x00004000 (16KB) | xrw (ISR vector + PHY code) |
| RAM256_AREA | 0x00004000 | 0x00040000 (256KB) | xrw (data, bss, heap, stacks) |
| FLASH_APP | 0x0081F000 | 0x00189000 (~1524KB) | xr (application code) |

### 1.2 EC626E (4MB Flash)

| Region | Origin | Length | Attributes |
|--------|--------|--------|------------|
| RAM16_AREA | 0x00000000 | 0x00004000 (16KB) | xrw (identical to EC626) |
| RAM256_AREA | 0x00004000 | 0x00040000 (256KB) | xrw (identical to EC626) |
| FLASH_APP | 0x0081F000 | 0x002FD000 (~3060KB) | xr (application code) |

### 1.3 RAM Layout within RAM256_AREA (EC626 and EC626E identical)

```
0x00004000 ┌──────────────────┐
           │ PHY ISR code     │  (RAM16: 0x00000000-0x00004000)
           │ .data            │
           │ .bss             │
           │ ──────────────── │
           │ Task stacks      │  (malloc-allocated, 0xA5 fill guard)
           │ ──────────────── │
           │ Heap             │  (0x319A0 - 0x42000, ~58KB)
0x000309A0 ├──────────────────┤  __StackLimit (MSP bottom)
           │ Main Stack (MSP) │  4KB (0x309A0-0x319A0)
0x000319A0 ├──────────────────┤  __StackTop / _heap_memory_start
           │ Heap area        │  ~58KB
0x00042000 └──────────────────┘  _heap_memory_end
```

**Key point**: EC626 and EC626E have IDENTICAL RAM layout. RAM dump analysis is fully portable.

### 1.4 Key RAM Addresses (EC626 and EC626E identical)

| Address | Name | Description |
|---------|------|-------------|
| 0x43FDC | EC_RESET_REASON_ADDR | Reset reason register |
| 0x43FE0 | EC_ASSERT_PC_ADDR | ASSERT PC value |
| 0x43FE4 | EC_ASSERT_LR_ADDR | ASSERT LR value |
| 0x43FE8 | EC_EXCEPTION_MAGIC_ADDR | Exception magic (0x00EC00EC on crash) |
| 0x43FF0 | EC_EXCEP_STORE_RAM_ADDR | Pointer to excep_store struct |

### 1.5 Key Symbol Addresses (varies per build)

| Symbol | Address | Notes |
|--------|---------|------|
| __isr_vector | 0x0081F000 | Flash APP start |
| __stack_start | 0x0003099C | MSP start |
| __StackLimit | 0x000309A0 | MSP bottom |
| __StackTop | 0x000319A0 | MSP top |
| _heap_memory_start | 0x000319A0 | = __StackTop |
| _heap_memory_end | 0x00042000 | Heap end |
| excep_store | varies (bss) | Exception store struct |
| pxCurrentTCB | varies (bss) | FreeRTOS current task TCB pointer |

### 1.6 Flash Address Ranges

| Chip | Flash Start | Flash End | App Code Size |
|------|------------|-----------|---------------|
| EC626 | 0x0081F000 | 0x00A00000 | 1524KB |
| EC626E | 0x0081F000 | 0x00C00000 | 3060KB |

## 2. Reset Reason Register (EC_RESET_REASON_ADDR = 0x43FDC)

### ResetReason_e values

| Value | Name | Description |
|-------|------|-------------|
| 1 | RESET_REASON_HARDFAULT | Hardware exception |
| 2 | RESET_REASON_ASSERT | Software assertion |
| 3 | RESET_REASON_WDT | Watchdog timeout |
| 4 | RESET_REASON_FOTA | FOTA update triggered |
| 5 | RESET_REASON_FLHDRV | Flash driver triggered |
| 6 | RESET_REASON_XICOVL | XIC interrupt overflow |
| 7 | RESET_REASON_BAT | Battery low |
| 8 | RESET_REASON_TEMP | Temperature high |

### LastResetState_e (hardware register, distinct from reset_reason)

| Value | Name |
|-------|------|
| - | LAST_RESET_POR | Power-on reset |
| - | LAST_RESET_PAD | External pad reset |
| - | LAST_RESET_WDT | APB WDT reset |
| - | LAST_RESET_AONWDT | AON WDT reset |
| - | LAST_RESET_LOCKUP | Cortex-M LOCKUP |
| - | LAST_RESET_SWRESET | Software reset |
| - | LAST_RESET_BATLOW | Battery low hardware reset |
| - | LAST_RESET_TEMPHI | Temperature high hardware reset |

**Important**: reset_reason (0x43FDC) is written by software. LastResetState is a hardware register. They may differ.

## 3. ec_exception_store Structure

### Structure Layout (GCC, Cortex-M) — identical across EC626/EC626E

```
typedef struct _ec_exception_store {
    uint32_t ec_start_flag;          // +0x00: 0xA2A0A1A3 (ASSERT) or 0xF2F0F1F3 (HardFault)
    uint32_t ec_hardfault_flag;      // +0x04: 0xE2E0E1E3 (HardFault) or 0x0 (ASSERT)
    uint32_t ec_assert_flag;         // +0x08: 0xB2B0B1B3 (ASSERT) or 0x0 (HardFault)
    uint32_t ec_exception_count;     // +0x0C: incremental counter
    ec_m3_exception_regs excep_regs; // +0x10: register dump (see below)
    uint32_t func_call_stack[4];     // +0x8C: call trace (FUNC_CALL_TRACE only)
    uint8_t  curr_task_name[12];     // +0x9C: task name (HardFault path only)
    uint32_t curr_time;              // +0xA8: timestamp
    uint32_t fw_info;                // +0xAC: firmware info
    uint32_t ec_end_flag;            // +0xB0: 0xA0A1A2A9 (ASSERT) or 0xF0F1F2F9 (HardFault)
} ec_exception_store;
```

Total size: 0xB4 (180 bytes)

### ec_m3_exception_regs.stack_frame Layout

```
struct {
    uint32_t r0;         // +0x00
    uint32_t r1;         // +0x04
    uint32_t r2;         // +0x08
    uint32_t r3;         // +0x0C
    uint32_t r4;         // +0x10 (saved by ec_assert_regs)
    uint32_t r5;         // +0x14
    uint32_t r6;         // +0x18
    uint32_t r7;         // +0x1C
    uint32_t r8;         // +0x20
    uint32_t r9;         // +0x24
    uint32_t r10;        // +0x28
    uint32_t r11;        // +0x2C
    uint32_t r12;        // +0x30
    uint32_t sp;         // +0x34 (SP at crash time)
    uint32_t lr;         // +0x38 (LR at crash time)
    uint32_t pc;         // +0x3C (PC at crash time)
    uint32_t psr.value;  // +0x40 (xPSR)
    uint32_t exc_return; // +0x44 (EXC_RETURN value)
    uint32_t msp;        // +0x48 (MSP at crash time)
    uint32_t psp;        // +0x4C (PSP at crash time)
    uint32_t CONTROL;    // +0x50
    uint32_t BASEPRI;    // +0x54
    uint32_t PRIMASK;    // +0x58
    uint32_t FAULTMASK;  // +0x5C
} stack_frame;
```

### Exception Type Identification

| ec_start_flag | ec_hardfault_flag | ec_assert_flag | reset_reason | Type |
|---------------|-------------------|----------------|--------------|------|
| 0xA2A0A1A3 | 0x00000000 | 0xB2B0B1B3 | 2 (ASSERT) | **Software ASSERT** |
| 0xA2A0A1A3 | 0x00000000 | 0xB2B0B1B3 | 3 (WDT) | **WDT Timeout** (via ASSERT path) |
| 0xA2A0A1A3 | 0x00000000 | 0xB2B0B1B3 | 6 (XICOVL) | **XIC Overflow** |
| 0xF2F0F1F3 | 0xE2E0E1E3 | 0x00000000 | 1 (HARDFAULT) | **HardFault** |
| (none found) | — | — | 3 (WDT) | **Default_Handler trap / AON WDT** |
| (none found) | — | — | 0 or other | **No crash data** |

### FS Assert Detection

When lfs_assert() triggers, EC_ASSERT is called with magic values:
- R1 = 0x46535F41 ("FS_A")
- R2 = 0x73736572 ("sser")
- R3 = 0x745F7346 ("t_sF")

Check R1/R2/R3 against these values to identify filesystem-related assertions.

### HardFault vs ASSERT: Key Differences

**HardFault path** (Ec_HardFault_Handler):
- R0-R3, R12, LR, PC, xPSR from exception stack frame (hardware push)
- R4-R11 from ec_assert_regs inline assembly
- curr_task_name filled from pcTaskGetTaskName()
- Fault registers (HFSR, MFSR, BFSR, UFSR) read from SCB
- ec_start_flag = 0xF2F0F1F3
- ec_hardfault_flag = 0xE2E0E1E3
- ec_end_flag = 0xF0F1F2F9

**ASSERT path** (ec_assert_lite):
- R0-R11 from ec_assert_regs inline assembly
- R0-R3 = values BEFORE EC_ASSERT macro arguments
- R12 = 0x0 (explicitly set)
- PC = EC_ASSERT_PC_ADDR value - 5 (GCC path)
- LR = EC_ASSERT_LR_ADDR value (GCC path)
- Fault registers = 0 (not a hardware fault)
- curr_task_name NOT filled (stays empty)
- ec_start_flag = 0xA2A0A1A3
- ec_assert_flag = 0xB2B0B1B3
- ec_end_flag = 0xA0A1A2A9

**WDT path** (NMI -> ec_assert_lite):
- Same as ASSERT path, but reset_reason = 3 (RESET_REASON_WDT)
- R0=0, R1=3 (RESET_REASON_WDT), R2=0, R3=0
- PC/LR point to code at time of NMI, not original crash

## 4. HardFault Register Source

In `Ec_HardFault_Handler`, registers come from two sources:

```c
void Ec_HardFault_Handler(uint32_t *stack_sp, uint32_t *stack_psp, uint32_t stack_lr)
{
    if(stack_lr & 0x4)  // bit2=1 -> PSP was used
        stack = stack_psp;
    else                  // bit2=0 -> MSP was used
        stack = stack_sp;

    // Exception stack frame (hardware-pushed):
    excep_store.excep_regs.stack_frame.r0    = stack[0];
    excep_store.excep_regs.stack_frame.r1    = stack[1];
    excep_store.excep_regs.stack_frame.r2    = stack[2];
    excep_store.excep_regs.stack_frame.r3    = stack[3];
    excep_store.excep_regs.stack_frame.r12   = stack[4];
    excep_store.excep_regs.stack_frame.lr    = stack[5];
    excep_store.excep_regs.stack_frame.pc    = stack[6];
    excep_store.excep_regs.stack_frame.psr   = stack[7];

    // SP = stack pointer + 0x20 (after hardware pop)
    excep_store.excep_regs.stack_frame.sp = (uint32_t)stack + 0x20;
}
```

### HardFault Stack Frame Layout (hardware-pushed by Cortex-M)

```
Stack at entry:
  SP+0x00: R0
  SP+0x04: R1
  SP+0x08: R2
  SP+0x0C: R3
  SP+0x10: R12
  SP+0x14: LR (return address)
  SP+0x18: PC (faulting instruction address)
  SP+0x1C: xPSR
```

## 5. ASSERT PC Calculation

### GCC Path (EC626/EC626E uses GCC)

```c
// In EC_ASSERT macro:
*((unsigned int *)EC_ASSERT_PC_ADDR) = (int)__current_pc();  // = __builtin_return_address(0)
*((unsigned int *)EC_ASSERT_LR_ADDR) = (int)__GET_RETURN_ADDRESS();

// In ec_assert_lite:
excep_store.excep_regs.stack_frame.pc = *((unsigned int *)EC_ASSERT_PC_ADDR) - 5;
excep_store.excep_regs.stack_frame.lr = *((unsigned int *)EC_ASSERT_LR_ADDR);
```

**PC calculation**: `saved_PC = EC_ASSERT_PC_ADDR_value - 5`
- EC_ASSERT_PC_ADDR_value = `__builtin_return_address(0)` = return address of EC_ASSERT macro call
- The -5 offset accounts for the BL instruction to ec_assert_lite

**To find actual assert address**: `actual_addr = saved_PC + 5`

### ARM CC Path (not used on EC626, included for reference)

PC calculation differs: `saved_PC = EC_ASSERT_PC_ADDR_value - 0xA`

## 6. FreeRTOS TCB Layout (GCC Cortex-M)

```
Offset  Size  Field
0x00    4     pxTopOfStack
0x04    20    xStateListItem (xListItem = value+next+prev+owner+container)
0x18    20    xEventListItem
0x2C    4     uxPriority (with uxBasePriority in some versions)
0x30    4     pxStack (stack bottom pointer)
0x34    16    pcTaskName[configMAX_TASK_NAME_LEN]
0x44    4     (stack end / high water mark base)
0x48    4     uxTaskNumber or stack high water mark value
```

**Task name** at offset +0x34, **stack bottom** (pxStack) at offset +0x30.

TCB layout is identical across EC626/EC626E.

### Stack Fill Pattern

FreeRTOS fills task stack with 0xA5A5A5A5 (`tskSTACK_FILL_BYTE`).
Check bottom of stack for this pattern:
- 0xA5A5A5A5 intact -> no stack overflow
- Overwritten -> **stack overflow confirmed**

## 7. Dump File Format

### RAM Dump (typical file: RamDumpData_*.bin)

- Size: 0x44000 (272KB) = RAM16_AREA + RAM256_AREA
- File offset = RAM address directly (base = 0x00000000)
- Contains: all SRAM content from address 0 to 0x43FFF
- **EC626 and EC626E have the same RAM dump format**

### How to read a value from dump

```
file_offset = target_RAM_address
value = read_4_bytes_at(dump_file, file_offset)
```

No address translation needed — direct 1:1 mapping.

### Key addresses to read from dump

1. `EC_RESET_REASON_ADDR` (0x43FDC) -> reset reason enum value
2. `EC_ASSERT_PC_ADDR` (0x43FE0) -> get actual PC (for ASSERT/WDT path)
3. `EC_ASSERT_LR_ADDR` (0x43FE4) -> get actual LR (for ASSERT/WDT path)
4. `EC_EXCEPTION_MAGIC_ADDR` (0x43FE8) -> verify crash state (0x00EC00EC)
5. `EC_EXCEP_STORE_RAM_ADDR` (0x43FF0) -> find excep_store address
6. `excep_store` at address from #5 -> parse exception info
7. `pxCurrentTCB` -> read pointer, then read TCB at that address
8. Task stack region -> check guard pattern

## 8. Exception Handler Architecture

### All Exception Handlers on EC626

| Vector | Handler | Behavior | Writes excep_store? |
|--------|---------|----------|-------------------|
| NMI (2) | NMI_Handler (bsp_custom.c) | WDT -> EC_ASSERT(WDT) or silent reset | Yes (via ASSERT path) |
| HardFault (3) | HardFault_Handler (startup -> Ec_HardFault_Handler) | Full register save | Yes |
| MemManage (4) | Default_Handler | `b .` (infinite loop) | **No** |
| BusFault (5) | Default_Handler | `b .` (infinite loop) | **No** |
| UsageFault (6) | Default_Handler | `b .` (infinite loop) | **No** |
| SVC (11) | Default_Handler | `b .` (infinite loop) | **No** |
| PendSV (14) | PendSV_Handler (FreeRTOS) | Normal context switch | **No** |
| SysTick (15) | SysTick_Handler | Normal tick | **No** |

**Critical**: MemManage, BusFault, and UsageFault have NO meaningful handlers. They will trap in infinite loop until WDT fires. The WDT-ASSERT excep_store will NOT reflect the actual fault cause.

### WDT Timeout Flow

```
WDT expires
  -> NMI interrupt
  -> NMI_Handler()
     -> if is_in_excep_handler():
         stop WDT, stay stuck -> AON WDT fires eventually
     -> elif EXCEP_OPTION_SILENT_RESET:
         write RESET_REASON_WDT, system reset immediately
     -> else:
         EC_ASSERT(0, RESET_REASON_WDT, 0, 0)
         -> ec_assert_lite()
            -> write excep_store (type=ASSERT, reset_reason=WDT)
            -> while(1) loop
            -> AON WDT fires or user collects dump
```

### ExcepConfigOp Options

| Value | Name | Behavior |
|-------|------|----------|
| 0 | EXCEP_OPTION_DUMP_FLASH_EPAT_LOOP | Dump to flash (non-EC626) + EPAT UART output + while(1) |
| 1 | EXCEP_OPTION_PRINT_RESET | Print to console + system reset |
| 2 | EXCEP_OPTION_DUMP_FLASH_RESET | Dump to flash (non-EC626) + system reset |
| 3 | EXCEP_OPTION_DUMP_FLASH_EPAT_RESET | Dump to flash + EPAT + system reset |
| 4 | EXCEP_OPTION_SILENT_RESET | Immediate system reset, no data saved |

**EC626**: dump_ram_to_flash() is compiled out. EC626 relies on RAM retention during reset.
