#!/usr/bin/env python3
"""EC platform FreeRTOS task stack scanning and TCB reading."""

import struct

from ec_constants import u32, STACK_FILL_PATTERN


def scan_task_stacks(dump_data, ram_size):
    """Scan RAM for FreeRTOS task stacks (0xA5A5A5A5 fill pattern)."""
    regions = []
    in_region = False
    region_start = 0

    for i in range(0, min(len(dump_data), ram_size) - 3, 4):
        val = struct.unpack_from('<I', dump_data, i)[0]
        if val == STACK_FILL_PATTERN:
            if not in_region:
                region_start = i
                in_region = True
        else:
            if in_region:
                size = i - region_start
                if size >= 64:
                    bottom_ok = struct.unpack_from('<I', dump_data, region_start)[0] == STACK_FILL_PATTERN
                    regions.append({
                        'start': region_start,
                        'end': i,
                        'size': size,
                        'guard_ok': bottom_ok,
                    })
                in_region = False

    return regions


def scan_stack_code_addrs(dump_data, sp, stack_end, flash_start, flash_end, max_words=128):
    """Scan stack for code addresses to build call chain.

    Filters out likely literal pool entries by requiring Thumb bit set
    (bit 0 = 1) which is the norm for return addresses on Cortex-M.
    """
    addrs = []
    for i in range(0, min(max_words * 4, stack_end - sp), 4):
        if sp + i + 4 > len(dump_data):
            break
        val = struct.unpack_from('<I', dump_data, sp + i)[0]
        clean = val & ~1
        if flash_start <= clean <= flash_end:
            # Prefer addresses with Thumb bit set (return addresses are odd)
            # but also accept even addresses that look like valid code
            addrs.append((i, val))
    return addrs


def filter_call_chain(addrs, map_file=None):
    """Filter call chain addresses to remove likely false positives.

    Conservative filter: only removes clearly invalid entries.
    - Even addresses (no Thumb bit) with very large symbol offsets (>0x200)
      are likely data constants loaded from literal pools, not return addresses.
    - All odd addresses (Thumb bit set) are kept, as ARM Cortex-M return
      addresses always have bit 0 set.
    """
    if not map_file:
        return addrs

    from ec_build_config import find_symbol
    filtered = []
    for offset, addr in addrs:
        # Odd address (Thumb bit set) → always keep (likely return address)
        if addr & 1:
            filtered.append((offset, addr))
            continue

        # Even address: check if it's deep in a function (literal pool tail)
        sym = find_symbol(map_file, addr)
        if sym and sym[2] > 0x200:
            continue
        filtered.append((offset, addr))
    return filtered


def read_tcb_task_name(dump_data, tcb_addr, name_offset=0x34, name_len=12):
    """Read task name from FreeRTOS TCB."""
    if tcb_addr == 0 or tcb_addr + name_offset + name_len > len(dump_data):
        return ""
    data = dump_data[tcb_addr + name_offset:tcb_addr + name_offset + name_len]
    return data.split(b'\x00')[0].decode('ascii', errors='replace')


# ── ec_task_list scanning ────────────────────────────────────────────────

EC_TASK_LIST_ENTRY_SIZE = 24


def _estimate_stack_usage(dump_data, px_top_of_stack, stack_end_hint, dlen):
    """Estimate stack usage from pxTopOfStack via 0xA5A5A5A5 fill scanning.

    Does NOT depend on TCB pxStack offset. Instead scans the fill pattern
    to find the stack bottom boundary.

    Returns dict with stack info, or None if unable to determine.
    """
    if not px_top_of_stack or px_top_of_stack >= dlen or px_top_of_stack < 4:
        return None

    # Scan downward from pxTopOfStack to find stack bottom (first non-fill)
    stack_bottom = None
    scan_addr = px_top_of_stack
    while scan_addr >= 4:
        scan_addr -= 4
        val = u32(dump_data, scan_addr)
        if val != STACK_FILL_PATTERN:
            stack_bottom = scan_addr + 4
            break

    if stack_bottom is None:
        return None

    # Determine stack top boundary
    stack_top = px_top_of_stack  # default: current SP level
    if stack_end_hint and px_top_of_stack < stack_end_hint < dlen:
        stack_top = stack_end_hint

    stack_size = stack_top - stack_bottom
    if stack_size <= 0:
        return None

    # High-water mark: scan from stack_bottom upward for first non-fill
    high_water = stack_bottom
    for addr in range(stack_bottom, min(px_top_of_stack, dlen), 4):
        if u32(dump_data, addr) != STACK_FILL_PATTERN:
            high_water = addr
            break

    used = px_top_of_stack - stack_bottom
    usage_pct = min(100, used * 100 // stack_size) if stack_size > 0 else 0

    # Guard word check
    guard_ok = None
    if stack_bottom + 4 <= dlen:
        guard_ok = u32(dump_data, stack_bottom) == STACK_FILL_PATTERN

    return {
        'stack_bottom': stack_bottom,
        'stack_top': stack_top,
        'stack_size': stack_size,
        'used': used,
        'usage_pct': usage_pct,
        'high_water': high_water,
        'guard_ok': guard_ok,
    }


def scan_ec_task_list(dump_data, task_list_addr, task_list_size,
                      flash_start=0, flash_end=0, map_file=None):
    """Parse ec_task_list array from RAM dump.

    Each entry is 24 bytes:
      offset  0: name[8]     (null-terminated ASCII task name)
      offset  8: tcb_ptr     (u32, TCB pointer)
      offset 12: stack_end   (u32, possible stack end boundary)
      offset 16: funptr      (u32, caller LR)
      offset 20: unknown     (u32)

    Returns list of task entry dicts, or empty list if unavailable.
    """
    dlen = len(dump_data)
    if not task_list_addr or task_list_addr + 24 > dlen:
        return []

    # Determine entry count from MAP-reported size
    if task_list_size > 0:
        num_entries = task_list_size // EC_TASK_LIST_ENTRY_SIZE
    else:
        num_entries = 8  # fallback: scan up to 8

    tasks = []
    for i in range(num_entries):
        base = task_list_addr + i * EC_TASK_LIST_ENTRY_SIZE
        if base + EC_TASK_LIST_ENTRY_SIZE > dlen:
            break

        name_raw = dump_data[base:base + 8]
        if name_raw == b'\x00' * 8:
            continue
        name = name_raw.split(b'\x00')[0].decode('ascii', errors='replace')
        if not name or not name.isprintable():
            continue

        tcb_ptr = u32(dump_data, base + 8)
        f2 = u32(dump_data, base + 12)    # possible stack end
        f3 = u32(dump_data, base + 16)    # funptr
        f4 = u32(dump_data, base + 20)    # unknown

        entry = {
            'name': name,
            'tcb_addr': tcb_ptr,
            'stack_end_hint': f2,
            'funptr': f3,
            'field5': f4,
            'pxTopOfStack': None,
            'stack_usage': None,
        }

        # Read pxTopOfStack from TCB+0x00
        if tcb_ptr and 0 < tcb_ptr < dlen and tcb_ptr + 4 <= dlen:
            px_top = u32(dump_data, tcb_ptr)
            if 0 < px_top < dlen:
                entry['pxTopOfStack'] = px_top
                # Estimate stack usage
                entry['stack_usage'] = _estimate_stack_usage(
                    dump_data, px_top, f2, dlen)

        # Resolve funptr symbol name
        if map_file and flash_start <= (f3 & ~1) <= flash_end:
            from ec_build_config import find_symbol
            sym = find_symbol(map_file, f3)
            entry['funptr_name'] = sym[0] if sym else None

        tasks.append(entry)

    return tasks


def print_task_list_report(tasks, map_file=None):
    """Print formatted task list report."""
    if not tasks:
        return

    print(f"  {len(tasks)} tasks found in ec_task_list")
    print(f"  {'Name':<10} {'TCB':>10} {'pxTopOfStk':>10} "
          f"{'Stack Bottom':>12} {'Stack Top':>10} "
          f"{'Size':>6} {'Usage':>6} {'Guard'}")
    print(f"  {'-'*10} {'-'*10} {'-'*10} "
          f"{'-'*12} {'-'*10} "
          f"{'-'*6} {'-'*6} {'-'*6}")

    for t in tasks:
        tcb_str = f"0x{t['tcb_addr']:05X}" if t['tcb_addr'] else '-'
        top_str = f"0x{t['pxTopOfStack']:05X}" if t['pxTopOfStack'] else '-'

        su = t.get('stack_usage')
        if su:
            bot_str = f"0x{su['stack_bottom']:05X}"
            top_s = f"0x{su['stack_top']:05X}"
            sz_str = f"{su['stack_size']:5d}"
            use_str = f"{su['usage_pct']:4d}%"
            guard = "OK" if su.get('guard_ok') else "OVERFLOW"
        else:
            bot_str = "-"
            top_s = "-"
            sz_str = "-"
            use_str = "N/A"
            guard = "-"

        print(f"  {t['name']:<10} {tcb_str:>10} {top_str:>10} "
              f"{bot_str:>12} {top_s:>10} "
              f"{sz_str:>6} {use_str:>6} {guard}")


# ── FreeRTOS kernel state ────────────────────────────────────────────────

def read_kernel_state(dump_data, config, flash_start=0, flash_end=0):
    """Read and validate FreeRTOS kernel state variables from RAM dump.

    Kernel variables may be corrupted in crash dumps. Each value is
    validated and marked as corrupted if unreasonable.

    Returns list of dicts with name, addr, raw_value, valid, hint.
    """
    dlen = len(dump_data)
    results = []

    # (name, config_key, validation_fn, corruption_hint)
    kernel_vars = [
        ('uxCurrentNumberOfTasks', 'uxCurrentNumberOfTasks_addr',
         lambda v: 0 <= v <= 256,
         'value is in flash range or too large (corrupted)'),
        ('xTickCount', 'xTickCount_addr',
         lambda v: v < 0xFFFFFFFF,
         None),
        ('xSchedulerRunning', 'xSchedulerRunning_addr',
         lambda v: v in (0, 1),
         'value is not 0 or 1 (corrupted)'),
        ('uxCriticalNesting', 'uxCriticalNesting_addr',
         lambda v: v < 256,
         'value is in flash range or too large (corrupted)'),
    ]

    for name, key, validator, hint in kernel_vars:
        addr = config.get(key)
        if addr is None or addr + 4 > dlen:
            continue
        raw = u32(dump_data, addr)
        is_valid = validator(raw)

        # Cross-check: Flash address values are always corrupted for kernel vars
        if flash_start <= raw <= flash_end:
            is_valid = False
            hint = f'0x{raw:08X} is in flash range (corrupted)'

        results.append({
            'name': name,
            'addr': addr,
            'raw_value': raw,
            'valid': is_valid,
            'hint': hint if not is_valid else None,
        })

    return results


def print_kernel_state_report(kernel_vars):
    """Print formatted kernel state report with corruption warnings."""
    if not kernel_vars:
        return

    print(f"  {'Variable':<28} {'Value':>14}  {'Status'}")
    print(f"  {'-'*28} {'-'*14}  {'-'*20}")

    for v in kernel_vars:
        val_str = f"{v['raw_value']}"
        if v['name'] == 'uxCurrentNumberOfTasks' and v['valid']:
            val_str += f" ({v['raw_value']} tasks)"
        elif v['name'] == 'xSchedulerRunning' and v['valid']:
            val_str += f" ({'running' if v['raw_value'] else 'stopped'})"
        elif v['name'] == 'uxCriticalNesting' and v['valid']:
            val_str += f" (nesting={v['raw_value']})"

        if v['valid']:
            status = "OK"
        else:
            status = f"CORRUPTED ({v['hint']})"

        print(f"  {v['name']:<28} {val_str:>14}  {status}")
