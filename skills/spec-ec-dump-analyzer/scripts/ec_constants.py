#!/usr/bin/env python3
"""EC 平台共享常量和工具函数。

本文件是整个 dump 分析工具的"协议字典"。所有结构体偏移、魔数标志、
复位原因枚举均来源于 EC 平台固件头文件（mem_map.h, ec_port.h,
mm_debug.h, heap_6.c），修改任何值都可能导致分析结果完全错误。

常量按来源分组，每组标注出处头文件和适用范围：
  - universal = 所有 EC 芯片通用（EC626/EC626E/EC616）
  - fallback  = 仅 EC626 默认值，无 MAP 时回退使用

自检：import 本模块时会自动运行 _self_check()，验证常量之间的
推导一致性（如 STORE_SIZE 必须等于各偏移累加）。如果自检失败，
会抛出 AssertionError 并列出具体错误。
"""

import struct

# ═══════════════════════════════════════════════════════════════════════════
# ── 1. RAM_END 固定偏移（universal, 来源: mem_map.h）────────────────
# EC_xxx_ADDR = EC_RAM_END_ADDR - offset，所有 EC 芯片共用偏移
# ═══════════════════════════════════════════════════════════════════════════
RAM_END_OFF_RESET_REASON = 0x24   # ResetReason_e
RAM_END_OFF_ASSERT_PC    = 0x20   # ASSERT 时的 PC 值
RAM_END_OFF_ASSERT_LR    = 0x1C   # ASSERT 时的 LR 值
RAM_END_OFF_MAGIC        = 0x18   # excep_store 有效性标记
RAM_END_OFF_STORE_PTR    = 0x10   # excep_store 间接指针

# ═══════════════════════════════════════════════════════════════════════════
# ── 2. 异常标志魔数（universal, 来源: ec_port.h / excep_store 定义）──
# 这些值在固件初始化时写入 excep_store 头部，用于区分异常类型
# ═══════════════════════════════════════════════════════════════════════════
EC_EXCEP_START_FLAG       = 0xA2A0A1A3   # ASSERT / WDT / FS Assert 路径
EC_HARDFAULT_START_FLAG   = 0xF2F0F1F3   # HardFault 路径
EC_EXCEP_ASSERT_FLAG      = 0xB2B0B1B3
EC_EXCEP_END_FLAG         = 0xA0A1A2A9   # ASSERT end
EC_HARDFAULT_END_FLAG     = 0xF0F1F2F9   # HardFault end
EC_HARDFAULT_FLAG         = 0xE2E0E1E3
EC_EXCEP_MAGIC_NUMBER     = 0x00EC00EC
STACK_FILL_PATTERN        = 0xA5A5A5A5   # FreeRTOS 栈填充标记

# ═══════════════════════════════════════════════════════════════════════════
# ── 3. 复位原因枚举（universal, 来源: ResetReason_e in ec_port.h）────
# ═══════════════════════════════════════════════════════════════════════════
RESET_REASON_HARDFAULT    = 1
RESET_REASON_ASSERT       = 2
RESET_REASON_WDT          = 3
RESET_REASON_FOTA         = 4
RESET_REASON_FLHDRV       = 5
RESET_REASON_XICOVL       = 6
RESET_REASON_BAT          = 7
RESET_REASON_TEMP         = 8

# ═══════════════════════════════════════════════════════════════════════════
# ── 4. FS Assert 魔数（universal, 来源: littlefs fs_assert 触发）─────
# R1/R2/R3 寄存器值匹配这三项时判定为 FS Assert
# ═══════════════════════════════════════════════════════════════════════════
FS_ASSERT_MAGIC0 = 0x46535F41   # "FS_A"
FS_ASSERT_MAGIC1 = 0x73736572   # "sser"
FS_ASSERT_MAGIC2 = 0x745F7346   # "t_sF"

# ═══════════════════════════════════════════════════════════════════════════
# ── 5. EC626 默认值（fallback, 无 MAP 文件时使用）────────────────────
# 注意: EC626E/EC616 的 RAM_END 和 Flash 范围不同，有 MAP 时会自动推导
# ═══════════════════════════════════════════════════════════════════════════
DEFAULT_RAM_END     = 0x44000
DEFAULT_FLASH_START = 0x0081F000
DEFAULT_FLASH_END   = 0x00A00000

# ═══════════════════════════════════════════════════════════════════════════
# ── 6. ec_m3_exception_regs 结构体布局（universal, 来源: ec_port.h）──
#       offset  0: header (16 bytes: 4 x uint32 flags)
#       offset 16: stack_frame (R0..PC + PSR + EXC_RETURN + MSP/PSP + CONTROL/BASEPRI/PRIMASK/FAULTMASK)
#       offset 16+96: fault registers (SYS_CTRL + MFSR/BFSR/UFSR + HFSR + DFSR + MMFAR + BFAR + AFSR)
#       offset 16+96+28: func_call[4] + task_name[12] + curr_time + fw_info + end_flag
# ═══════════════════════════════════════════════════════════════════════════
HEADER_SIZE = 16

# stack_frame 寄存器偏移（相对于 stack_frame 起始）
SF_R0         = 0x00
SF_R1         = 0x04
SF_R2         = 0x08
SF_R3         = 0x0C
SF_R4         = 0x10
SF_R5         = 0x14
SF_R6         = 0x18
SF_R7         = 0x1C
SF_R8         = 0x20
SF_R9         = 0x24
SF_R10        = 0x28
SF_R11        = 0x2C
SF_R12        = 0x30
SF_SP         = 0x34
SF_LR         = 0x38
SF_PC         = 0x3C
SF_PSR        = 0x40
SF_EXC_RETURN = 0x44
SF_MSP        = 0x48
SF_PSP        = 0x4C
SF_CONTROL    = 0x50
SF_BASEPRI    = 0x54
SF_PRIMASK    = 0x58
SF_FAULTMASK  = 0x5C

SF_SIZE = 24 * 4  # 96 bytes (R0..PC=16 + xPSR..FAULTMASK=8)

# Fault 寄存器偏移（相对于 stack_frame 起始，紧跟在 SF_SIZE 之后）
OFF_SYS_CTRL  = SF_SIZE + 0    # uint32
OFF_MFSR      = SF_SIZE + 4    # uint8
OFF_BFSR      = SF_SIZE + 5    # uint8
OFF_UFSR      = SF_SIZE + 6    # uint16
OFF_HFSR      = SF_SIZE + 8    # uint32
OFF_DFSR      = SF_SIZE + 12   # uint32
OFF_MMFAR     = SF_SIZE + 16   # uint32
OFF_BFAR      = SF_SIZE + 20   # uint32
OFF_AFSR      = SF_SIZE + 24   # uint32
REGS_SIZE     = SF_SIZE + 28   # ec_m3_exception_regs 总大小

# excep_store 后续字段偏移（相对于 excep_store 起始地址）
OFF_FUNC_CALL  = HEADER_SIZE + REGS_SIZE           # 4 x uint32 调用链
OFF_TASK_NAME  = OFF_FUNC_CALL + 4 * 4             # 12 bytes 任务名
OFF_CURR_TIME  = OFF_TASK_NAME + 12                # uint32
OFF_FW_INFO    = OFF_CURR_TIME + 4                 # uint32
OFF_END_FLAG   = OFF_FW_INFO + 4                   # uint32
STORE_SIZE     = OFF_END_FLAG + 4                   # ec_exception_store 总大小

# ═══════════════════════════════════════════════════════════════════════════
# ── 7. mm_trace_node 结构体布局（来源: mm_debug.c）───────────────────
# 每个节点 24 字节，记录一次未释放的 pvPortMalloc 分配
#
# 已知布局版本：
#   layout_v1 (DWARF 标准): memptr@0, funptr@4, length@8, task[8]@12, next@20
#   layout_v2 (部分固件):   ???@0, next@4, memptr@8, funptr@12, length@16, task[4]@20
#
# scan_trace_node() 会自动检测实际布局版本。
# ═══════════════════════════════════════════════════════════════════════════
MM_TRACE_NODE_SIZE = 24

# Layout V1 (DWARF 标准，来源于 ELF 调试信息)
MM_TRACE_V1_MEMPTR  = 0     # void* malloc 返回地址
MM_TRACE_V1_FUNPTR  = 4     # uint32 调用者 LR
MM_TRACE_V1_LENGTH  = 8     # uint32 分配大小
MM_TRACE_V1_TASK    = 12    # char[8] 任务名
MM_TRACE_V1_NEXT    = 20    # ptr hash 链表指针
MM_TRACE_V1_TASK_LEN = 8

# Layout V2 (部分 EC 固件实测，funptr@12 且 task@20 仅 4 字节)
MM_TRACE_V2_MEMPTR  = 8     # void* malloc 返回地址
MM_TRACE_V2_FUNPTR  = 12    # uint32 调用者 LR
MM_TRACE_V2_LENGTH  = 16    # uint32 分配大小
MM_TRACE_V2_TASK    = 20    # char[4] 任务名（仅前 4 字符）
MM_TRACE_V2_NEXT    = 4     # ptr hash 链表指针
MM_TRACE_V2_TASK_LEN = 4

# 向后兼容别名（默认 V1）
MM_TRACE_NODE_MEMPTR  = MM_TRACE_V1_MEMPTR
MM_TRACE_NODE_FUNPTR  = MM_TRACE_V1_FUNPTR
MM_TRACE_NODE_LENGTH  = MM_TRACE_V1_LENGTH
MM_TRACE_NODE_TASK    = MM_TRACE_V1_TASK
MM_TRACE_NODE_NEXT    = MM_TRACE_V1_NEXT

# ═══════════════════════════════════════════════════════════════════════════
# ── 8. TLSF 块头布局（来源: heap_6.c, MM_DEBUG_EN 启用）─────────────
# ═══════════════════════════════════════════════════════════════════════════
TLSF_FREE_BIT = 1
TLSF_PREV_FREE_BIT = 2
TLSF_HEAD_BOUNDARY = 0xbeafdead
TLSF_TAIL_BOUNDARY = 0xdeadbeaf
TLSF_BLOCK_START_OFFSET = 16  # prev_phys(4) + head_bound(4) + alloc_owner(4) + size(4)
TLSF_HEADER_OVERHEAD = 12


def u32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]

def u16(data, offset):
    return struct.unpack_from('<H', data, offset)[0]

def u8(data, offset):
    return struct.unpack_from('<B', data, offset)[0]


# ═══════════════════════════════════════════════════════════════════════════
# ── 9. PMUD dump 文件头部检测与剥离（来源: RamDumpData_*.bin 文件格式）──
# EC626/EC626E/EC616 的 RAM dump 工具在 dump 前添加 PMUD 头部（魔数
# "PMUD"，通常 0x48=72 字节），包含时间戳、芯片标识、RAM 大小等元数据。
# 所有脚本函数期望 dump_data 的文件偏移 == RAM 地址，因此必须在加载后
# 调用 strip_pmud_header() 剥离头部。
# ═══════════════════════════════════════════════════════════════════════════
PMUD_MAGIC = b'PMUD'
PMUD_RAM_SIZE_OFFSET = 0x28  # uint32, little-endian, RAM 字节大小


def strip_pmud_header(dump_data):
    """检测并剥离 PMUD dump 文件头部。

    如果 dump 以 "PMUD" 魔数开头，从 offset 0x28 读取 RAM 大小，
    计算 header_size = len(dump) - ram_size，然后剥离头部返回纯 RAM 数据。
    如果不是 PMUD 格式（裸 dump），原样返回。

    Returns:
        (ram_data, header_size): 剥离头部后的 bytes（file_offset == RAM_addr），
                                  以及头部大小（0 表示裸 dump）。
    """
    if len(dump_data) < 0x30:
        return dump_data, 0

    # 检测 PMUD 魔数
    if dump_data[:4] != PMUD_MAGIC:
        return dump_data, 0

    # 读取 RAM 大小字段
    ram_size = u32(dump_data, PMUD_RAM_SIZE_OFFSET)

    # 验证合理性
    if ram_size == 0 or ram_size >= len(dump_data):
        return dump_data, 0

    header_size = len(dump_data) - ram_size
    if header_size <= 0 or header_size > 4096:
        return dump_data, 0

    return dump_data[header_size:], header_size


# ═══════════════════════════════════════════════════════════════════════════
# ── 运行时自检（import 时自动执行）──────────────────────────────────────
# 验证常量推导一致性，防止意外修改导致分析结果错误
# ═══════════════════════════════════════════════════════════════════════════
def _self_check():
    """验证常量推导一致性，失败时抛出 AssertionError。"""
    errors = []

    # ec_m3_exception_regs 结构体完整性
    if SF_SIZE != 24 * 4:
        errors.append(f"SF_SIZE={SF_SIZE} != expected {24*4}")
    if REGS_SIZE != SF_SIZE + 28:
        errors.append(f"REGS_SIZE={REGS_SIZE} != SF_SIZE+28={SF_SIZE+28}")

    # excep_store 偏移链推导验证
    _regs_end  = HEADER_SIZE + REGS_SIZE
    _func_call = _regs_end
    _task_name = _func_call + 4 * 4
    _curr_time = _task_name + 12
    _fw_info   = _curr_time + 4
    _end_flag  = _fw_info + 4
    _store_sz  = _end_flag + 4

    if OFF_FUNC_CALL != _func_call:
        errors.append(f"OFF_FUNC_CALL={OFF_FUNC_CALL:#x} != derived {_func_call:#x}")
    if OFF_TASK_NAME != _task_name:
        errors.append(f"OFF_TASK_NAME={OFF_TASK_NAME:#x} != derived {_task_name:#x}")
    if OFF_CURR_TIME != _curr_time:
        errors.append(f"OFF_CURR_TIME={OFF_CURR_TIME:#x} != derived {_curr_time:#x}")
    if OFF_FW_INFO != _fw_info:
        errors.append(f"OFF_FW_INFO={OFF_FW_INFO:#x} != derived {_fw_info:#x}")
    if OFF_END_FLAG != _end_flag:
        errors.append(f"OFF_END_FLAG={OFF_END_FLAG:#x} != derived {_end_flag:#x}")
    if STORE_SIZE != _store_sz:
        errors.append(f"STORE_SIZE={STORE_SIZE:#x} != derived {_store_sz:#x}")

    # trace_node 布局完整性
    if MM_TRACE_NODE_SIZE != 24:
        errors.append(f"MM_TRACE_NODE_SIZE={MM_TRACE_NODE_SIZE} != 24")
    if MM_TRACE_NODE_NEXT != MM_TRACE_NODE_TASK + 8:
        errors.append(f"MM_TRACE_NODE_NEXT={MM_TRACE_NODE_NEXT} != TASK+8={MM_TRACE_NODE_TASK+8}")

    # TLSF 布局
    if TLSF_BLOCK_START_OFFSET != 16:
        errors.append(f"TLSF_BLOCK_START_OFFSET={TLSF_BLOCK_START_OFFSET} != 16")

    # RAM_END 偏移单调递减（store_ptr < magic < assert_lr < assert_pc < reset_reason）
    offsets = [RAM_END_OFF_STORE_PTR, RAM_END_OFF_MAGIC,
               RAM_END_OFF_ASSERT_LR, RAM_END_OFF_ASSERT_PC,
               RAM_END_OFF_RESET_REASON]
    for i in range(len(offsets) - 1):
        if offsets[i] >= offsets[i + 1]:
            errors.append(f"RAM_END offsets not monotonically decreasing: {offsets}")
            break

    if errors:
        msg = "ec_constants self-check FAILED:\n" + "\n".join(f"  - {e}" for e in errors)
        raise AssertionError(msg)


_self_check()
