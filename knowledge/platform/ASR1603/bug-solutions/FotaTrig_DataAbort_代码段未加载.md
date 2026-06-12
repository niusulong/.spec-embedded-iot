# Crash Dump 分析报告 — FotaTrig 任务 DataAbort (代码段未加载)

## 0. 结构化摘要

| **字段** | 值 |
|----------|-----|
| **平台** | ASR1603 |
| **模块** | FOTA / 启动加载 |
| **问题分类** | 代码段未加载(DataAbort) |
| **症状关键词** | DataAbort, FotaTrig死机, BIST残留, PSRAM未加载 |
| **根因概述** | `NON_OTA_CODE_IN_PSRAM` 段未从Flash搬运到PSRAM，CPU执行BIST内存测试残留数据(0xAA/0x55)触发Permission fault，FAULT_ADDRESS=0x4 |
| **调用链摘要** | `fota_trigger_worker_thread()` (PL_CODE_IN_PSRAM,已加载) → `nwy_fota_get_reboot_count()` (NON_OTA_CODE_IN_PSRAM,未加载) → BIST残留 → DataAbort |
| **检索关键词** | DataAbort, FotaTrig, NON_OTA_CODE_IN_PSRAM, BIST, 代码段未加载, nwy_fota_get_reboot_count, scatter |

---

## 基本信息

| 项目 | 内容 |
|------|------|
| 问题描述 | FotaTrig 线程调用 nwy_fota_get_reboot_count 时触发 DataAbort，根因为 NON_OTA_CODE_IN_PSRAM 段未加载 |
| 平台 | ASR1603 Cortex-R5 |
| 复现条件 | FOTA 触发流程中调用 nwy_fota_get_reboot_count |
| dump 文件 | .spec\\logs\\dump_log1 |

## 版本校验

| 来源 | 版本 |
|------|------|
| EE_Hbuf DBversionID | dbID=0x00007b29 |
| com_CustVer.bin | SDK_1.011.124 May 9 2026 11:54:09 |
| AXF BuildVersion | N/A |
| 版本匹配 | **匹配** |

## 寄存器 Dump

`
PC   = 0x7E880040  LR   = 0x7E6F7453  SP   = 0x7ECA86D8
CPSR = 0x20000133
R0   = 0x000000AA  R1   = 0x7E9CD7B8  R2   = 0x06800000  R3   = 0x00000006
R4   = 0x00000000  R5   = 0x00000000  R6   = 0x00000006  R7   = 0x7ECA89A0
`

## EE 类型与异常信息

| 项目 | 值 | 含义 |
|------|-----|------|
| EE 类型 | 0x1C2 (450) | EXCEPTION |
| 异常类型 | DataAbort | 数据访问异常 |
| FAULT_STATUS | 0x0000080D | FSC=0x0D (Permission fault MPU), WnR=1 (写操作) |
| FAULT_ADDRESS | 0x00000004 | 近 NULL 地址偏移 4 |
| ISR 上下文 | 否 | task_name=FotaTrig |
| 崩溃任务 | FotaTrig | |
| 栈范围 | 0x7ECA7F10..0x7ECA870B | 2044 bytes |

### Debug Prints
- DataAbort[AT 7E880040],NULL

## AXF vs DDR 代码完整性检查

| 项目 | 结果 |
|------|------|
| AXF 字节 | 00 90 01 90 68 46 FF F7 B5 FC 00 28 01 DD 01 98 (正确代码) |
| DDR 字节 | 80 0A 82 28 AA A2 8A 28 8A AA AA AA AA 88 A8 8A (BIST残留) |
| 一致性 | 2339/855356 字节一致 (0.3%), **99.7% 不一致** |
| 损坏类型 | **BIST_CHECKERBOARD** — 段从未加载，非运行时损坏 |

### 段归属分析

| 地址 | 所属段 | 段范围 | 状态 |
|------|--------|--------|------|
| PC 0x7E880040 | **NON_OTA_CODE_IN_PSRAM** | 0x7E815000..0x7E950D7C (1263 KB) | **未加载 (BIST checkerboard)** |
| LR 0x7E6F7453 | PL_CODE_IN_PSRAM | 0x7E5ED000..0x7E7CDA8C | 已加载(正常运行) |

### 关键对比

| 对比位置 | 一致性 | 主导字节 |
|----------|--------|----------|
| 段首 0x7E815000 | 64/64 不一致 (100%) | 0xAA/0x55 BIST残留 |
| PC 0x7E880040 | 64/64 不一致 (100%) | 0xAA/0x55 BIST残留 |
| 段尾 0x7E950D3C | 64/64 不一致 (100%) | 0xAA/0x55 BIST残留 |

**结论：整段 NON_OTA_CODE_IN_PSRAM 全部为 BIST checkerboard (0x55占53.4%, 0xAA占7.6%)，段首到段尾100%不一致，代码从未被加载到 PSRAM。**

## 地址解析

| 地址 | 函数 | 偏移 | 来源文件 |
|------|------|------|---------|
| PC: 0x7E880040 | nwy_fota_get_reboot_count | +0x4 | nwy_at_fota.c (NON_OTA_CODE_IN_PSRAM) |
| LR: 0x7E6F7453 | fota_trigger_worker_thread | +0x1FF | download.c (PL_CODE_IN_PSRAM) |

## 栈分析

| 项目 | 值 |
|------|-----|
| 栈大小 | 2044 bytes |
| 峰值使用 | 1084 bytes (53.0%) |
| 栈底 0xEF | 完整(960 bytes 未使用填充保留) |
| 栈溢出判定 | **排除** |

### 全线程扫描结果

| 项目 | 值 |
|------|-----|
| 扫描线程数 | 153 |
| 栈溢出线程 | **无** |
| 高使用率线程 (>90%) | 17个 (ATChanT 97%, afshTask 95%, abshTask 95%等) |

### 调用链(栈回溯)

`
_tx_thread_shell_entry()
  +-> fota_trigger_worker_thread()   download.c  (LR 0x7E6F744B)
       +-> diagPrintf_Extend()       (0x7E6BAC03)
       +-> malloc_shell()            (0x7E6BC73D x2)
       +-> lfs_file_sync()           (0x7E72605F)
       |   +-> lfs_dir_update()      (0x7E724D83)
       |       +-> spi_nor_do_read() (0x7E6E0E1B)
       +-> lfs_dir_commit()          (0x7E72538D)
       +-> nwy_fota_get_reboot_count()  nwy_at_fota.c  (PC 0x7E880040) <-- CRASH
`

## 堆完整性检查

| 项目 | 结果 |
|------|------|
| 发现 TX_BYTE_POOL 数量 | 0 |
| 堆状态 | 未找到标准堆结构(可能使用其他内存管理) |

## 崩溃指令反汇编

> **注意：PSRAM 代码未加载，CPU 执行了 BIST 残留数据而非编译代码。以下为 AXF 中的正确指令(非 CPU 实际执行的)。**

AXF 中 nwy_fota_get_reboot_count 函数反汇编(正确代码)：

`sm
0x7e88003c:  push {r2, r3, r4, lr}
0x7e88003e:  movs r0, #0
0x7e880040:  str r0, [sp, #0]          <-- PC 崩溃点(AXF正确指令)
0x7e880042:  str r0, [sp, #4]
0x7e880044:  mov r0, sp
0x7e880046:  bl 0x7e87f9b4             (nwy_fota_read_nv)
0x7e88004a:  cmp r0, #0
0x7e88004c:  ble 0x7e880052
0x7e88004e:  ldr r0, [sp, #4]
0x7e880050:  pop {r2, r3, r4, pc}
0x7e880052:  movs r0, #0
0x7e880054:  pop {r2, r3, r4, pc}
`

CPU 在 DDR 中实际执行的是 BIST 残留数据: 80 0A 82 28 AA A2 ...，这被 CPU 解码为非法/非预期指令，最终触发 DataAbort (Permission fault, FAULT_ADDRESS=0x4)。

## 调用链

`
fota_trigger_worker_thread()   download.c   (PL_CODE_IN_PSRAM, 已加载)
  +-> nwy_fota_get_reboot_count()  nwy_at_fota.c  (NON_OTA_CODE_IN_PSRAM, 未加载!)
       +-> CPU 执行 BIST 残留 -> DataAbort (FAULT_ADDRESS=0x4)
`

**跨段调用**：LR 在 PL_CODE_IN_PSRAM(已加载)，PC 在 NON_OTA_CODE_IN_PSRAM(未加载)。

## 根因分析

### 根因

**NON_OTA_CODE_IN_PSRAM 代码段未被加载到 PSRAM，导致 fota_trigger_worker_thread 跨段调用 nwy_fota_get_reboot_count 时 CPU 执行了 BIST 内存测试残留数据，触发 DataAbort。**

### 证据链

1. **代码完整性检查**：NON_OTA_CODE_IN_PSRAM 段在 DDR 中 99.7% 与 AXF 不一致，整段为 BIST checkerboard 模式 (0x55占53.4%, 0xAA占7.6%)，段首到段尾100%不一致 -> 代码从未被加载
2. **跨段调用**：LR 在 PL_CODE_IN_PSRAM(已加载，正常运行)，PC 在 NON_OTA_CODE_IN_PSRAM(未加载) -> 调用跨越已加载/未加载段边界
3. **FAULT_ADDRESS=0x4**：CPU 执行 BIST 残留数据后被错误解码，触发 Permission fault 写操作到近 NULL 地址
4. **栈分析排除溢出**：FotaTrig 栈使用率仅 53%，栈底 0xEF 完整，全 153 线程无栈溢出
5. **MAP 文件确认**：LoadNON_OTA_CODE_IN_PSRAMBase = 0x8063F7D0(Flash 加载地址)，Execution Region 0x7E815000..0x7E950D7C -> 该段有独立的加载地址和执行地址，需启动时从 Flash 搬运到 PSRAM，但搬运未执行
6. **DDR 搜索**：在 DDR 中找到 NON_OTA 字符串(VA=0x7E6F57A4)，附近有 RW_CPZ_ 和 reserved 标记，但未找到段加载配置关键词

### 损坏类型判定

| 条件 | 结果 |
|------|------|
| DDR 为 BIST checkerboard? | Yes (0x55占53.4%, 0xAA占7.6%) |
| 不一致范围对齐段边界? | Yes (段首到段尾100%不一致) |
| LR 在已加载段、PC 在未加载段? | Yes (跨段调用确认) |
| **判定** | **代码段未加载(非运行时损坏)** |

## 下一步行动

- [ ] 排查 scatter/linker script 中 NON_OTA_CODE_IN_PSRAM 段的加载条件，确认什么条件下该段会被/不会被搬运到 PSRAM
- [ ] 检查启动代码中段加载逻辑(如 boot loader 的段搬运流程)，确认 NON_OTA_CODE_IN_PSRAM 是否有条件加载(如仅 OTA 模式加载)
- [ ] 确认当前固件是否运行在非 OTA 模式，若是，则 nwy_at_fota.c 中的函数不应被调用或应有运行时保护
- [ ] 考虑在 fota_trigger_worker_thread 中增加 NON_OTA_CODE_IN_PSRAM 段加载状态检查，避免在段未加载时调用其中的函数
- [ ] 检查 PL_CODE_IN_PSRAM 段中的 fota_trigger_worker_thread 为何会调用 NON_OTA_CODE_IN_PSRAM 段中的函数，是否编译配置有误

## 相关文件

- AXF: .spec/logs/dump_log1/com_EE_Hbuf.axf
- DDR dump: .spec/logs/dump_log1/com_DDR_RW.bin
- MAP: .spec/logs/dump_log1/com_EE_Hbuf.map
- EE_Hbuf: .spec/logs/dump_log1/com_EE_Hbuf.bin
- CustVer: .spec/logs/dump_log1/com_CustVer.bin