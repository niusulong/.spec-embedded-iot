#!/usr/bin/env python3
"""LWIP memp pool scanning and leak detection for EC platform."""

from ec_constants import u32
from ec_build_config import find_symbol
from ec_elf_reader import read_memp_desc_from_elf


# ── LWIP memp pool descriptions and protocol grouping ────────────────────
POOL_DESCRIPTIONS = {
    'API_MSG': 'lwIP API message structs (socket/netconn calls)',
    'DNS_API_MSG': 'DNS API message structs (lwip_getaddrinfo)',
    'FRAG_PBUF': 'IP fragment pbuf references',
    'IP6_REASSDATA': 'IPv6 reassembly data',
    'ND6_QUEUE': 'IPv6 ND6 queue entries',
    'NETBUF': 'network buffer structs (sequential API recv)',
    'NETCONN': 'network connection structs (one per socket)',
    'NETDB': 'netdb (getaddrinfo) entries',
    'PBUF': 'pbuf structs (ROM/REF type)',
    'RAW_PCB': 'raw protocol control blocks',
    'REASSDATA': 'IP reassembly data',
    'SOCKET_SETGETSOCKOPT_DATA': 'socket set/getsockopt data',
    'TCPIP_MSG_API': 'tcpip thread API messages',
    'TCPIP_MSG_INPKT': 'tcpip thread input packets',
    'TCP_PCB': 'TCP protocol control blocks',
    'TCP_PCB_LISTEN': 'TCP listening PCBs',
    'TCP_SEG': 'TCP segment structs',
    'UDP_PCB': 'UDP protocol control blocks',
}

# Pool grouping by protocol/module for leak attribution.
# Key insight: LWIP memp pools are SHARED system resources. A single UDP socket
# only consumes UDP_PCB + NETCONN. ALL pools being 100% usually means a systemic
# issue, not a single-protocol leak. Group pools to help identify which module
# might be responsible for exhaustion.
POOL_GROUPS = {
    'udp': [
        'UDP_PCB',        # one per UDP socket
        'NETCONN',        # one per socket (shared with TCP)
        'NETBUF',         # per received datagram
        'PBUF',           # pbuf ref/rom type
    ],
    'tcp': [
        'TCP_PCB',        # one per TCP connection
        'TCP_PCB_LISTEN', # one per listening socket
        'TCP_SEG',        # per queued TCP segment
    ],
    'dns': [
        'DNS_API_MSG',    # DNS query API message
        'NETDB',          # getaddrinfo result
    ],
    'api': [
        'API_MSG',           # socket API call message
        'TCPIP_MSG_API',     # tcpip thread API message
        'TCPIP_MSG_INPKT',   # tcpip thread input packet message
        'SOCKET_SETGETSOCKOPT_DATA',
    ],
    'ip': [
        'REASSDATA',      # IP reassembly
        'FRAG_PBUF',      # IP fragment
        'IP6_REASSDATA',  # IPv6 reassembly
        'ND6_QUEUE',      # IPv6 ND6
    ],
    'raw': [
        'RAW_PCB',        # raw protocol PCB
    ],
}

# Reverse lookup: pool name -> group name
_POOL_TO_GROUP = {}
for _grp, _pools in POOL_GROUPS.items():
    for _p in _pools:
        _POOL_TO_GROUP[_p] = _grp


def get_pool_group(pool_name):
    """Return the protocol group for a memp pool name."""
    return _POOL_TO_GROUP.get(pool_name, 'other')


def _walk_free_list(dump_data, tab_ptr, max_walk=256):
    """Walk LWIP memp free list from tab head pointer."""
    free_count = 0
    free_addrs = set()
    dlen = len(dump_data)
    ptr = tab_ptr
    while ptr != 0 and free_count < max_walk:
        if ptr in free_addrs:
            break
        free_addrs.add(ptr)
        if ptr + 4 > dlen:
            break
        ptr = u32(dump_data, ptr)
        free_count += 1
    return free_count, free_addrs


def _derive_elem_stride(free_addrs_sorted):
    """Derive element stride from sorted free element addresses."""
    if len(free_addrs_sorted) < 2:
        return 0
    diffs = []
    for i in range(1, len(free_addrs_sorted)):
        diff = free_addrs_sorted[i] - free_addrs_sorted[i - 1]
        if diff > 0:
            diffs.append(diff)
    if not diffs:
        return 0
    min_stride = min(diffs)
    for d in diffs:
        if d % min_stride != 0:
            return 0
    return min_stride


def _enumerate_pool_elements(pool_base, elem_size, total_count, free_addr_set):
    """Enumerate all elements in a memp pool."""
    aligned_base = (pool_base + 3) & ~3
    elements = []
    for i in range(total_count):
        addr = aligned_base + i * elem_size
        elements.append({
            'addr': addr,
            'index': i,
            'is_free': addr in free_addr_set,
        })
    return elements


def _analyze_allocated_elements(dump_data, allocated_elems, elem_size,
                                flash_start, flash_end, map_file=None):
    """Analyze allocated (in-use) pool elements for leak detection."""
    dlen = len(dump_data)
    n_words = min(elem_size // 4, 8) if elem_size >= 4 else 2
    results = []
    for elem in allocated_elems:
        addr = elem['addr']
        raw_words = []
        flash_ptrs = []
        for w in range(n_words):
            word_addr = addr + w * 4
            if word_addr + 4 <= dlen:
                val = u32(dump_data, word_addr)
                raw_words.append(val)
                if flash_start <= val < flash_end:
                    flash_ptrs.append(val)
            else:
                raw_words.append(0)
        holder = flash_ptrs[0] if flash_ptrs else None
        results.append({
            'addr': addr,
            'index': elem['index'],
            'raw_words': raw_words,
            'flash_ptrs': flash_ptrs,
            'holder': holder,
        })
    return results


def _summarize_holders(allocated_analysis, map_file=None):
    """Summarize allocated elements by holder (first Flash pointer)."""
    if not allocated_analysis:
        return []
    holder_counts = {}
    for elem in allocated_analysis:
        h = elem.get('holder')
        if h is not None:
            holder_counts[h] = holder_counts.get(h, 0) + 1
        else:
            holder_counts[None] = holder_counts.get(None, 0) + 1
    result = []
    for addr, count in sorted(holder_counts.items(), key=lambda x: -x[1]):
        if addr is not None and map_file:
            sym = find_symbol(map_file, addr)
            name = sym[0] if sym else f'0x{addr:08X}'
        elif addr is not None:
            name = f'0x{addr:08X}'
        else:
            name = '<no flash ptr>'
        result.append((name, count))
    return result


def scan_memp_pools(dump_data, memp_tabs, memp_bases, max_walk=256,
                    memp_descs=None, elf_file=None,
                    flash_start=0, flash_end=0, map_file=None,
                    util_threshold=80):
    """Scan LWIP memp pools for exhaustion and utilization from RAM dump."""
    if not memp_tabs:
        return []

    sorted_pools = sorted(memp_tabs.items(), key=lambda x: x[1])
    all_bases_sorted = sorted(set(memp_bases.values()))
    results = []
    dump_len = len(dump_data)

    for i, (name, tab_addr) in enumerate(sorted_pools):
        if tab_addr + 4 > dump_len:
            continue

        tab_ptr = u32(dump_data, tab_addr)
        pool_base = memp_bases.get(name, 0)

        free_count, free_addrs = _walk_free_list(dump_data, tab_ptr, max_walk)

        elem_size = 0
        total_count = 0
        size_source = 'unknown'

        # Method A: Read from ELF memp_desc struct
        if memp_descs and name in memp_descs and elf_file:
            desc = read_memp_desc_from_elf(elf_file, memp_descs[name])
            if desc:
                raw_size = desc['size']
                elem_size = (raw_size + 3) & ~3
                total_count = desc['num']
                size_source = f'ELF memp_desc(size={raw_size},num={total_count})'

        # Method B: Derive from free list stride
        if elem_size == 0 and len(free_addrs) >= 2:
            sorted_free = sorted(free_addrs)
            stride = _derive_elem_stride(sorted_free)
            if stride > 0:
                if pool_base and all_bases_sorted:
                    larger_bases = [b for b in all_bases_sorted if b > pool_base]
                    pool_end = min(larger_bases) if larger_bases else 0
                    pool_region = pool_end - pool_base if pool_end else 0
                    if pool_region > 0 and pool_region % stride == 0:
                        elem_size = stride
                        total_count = pool_region // stride
                        size_source = f'stride({stride},from {len(free_addrs)} free)'

        # Method C: For exhausted pools, try common element sizes
        if elem_size == 0 and tab_ptr == 0 and pool_base and all_bases_sorted:
            larger_bases = [b for b in all_bases_sorted if b > pool_base]
            pool_end = min(larger_bases) if larger_bases else 0
            pool_region = pool_end - pool_base if pool_end else 0
            if pool_region > 0:
                aligned_base = (pool_base + 3) & ~3
                usable = pool_end - aligned_base
                candidates = []
                for es in range(8, 257, 4):
                    for cnt in range(2, 65):
                        used = cnt * es
                        if used > usable:
                            break
                        if usable - used <= 15:
                            candidates.append((es, cnt, usable - used))
                if candidates:
                    def _score(c):
                        _es, _cnt, _pad = c
                        s = _pad * 2
                        if 4 <= _cnt <= 32: s -= 100
                        if 12 <= _es <= 128: s -= 50
                        if _cnt > 32: s += 50
                        if _es < 12: s += 30
                        return s
                    candidates.sort(key=_score)
                    elem_size = candidates[0][0]
                    total_count = candidates[0][1]
                    size_source = f'estimated(region={usable},stride={elem_size},pad={candidates[0][2]})'

        used_count = 0
        util_pct = 0.0
        if total_count > 0:
            used_count = total_count - free_count
            util_pct = (used_count / total_count * 100)
        elif free_count == 0 and tab_ptr == 0:
            used_count = 0
            util_pct = 100.0

        if tab_ptr == 0:
            status = 'EXHAUSTED'
        elif util_pct >= util_threshold:
            status = 'HIGH'
        else:
            status = 'OK'

        allocated_analysis = None
        holder_summary = None
        if status in ('EXHAUSTED', 'HIGH') and elem_size > 0 and total_count > 0:
            elements = _enumerate_pool_elements(pool_base, elem_size, total_count, free_addrs)
            alloc_elems = [e for e in elements if not e['is_free']]
            if alloc_elems:
                allocated_analysis = _analyze_allocated_elements(
                    dump_data, alloc_elems, elem_size, flash_start, flash_end, map_file)
                holder_summary = _summarize_holders(allocated_analysis, map_file)

        pool_size = 0
        if pool_base and all_bases_sorted:
            larger_bases = [b for b in all_bases_sorted if b > pool_base]
            if larger_bases:
                pool_size = min(larger_bases) - pool_base

        results.append({
            'name': name,
            'tab_addr': tab_addr,
            'tab_ptr': tab_ptr,
            'free_count': free_count,
            'pool_base': pool_base,
            'pool_size': pool_size,
            'elem_size': elem_size,
            'total_count': total_count,
            'used_count': used_count,
            'util_pct': util_pct,
            'status': status,
            'allocated': allocated_analysis,
            'holder_summary': holder_summary,
            'size_source': size_source,
        })

    return results


def print_memp_report(pools, verbose=False):
    """Print formatted LWIP memp pool report with utilization and leak analysis."""
    if not pools:
        print("  No LWIP memp pool symbols found in MAP file")
        return

    exhausted = [p for p in pools if p['status'] == 'EXHAUSTED']
    high = [p for p in pools if p['status'] == 'HIGH']
    ok_pools = [p for p in pools if p['status'] == 'OK']

    print(f"  {len(pools)} pools scanned, {len(exhausted)} EXHAUSTED, {len(high)} HIGH")

    has_elem_info = any(p['elem_size'] > 0 for p in pools)
    if has_elem_info:
        print(f"\n  {'Pool':<28} {'Free':>5} {'Used':>5} {'Total':>5} {'Util':>6}  {'ElemSz':>6}  {'Status'}")
        print(f"  {'-'*28} {'-'*5} {'-'*5} {'-'*5} {'-'*6}  {'-'*6}  {'-'*20}")
        for p in exhausted + high + ok_pools:
            status_str = '*** EXHAUSTED ***' if p['status'] == 'EXHAUSTED' else \
                         'HIGH' if p['status'] == 'HIGH' else 'OK'
            total_str = str(p['total_count']) if p['total_count'] > 0 else '?'
            used_str = str(p['used_count']) if p['total_count'] > 0 else '?'
            util_str = f"{p['util_pct']:.0f}%" if p['total_count'] > 0 else '?'
            elem_str = f"0x{p['elem_size']:X}" if p['elem_size'] > 0 else '?'
            print(f"  {p['name']:<28} {p['free_count']:>5} {used_str:>5} {total_str:>5} {util_str:>6}  {elem_str:>6}  {status_str}")
    else:
        print(f"\n  {'Pool':<28} {'Free':>5}  {'Status'}")
        print(f"  {'-'*28} {'-'*5}  {'-'*20}")
        for p in exhausted + ok_pools:
            status_str = '*** EXHAUSTED ***' if p['status'] == 'EXHAUSTED' else 'OK'
            print(f"  {p['name']:<28} {p['free_count']:>5}  {status_str}")

    critical = exhausted + high
    if critical:
        for p in critical:
            status_str = '*** EXHAUSTED ***' if p['status'] == 'EXHAUSTED' else 'HIGH utilization'
            desc = POOL_DESCRIPTIONS.get(p['name'], '')
            desc_str = f" ({desc})" if desc else ""
            print(f"\n  *** {p['name']}{desc_str}: {status_str} ***")
            if p['total_count'] > 0:
                print(f"    {p['used_count']}/{p['total_count']} elements in use ({p['util_pct']:.0f}%)")
            else:
                print(f"    0 free elements (total unknown)")
            if p['elem_size'] > 0:
                print(f"    Element size: {p['elem_size']} bytes (source: {p['size_source']})")
            if p.get('holder_summary'):
                print(f"    Holder analysis (by first Flash pointer in allocated elements):")
                for holder_name, count in p['holder_summary']:
                    print(f"      {count} element(s) held by {holder_name}")

                # Conservative leak assessment: only judge "Likely LEAK" when
                # we can actually identify the holder (not "<no flash ptr>").
                # Small pools (≤10) at 100% may be normal peak usage.
                has_identified_holder = any(
                    not n.startswith('<') for n, _ in p['holder_summary'])

                if not has_identified_holder:
                    print(f"    Holder analysis inconclusive (no Flash code pointers found)")
                    print(f"    (First word of elements is likely a RAM linked-list pointer)")
                elif len(p['holder_summary']) == 1 and p['holder_summary'][0][1] >= 2:
                    holder_name = p['holder_summary'][0][0]
                    count = p['holder_summary'][0][1]
                    if p['total_count'] > 0 and count >= p['total_count'] * 0.5:
                        print(f"    ** Likely LEAK: {count}/{p['total_count']} elements held by {holder_name}")
                elif len(p['holder_summary']) <= 3 and p['total_count'] > 0:
                    total_held = sum(c for _, c in p['holder_summary'])
                    if total_held >= p['total_count'] * 0.8:
                        print(f"    ** Likely LEAK: {total_held}/{p['total_count']} elements held by few holders")
            if verbose and p.get('allocated'):
                print(f"    Allocated elements detail:")
                for elem in p['allocated']:
                    raw_hex = ' '.join(f'0x{w:08X}' for w in elem['raw_words'][:4])
                    holder = elem.get('holder')
                    if holder and p.get('holder_summary'):
                        for hname, _ in p['holder_summary']:
                            if hname.startswith('0x') and int(hname, 16) == holder:
                                holder_str = hname
                                break
                            elif not hname.startswith('0x'):
                                holder_str = hname
                                break
                        else:
                            holder_str = f'0x{holder:08X}'
                    elif holder:
                        holder_str = f'0x{holder:08X}'
                    else:
                        holder_str = 'none'
                    print(f"      elem[{elem['index']}] @0x{elem['addr']:08X}: {raw_hex}  holder={holder_str}")
    elif not exhausted and not high:
        print(f"\n  All {len(pools)} pools have sufficient free elements")

    # Protocol-grouped summary for critical pools
    critical = exhausted + high
    if critical:
        print(f"\n  --- Pool exhaustion by protocol group ---")
        group_status = {}
        for p in critical:
            grp = get_pool_group(p['name'])
            if grp not in group_status:
                group_status[grp] = {'exhausted': 0, 'high': 0, 'pools': []}
            if p['status'] == 'EXHAUSTED':
                group_status[grp]['exhausted'] += 1
            else:
                group_status[grp]['high'] += 1
            group_status[grp]['pools'].append(p['name'])

        for grp in ('udp', 'tcp', 'dns', 'api', 'ip', 'raw', 'other'):
            if grp not in group_status:
                continue
            gs = group_status[grp]
            pool_names = ', '.join(gs['pools'])
            status_parts = []
            if gs['exhausted']:
                status_parts.append(f"{gs['exhausted']} exhausted")
            if gs['high']:
                status_parts.append(f"{gs['high']} high")
            print(f"    [{grp:>4s}] {', '.join(status_parts)}: {pool_names}")

        # Systemic exhaustion warning
        total_critical = len(critical)
        total_pools = len(pools)
        if total_critical == total_pools:
            print(f"\n  *** ALL {total_pools} pools exhausted/high simultaneously ***")
            print(f"      This is unlikely caused by a single protocol/module leak.")
            print(f"      Possible causes: heap exhaustion affecting LWIP internals,")
            print(f"      systemic resource leak, or extended heavy network activity.")
            print(f"      NOTE: memp pools use independent BSS memory (MEMP_MEM_MALLOC=0),")
            print(f"      so memp exhaustion does NOT directly cause heap malloc failure.")
