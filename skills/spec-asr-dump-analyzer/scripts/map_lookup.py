#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MAP file address lookup for ARM/Thumb embedded systems.
Decodes PC/LR/SP addresses from crash dumps to function names and source locations.

This script is now a thin CLI wrapper around common.parse_map_file / common.lookup_address.
All parsing and search logic lives in common.py.

Usage:
    python map_lookup.py <map_file> --pc <hex> [--lr <hex>] [--sp <hex>]
    python map_lookup.py <map_file> --addr <hex>

Example:
    python map_lookup.py firmware.map --pc 0x7e880040 --lr 0x7e6f7453 --sp 0x7eca86d8
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import parse_map_file, lookup_address


def format_result(addr_name, target_addr, label=""):
    """Format a lookup result for display."""
    if not addr_name:
        return "  {}: 0x{:08x} -> NOT FOUND".format(label, target_addr)

    addr, name, size, section, is_thumb = addr_name
    code_addr = addr & ~1
    offset = target_addr - code_addr
    thumb_tag = " (Thumb)" if is_thumb else ""

    result = []
    result.append("  {}: 0x{:08x}".format(label, target_addr))
    result.append("    Function: {} (0x{:x}, {} bytes{})".format(
        name, addr, size, thumb_tag))
    result.append("    Section:  {}".format(section))
    result.append("    Offset:   +{} bytes (0x{:x})".format(offset, offset))

    if is_thumb:
        result.append("    Note:     Thumb mode, instruction at +{} bytes from function start".format(
            offset))

    return "\n".join(result)


def main():
    parser = argparse.ArgumentParser(
        description='MAP file address decoder for crash dump analysis')
    parser.add_argument('map_file', help='Path to MAP file')
    parser.add_argument('--pc', help='PC (Program Counter) address (hex)')
    parser.add_argument('--lr', help='LR (Link Register) address (hex)')
    parser.add_argument('--sp', help='SP (Stack Pointer) address (hex)')
    parser.add_argument('--addr', help='Generic address to look up (hex)')
    parser.add_argument('--call-stack', nargs='+',
                        help='Stack addresses for call trace (hex)')

    args = parser.parse_args()

    if not any([args.pc, args.lr, args.sp, args.addr, args.call_stack]):
        print("Error: Provide at least one address (--pc, --lr, --sp, --addr, --call-stack)")
        parser.print_help()
        sys.exit(1)

    print("Parsing MAP file: {}".format(args.map_file))
    entries, code_addrs = parse_map_file(args.map_file)
    print("Loaded {} symbols\n".format(len(entries)))

    if args.addr:
        addr = int(args.addr, 16)
        result = lookup_address(entries, addr, code_addrs)
        print(format_result(result, addr, "ADDR"))
        print()

    if args.pc:
        pc = int(args.pc, 16)
        result = lookup_address(entries, pc, code_addrs)
        print(format_result(result, pc, "PC"))
        if result:
            _, name, _, _, _ = result
            print("    => Crash location: {}()".format(name))
        print()

    if args.lr:
        lr = int(args.lr, 16)
        result = lookup_address(entries, lr, code_addrs)
        print(format_result(result, lr, "LR"))
        if result:
            _, name, _, _, _ = result
            print("    => Caller: {}()".format(name))
        print()

    if args.sp:
        sp = int(args.sp, 16)
        result = lookup_address(entries, sp, code_addrs)
        print(format_result(result, sp, "SP"))
        if not result:
            print("    => SP points to stack/data memory, not a code section")
        print()

    if args.call_stack:
        print("Call Stack Trace:")
        for i, addr_str in enumerate(args.call_stack):
            addr = int(addr_str, 16)
            result = lookup_address(entries, addr, code_addrs)
            if result:
                _, name, size, section, _ = result
                code_addr = result[0] & ~1
                offset = addr - code_addr
                print("  #{}: 0x{:08x} -> {}()+0x{:x}  ({})".format(
                    i, addr, name, offset, section))
            else:
                print("  #{}: 0x{:08x} -> <unknown>".format(i, addr))
        print()


if __name__ == '__main__':
    main()
