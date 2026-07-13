#!/usr/bin/env python3
"""Build output parsing, chip config building, and symbol lookup for EC platform.

Supports two input formats (auto-detected by file extension):
  - GCC MAP file (.map): full Memory Configuration + symbol table
  - arm-none-eabi-nm -S output (.symbols): symbol table with sizes

Both formats produce the same config dict consumed by all analyzer modules.
"""

import re

from ec_elf_reader import read_elf_symbols, has_elftools
from ec_constants import (
    RAM_END_OFF_RESET_REASON, RAM_END_OFF_ASSERT_PC, RAM_END_OFF_ASSERT_LR,
    RAM_END_OFF_MAGIC, RAM_END_OFF_STORE_PTR,
    DEFAULT_RAM_END, DEFAULT_FLASH_START, DEFAULT_FLASH_END,
)



# .symbols line format (arm-none-eabi-nm -S)
_SYM_LINE_RE = re.compile(
    r'^([0-9a-fA-F]{8})\s+([0-9a-fA-F]{2,8})\s+(\w)\s+(\S+)$')

def parse_map_config(filepath):
    """Parse build output file to extract memory layout and key symbols.

    Auto-detects format by extension: .map or .symbols.
    Returns a config dict (same shape regardless of input format).
    """
    if filepath.lower().endswith('.symbols'):
        return _parse_symbols(filepath)
    if filepath.lower().endswith('.elf'):
        return _parse_elf(filepath)
    return _parse_map(filepath)


def _parse_map(map_file):
    """Extract memory layout and key symbols from GCC MAP file.

    Returns a config dict with:
      ram_end, reset_reason_addr, assert_pc_addr, assert_lr_addr,
      magic_addr, store_ptr_addr, flash_start, flash_end,
      excep_store_addr (may be None), pxCurrentTCB_addr (may be None),
      chip_hint (guessed chip name from RAM/Flash size)
    """
    config = {}
    ram_regions = []
    flash_start = None
    flash_end = None

    # Phase 1: Parse Memory Configuration section
    in_mem_config = False
    mem_region_re = re.compile(
        r'^(\S+)\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+(\S+)')

    with open(map_file, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            stripped = line.strip()
            if stripped == 'Memory Configuration':
                in_mem_config = True
                continue
            if in_mem_config:
                if stripped.startswith('Linker script') or stripped.startswith('*default*'):
                    if stripped.startswith('Linker script'):
                        in_mem_config = False
                    continue
                if stripped.startswith('Name') and 'Origin' in stripped:
                    continue
                if not stripped:
                    continue
                m = mem_region_re.match(stripped)
                if m:
                    name, origin_s, length_s, attrs = m.groups()
                    origin = int(origin_s, 16)
                    length = int(length_s, 16)
                    end = origin + length
                    name_upper = name.upper()
                    if 'RAM' in name_upper:
                        ram_regions.append((name, origin, length, end))
                    if 'FLASH' in name_upper:
                        flash_start = origin
                        flash_end = end

    # Calculate RAM_END and key addresses
    if ram_regions:
        ram_end = max(r[3] for r in ram_regions)
        config['ram_end'] = ram_end
        config['reset_reason_addr'] = ram_end - RAM_END_OFF_RESET_REASON
        config['assert_pc_addr'] = ram_end - RAM_END_OFF_ASSERT_PC
        config['assert_lr_addr'] = ram_end - RAM_END_OFF_ASSERT_LR
        config['magic_addr'] = ram_end - RAM_END_OFF_MAGIC
        config['store_ptr_addr'] = ram_end - RAM_END_OFF_STORE_PTR

    if flash_start is not None:
        config['flash_start'] = flash_start
        config['flash_end'] = flash_end

    # Phase 2: Find key symbols
    sym_pattern = re.compile(r'^\s+(0x[0-9a-fA-F]+)\s{8,}([A-Za-z_]\w*)')
    config['excep_store_addr'] = None
    config['pxCurrentTCB_addr'] = None
    memp_bases = {}
    memp_descs = {}
    config['memp_pools_addr'] = None

    config['trace_node_addr'] = None
    config['node_hash_addr'] = None
    config['free_node_addr'] = None

    config['gTotalHeapSize_addr'] = None
    config['ucHeap_addr'] = None

    # Assert and task info symbols
    config['ec_assert_buff_addr'] = None
    config['ec_stack_end_addr_addr'] = None
    config['ec_stack_start_addr_addr'] = None
    config['ec_task_list_addr'] = None
    config['ec_task_list_size'] = 0
    config['curr_task_numb_addr'] = None

    # FreeRTOS kernel state
    config['uxCurrentNumberOfTasks_addr'] = None
    config['xTickCount_addr'] = None
    config['xSchedulerRunning_addr'] = None
    config['uxCriticalNesting_addr'] = None

    with open(map_file, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            m = sym_pattern.match(line)
            if m:
                name = m.group(2)
                addr = int(m.group(1), 16)
                if name == 'excep_store':
                    config['excep_store_addr'] = addr
                elif name == 'pxCurrentTCB':
                    config['pxCurrentTCB_addr'] = addr
                elif name == 'memp_pools':
                    config['memp_pools_addr'] = addr
                elif name == 'trace_node':
                    config['trace_node_addr'] = addr
                elif name == 'node_hash':
                    config['node_hash_addr'] = addr
                elif name == 'free_node':
                    config['free_node_addr'] = addr
                elif name == 'gTotalHeapSize':
                    config['gTotalHeapSize_addr'] = addr
                elif name == 'ucHeap':
                    config['ucHeap_addr'] = addr
                elif name == 'ec_assert_buff':
                    config['ec_assert_buff_addr'] = addr
                elif name == 'ec_stack_end_addr':
                    config['ec_stack_end_addr_addr'] = addr
                elif name == 'ec_stack_start_addr':
                    config['ec_stack_start_addr_addr'] = addr
                elif name == 'ec_task_list':
                    config['ec_task_list_addr'] = addr
                elif name == 'curr_task_numb':
                    config['curr_task_numb_addr'] = addr
                elif name == 'uxCurrentNumberOfTasks':
                    config['uxCurrentNumberOfTasks_addr'] = addr
                elif name == 'xTickCount':
                    config['xTickCount_addr'] = addr
                elif name == 'xSchedulerRunning':
                    config['xSchedulerRunning_addr'] = addr
                elif name == 'uxCriticalNesting':
                    config['uxCriticalNesting_addr'] = addr
                elif name.startswith('memp_memory_') and name.endswith('_base'):
                    pool_name = name[len('memp_memory_'):-len('_base')]
                    memp_bases[pool_name] = addr
                elif name.startswith('memp_') and not name.startswith('memp_memory_') \
                        and not name.startswith('memp_tab_') \
                        and name != 'memp_pools' and name != 'memp_malloc' \
                        and name != 'memp_free' and name != 'memp_init' \
                        and name != 'memp_realloc' and name != 'memp_num':
                    pool_name = name[len('memp_'):]
                    memp_descs[pool_name] = addr

    # Phase 2b: Find memp_tab_*, trace_node size, ec_task_list size
    memp_tabs = {}
    tab_section_re = re.compile(r'^\s+\.bss\.memp_tab_(\S+)')
    trace_node_section_re = re.compile(r'^\s+\.bss\.trace_node\s*$')
    task_list_section_re = re.compile(r'^\s+\.bss\.ec_task_list\s*$')
    addr_size_re = re.compile(r'^\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)')
    config['trace_node_size'] = 0

    with open(map_file, 'r', encoding='utf-8', errors='replace') as f:
        prev_was_tab_section = False
        prev_was_trace_node_section = False
        prev_was_task_list_section = False
        tab_pool_name = None
        for line in f:
            m = tab_section_re.match(line)
            if m:
                prev_was_tab_section = True
                tab_pool_name = m.group(1)
                prev_was_trace_node_section = False
                prev_was_task_list_section = False
                continue
            if trace_node_section_re.match(line):
                prev_was_trace_node_section = True
                prev_was_tab_section = False
                prev_was_task_list_section = False
                continue
            if task_list_section_re.match(line):
                prev_was_task_list_section = True
                prev_was_tab_section = False
                prev_was_trace_node_section = False
                continue
            am = addr_size_re.match(line)
            if prev_was_tab_section and am and tab_pool_name:
                memp_tabs[tab_pool_name] = int(am.group(1), 16)
            if prev_was_trace_node_section and am:
                config['trace_node_size'] = int(am.group(2), 16)
            if prev_was_task_list_section and am:
                config['ec_task_list_size'] = int(am.group(2), 16)
            prev_was_tab_section = False
            prev_was_trace_node_section = False
            prev_was_task_list_section = False
            tab_pool_name = None

    config['memp_tabs'] = memp_tabs
    config['memp_bases'] = memp_bases
    config['memp_descs'] = memp_descs

    config['chip_hint'] = _guess_chip(ram_regions, flash_start, flash_end)
    return config


def _guess_chip(ram_regions, flash_start, flash_end):
    """Guess chip variant from memory region sizes."""
    if not ram_regions or flash_start is None:
        return 'unknown'
    ram_total = sum(r[2] for r in ram_regions)
    flash_size = flash_end - flash_start
    if ram_total == 0x44000 and flash_size <= 0x1A0000:
        return 'ec626'
    elif ram_total == 0x44000 and flash_size > 0x1A0000:
        return 'ec626e'
    else:
        return f'unknown(ram=0x{ram_total:x},flash=0x{flash_size:x})'


def get_default_config():
    """Return default config (EC626 fallback when no MAP file)."""
    ram_end = DEFAULT_RAM_END
    return {
        'ram_end': ram_end,
        'reset_reason_addr': ram_end - RAM_END_OFF_RESET_REASON,
        'assert_pc_addr': ram_end - RAM_END_OFF_ASSERT_PC,
        'assert_lr_addr': ram_end - RAM_END_OFF_ASSERT_LR,
        'magic_addr': ram_end - RAM_END_OFF_MAGIC,
        'store_ptr_addr': ram_end - RAM_END_OFF_STORE_PTR,
        'flash_start': DEFAULT_FLASH_START,
        'flash_end': DEFAULT_FLASH_END,
        'excep_store_addr': None,
        'pxCurrentTCB_addr': None,
        'ec_assert_buff_addr': None,
        'ec_stack_end_addr_addr': None,
        'ec_stack_start_addr_addr': None,
        'ec_task_list_addr': None,
        'ec_task_list_size': 0,
        'curr_task_numb_addr': None,
        'uxCurrentNumberOfTasks_addr': None,
        'xTickCount_addr': None,
        'xSchedulerRunning_addr': None,
        'uxCriticalNesting_addr': None,
        'chip_hint': 'ec626 (default, no MAP)',
    }


def build_config(args):
    """Build config from --map argument (accepts .map or .symbols), else defaults.

    Manual CLI overrides take highest priority.
    """
    if args.map:
        config = parse_map_config(args.map)
    else:
        config = get_default_config()

    if hasattr(args, 'flash_start') and args.flash_start is not None:
        config['flash_start'] = args.flash_start
    if hasattr(args, 'flash_end') and args.flash_end is not None:
        config['flash_end'] = args.flash_end
    if hasattr(args, 'store_addr') and args.store_addr is not None:
        config['excep_store_addr'] = args.store_addr
    if hasattr(args, 'tcb_addr') and args.tcb_addr is not None:
        config['pxCurrentTCB_addr'] = args.tcb_addr

    return config


def find_symbol(filepath, target_addr):
    """Find the symbol containing target_addr in build output file.

    Auto-detects format (.map vs .symbols).
    Returns (symbol_name, symbol_addr, offset) or None.
    """
    if filepath.lower().endswith('.symbols'):
        return _find_symbol_in_symbols(filepath, target_addr)
    if filepath.lower().endswith('.elf'):
        return _find_symbol_in_elf(filepath, target_addr)
    return _find_symbol_in_map(filepath, target_addr)



def _find_symbol_in_elf(elf_path, target_addr):
    """Find enclosing symbol in ELF .symtab using symbol size for precise match."""
    if not has_elftools():
        return None
    clean_addr = target_addr & ~1
    best = None
    best_size = 0
    for addr, size, typ, name in read_elf_symbols(elf_path):
        sym_addr = addr & ~1
        if sym_addr <= clean_addr and (size == 0 or clean_addr < sym_addr + size):
            if best is None or (size > 0 and (best_size == 0 or size < best_size)):
                best = (name, sym_addr)
                best_size = size
    if best:
        return best[0], best[1], clean_addr - best[1]
    return None


def _find_symbol_in_map(map_file, target_addr):
    """Find enclosing symbol in MAP file by best address match.
    Returns (symbol_name, symbol_addr, offset) or None.
    """
    clean_addr = target_addr & ~1
    sym_pattern = re.compile(r'^\s+(0x[0-9a-fA-F]+)\s{8,}([A-Za-z_]\w*)')

    best = None
    with open(map_file, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            m = sym_pattern.match(line)
            if m:
                try:
                    addr = int(m.group(1), 16)
                    name = m.group(2)
                    if addr <= clean_addr and addr > 0x1000:
                        if best is None or addr > best[0]:
                            best = (addr, name)
                except ValueError:
                    continue

    if best:
        return best[1], best[0], clean_addr - best[0]
    return None


# .symbols file parser (arm-none-eabi-nm -S output) ──────────────────

def _parse_symbols(symbols_file):
    """Extract config from arm-none-eabi-nm style .symbols file.

    Format per line: addr size type name
    Unlike MAP, there is no Memory Configuration section.
    RAM_END is estimated from BSS symbol maximum end address.
    Flash range is estimated from text symbol addresses.
    """
    symbols = []

    with open(symbols_file, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            m = _SYM_LINE_RE.match(line.strip())
            if m:
                addr = int(m.group(1), 16)
                size = int(m.group(2), 16)
                typ = m.group(3)
                name = m.group(4)
                symbols.append((addr, size, typ, name))

    config = {
        'excep_store_addr': None,
        'pxCurrentTCB_addr': None,
        'memp_pools_addr': None,
        'trace_node_addr': None,
        'node_hash_addr': None,
        'free_node_addr': None,
        'gTotalHeapSize_addr': None,
        'ucHeap_addr': None,
        'trace_node_size': 0,
        'ec_assert_buff_addr': None,
        'ec_stack_end_addr_addr': None,
        'ec_stack_start_addr_addr': None,
        'ec_task_list_addr': None,
        'ec_task_list_size': 0,
        'curr_task_numb_addr': None,
        'uxCurrentNumberOfTasks_addr': None,
        'xTickCount_addr': None,
        'xSchedulerRunning_addr': None,
        'uxCriticalNesting_addr': None,
    }
    memp_bases = {}
    memp_tabs = {}
    memp_descs = {}

    bss_max_end = 0
    text_min_addr = 0xFFFFFFFF
    text_max_end = 0

    for addr, size, typ, name in symbols:
        if name == 'excep_store':
            config['excep_store_addr'] = addr
        elif name == 'pxCurrentTCB':
            config['pxCurrentTCB_addr'] = addr
        elif name == 'memp_pools':
            config['memp_pools_addr'] = addr
        elif name == 'trace_node':
            config['trace_node_addr'] = addr
            config['trace_node_size'] = size
        elif name == 'node_hash':
            config['node_hash_addr'] = addr
        elif name == 'free_node':
            config['free_node_addr'] = addr
        elif name == 'gTotalHeapSize':
            config['gTotalHeapSize_addr'] = addr
        elif name == 'ucHeap':
            config['ucHeap_addr'] = addr
        elif name == 'ec_assert_buff':
            config['ec_assert_buff_addr'] = addr
        elif name == 'ec_stack_end_addr':
            config['ec_stack_end_addr_addr'] = addr
        elif name == 'ec_stack_start_addr':
            config['ec_stack_start_addr_addr'] = addr
        elif name == 'ec_task_list':
            config['ec_task_list_addr'] = addr
            config['ec_task_list_size'] = size
        elif name == 'curr_task_numb':
            config['curr_task_numb_addr'] = addr
        elif name == 'uxCurrentNumberOfTasks':
            config['uxCurrentNumberOfTasks_addr'] = addr
        elif name == 'xTickCount':
            config['xTickCount_addr'] = addr
        elif name == 'xSchedulerRunning':
            config['xSchedulerRunning_addr'] = addr
        elif name == 'uxCriticalNesting':
            config['uxCriticalNesting_addr'] = addr
        elif name.startswith('memp_memory_') and name.endswith('_base'):
            pool_name = name[len('memp_memory_'):-len('_base')]
            memp_bases[pool_name] = addr
        elif name.startswith('memp_tab_'):
            pool_name = name[len('memp_tab_'):]
            memp_tabs[pool_name] = addr

        # RAM/Flash layout estimation from symbol types
        if typ in ('B', 'b'):
            end = addr + size
            if end > bss_max_end:
                bss_max_end = end
        if typ in ('T', 't') and addr >= 0x00800000:
            end = addr + size
            if addr < text_min_addr:
                text_min_addr = addr
            if end > text_max_end:
                text_max_end = end

    config['memp_bases'] = memp_bases
    config['memp_tabs'] = memp_tabs
    config['memp_descs'] = memp_descs

    # RAM_END estimation: align BSS max end up to nearest page boundary.
    # The gap between bss_max_end and true RAM_END is the reserved area
    # (reset_reason, excep_store ptr, etc.), typically < 0x30 bytes.
    if bss_max_end > 0:
        ram_end_est = (bss_max_end + 0xFFF) & ~0xFFF
        if ram_end_est - bss_max_end > 0x1000:
            ram_end_est = (bss_max_end + 0xFF) & ~0xFF
    else:
        ram_end_est = DEFAULT_RAM_END

    config['ram_end'] = ram_end_est
    config['reset_reason_addr'] = ram_end_est - RAM_END_OFF_RESET_REASON
    config['assert_pc_addr'] = ram_end_est - RAM_END_OFF_ASSERT_PC
    config['assert_lr_addr'] = ram_end_est - RAM_END_OFF_ASSERT_LR
    config['magic_addr'] = ram_end_est - RAM_END_OFF_MAGIC
    config['store_ptr_addr'] = ram_end_est - RAM_END_OFF_STORE_PTR

    if text_min_addr < 0xFFFFFFFF:
        config['flash_start'] = text_min_addr
        config['flash_end'] = text_max_end
    else:
        config['flash_start'] = DEFAULT_FLASH_START
        config['flash_end'] = DEFAULT_FLASH_END

    config['chip_hint'] = _guess_chip_from_bss(bss_max_end, text_max_end)
    return config


def _find_symbol_in_symbols(symbols_file, target_addr):
    """Find enclosing symbol in .symbols file using symbol size for precise match.

    Returns (symbol_name, symbol_addr, offset) or None.
    """
    clean_addr = target_addr & ~1
    best = None
    best_size = 0

    with open(symbols_file, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            m = _SYM_LINE_RE.match(line.strip())
            if m:
                addr = int(m.group(1), 16)
                size = int(m.group(2), 16)
                name = m.group(4)
                if addr <= clean_addr and (size == 0 or clean_addr < addr + size):
                    if best is None or (size > 0 and (best_size == 0 or size < best_size)):
                        best = (name, addr)
                        best_size = size

    if best:
        return best[0], best[1], clean_addr - best[1]
    return None




# ── ELF file parser (via pyelftools .symtab) ──────────────────────────

def _parse_elf(elf_path):
    """Extract config from ELF file .symtab section.

    Requires pyelftools. Falls back to defaults if unavailable.
    RAM_END estimation same as .symbols (from BSS symbol max end).
    """
    if not has_elftools():
        return get_default_config()

    symbols = read_elf_symbols(elf_path)
    if not symbols:
        return get_default_config()

    config = {
        'excep_store_addr': None,
        'pxCurrentTCB_addr': None,
        'memp_pools_addr': None,
        'trace_node_addr': None,
        'node_hash_addr': None,
        'free_node_addr': None,
        'gTotalHeapSize_addr': None,
        'ucHeap_addr': None,
        'trace_node_size': 0,
        'ec_assert_buff_addr': None,
        'ec_stack_end_addr_addr': None,
        'ec_stack_start_addr_addr': None,
        'ec_task_list_addr': None,
        'ec_task_list_size': 0,
        'curr_task_numb_addr': None,
        'uxCurrentNumberOfTasks_addr': None,
        'xTickCount_addr': None,
        'xSchedulerRunning_addr': None,
        'uxCriticalNesting_addr': None,
    }
    memp_bases = {}
    memp_tabs = {}
    memp_descs = {}

    bss_max_end = 0
    text_min_addr = 0xFFFFFFFF
    text_max_end = 0

    for addr, size, typ, name in symbols:
        clean_addr = addr & ~1

        if name == 'excep_store':
            config['excep_store_addr'] = clean_addr
        elif name == 'pxCurrentTCB':
            config['pxCurrentTCB_addr'] = clean_addr
        elif name == 'memp_pools':
            config['memp_pools_addr'] = clean_addr
        elif name == 'trace_node':
            config['trace_node_addr'] = clean_addr
            config['trace_node_size'] = size
        elif name == 'node_hash':
            config['node_hash_addr'] = clean_addr
        elif name == 'free_node':
            config['free_node_addr'] = clean_addr
        elif name == 'gTotalHeapSize':
            config['gTotalHeapSize_addr'] = clean_addr
        elif name == 'ucHeap':
            config['ucHeap_addr'] = clean_addr
        elif name == 'ec_assert_buff':
            config['ec_assert_buff_addr'] = clean_addr
        elif name == 'ec_stack_end_addr':
            config['ec_stack_end_addr_addr'] = clean_addr
        elif name == 'ec_stack_start_addr':
            config['ec_stack_start_addr_addr'] = clean_addr
        elif name == 'ec_task_list':
            config['ec_task_list_addr'] = clean_addr
            config['ec_task_list_size'] = size
        elif name == 'curr_task_numb':
            config['curr_task_numb_addr'] = clean_addr
        elif name == 'uxCurrentNumberOfTasks':
            config['uxCurrentNumberOfTasks_addr'] = clean_addr
        elif name == 'xTickCount':
            config['xTickCount_addr'] = clean_addr
        elif name == 'xSchedulerRunning':
            config['xSchedulerRunning_addr'] = clean_addr
        elif name == 'uxCriticalNesting':
            config['uxCriticalNesting_addr'] = clean_addr
        elif name.startswith('memp_memory_') and name.endswith('_base'):
            pool_name = name[len('memp_memory_'):-len('_base')]
            memp_bases[pool_name] = clean_addr
        elif name.startswith('memp_tab_'):
            pool_name = name[len('memp_tab_'):]
            memp_tabs[pool_name] = clean_addr

        if typ in ('B', 'b'):
            end = clean_addr + size
            if end > bss_max_end:
                bss_max_end = end
        if typ in ('T', 't') and clean_addr >= 0x00800000:
            end = clean_addr + size
            if clean_addr < text_min_addr:
                text_min_addr = clean_addr
            if end > text_max_end:
                text_max_end = end

    config['memp_bases'] = memp_bases
    config['memp_tabs'] = memp_tabs
    config['memp_descs'] = memp_descs

    if bss_max_end > 0:
        ram_end_est = (bss_max_end + 0xFFF) & ~0xFFF
        if ram_end_est - bss_max_end > 0x1000:
            ram_end_est = (bss_max_end + 0xFF) & ~0xFF
    else:
        ram_end_est = DEFAULT_RAM_END

    config['ram_end'] = ram_end_est
    config['reset_reason_addr'] = ram_end_est - RAM_END_OFF_RESET_REASON
    config['assert_pc_addr'] = ram_end_est - RAM_END_OFF_ASSERT_PC
    config['assert_lr_addr'] = ram_end_est - RAM_END_OFF_ASSERT_LR
    config['magic_addr'] = ram_end_est - RAM_END_OFF_MAGIC
    config['store_ptr_addr'] = ram_end_est - RAM_END_OFF_STORE_PTR

    if text_min_addr < 0xFFFFFFFF:
        config['flash_start'] = text_min_addr
        config['flash_end'] = text_max_end
    else:
        config['flash_start'] = DEFAULT_FLASH_START
        config['flash_end'] = DEFAULT_FLASH_END

    config['chip_hint'] = _guess_chip_from_bss(bss_max_end, text_max_end)
    return config

def _guess_chip_from_bss(bss_max_end, text_max_end):
    """Guess chip variant from symbol address ranges (.symbols mode)."""
    if bss_max_end == 0:
        return 'unknown'
    if 0x40000 <= bss_max_end <= 0x50000:
        if text_max_end <= 0x00A00000:
            return 'ec626'
        return 'ec626e'
    return f'unknown(bss_max=0x{bss_max_end:x})'
