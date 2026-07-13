#!/usr/bin/env python3
"""EC platform exception store parsing and Cortex-M fault decoding."""

from ec_constants import (
    u32, u16, u8,
    HEADER_SIZE, SF_PSR, SF_EXC_RETURN, SF_MSP, SF_PSP, SF_CONTROL,
    SF_BASEPRI, SF_PRIMASK, SF_FAULTMASK, SF_SIZE, STORE_SIZE,
    OFF_FUNC_CALL, OFF_TASK_NAME, OFF_CURR_TIME, OFF_FW_INFO, OFF_END_FLAG,
    EC_EXCEP_START_FLAG, EC_HARDFAULT_START_FLAG, EC_EXCEP_ASSERT_FLAG,
    EC_HARDFAULT_FLAG, FS_ASSERT_MAGIC0, FS_ASSERT_MAGIC1, FS_ASSERT_MAGIC2,
    RESET_REASON_WDT, RESET_REASON_XICOVL,
    RESET_REASON_HARDFAULT, RESET_REASON_ASSERT, RESET_REASON_FOTA,
    RESET_REASON_FLHDRV, RESET_REASON_BAT, RESET_REASON_TEMP,
)


def parse_excep_store(dump_data, store_addr, config):
    """Parse ec_exception_store from RAM dump data."""
    data = dump_data[store_addr:store_addr + STORE_SIZE]

    result = {
        'ec_start_flag': u32(data, 0),
        'ec_hardfault_flag': u32(data, 4),
        'ec_assert_flag': u32(data, 8),
        'ec_exception_count': u32(data, 12),
    }

    rr_addr = config['reset_reason_addr']
    if rr_addr + 4 <= len(dump_data):
        result['reset_reason'] = u32(dump_data, rr_addr)
    else:
        result['reset_reason'] = 0

    if result['ec_start_flag'] == EC_EXCEP_START_FLAG and result['ec_assert_flag'] == EC_EXCEP_ASSERT_FLAG:
        if result['reset_reason'] == RESET_REASON_WDT:
            result['type'] = 'WDT'
        elif result['reset_reason'] == RESET_REASON_XICOVL:
            result['type'] = 'XIC'
        else:
            result['type'] = 'ASSERT'
    elif result['ec_start_flag'] == EC_HARDFAULT_START_FLAG and result['ec_hardfault_flag'] == EC_HARDFAULT_FLAG:
        result['type'] = 'HardFault'
    elif result['ec_hardfault_flag'] == EC_HARDFAULT_FLAG:
        result['type'] = 'HardFault'
    else:
        result['type'] = 'Unknown'

    sf_base = HEADER_SIZE
    regs = {}
    reg_names = ['R0','R1','R2','R3','R4','R5','R6','R7',
                 'R8','R9','R10','R11','R12','SP','LR','PC']
    for i, name in enumerate(reg_names):
        regs[name] = u32(data, sf_base + i * 4)

    regs['xPSR']        = u32(data, sf_base + SF_PSR)
    regs['EXC_RETURN']  = u32(data, sf_base + SF_EXC_RETURN)
    regs['MSP']         = u32(data, sf_base + SF_MSP)
    regs['PSP']         = u32(data, sf_base + SF_PSP)
    regs['CONTROL']     = u32(data, sf_base + SF_CONTROL)
    regs['BASEPRI']     = u32(data, sf_base + SF_BASEPRI)
    regs['PRIMASK']     = u32(data, sf_base + SF_PRIMASK)
    regs['FAULTMASK']   = u32(data, sf_base + SF_FAULTMASK)
    result['regs'] = regs

    fault_base = sf_base + SF_SIZE
    result['fault'] = {
        'SHCSR':  u32(data, fault_base),
        'MFSR':   u8(data, fault_base + 4),
        'BFSR':   u8(data, fault_base + 5),
        'UFSR':   u16(data, fault_base + 6),
        'HFSR':   u32(data, fault_base + 8),
        'DFSR':   u32(data, fault_base + 12),
        'MMFAR':  u32(data, fault_base + 16),
        'BFAR':   u32(data, fault_base + 20),
        'AFSR':   u32(data, fault_base + 24),
    }

    result['func_call_stack'] = [u32(data, OFF_FUNC_CALL + i*4) for i in range(4)]
    result['task_name'] = data[OFF_TASK_NAME:OFF_TASK_NAME+12].split(b'\x00')[0].decode('ascii', errors='replace')
    result['curr_time'] = u32(data, OFF_CURR_TIME)
    result['fw_info']   = u32(data, OFF_FW_INFO)
    result['ec_end_flag'] = u32(data, OFF_END_FLAG)

    result['is_fs_assert'] = (
        regs['R1'] == FS_ASSERT_MAGIC0 and
        regs['R2'] == FS_ASSERT_MAGIC1 and
        regs['R3'] == FS_ASSERT_MAGIC2
    )

    return result


def decode_fault(fault):
    """Decode Cortex-M fault status registers into human-readable strings."""
    lines = []

    hfsr = fault['HFSR']
    if hfsr & (1 << 1):
        lines.append("  VECTTL: Vector table read fault")
    if hfsr & (1 << 30):
        lines.append("  FORCED: Escalated from configurable fault")
    if hfsr & (1 << 31):
        lines.append("  DEBUGEVT: Debug event")

    mfsr = fault['MFSR']
    if mfsr:
        if mfsr & 1: lines.append("  IACCVIOL: Instruction access violation")
        if mfsr & 2: lines.append("  DACCVIOL: Data access violation")
        if mfsr & 8: lines.append("  MUNSTKERR: MemManage unstacking error")
        if mfsr & 16: lines.append("  MSTKERR: MemManage stacking error")
        if mfsr & 64: lines.append("  MMARVALID: MMFAR valid")
        if mfsr & 64: lines.append(f"    MMFAR = 0x{fault['MMFAR']:08X}")

    bfsr = fault['BFSR']
    if bfsr:
        if bfsr & 1: lines.append("  IBUSERR: Instruction bus error")
        if bfsr & 2: lines.append("  PRECISEER: Precise data bus error")
        if bfsr & 4: lines.append("  IMPREISEER: Imprecise data bus error")
        if bfsr & 8: lines.append("  UNSTKERR: BusFault unstacking error")
        if bfsr & 16: lines.append("  STKERR: BusFault stacking error")
        if bfsr & 128: lines.append("  BFARVALID: BFAR valid")
        if bfsr & 128: lines.append(f"    BFAR = 0x{fault['BFAR']:08X}")

    ufsr = fault['UFSR']
    if ufsr:
        if ufsr & 1: lines.append("  UNDEFINSTR: Undefined instruction")
        if ufsr & 2: lines.append("  INVSTATE: Invalid state (ARM mode in Thumb)")
        if ufsr & 4: lines.append("  INVPC: Invalid EXC_RETURN")
        if ufsr & 8: lines.append("  NOCP: No coprocessor")
        if ufsr & 256: lines.append("  UNALIGNED: Unaligned access")
        if ufsr & 512: lines.append("  DIVBYZERO: Divide by zero")

    return '\n'.join(lines) if lines else "  (no fault details)"


def reset_reason_str(reason):
    """Return human-readable string for reset reason value."""
    names = {
        RESET_REASON_HARDFAULT: "HardFault",
        RESET_REASON_ASSERT:    "ASSERT",
        RESET_REASON_WDT:       "WDT Timeout",
        RESET_REASON_FOTA:      "FOTA Reset",
        RESET_REASON_FLHDRV:    "Flash Driver Reset",
        RESET_REASON_XICOVL:    "XIC Overflow",
        RESET_REASON_BAT:       "Battery Low",
        RESET_REASON_TEMP:      "Temperature High",
    }
    return names.get(reason, f"Unknown({reason})")


def find_excep_store(dump_data, config):
    """Find excep_store in dump. Try MAP symbol, then store_ptr, then brute scan."""
    from ec_constants import EC_EXCEP_START_FLAG, EC_HARDFAULT_START_FLAG, STORE_SIZE
    # Method 1: Use symbol address from MAP
    sym_addr = config.get('excep_store_addr')
    if sym_addr is not None and sym_addr + STORE_SIZE <= len(dump_data):
        flag0 = u32(dump_data, sym_addr)
        if flag0 in (EC_EXCEP_START_FLAG, EC_HARDFAULT_START_FLAG):
            return sym_addr, 'symbol'

    # Method 2: Use store_ptr from RAM tail
    ptr_addr = config.get('store_ptr_addr')
    if ptr_addr and ptr_addr + 4 <= len(dump_data):
        ptr_val = u32(dump_data, ptr_addr)
        if 0 < ptr_val < len(dump_data) - STORE_SIZE:
            flag0 = u32(dump_data, ptr_val)
            if flag0 in (EC_EXCEP_START_FLAG, EC_HARDFAULT_START_FLAG):
                return ptr_val, 'store_ptr'

    # Method 3: Brute-force scan for flag patterns
    for i in range(0, len(dump_data) - STORE_SIZE, 4):
        flag0 = u32(dump_data, i)
        if flag0 in (EC_EXCEP_START_FLAG, EC_HARDFAULT_START_FLAG):
            return i, 'scan'

    return None, None


def read_assert_buff(dump_data, assert_buff_addr, max_len=200):
    """Read ec_assert_buff null-terminated string from RAM dump.

    When an ASSERT fires, the firmware writes a human-readable message
    into this buffer containing function name, line number, and values.

    Args:
        dump_data: full RAM dump bytes
        assert_buff_addr: address of ec_assert_buff symbol
        max_len: maximum bytes to read (default 200 from MAP size 0xC8)

    Returns:
        str: decoded assert message string, or None if not available
    """
    if assert_buff_addr is None or assert_buff_addr >= len(dump_data):
        return None
    end = min(assert_buff_addr + max_len, len(dump_data))
    raw = dump_data[assert_buff_addr:end]
    text = raw.split(b'\x00')[0].decode('ascii', errors='replace')
    return text if text else None


def read_stack_range(dump_data, stack_end_addr_addr, stack_start_addr_addr):
    """Read ec_stack_end_addr and ec_stack_start_addr global variables.

    These are global u32 variables whose values are the actual stack
    boundary addresses for the crashed task.

    Args:
        dump_data: full RAM dump bytes
        stack_end_addr_addr: address of ec_stack_end_addr symbol
        stack_start_addr_addr: address of ec_stack_start_addr symbol

    Returns:
        dict with 'stack_end' and 'stack_start' (int), or None
    """
    dlen = len(dump_data)
    if not stack_end_addr_addr or not stack_start_addr_addr:
        return None
    if stack_end_addr_addr + 4 > dlen or stack_start_addr_addr + 4 > dlen:
        return None
    stack_end = u32(dump_data, stack_end_addr_addr)
    stack_start = u32(dump_data, stack_start_addr_addr)
    # Validate: both must be in RAM range and start < end
    if 0 < stack_start < dlen and 0 < stack_end <= dlen and stack_start < stack_end:
        return {'stack_end': stack_end, 'stack_start': stack_start}
    return None
