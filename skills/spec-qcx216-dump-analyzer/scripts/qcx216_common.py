#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QCX216 dump 通用读写工具。

QCX216 的 RamDumpData_*.bin 是从物理地址 0x0 开始的统一地址空间转储
（Flash 代码 + RAM 拼接），已用真实 dump 验证：偏移 == 物理地址。
因此 DumpReader 直接按「地址」读写，base 默认 0x0。
"""
import struct


def u32(data: bytes, off: int):
    if off < 0 or off + 4 > len(data):
        return None
    return struct.unpack_from("<I", data, off)[0]


def u16(data: bytes, off: int):
    if off < 0 or off + 2 > len(data):
        return None
    return struct.unpack_from("<H", data, off)[0]


def u8(data: bytes, off: int):
    if off < 0 or off + 1 > len(data):
        return None
    return data[off]


def to_ascii(b: bytes) -> str:
    return "".join(chr(x) if 32 <= x < 127 else "." for x in b)


class DumpReader:
    """按物理地址访问 dump（base=0x0 时偏移==地址）。"""

    def __init__(self, path: str, base: int = 0x0):
        with open(path, "rb") as f:
            self.data = f.read()
        self.base = base
        self.size = len(self.data)

    def _off(self, addr: int) -> int:
        off = addr - self.base
        return off

    def u32(self, addr: int):
        return u32(self.data, self._off(addr))

    def u16(self, addr: int):
        return u16(self.data, self._off(addr))

    def u8(self, addr: int):
        return u8(self.data, self._off(addr))

    def read(self, addr: int, size: int) -> bytes:
        off = self._off(addr)
        if off < 0:
            return b""
        return self.data[off:off + size]

    def covers(self, addr: int, size: int = 4) -> bool:
        off = self._off(addr)
        return 0 <= off and off + size <= self.size


def make_mem(dump, elf=None):
    """构造 ThumbDisasm 用的 mem(addr)->u8 函数：优先 dump，超 dump 范围回退 ELF section。

    dump（RamDumpData）只含 0x0~0x540000（向量表+部分Flash+RAM）；协议栈代码常在
    0x8Cxxxx~0x9Axxxx 段（超 dump），此时需从崩溃固件 ELF 的 section 读字节才能反汇编
    （如 cmsTaskEntry@0x8C227E）。传 elf=None 则超范围返回 None（与原 dump.u8 行为一致）。
    """
    def _mem(addr):
        v = dump.u8(addr)
        if v is not None:
            return v
        return elf.read_u8(addr) if elf is not None else None
    return _mem


# Cortex-M 异常存储 magic（已从 RamDumpData_20260629_103016.bin 逆向）
EXCEP_MAGIC1 = 0xEC112013          # excepInfoStore[0]
EXCEP_MAGIC2 = 0xAA010129          # excepInfoStore[1]，0xAA 前缀辅助判别

# FreeRTOS TCB 字段偏移（Cortex-M 移植，已用 IDLE 任务 TCB 验证）
TCB_OFF_TOP_OF_STACK = 0x00        # pxTopOfStack
TCB_OFF_STACK_BASE = 0x30          # pxStack（栈底）
TCB_OFF_TASK_NAME = 0x34           # pcTaskName
TCB_NAME_MAX = 16                  # 常见 configMAX_TASK_NAME_LEN

# FreeRTOS 任务栈填充魔法字（栈底哨兵）
STACK_FILL_MAGIC = 0xA5A5A5A5      # vPortInitialiseStack 用 0xa5 填充
