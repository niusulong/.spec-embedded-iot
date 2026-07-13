# EC Platform Crash Analysis Patterns

## Table of Contents

- [Pattern 1: HardFault — NULL Pointer](#pattern-1-hardfault--null-pointer-dereference)
- [Pattern 2: HardFault — Stack Overflow](#pattern-2-hardfault--stack-overflow)
- [Pattern 3: HardFault — BusFault](#pattern-3-hardfault--busfault-invalid-address-access)
- [Pattern 4: HardFault — Undefined Instruction](#pattern-4-hardfault--undefined-instruction)
- [Pattern 5: ASSERT — Software Assertion](#pattern-5-assert--software-assertion-failure)
- [Pattern 6: WDT Timeout (No Registers)](#pattern-6-wdt-timeout-no-exception-registers)
- [Pattern 7: Heap Corruption](#pattern-7-heap-corruption)
- [Pattern 8: Interrupt Context Crash](#pattern-8-interrupt-context-crash)
- [Pattern 9: Double Fault](#pattern-9-double-fault)
- [Pattern 10: PS/Prebuilt Library ASSERT](#pattern-10-psprebuilt-library-assert)
- [Pattern 11: WDT via NMI→ASSERT](#pattern-11-wdt-timeout-via-nmi---assert-path)
- [Pattern 12: Default_Handler Trap](#pattern-12-default_handler-trap-unhandled-configurable-fault)
- [Pattern 13: AON WDT Reset](#pattern-13-aon-wdt-reset)
- [Pattern 14: LOCKUP](#pattern-14-lockup-double-fault)
- [Pattern 15: XIC Overflow](#pattern-15-xic-interrupt-overflow)
- [Pattern 16: FS Assert](#pattern-16-fs-assert-filesystem-corruption)
- [Pattern 17: Silent Reset](#pattern-17-silent-reset-mode)
- [Pattern 18: Crash During Handler](#pattern-18-crash-during-crash-handler)
- [Pattern 19: Exception Config Impact](#pattern-19-exception-handler-configuration-impact)
- [Complete Crash-to-Dump Decision Tree](#complete-crash-to-dump-decision-tree)

## Pattern 1: HardFault — NULL Pointer Dereference

**Symptoms**:
- HFSR.FORCED = 1, MFSR.DACCVIOL = 1, MFSR.MMARVALID = 1
- MMFAR ≈ 0x00000000 or small value
- PC points to ldr/str instruction with Rn ≈ 0

**Analysis**:
1. Read PC from excep_store → resolve to function
2. Check MMFAR → target address of the faulting access
3. If MMFAR ≈ 0 → NULL pointer dereference
4. Check R0-R3 → which register was the base pointer

**Example**: `ldr r0, [r3, #0x10]` with R3=0 → MMFAR=0x10 → NULL->member access

## Pattern 2: HardFault — Stack Overflow

**Symptoms**:
- HFSR.FORCED = 1, MFSR.DACCVIOL = 1 or BFSR.STKERR = 1
- PC points to push/str[sp] instruction
- SP below or near task stack bottom

**Analysis**:
1. Read SP, pxStack from TCB
2. Check if SP < pxStack → direct overflow
3. Check stack bottom guard (0xA5A5A5A5) → guard corrupted = overflow
4. Calculate stack usage: (stack_top - SP) / stack_size
5. If usage > 95% → high risk even if guard intact

**Subtlety**: In async exceptions (HardFault from interrupt), the overflow may have occurred before the fault. SP at crash time may have recovered. Always check guard pattern.

## Pattern 3: HardFault — BusFault (Invalid Address Access)

**Symptoms**:
- HFSR.FORCED = 1, BFSR.PRECISERR = 1, BFSR.BFARVALID = 1
- BFAR points to peripheral or unmapped address

**Analysis**:
1. Check BFAR → what address was accessed
2. If BFAR in peripheral range (0x4xxxxxxx) → wrong peripheral register
3. If BFAR in unmapped RAM → array out-of-bounds or pointer corruption

## Pattern 4: HardFault — Undefined Instruction

**Symptoms**:
- HFSR.FORCED = 1, UFSR.UNDEFINSTR = 1
- PC points to data area or invalid code

**Analysis**:
1. Resolve PC → if PC in .data/.bss → function pointer corruption
2. Check LR → where was the corrupted call from
3. May indicate stack overflow that corrupted a saved LR on stack

## Pattern 5: ASSERT — Software Assertion Failure

**Symptoms**:
- ec_start_flag = 0xA2A0A1A3, ec_assert_flag = 0xB2B0B1B3
- All fault registers = 0
- ec_hardfault_flag = 0

**Analysis**:
1. Resolve PC (actual = saved_PC + 5 for GCC) → ASSERT location
2. Resolve LR → caller function
3. Check R0-R3 in excep_store → values before ASSERT arguments
   - These are NOT the ec_assert_lite(func, line, v1, v2, v3) parameters!
   - R0-R3 are saved by ec_assert_regs BEFORE ec_assert_lite is called
4. Check task name from TCB (not from excep_store.task_name, which is empty for ASSERT)
5. Identify which ASSERT condition failed (need source code or reverse engineering)

## Pattern 6: WDT Timeout (No Exception Registers)

**Symptoms**:
- No excep_store written (magic not set)
- Device reset with PMU indicating WDT reset
- No HardFault/ASSERT in dump

**Analysis**:
1. Check PMU reset reason register if available
2. Scan task stacks → any stack overflow could cause scheduler lockup
3. Look for infinite loops: check each task's stack for repeated patterns
4. Check heap integrity → corrupted heap could cause spin in malloc/free

## Pattern 7: Heap Corruption

**Symptoms**:
- HardFault or ASSERT in malloc/free/tx_byte_allocate
- Or: random crashes in different places (corrupted metadata)

**Analysis**:
1. Scan heap structure for invalid block headers
2. Check if any task stack overflowed into heap area
3. Look for patterns: if heap block boundaries corrupted → buffer overflow in adjacent allocation
4. Check func_call_stack in excep_store → if malloc/free → heap corruption likely

## Pattern 8: Interrupt Context Crash

**Symptoms**:
- EXC_RETURN bit2=0 (MSP used) or task_name from excep_store = handler name
- SP = MSP, not PSP

**Analysis**:
1. Identify which ISR was running (check IPSR in xPSR or vector table)
2. ISR stack = MSP, check MSP range
3. ISR cannot use task stack → if ISR overflows MSP, check ISR stack size
4. Look at __isr_stack_start/end symbols for ISR stack limits

## Pattern 9: Double Fault

**Symptoms**:
- ec_exception_count > 1
- Or: crash in HardFault handler itself

**Analysis**:
1. If count > 1, the first exception is the root cause
2. Second exception may be caused by corrupted state from first
3. Focus on the first exception's PC/LR and fault registers

## Pattern 10: PS/Prebuilt Library ASSERT

**Symptoms**:
- ASSERT in libps.a, libpsif.a, liblwip.a etc. (no source)
- PC in prebuilt function

**Analysis**:
1. Resolve PC to function name from MAP
2. Resolve LR to caller → trace back to application code
3. Check R0-R3 at crash time → may contain assert condition values
4. Look for related UNILOG debug messages in code (grep for function name)
5. Contact library vendor for ASSERT conditions

## Pattern 11: WDT Timeout via NMI -> ASSERT Path

**Symptoms**:
- excep_store present with ASSERT-style flags (ec_start_flag=0xA2A0A1A3, ec_assert_flag=0xB2B0B1B3)
- reset_reason = RESET_REASON_WDT (3)
- R1 = 3 (RESET_REASON_WDT), R0/R2/R3 = 0

**Analysis**:
1. WDT triggers NMI -> NMI_Handler() -> EC_ASSERT(0, RESET_REASON_WDT, 0, 0)
2. This enters ec_assert_lite() path, so excep_store IS written
3. Root cause is NOT the ASSERT itself but whatever caused WDT to expire
4. Check stack overflow scan results -> overflow can cause scheduler lockup
5. Look at PC/LR in excep_store -> these point to the code executing when NMI fired (usually while(1) loop)
6. Check if exception handler was already running (ec_exception_count > 1 or task context)

**WDT root causes (by likelihood)**:
- Default_Handler trap: unhandled MemManage/BusFault/UsageFault hits empty handler -> while(1)
- Task deadlock: mutex/semaphore circular wait
- Interrupt disabled too long: __disable_irq() without matching __enable_irq()
- Infinite loop in application code
- Task stack overflow corrupting scheduler data structures

**Key discriminator**: If excep_store is NOT found but reset_reason = WDT, the crash happened in a path that does NOT write excep_store:
- Default_Handler (MemManage/BusFault/UsageFault direct, not escalated to HardFault)
- Boot image crash
- AON WDT reset (LAST_RESET_AONWDT)

## Pattern 12: Default_Handler Trap (Unhandled Configurable Fault)

**Symptoms**:
- No excep_store written
- WDT reset (reset_reason = WDT or no data)
- MemManage, BusFault, or UsageFault vectors all alias to Default_Handler (infinite loop `b .`)

**Analysis**:
1. On EC626, MemManage/BusFault/UsageFault handlers are empty infinite loops
2. They do NOT write excep_store - system hangs until WDT fires
3. WDT fires -> NMI -> EC_ASSERT(RESET_REASON_WDT) -> excep_store written
4. The excep_store will show the WDT ASSERT, but the actual cause was the configurable fault
5. To diagnose: examine the task stack of the crashing task for clues
6. Check IPSR in stack context - if available, it may indicate which exception fired
7. Look at SCB registers in RAM (CFSR at 0xE000ED28, HFSR at 0xE000ED2C) - may still hold fault info

**Important**: This pattern is why a WDT timeout should always prompt investigation of whether a configurable fault was the real cause.

## Pattern 13: AON WDT Reset

**Symptoms**:
- No excep_store written
- Reset reason = LAST_RESET_AONWDT (hardware register)
- System reset with no software crash data

**Analysis**:
1. AON WDT is always-on at system start, must be explicitly stopped via slpManAonWdtStop()
2. During exception handling, code feeds AON WDT via slpManAonWdtFeed()
3. If exception handler takes too long (> AON WDT timeout), AON WDT fires
4. If a second exception occurs during crash handler, NMI_Handler stops main WDT but system stays stuck
5. Eventually AON WDT expires -> hardware reset with no crash data update

**Investigation**: Check if EC_EXCEPTION_MAGIC_ADDR (0x43FE8) has 0x00EC00EC -> if yes, an exception was being handled when AON WDT fired.

## Pattern 14: LOCKUP (Double Fault)

**Symptoms**:
- No excep_store written (or partially written)
- Reset reason may be LAST_RESET_LOCKUP if LockupReset is enabled
- Otherwise leads to WDT/AONWDT path

**Analysis**:
1. Cortex-M3 LOCKUP occurs when a fault happens during HardFault handler execution
2. Or when an escalated fault (already at HardFault priority) faults again
3. LockupReset can be configured via ResetLockupCfg():
   - true: immediate system reset (LAST_RESET_LOCKUP)
   - false: CPU stops, eventually WDT fires
4. No useful crash data in either case
5. The first fault's excep_store may be partially written (if LOCKUP happened during handler)

## Pattern 15: XIC Interrupt Overflow

**Symptoms**:
- excep_store may be present with reset_reason = RESET_REASON_XICOVL (6)
- Or no excep_store if XICOverFlowCallback() called __NVIC_SystemReset() directly

**Analysis**:
1. XIC (interrupt controller) overflow indicates interrupt storm
2. Too many interrupts pending simultaneously
3. Handler writes RESET_REASON_XICOVL and calls __NVIC_SystemReset()
4. Root cause: ISR stuck in infinite loop, interrupt re-entry, or peripheral malfunction
5. No stack trace or crash registers - system reset is immediate

## Pattern 16: FS Assert (Filesystem Corruption)

**Symptoms**:
- excep_store present with ASSERT-style flags
- R1 = 0x46535F41, R2 = 0x73736572, R3 = 0x745F7346 (FS_ASSERT_MAGIC values)
- PC in lfs_assert() or littlefs code

**Analysis**:
1. Triggered by littlefs lfs_assert() -> EC_ASSERT(test, FS_MAGIC0/1/2)
2. Filesystem detected internal inconsistency
3. Special handling: increments fs_assert_count, erases FS region on crash
4. If count reaches EC_FS_ASSERT_REFORMAT_THRESHOLD (10), FS reformatted on next boot
5. Investigate: flash wear, power loss during write, filesystem bug
6. Check FS region in flash for corruption patterns

## Pattern 17: Silent Reset Mode

**Symptoms**:
- No excep_store written
- EC_EXCEPTION_MAGIC_ADDR is cleared (~0x00EC00EC)
- Reset reason may be set but no crash registers

**Analysis**:
1. When PLAT_CONFIG_ITEM_FAULT_ACTION = EXCEP_OPTION_SILENT_RESET (4)
2. System resets immediately without saving crash data
3. EC_EXCEPTION_MAGIC_ADDR is immediately cleared
4. No analysis possible from dump - need to change config to debug mode

## Pattern 18: Crash During Crash Handler

**Symptoms**:
- excep_store partially written (some fields valid, others zero/garbage)
- ec_start_flag valid but ec_end_flag invalid
- Registers may be inconsistent

**Analysis**:
1. Second exception occurred while Ec_HardFault_Handler or ec_assert_lite was running
2. NMI_Handler detects is_in_excep_handler() and stops WDT but does not reset
3. System remains stuck in while(1)
4. Eventually AON WDT fires
5. Focus on the first exception's data (PC/LR that were already saved)
6. Be cautious of partially written fields

## Pattern 19: Exception Handler Configuration Impact

The behavior of crash dump depends on ExcepConfigOp configuration:

| Config | Flash Dump | End Behavior | Dump Availability |
|--------|-----------|-------------|-------------------|
| EXCEP_OPTION_DUMP_FLASH_EPAT_LOOP (0) | Yes (non-EC626) | while(1) loop | RAM + Flash + UART |
| EXCEP_OPTION_PRINT_RESET (1) | No | System reset | RAM only (if retained) |
| EXCEP_OPTION_DUMP_FLASH_RESET (2) | Yes (non-EC626) | System reset | RAM + Flash |
| EXCEP_OPTION_DUMP_FLASH_EPAT_RESET (3) | Yes (non-EC626) | System reset | RAM + Flash + UART |
| EXCEP_OPTION_SILENT_RESET (4) | No | System reset | None (magic cleared) |

**EC626 note**: dump_ram_to_flash() is compiled out (#ifndef CHIP_EC626). EC626 relies on RAM retention.

## Complete Crash-to-Dump Decision Tree

```
System Crash
├── HardFault
│   ├── Ec_HardFault_Handler() runs
│   │   ├── excep_store written (type=HardFault)
│   │   ├── reset_reason = RESET_REASON_HARDFAULT
│   │   └── Config determines: loop / reset / silent reset
│   └── LOCKUP (double fault in handler)
│       ├── No excep_store update
│       └── LockupReset or WDT/AONWDT
├── MemManage / BusFault / UsageFault (direct, not escalated)
│   ├── Default_Handler (infinite loop)
│   ├── WDT fires -> NMI -> EC_ASSERT(WDT)
│   │   ├── excep_store written (type=WDT, looks like ASSERT)
│   │   └── reset_reason = RESET_REASON_WDT
│   └── Or: AON WDT fires -> no data
├── EC_ASSERT / EC_ASSERT_LITE
│   ├── ec_assert_lite() runs
│   │   ├── excep_store written (type=ASSERT)
│   │   ├── reset_reason = RESET_REASON_ASSERT (or WDT/XIC if triggered by those)
│   │   └── Special case: FS_ASSERT has magic R1/R2/R3 values
│   └── Silent reset mode: magic cleared, no data
├── NMI (WDT timeout)
│   ├── First time (not in handler): EC_ASSERT(WDT) -> ASSERT path
│   └── Already in handler: stop WDT, stay stuck -> AON WDT
├── XIC Overflow
│   ├── RESET_REASON_XICOVL written
│   └── __NVIC_SystemReset() - no excep_store
└── Hardware Reset (POR/BAT/TEMP/PAD)
    └── No software data
```
