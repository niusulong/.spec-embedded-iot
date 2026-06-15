#!/usr/bin/env python3
"""
EC Platform RAM Dump Analyzer (EC626/EC626E/EC616)
Entry point — imports from modular components.

Subcommands: full-analyze, parse-excep, resolve, scan-stacks, scan-memp, scan-heap
"""

import sys
import os
import argparse

# Ensure local imports work regardless of cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ec_constants import u32, STACK_FILL_PATTERN, strip_pmud_header
from ec_build_config import parse_map_config, build_config, find_symbol
from ec_excep_store import (
    parse_excep_store, decode_fault, reset_reason_str, find_excep_store,
    read_assert_buff, read_stack_range,
)
from ec_stack_analyzer import (
    scan_task_stacks, scan_stack_code_addrs, filter_call_chain, read_tcb_task_name,
    scan_ec_task_list, print_task_list_report,
    read_kernel_state, print_kernel_state_report,
)
from ec_memp_scanner import scan_memp_pools, print_memp_report
from ec_elf_reader import addr_to_source, has_elftools
from ec_disasm import lookup_disasm, format_disasm, load_disasm_index
from ec_heap_analyzer import (
    scan_trace_node, print_trace_node_report,
    scan_tlsf_heap, print_tlsf_heap_report,
)


# ── Subcommands ──────────────────────────────────────────────────────────

def cmd_parse_excep(args):
    """Parse excep_store from RAM dump."""
    with open(args.dump, 'rb') as f:
        dump_raw = f.read()
    dump, hdr = strip_pmud_header(dump_raw)
    if hdr:
        print(f"[PMUD] Dump header detected ({hdr} bytes), stripped")

    config = build_config(args)

    if args.map:
        print(f"[MAP Config] chip_hint={config.get('chip_hint','?')}, "
              f"RAM_END=0x{config['ram_end']:X}, "
              f"Flash=0x{config['flash_start']:X}-0x{config['flash_end']:X}")
    else:
        print(f"[Default Config] Using EC626 defaults (no --map provided)")

    store_addr = args.store_addr
    detect_method = 'manual'
    if store_addr is None:
        store_addr, detect_method = find_excep_store(dump, config)
        if store_addr is not None:
            print(f"[Auto-detect] excep_store found at 0x{store_addr:08X} (via {detect_method})")
    else:
        print(f"[Manual] excep_store at 0x{store_addr:08X}")

    if store_addr is None:
        print("ERROR: Cannot find excep_store. Specify --store-addr manually.")
        return 1

    result = parse_excep_store(dump, store_addr, config)

    print(f"\n{'='*60}")
    print(f"EC Platform Exception Store Analysis")
    print(f"{'='*60}")
    print(f"Exception Type   : {result['type']}")
    print(f"Exception Count  : {result['ec_exception_count']}")
    print(f"ec_start_flag    : 0x{result['ec_start_flag']:08X}")
    print(f"ec_hardfault_flag: 0x{result['ec_hardfault_flag']:08X}")
    print(f"ec_assert_flag   : 0x{result['ec_assert_flag']:08X}")
    print(f"ec_end_flag      : 0x{result['ec_end_flag']:08X}")
    print(f"reset_reason     : {result['reset_reason']} ({reset_reason_str(result['reset_reason'])})")

    if result['is_fs_assert']:
        print(f"  *** FS ASSERT detected (filesystem assertion) ***")

    regs = result['regs']
    print(f"\n--- Core Registers ---")
    for name in ['R0','R1','R2','R3','R4','R5','R6','R7',
                 'R8','R9','R10','R11','R12','SP','LR','PC']:
        print(f"  {name:6s} = 0x{regs[name]:08X}")
    print(f"  xPSR       = 0x{regs['xPSR']:08X}")
    print(f"  EXC_RETURN = 0x{regs['EXC_RETURN']:08X}")
    print(f"  MSP        = 0x{regs['MSP']:08X}")
    print(f"  PSP        = 0x{regs['PSP']:08X}")
    print(f"  CONTROL    = 0x{regs['CONTROL']:08X}")

    if regs['EXC_RETURN'] & 0x4:
        print(f"  [SP = PSP, task context]")
    else:
        print(f"  [SP = MSP, handler/interrupt context]")

    print(f"\n--- Fault Status ---")
    fault = result['fault']
    print(f"  HFSR = 0x{fault['HFSR']:08X}")
    print(f"  MFSR = 0x{fault['MFSR']:02X}")
    print(f"  BFSR = 0x{fault['BFSR']:02X}")
    print(f"  UFSR = 0x{fault['UFSR']:04X}")
    print(f"  MMFAR= 0x{fault['MMFAR']:08X}")
    print(f"  BFAR = 0x{fault['BFAR']:08X}")
    print(decode_fault(fault))

    print(f"\n--- Additional ---")
    print(f"  Task name     : \"{result['task_name']}\"")
    print(f"  func_call_stack: {[f'0x{a:08X}' for a in result['func_call_stack']]}")
    print(f"  curr_time      : 0x{result['curr_time']:08X}")

    return 0


def cmd_resolve(args):
    """Resolve addresses to symbols, source lines, and disassembly."""
    build_file = args.map
    if not build_file:
        elf_file = getattr(args, 'elf', None)
        if elf_file:
            build_file = elf_file
        else:
            print("ERROR: --map or --elf is required for resolve")
            return 1

    elf_file = getattr(args, 'elf', None)
    disasm_file = getattr(args, 'disasm', None)
    disasm_idx = None
    if disasm_file:
        disasm_idx = load_disasm_index(disasm_file)

    for addr_str in args.addresses:
        addr = int(addr_str, 0)
        parts = [f"0x{addr:08X}"]

        sym = find_symbol(build_file, addr)
        if sym:
            name, sym_addr, offset = sym
            parts.append(f"{name} (0x{sym_addr:08X}+0x{offset:X})")
        else:
            parts.append("(not found)")

        if elf_file and has_elftools():
            src = addr_to_source(elf_file, addr)
            if src:
                parts.append(f"at {src}")

        print(" -> ".join(parts))

        if disasm_file and disasm_idx is not None:
            r = lookup_disasm(disasm_file, addr, context=5, index=disasm_idx)
            if r:
                for line in format_disasm(r, addr):
                    print(line)


def cmd_scan_stacks(args):
    """Scan all task stacks for overflow."""
    with open(args.dump, 'rb') as f:
        dump_raw = f.read()
    dump, hdr = strip_pmud_header(dump_raw)
    if hdr:
        print(f"[PMUD] Dump header detected ({hdr} bytes), stripped")

    regions = scan_task_stacks(dump, len(dump))
    overflow_count = 0

    print(f"Found {len(regions)} task stack regions:")
    for r in regions:
        status = "OK" if r['guard_ok'] else "*** OVERFLOW ***"
        if not r['guard_ok']:
            overflow_count += 1
        print(f"  0x{r['start']:08X}-0x{r['end']:08X}  size={r['size']:5d}  guard={status}")

    if overflow_count > 0:
        print(f"\n*** {overflow_count} stack(s) with OVERFLOW detected! ***")
    else:
        print(f"\nAll stack guards intact. No overflow detected.")
    return 0


def cmd_scan_memp(args):
    """Scan LWIP memp pools for exhaustion and leak analysis."""
    if not args.map:
        print("ERROR: --map is required for scan-memp")
        return 1

    with open(args.dump, 'rb') as f:
        dump_raw = f.read()
    dump, hdr = strip_pmud_header(dump_raw)
    if hdr:
        print(f"[PMUD] Dump header detected ({hdr} bytes), stripped")

    config = parse_map_config(args.map)
    memp_tabs = config.get('memp_tabs', {})
    memp_bases = config.get('memp_bases', {})
    memp_descs = config.get('memp_descs', {})
    flash_start = config.get('flash_start', 0)
    flash_end = config.get('flash_end', 0)

    if not memp_tabs:
        print("No LWIP memp_tab symbols found in MAP file.")
        print("This build may not include LWIP, or symbols are stripped.")
        return 1

    elf_file = getattr(args, 'elf', None)
    util_threshold = getattr(args, 'util_threshold', 80)

    pools = scan_memp_pools(dump, memp_tabs, memp_bases,
                            memp_descs=memp_descs, elf_file=elf_file,
                            flash_start=flash_start, flash_end=flash_end,
                            map_file=args.map,
                            util_threshold=util_threshold)

    print(f"\nLWIP Memp Pool Scan ({len(pools)} pools from MAP)")
    print(f"{'='*60}")
    print_memp_report(pools, verbose=args.verbose)

    exhausted = [p for p in pools if p['status'] == 'EXHAUSTED']
    if exhausted:
        print(f"\nSummary: {len(exhausted)}/{len(pools)} pools EXHAUSTED")
    else:
        print(f"\nSummary: All {len(pools)} pools OK")
    return 0


def cmd_scan_heap(args):
    """Scan FreeRTOS heap: TLSF utilization + trace_node allocation tracking."""
    if not args.map:
        print("ERROR: --map is required for scan-heap")
        return 1

    with open(args.dump, 'rb') as f:
        dump_raw = f.read()
    dump, hdr = strip_pmud_header(dump_raw)
    if hdr:
        print(f"[PMUD] Dump header detected ({hdr} bytes), stripped")

    config = parse_map_config(args.map)
    flash_start = config.get('flash_start', 0)
    flash_end = config.get('flash_end', 0)

    # Part 1: TLSF heap utilization
    tlsf_result = None
    gTotalHeapSize_addr = config.get('gTotalHeapSize_addr')
    ucHeap_addr = config.get('ucHeap_addr')
    if gTotalHeapSize_addr and ucHeap_addr:
        total_heap = u32(dump, gTotalHeapSize_addr)
        heap_ptr = u32(dump, ucHeap_addr)
        if total_heap > 0 and heap_ptr > 0:
            tlsf_result = scan_tlsf_heap(dump, heap_ptr, total_heap,
                                         map_file=args.map,
                                         flash_start=flash_start, flash_end=flash_end)

    print(f"\nFreeRTOS Heap Analysis (heap_6 / TLSF)")
    print(f"{'='*60}")

    if tlsf_result:
        print(f"\n## TLSF Heap Utilization")
        print_tlsf_heap_report(tlsf_result, verbose=args.verbose)
    else:
        print(f"  TLSF heap symbols not found (gTotalHeapSize/ucHeap)")

    # Part 2: trace_node allocation tracking
    trace_node_addr = config.get('trace_node_addr')
    if trace_node_addr:
        trace_node_size = config.get('trace_node_size', 0)
        node_hash_addr = config.get('node_hash_addr')
        free_node_addr = config.get('free_node_addr')
        trace_result = scan_trace_node(dump, trace_node_addr, trace_node_size,
                                       node_hash_addr=node_hash_addr,
                                       free_node_addr=free_node_addr,
                                       map_file=args.map,
                                       flash_start=flash_start, flash_end=flash_end)

        print(f"\n## Heap Allocation Trace (trace_node / mm_debug)")
        print_trace_node_report(trace_result, verbose=args.verbose)
    else:
        print(f"\n  trace_node symbols not found (mm_debug not enabled)")
        trace_result = None

    # Summary
    print(f"\n{'='*60}")
    if tlsf_result:
        print(f"TLSF Heap: {tlsf_result['used_size']}/{tlsf_result['total_size']} bytes "
              f"({tlsf_result['util_pct']:.1f}% used), "
              f"largest free = {tlsf_result['largest_free']} bytes")
    if trace_result and trace_result is not None:
        tn = trace_result
        full_str = " [FULL]" if tn.get('trace_node_full') else ""
        print(f"trace_node: {tn['active_nodes']}/{tn['total_nodes']} entries{full_str}, "
              f"{tn['total_allocated_bytes']} bytes tracked")
    return 0


def cmd_full_analyze(args):
    """Full analysis: auto-config from MAP + parse exception + resolve + scan stacks."""
    with open(args.dump, 'rb') as f:
        dump_raw = f.read()
    dump, hdr = strip_pmud_header(dump_raw)
    if hdr:
        print(f"[PMUD] Dump header detected ({hdr} bytes), stripped")

    config = build_config(args)

    flash_start = config['flash_start']
    flash_end = config['flash_end']
    chip_hint = config.get('chip_hint', 'unknown')

    print(f"[Config] chip_hint={chip_hint}")
    print(f"  RAM_END=0x{config['ram_end']:X}  "
          f"RESET_REASON=0x{config['reset_reason_addr']:X}  "
          f"MAGIC=0x{config['magic_addr']:X}  "
          f"STORE_PTR=0x{config['store_ptr_addr']:X}")
    print(f"  Flash=0x{flash_start:X}-0x{flash_end:X}")
    if config.get('excep_store_addr') is not None:
        print(f"  excep_store=0x{config['excep_store_addr']:X} (from MAP symbol)")
    if config.get('pxCurrentTCB_addr') is not None:
        print(f"  pxCurrentTCB=0x{config['pxCurrentTCB_addr']:X} (from MAP symbol)")

    # Step 1: Find and parse excep_store
    store_addr, detect_method = find_excep_store(dump, config)
    if store_addr is None:
        print("\nERROR: Cannot find excep_store in dump.")
        print("  Possible causes: WDT/AONWDT/LOCKUP/XIC reset without excep_store,")
        print("  or EXCEP_OPTION_SILENT_RESET mode, or bootloader crash.")
        rr_addr = config['reset_reason_addr']
        if rr_addr + 4 <= len(dump):
            rr = u32(dump, rr_addr)
            print(f"  reset_reason (0x{rr_addr:08X}) = {rr} ({reset_reason_str(rr)})")
        else:
            print(f"  reset_reason: dump too small to read")
        return 1

    print(f"\n[Auto-detect] excep_store at 0x{store_addr:08X} (via {detect_method})")
    result = parse_excep_store(dump, store_addr, config)
    regs = result['regs']
    fault = result['fault']

    # Step 1.5: Read assert buffer and crash stack range
    assert_buff = read_assert_buff(dump, config.get('ec_assert_buff_addr'))
    stack_range = read_stack_range(dump,
                                   config.get('ec_stack_end_addr_addr'),
                                   config.get('ec_stack_start_addr_addr'))

    # Step 2: Read pxCurrentTCB
    # Prefer excep_store task_name (saved by firmware at crash time) over TCB
    # TCB offsets may vary across FreeRTOS configs; excep_store is more reliable.
    tcb_var_addr = config.get('pxCurrentTCB_addr')
    tcb_addr = 0
    tcb_name = result.get('task_name', '')
    if tcb_var_addr and tcb_var_addr + 4 <= len(dump):
        tcb_addr = u32(dump, tcb_var_addr)
    if tcb_addr and tcb_addr + 0x48 < len(dump):
        # Only use TCB name if excep_store didn't provide one
        if not tcb_name:
            tcb_name = read_tcb_task_name(dump, tcb_addr)
        px_top_of_stack = u32(dump, tcb_addr)
        px_stack = u32(dump, tcb_addr + 0x30)
    else:
        px_top_of_stack = 0
        px_stack = 0

    # Step 3: Resolve symbols
    pc_sym = lr_sym = None
    if args.map:
        pc_sym = find_symbol(args.map, regs['PC'])
        lr_sym = find_symbol(args.map, regs['LR'])

    # Step 4: Scan stacks
    stack_regions = scan_task_stacks(dump, len(dump))
    overflow_count = sum(1 for r in stack_regions if not r['guard_ok'])

    # Step 4.5: Parse ec_task_list (all tasks)
    task_list = None
    task_list_addr = config.get('ec_task_list_addr')
    if task_list_addr and task_list_addr + 24 <= len(dump):
        task_list_size = config.get('ec_task_list_size', 0)
        task_list = scan_ec_task_list(dump, task_list_addr, task_list_size,
                                      flash_start=flash_start, flash_end=flash_end,
                                      map_file=args.map if args.map else None)

    # Step 5: Scan LWIP memp pools
    memp_pools = None
    memp_tabs = config.get('memp_tabs', {})
    memp_bases = config.get('memp_bases', {})
    memp_descs = config.get('memp_descs', {})
    elf_file = getattr(args, 'elf', None)
    if memp_tabs:
        memp_pools = scan_memp_pools(dump, memp_tabs, memp_bases,
                                    memp_descs=memp_descs, elf_file=elf_file,
                                    flash_start=flash_start, flash_end=flash_end,
                                    map_file=args.map,
                                    util_threshold=80)

    # Step 5.5: Scan heap trace_node
    heap_trace = None
    trace_node_addr = config.get('trace_node_addr')
    if trace_node_addr and args.map:
        trace_node_size = config.get('trace_node_size', 0)
        node_hash_addr = config.get('node_hash_addr')
        free_node_addr = config.get('free_node_addr')
        heap_trace = scan_trace_node(dump, trace_node_addr, trace_node_size,
                                     node_hash_addr=node_hash_addr,
                                     free_node_addr=free_node_addr,
                                     map_file=args.map,
                                     flash_start=flash_start, flash_end=flash_end)

    # Step 5.7: Scan TLSF heap utilization
    tlsf_heap = None
    gTotalHeapSize_addr = config.get('gTotalHeapSize_addr')
    ucHeap_addr = config.get('ucHeap_addr')
    if gTotalHeapSize_addr and ucHeap_addr:
        total_heap = u32(dump, gTotalHeapSize_addr)
        heap_ptr = u32(dump, ucHeap_addr)
        if total_heap > 0 and heap_ptr > 0:
            tlsf_heap = scan_tlsf_heap(dump, heap_ptr, total_heap,
                                       map_file=args.map,
                                       flash_start=flash_start, flash_end=flash_end)

    # Step 5.8: Read FreeRTOS kernel state
    kernel_state = read_kernel_state(dump, config,
                                      flash_start=flash_start, flash_end=flash_end)

    # ── Output ──
    print(f"\n{'='*60}")
    print(f"EC Platform Crash Dump Full Analysis (chip={chip_hint})")
    print(f"{'='*60}")

    etype = result['type']
    print(f"\n## Exception Type: {etype}")
    print(f"  ec_start_flag=0x{result['ec_start_flag']:08X} hardfault_flag=0x{result['ec_hardfault_flag']:08X} "
          f"assert_flag=0x{result['ec_assert_flag']:08X} count={result['ec_exception_count']}")
    rr_raw = result['reset_reason']
    rr_str = reset_reason_str(rr_raw)
    # Show raw reset_reason but note when type was inferred from excep_store flags
    if 'Unknown' in rr_str and etype in ('ASSERT', 'HardFault', 'WDT', 'XIC'):
        print(f"  reset_reason={rr_raw} ({rr_str}; type inferred from excep_store flags)")
    else:
        print(f"  reset_reason={rr_raw} ({rr_str})")

    if etype == 'WDT':
        print(f"  *** WDT Timeout: NMI triggered EC_ASSERT(0, RESET_REASON_WDT, 0, 0) ***")
        print(f"  Root cause may be: task deadlock, interrupt storm, or Default_Handler trap")
    elif etype == 'XIC':
        print(f"  *** XIC Interrupt Overflow ***")
    elif result['is_fs_assert']:
        print(f"  *** FS ASSERT: filesystem corruption detected ***")
        print(f"  R1=0x{regs['R1']:08X} R2=0x{regs['R2']:08X} R3=0x{regs['R3']:08X} (FS_ASSERT_MAGIC)")

    if assert_buff:
        print(f"\n## Assert Info")
        for part in assert_buff.replace('\r\n', '\n').split('\n'):
            part = part.strip()
            if part:
                print(f"  {part}")
    if stack_range:
        sz = stack_range['stack_end'] - stack_range['stack_start']
        print(f"  Crash stack: 0x{stack_range['stack_start']:08X} - 0x{stack_range['stack_end']:08X} ({sz} bytes)")

    print(f"\n## Crash Location")
    pc_info = f" -> {pc_sym[0]} (0x{pc_sym[1]:08X}+0x{pc_sym[2]:X})" if pc_sym else ""
    lr_info = f" -> {lr_sym[0]} (0x{lr_sym[1]:08X}+0x{lr_sym[2]:X})" if lr_sym else ""
    pc_src = addr_to_source(args.elf, regs['PC']) if (elf_file and has_elftools()) else None
    lr_src = addr_to_source(args.elf, regs['LR']) if (elf_file and has_elftools()) else None
    pc_src_info = f"  [{pc_src}]" if pc_src else ""
    lr_src_info = f"  [{lr_src}]" if lr_src else ""
    print(f"  PC = 0x{regs['PC']:08X}{pc_info}{pc_src_info}")
    print(f"  LR = 0x{regs['LR']:08X}{lr_info}{lr_src_info}")

    if etype in ('ASSERT', 'WDT', 'XIC'):
        actual_pc = regs['PC'] + 5
        print(f"  Actual ASSERT address = 0x{actual_pc:08X} (PC+5 for GCC)")
        if args.map:
            actual_sym = find_symbol(args.map, actual_pc)
            if actual_sym:
                print(f"    -> {actual_sym[0]} (0x{actual_sym[1]:08X}+0x{actual_sym[2]:X})")

    print(f"\n## Registers")
    for name in ['R0','R1','R2','R3','R4','R5','R6','R7',
                 'R8','R9','R10','R11','R12','SP','LR','PC']:
        val = regs[name]
        hint = ""
        if flash_start <= (val & ~1) <= flash_end:
            hint = "  [FLASH code]"
        elif 0x40000000 <= val <= 0x50000000:
            hint = "  [Peripheral]"
        print(f"  {name:6s} = 0x{val:08X}{hint}")

    print(f"\n## Fault Analysis")
    if etype == 'HardFault':
        print(decode_fault(fault))
    else:
        print(f"  Not a HardFault - fault registers not applicable")

    print(f"\n## Task Context")
    print(f"  Task name: \"{tcb_name}\"" if tcb_name else "  Task name: (not available)")
    if tcb_addr and tcb_addr + 0x48 < len(dump):
        px_top = u32(dump, tcb_addr)
        px_stk = u32(dump, tcb_addr + 0x30)
        tcb_44 = u32(dump, tcb_addr + 0x44)
        tcb_48 = u32(dump, tcb_addr + 0x48)
        # Validate stack pointers are in RAM range
        dlen = len(dump)
        if 0 < px_stk < dlen and 0 < tcb_44 <= dlen and tcb_44 > px_stk:
            stack_size = tcb_44 - px_stk
            print(f"  Stack range: 0x{px_stk:08X} - 0x{tcb_44:08X} ({stack_size} bytes)")
            if stack_size and 0 < tcb_48 <= stack_size:
                print(f"  High water mark: {tcb_48} bytes ({tcb_48*100//stack_size}% usage)")
            guard = u32(dump, px_stk) if px_stk + 4 <= dlen else 0
            print(f"  Stack guard: {'OK' if guard == STACK_FILL_PATTERN else '*** OVERFLOW ***'}")
        else:
            print(f"  TCB stack fields invalid (pxStk=0x{px_stk:08X}, end=0x{tcb_44:08X})")
            print(f"  TCB layout may differ from expected offsets (TCB+0x30/0x44)")

    print(f"\n## Stack Overflow Scan")
    print(f"  {len(stack_regions)} task stacks found, {overflow_count} overflow(s)")
    if overflow_count > 0:
        for r in stack_regions:
            if not r['guard_ok']:
                print(f"    OVERFLOW: 0x{r['start']:08X}-0x{r['end']:08X} size={r['size']}")

    if task_list:
        print(f"\n## Task List (ec_task_list)")
        print_task_list_report(task_list, map_file=args.map if args.map else None)

    if memp_pools:
        print(f"\n## LWIP Memp Pool Status")
        print_memp_report(memp_pools)

    if heap_trace:
        print(f"\n## Heap Memory Trace (trace_node)")
        print_trace_node_report(heap_trace)

    if tlsf_heap:
        print(f"\n## TLSF Heap Utilization")
        print_tlsf_heap_report(tlsf_heap)

    # Call chain from stack
    if args.map and regs['SP'] < len(dump):
        stack_end = min(regs['SP'] + 0x400, len(dump))
        code_addrs = scan_stack_code_addrs(dump, regs['SP'], stack_end, flash_start, flash_end)
        code_addrs = filter_call_chain(code_addrs, args.map)
        if code_addrs:
            print(f"\n## Call Chain (from stack scan)")
            for offset, addr in code_addrs[:8]:
                sym = find_symbol(args.map, addr)
                sym_info = f" -> {sym[0]}+0x{sym[2]:X}" if sym else ""
                print(f"  SP+0x{offset:03X}: 0x{addr:08X}{sym_info}")

    # Disassembly context
    disasm_file = getattr(args, 'disasm', None)
    if disasm_file and os.path.isfile(disasm_file):
        disasm_idx = load_disasm_index(disasm_file)
        disasm_addrs = set()
        if pc_sym:
            disasm_addrs.add(regs['PC'])
        if lr_sym and lr_sym != pc_sym:
            disasm_addrs.add(regs['LR'])
        # Add call chain addresses (already filtered)
        if args.map and regs['SP'] < len(dump):
            stack_end = min(regs['SP'] + 0x400, len(dump))
            disasm_code_addrs = scan_stack_code_addrs(dump, regs['SP'], stack_end, flash_start, flash_end)
            disasm_code_addrs = filter_call_chain(disasm_code_addrs, args.map)
            for _, addr in disasm_code_addrs[:8]:
                disasm_addrs.add(addr)
        if disasm_addrs:
            print(f"\n## Disassembly Context")
            for addr in sorted(disasm_addrs):
                sym = find_symbol(args.map, addr) if args.map else None
                sym_info = f" ({sym[0]}+0x{sym[2]:X})" if sym else ""
                src = addr_to_source(args.elf, addr) if (elf_file and has_elftools()) else None
                src_info = f"  [{src}]" if src else ""
                print(f"  0x{addr:08X}{sym_info}{src_info}")
                r = lookup_disasm(disasm_file, addr, context=5, index=disasm_idx)
                if r:
                    for line in format_disasm(r, addr):
                        print(line)
                print()

    if kernel_state:
        print(f"\n## FreeRTOS Kernel State")
        print_kernel_state_report(kernel_state)

    # Root Cause Summary
    print(f"\n## Root Cause Summary")
    if overflow_count > 0:
        print(f"  *** STACK OVERFLOW detected in {overflow_count} task(s) ***")
        print(f"  Check if overflow task matches crash task")
    elif etype == 'HardFault':
        _summarize_hardfault(fault, pc_sym)
    elif etype == 'WDT':
        _summarize_wdt(regs, pc_sym, lr_sym, stack_regions)
        if memp_pools:
            _print_memp_summary(memp_pools, etype)
        if heap_trace and heap_trace.get('active_nodes', 0) > 0:
            _summarize_heap_trace(heap_trace, tlsf_heap)
    elif etype == 'XIC':
        print(f"  XIC Interrupt Overflow - interrupt storm detected")
        print(f"  Check for: stuck ISR, interrupt re-entry, or peripheral malfunction")
    elif result['is_fs_assert']:
        print(f"  Filesystem ASSERT (littlefs corruption)")
        print(f"  ASSERT location: {pc_sym[0] if pc_sym else 'unknown'}")
        print(f"  Called from: {lr_sym[0] if lr_sym else 'unknown'}")
        print(f"  Investigate: flash wear, power loss during write, or filesystem bug")
    elif etype == 'ASSERT':
        print(f"  Software ASSERT in {pc_sym[0] if pc_sym else 'unknown function'}")
        print(f"  Called from {lr_sym[0] if lr_sym else 'unknown'}")
        if assert_buff:
            print(f"  Assert message: \"{assert_buff[:80]}\"")
        _check_assert_subclass(regs, pc_sym, flash_start, flash_end)
        if memp_pools:
            _print_memp_summary(memp_pools, etype)
        if heap_trace and heap_trace.get('active_nodes', 0) > 0:
            _summarize_heap_trace(heap_trace, tlsf_heap)
    else:
        print(f"  Unknown exception type - manual analysis required")

    return 0


# ── Root cause helpers ───────────────────────────────────────────────────

def _summarize_hardfault(fault, pc_sym):
    """Print HardFault root cause summary."""
    hfsr = fault['HFSR']
    if hfsr & (1 << 30):  # FORCED
        if fault['MFSR']:
            print(f"  HardFault caused by MemManage fault")
            if fault['MFSR'] & 2:
                if fault['MFSR'] & 64:
                    mmfar = fault['MMFAR']
                    if mmfar < 0x100:
                        print(f"    NULL pointer dereference at MMFAR=0x{mmfar:08X}")
                    else:
                        print(f"    Data access violation at MMFAR=0x{mmfar:08X}")
                        print(f"    Possible: wild pointer / array out-of-bounds")
                else:
                    print(f"    Data access violation (MMFAR invalid)")
            if fault['MFSR'] & 1:
                print(f"    Instruction access violation (MPU/XN region)")
        elif fault['BFSR']:
            print(f"  HardFault caused by BusFault")
            if fault['BFSR'] & 2:
                if fault['BFSR'] & 128:
                    print(f"    Precise bus error at BFAR=0x{fault['BFAR']:08X}")
                    print(f"    Possible: invalid peripheral address or unmapped memory")
                else:
                    print(f"    Precise bus error (BFAR invalid)")
            if fault['BFSR'] & 16:
                print(f"    Stacking error (STKERR) - stack pointer may be corrupted")
            if fault['BFSR'] & 8:
                print(f"    Unstacking error (UNSTKERR) - stack corrupted during return")
        elif fault['UFSR']:
            print(f"  HardFault caused by UsageFault")
            if fault['UFSR'] & 1:
                print(f"    Undefined instruction - function pointer corruption or code damage")
            if fault['UFSR'] & 2:
                print(f"    Invalid state (ARM mode attempted on Thumb-only CPU)")
                print(f"    Possible: corrupted function pointer with bit0=0")
            if fault['UFSR'] & 256:
                print(f"    Unaligned access")
            if fault['UFSR'] & 512:
                print(f"    Divide by zero")
    else:
        print(f"  HardFault (HFSR=0x{hfsr:08X}, check VECTTL/DEBUGEVT)")


def _summarize_wdt(regs, pc_sym, lr_sym, stack_regions):
    """Print WDT root cause summary."""
    print(f"  WDT Timeout - system reset via NMI -> ASSERT path")
    print(f"  Possible root causes (check in order):")
    print(f"    1. Task deadlock: two tasks waiting on each other")
    print(f"    2. Default_Handler trap: unhandled MemManage/BusFault/UsageFault")
    print(f"       (these have empty infinite-loop handlers, no excep_store)")
    print(f"    3. Interrupt disabled too long: __disable_irq() without restore")
    print(f"    4. Infinite loop in application code")
    print(f"    5. Task stack overflow causing scheduler lockup")
    overflow = [r for r in stack_regions if not r['guard_ok']]
    if overflow:
        print(f"  *** {len(overflow)} stack overflow(s) found - likely WDT cause ***")


def _check_assert_subclass(regs, pc_sym, flash_start, flash_end):
    """Check if ASSERT belongs to a known subclass."""
    r0, r1, r2, r3 = regs['R0'], regs['R1'], regs['R2'], regs['R3']
    if r1 == 0 and r2 == 0 and r3 == 0:
        print(f"  R1-R3 are zero - may be EC_ASSERT(cond, 0, 0, 0) with cond=false")
    elif r0 == 0:
        print(f"  R0=0 - WDT or control assertion: EC_ASSERT(0, v1={r1}, v2={r2}, v3={r3})")
    pc = regs['PC'] & ~1
    if flash_start <= pc <= flash_end:
        if pc_sym and any(lib in pc_sym[0].lower() for lib in ['psif', 'ps_', 'cedr', 'ceu', 'lwip', 'cops']):
            print(f"  *** ASSERT in prebuilt protocol library ***")
            print(f"  Contact EigenComm for ASSERT condition details")


def _print_memp_summary(memp_pools, etype):
    """Print memp pool exhaustion summary for root cause section.

    Key considerations:
    1. LWIP memp pools use independent BSS memory (MEMP_MEM_MALLOC=0), separate
       from FreeRTOS heap. Memp exhaustion does NOT directly cause pvPortMallocEC
       to fail.
    2. Memp pools are SHARED system resources — UDP_PCB/NETCONN are shared across
       all UDP sockets, TCP_PCB across all TCP connections, etc. A single protocol
       module (e.g., COAP using 1 UDP socket) cannot exhaust ALL 17 pools.
    3. ALL pools at 100% simultaneously indicates a systemic issue, not a single-
       module leak. Consider: heap exhaustion affecting LWIP thread, system-wide
       resource mismanagement, or heavy concurrent network activity.
    """
    from ec_memp_scanner import get_pool_group

    exhausted = [p for p in memp_pools if p['status'] == 'EXHAUSTED']
    high = [p for p in memp_pools if p['status'] == 'HIGH']
    if not (exhausted or high):
        return

    total_pools = len(memp_pools)
    critical_count = len(exhausted) + len(high)
    all_exhausted = critical_count == total_pools

    print(f"  *** LWIP memp pool exhaustion detected ({critical_count}/{total_pools} pools) ***")

    for p in exhausted + high:
        holder_info = ""
        if p.get('holder_summary') and p['holder_summary']:
            top = p['holder_summary'][0]
            holder_info = f" ({top[1]} held by {top[0]})"
        if p['total_count'] > 0:
            used_str = f"{p['used_count']}/{p['total_count']} used"
        elif p['status'] == 'EXHAUSTED':
            used_str = "0 free (total unknown)"
        else:
            used_str = f"{p['free_count']} free (total unknown)"
        print(f"    {p['name']}: {used_str}{holder_info}")

    # Group by protocol for attribution
    group_status = {}
    for p in exhausted + high:
        grp = get_pool_group(p['name'])
        if grp not in group_status:
            group_status[grp] = []
        group_status[grp].append(p['name'])

    if len(group_status) > 1:
        grp_summary = ', '.join(f"{g}({','.join(ps)})" for g, ps in group_status.items())
        print(f"  Affected protocol groups: {grp_summary}")

    if all_exhausted:
        print(f"  *** ALL {total_pools} pools exhausted — systemic issue, not single-module leak ***")
        print(f"      memp pools use independent BSS memory (MEMP_MEM_MALLOC=0),")
        print(f"      separate from FreeRTOS heap. Memp exhaustion alone does NOT")
        print(f"      cause pvPortMallocEC (heap malloc) to fail.")
        print(f"      If crash is in pvPortMallocEC (heap), focus on heap leak analysis")
        print(f"      (trace_node, heap_6.c allocations). Memp exhaustion is a co-symptom.")
    else:
        # Only some pools exhausted — attribute to specific protocol
        for grp, pool_names in group_status.items():
            if grp in ('udp',) and len(pool_names) <= 3:
                print(f"  UDP-related pools ({', '.join(pool_names)}) exhausted — check UDP socket leak")
            elif grp in ('tcp',) and len(pool_names) <= 3:
                print(f"  TCP-related pools ({', '.join(pool_names)}) exhausted — check TCP connection leak")
            elif grp in ('dns',) and len(pool_names) <= 2:
                print(f"  DNS pools ({', '.join(pool_names)}) exhausted — check DNS query leak")

    _print_leak_assessment(exhausted + high)

    # Cautious causality statement
    if all_exhausted:
        print(f"  NOTE: Memp pool exhaustion is likely a CO-SYMPTOM of the same resource")
        print(f"  leak causing the {etype}, not the direct cause. Focus on heap leak analysis.")
    else:
        print(f"  Memp pool exhaustion may have caused or contributed to the {etype}")


def _print_leak_assessment(critical_pools):
    """Print leak assessment for exhausted/high-utilization pools.

    Leak assessment is conservative:
    - Holder analysis looks at the first Flash pointer in allocated elements.
      When holders show "<no flash ptr>", it means the first word is a RAM
      address (linked list pointer etc.), NOT that the element is leaked.
    - For small pools (≤10 elements), 100% utilization may be normal peak usage
      during active network operations, especially for transient pools like
      TCPIP_MSG_API that are allocated/freed per socket operation.
    - ALL pools at 100% simultaneously is more likely a systemic issue than
      individual leaks in each pool.
    """
    total_pools = len(critical_pools)
    all_exhausted = all(p['status'] == 'EXHAUSTED' for p in critical_pools)

    if all_exhausted and total_pools >= 10:
        print(f"    Assessment: ALL {total_pools} pools exhausted simultaneously.")
        print(f"    This pattern suggests a systemic issue (e.g., LWIP thread blocked,")
        print(f"    global resource leak, or heavy concurrent activity) rather than")
        print(f"    individual pool leaks. Investigate the common root cause.")
        return

    for p in critical_pools:
        if not p.get('holder_summary') or not p['holder_summary']:
            continue
        total_held = sum(c for _, c in p['holder_summary'])
        total = p['total_count']
        if total <= 0:
            continue

        # Check if the only holder is "<no flash ptr>" — weak evidence
        holders = p['holder_summary']
        has_identified_holder = any(not n.startswith('<') for n, _ in holders)

        top_holder, top_count = holders[0]
        if top_holder == '<no flash ptr>' and not has_identified_holder:
            # Cannot identify holders — note as inconclusive rather than "Likely LEAK"
            if p['status'] == 'EXHAUSTED':
                print(f"    {p['name']}: {total_held}/{total} used, holders unidentified")
                print(f"      (first word of each element is not a Flash pointer —")
                print(f"       may be linked-list head, not a leaked reference)")
            continue

        if top_count >= total * 0.5 and top_count >= 2:
            print(f"    ** Likely LEAK: {p['name']} - {top_count}/{total} elements held by {top_holder}")
        elif total_held >= total * 0.8 and len(holders) <= 3:
            holders_str = ', '.join(f"{c} by {n}" for n, c in holders)
            print(f"    ** Likely LEAK: {p['name']} - {total_held}/{total} held by few holders ({holders_str})")


def _summarize_heap_trace(heap_trace, tlsf_heap=None):
    """Print heap memory trace summary in root cause section."""
    if not heap_trace:
        return

    if tlsf_heap:
        status_str = tlsf_heap['status']
        print(f"  Heap overall: {tlsf_heap['used_size']}/{tlsf_heap['total_size']} bytes "
              f"({tlsf_heap['util_pct']:.1f}% used), "
              f"largest free = {tlsf_heap['largest_free']} bytes ({tlsf_heap['largest_free']/1024:.1f} KB)")
        if status_str == 'CRITICAL':
            print(f"    *** HEAP CRITICALLY LOW: {tlsf_heap['free_pct']:.1f}% free ***")
        elif status_str == 'HIGH':
            print(f"    ** HEAP HIGH USAGE: {tlsf_heap['free_pct']:.1f}% free **")

    active = heap_trace['active_nodes']
    total = heap_trace['total_nodes']
    total_bytes = heap_trace['total_allocated_bytes']
    print(f"  Heap allocation trace: {active}/{total} blocks unfreed, "
          f"{total_bytes} bytes ({total_bytes/1024:.1f} KB)")
    if heap_trace.get('trace_node_full'):
        print(f"    *** trace_node array FULL -- some allocations may be untracked ***")
    top3 = heap_trace.get('top_by_size', [])[:3]
    if top3:
        print(f"    Largest unfreed blocks:")
        for n in top3:
            funptr_map = heap_trace.get('funptr_to_caller', {})
            caller = funptr_map.get(n['funptr'], f"0x{n['funptr']:08X}")
            print(f"      {n['length']:5d} bytes by {n['task_name']} (caller={caller})")
    by_task = heap_trace.get('by_task', {})
    if by_task:
        top_task = max(by_task.items(), key=lambda x: x[1]['total_bytes'])
        print(f"    Top task: {top_task[0]} ({top_task[1]['total_bytes']} bytes, "
              f"{top_task[1]['count']} blocks)")
    if heap_trace.get('leak_indicators'):
        for li in heap_trace['leak_indicators'][:3]:
            print(f"    ** Suspicious: {li['caller']} [{li['task']}] -- "
                  f"{li['count']} blocks, {li['total']} bytes")


def main():
    parser = argparse.ArgumentParser(
        description='EC Platform RAM Dump Analyzer (EC626/EC626E/EC616). '
                    'Auto-detects chip variant from MAP file.')
    sub = parser.add_subparsers(dest='command')

    def add_common_args(p):
        p.add_argument('--map', help='MAP file (enables auto chip detection)')
        p.add_argument('--store-addr', type=lambda x: int(x, 0),
                       help='excep_store address (auto-detect if omitted)')
        p.add_argument('--flash-start', type=lambda x: int(x, 0), default=None,
                       help='Flash start address (auto from MAP)')
        p.add_argument('--flash-end', type=lambda x: int(x, 0), default=None,
                       help='Flash end address (auto from MAP)')

    p1 = sub.add_parser('parse-excep', help='Parse excep_store from RAM dump')
    p1.add_argument('dump', help='RAM dump .bin file')
    add_common_args(p1)

    p2 = sub.add_parser('resolve', help='Resolve addresses to symbols')
    p2.add_argument('addresses', nargs='+', help='Addresses to resolve')
    p2.add_argument('--map', help='MAP file')
    p2.add_argument('--elf', help='ELF file for source line mapping')
    p2.add_argument('--disasm', help='objdump -d output file for disassembly context')

    p3 = sub.add_parser('scan-stacks', help='Scan task stacks for overflow')
    p3.add_argument('dump', help='RAM dump .bin file')

    p4 = sub.add_parser('full-analyze', help='Full crash dump analysis')
    p4.add_argument('dump', help='RAM dump .bin file')
    add_common_args(p4)
    p4.add_argument('--tcb-addr', type=lambda x: int(x, 0),
                    help='pxCurrentTCB address (auto from MAP)')
    p4.add_argument('--elf', help='ELF file for memp_desc struct reading + source line mapping')
    p4.add_argument('--disasm', help='objdump -d output file (.txt) for disassembly context')

    p5 = sub.add_parser('scan-memp', help='Scan LWIP memp pools for exhaustion and leak analysis')
    p5.add_argument('dump', help='RAM dump .bin file')
    p5.add_argument('--map', required=True, help='MAP file (required for memp symbol addresses)')
    p5.add_argument('--elf', help='ELF file for memp_desc struct reading (optional)')
    p5.add_argument('--verbose', '-v', action='store_true', help='Show per-element allocation details')
    p5.add_argument('--util-threshold', type=int, default=80,
                    help='Utilization %% threshold for HIGH warning (default: 80)')

    p6 = sub.add_parser('scan-heap', help='Scan FreeRTOS heap allocation trace (trace_node / mm_debug)')
    p6.add_argument('dump', help='RAM dump .bin file')
    p6.add_argument('--map', required=True, help='MAP file (required for trace_node symbol addresses)')
    p6.add_argument('--verbose', '-v', action='store_true', help='Show all active trace_node entries')

    args = parser.parse_args()
    if args.command == 'parse-excep':
        return cmd_parse_excep(args)
    elif args.command == 'resolve':
        return cmd_resolve(args)
    elif args.command == 'scan-stacks':
        return cmd_scan_stacks(args)
    elif args.command == 'scan-memp':
        return cmd_scan_memp(args)
    elif args.command == 'scan-heap':
        return cmd_scan_heap(args)
    elif args.command == 'full-analyze':
        return cmd_full_analyze(args)
    else:
        parser.print_help()
        return 1


if __name__ == '__main__':
    sys.exit(main())
