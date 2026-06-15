#!/usr/bin/env python3
"""Disassembly context lookup from objdump -d output (.txt file).

Given a crash PC address, finds surrounding instructions in the
objdump disassembly file for context analysis.
"""

import re

# objdump -d line patterns:
# Function label:  "008e569c <coap_socket_connect_tcp1>:"
# Instruction:     "  8e56a0:	e92d 47f0 	stmdb	sp!, {r4, r5, r6, r7, r8, r9, sl, lr}"
_FUNC_RE = re.compile(r'^([0-9a-fA-F]{6,8})\s+<(\S+)>:')
_INST_RE = re.compile(r'^\s+([0-9a-fA-F]+):\s+')


def _parse_line(line):
    """Parse a single objdump line. Returns (addr, type, payload) or None.
    type: 'func' for function labels, 'inst' for instructions.
    """
    s = line.strip()
    if not s:
        return None
    m = _FUNC_RE.match(s)
    if m:
        return int(m.group(1), 16), 'func', m.group(2)
    m = _INST_RE.match(line)
    if m:
        return int(m.group(1), 16), 'inst', s
    return None


def load_disasm_index(disasm_file):
    """Build an address -> file-line-number index from objdump output.

    Returns list of (addr, line_no) sorted by addr, suitable for binary search.
    """
    index = []
    with open(disasm_file, 'r', encoding='utf-8', errors='replace') as f:
        for i, line in enumerate(f):
            parsed = _parse_line(line)
            if parsed:
                addr, typ, _ = parsed
                index.append((addr, i + 1))  # 1-based line number
    return index


def _bisect_right(index, addr):
    """Find insertion point for addr in sorted index list."""
    lo, hi = 0, len(index)
    while lo < hi:
        mid = (lo + hi) // 2
        if index[mid][0] <= addr:
            lo = mid + 1
        else:
            hi = mid
    return lo


def lookup_disasm(disasm_file, target_addr, context=10, index=None):
    """Look up disassembly context around target_addr.

    Args:
        disasm_file: path to objdump -d output file
        target_addr: address to look up
        context: number of instructions before and after
        index: pre-built index from load_disasm_index (optional)

    Returns dict with:
        'func_name': enclosing function name or None
        'lines': list of (addr, text) tuples around target
        'target_line_idx': index into 'lines' pointing to target_addr
    or None if address not found.
    """
    if index is None:
        index = load_disasm_index(disasm_file)
    if not index:
        return None

    # Find the closest instruction at or before target_addr
    pos = _bisect_right(index, target_addr)
    if pos == 0:
        return None

    # Check that we're within a reasonable range (same function)
    target_line_no = index[pos - 1][1]

    # Read the file around that line
    with open(disasm_file, 'r', encoding='utf-8', errors='replace') as f:
        all_lines = f.readlines()

    total_lines = len(all_lines)
    func_name = None
    result_lines = []
    target_idx = -1

    # Scan backward to find function label and collect context
    scan_start = max(0, target_line_no - context * 3 - 1)
    scan_end = min(total_lines, target_line_no + context * 2)

    # First pass: find function name by scanning backward from target
    for i in range(target_line_no - 1, max(0, target_line_no - 200), -1):
        parsed = _parse_line(all_lines[i])
        if parsed and parsed[1] == 'func':
            func_name = parsed[2]
            break

    # Collect instructions around target
    instructions = []
    for i in range(scan_start, scan_end):
        parsed = _parse_line(all_lines[i])
        if parsed:
            addr, typ, payload = parsed
            if typ == 'inst':
                instructions.append((i + 1, addr, payload, all_lines[i].rstrip()))
            elif typ == 'func' and addr <= target_addr + 0x1000:
                instructions.append((i + 1, addr, f'<{payload}>:', all_lines[i].rstrip()))

    # Find the target in collected instructions
    best_idx = -1
    for idx, (line_no, addr, _, _) in enumerate(instructions):
        if addr <= target_addr:
            best_idx = idx
        elif addr > target_addr:
            break

    if best_idx < 0:
        return None

    # Slice context around target
    start = max(0, best_idx - context)
    end = min(len(instructions), best_idx + context + 1)

    selected = instructions[start:end]
    target_in_selected = best_idx - start

    return {
        'func_name': func_name,
        'lines': [(ln, addr, text) for ln, addr, text, _ in selected],
        'target_line_idx': target_in_selected,
    }


def format_disasm(result, highlight_addr=None):
    """Format disassembly result for console output.

    Returns list of strings, one per line.
    """
    if not result:
        return ['(no disassembly available)']

    lines = []
    if result.get('func_name'):
        lines.append(f"  Function: {result['func_name']}")
        lines.append("")

    tidx = result.get('target_line_idx', -1)
    for i, (ln, addr, text) in enumerate(result['lines']):
        marker = ">>>" if i == tidx else "   "
        lines.append(f"  {marker} 0x{addr:08X}: {text}")

    return lines
