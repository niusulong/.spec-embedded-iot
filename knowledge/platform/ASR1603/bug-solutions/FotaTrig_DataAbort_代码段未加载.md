# Crash Dump 分析报告 — FotaTrig 任务 DataAbort (NON_OTA 段未加载，代码段设计矛盾)

> **文档版本**: v4 (2026-06-23 更新，对齐《FOTA 循环测试死机 — 最终分析解决报告》v3)
> **状态**: **✅ 已验证通过**（必现复现 + 修复后不再复现）
> **原始分析**: 2026-05-27（v3 最终版）

## 0. 结构化摘要

| **字段** | 值 |
|----------|-----|
| **工作项 ID** | NA |
| **平台** | ASR1603 (ARM Cortex-R + ThreadX) |
| **模块** | FOTA / 启动加载 / scatter 段放置 |
| **问题分类** | 代码段设计矛盾（重试逻辑在 mini system 运行，却调用了 mini system 下不加载的段） |
| **症状关键词** | DataAbort, FotaTrig死机, FOTA循环测试死机, mini.sys.enable, NON_OTA段未加载, BIST残留, PSRAM未加载, Permission fault |
| **根因概述** | FOTA 重试机制的 NV 读写函数（`nwy_fota_get/set_reboot_count`）被 scatter 通配符 `*nota-nota.lib (+RO)` 归入 `NON_OTA_CODE_IN_PSRAM` 段，但 mini system 模式（`mini.sys.enable=1`）启动时该段被跳过不加载，PSRAM 该区域保留硬件 BIST checkerboard 填充(0xAA/0x55)；而重试逻辑**仅在** mini system 模式下运行 —— 造成"必须执行的代码位于无法加载的段"这一设计矛盾。CPU 跨段调用时执行 BIST 残留数据 `movs r0,#0xAA → strh r2,[r5,#4]`(R5=0)，访问地址 0x4 → MPU Permission fault → DataAbort 死机。 |
| **调用链摘要** | `fota_trigger_worker_thread()` (PL_CODE_IN_PSRAM, 已加载) → `nwy_fota_get_reboot_count()` (NON_OTA_CODE_IN_PSRAM, mini.sys.enable=1 时未加载) → PSRAM 保留 BIST checkerboard → `movs r0,#0xAA; strh r2,[r5,#4]`(R5=0) → 访问 0x4 → Permission fault → DataAbort |
| **检索关键词** | DataAbort, FotaTrig, FOTA循环测试死机, NON_OTA_CODE_IN_PSRAM, mini.sys.enable, mini system, BIST checkerboard, 0xAA/0x55, 代码段未加载, nwy_fota_get_reboot_count, scatter, *nota-nota.lib, Permission fault, 段放置矛盾, PL_CODE_IN_PSRAM |

---

## 1. 问题描述

| 信息 | 值 |
|------|-----|
| 故障现象 | FOTA 循环测试 140+ 次后死机 |
| 异常类型 | DataAbort |
| PC | 0x7E880040 |
| FAULT_ADDRESS | 0x00000004 |
| FAULT_STATUS | 0x0000080D (FSC=0x0D Permission fault, WnR=1 写操作) |
| 崩溃任务 | FotaTrig |
| 触发概率 | 140+ 次循环后出现，但一旦进入失败路径则 **100% 必现** |
| 复现方法 | mini system 模式下拔 SIM 卡 → 60 秒后必现（见 §7 方法 A） |

dump 文件齐全：`com_EE_Hbuf.bin`（异常头）、`com_DDR_RW.bin`（PSRAM/DDR 转储）、`*.map`、`*.axf`、`com_CustVer.bin`。

## 2. 根本原因

### 2.1 一句话描述

**FOTA 重试机制的 NV 读写函数（`nwy_fota_get/set_reboot_count`）被链接器放入 `NON_OTA_CODE_IN_PSRAM` 段，但 mini system 模式启动时该段被跳过不加载；重试逻辑仅在 mini system 模式下运行，造成"必须执行的代码位于无法加载的段"这一设计矛盾。**

### 2.2 崩溃链

```
FOTA 下载失败（网络超时/下载停滞）
  → fota_trigger_worker_thread 重试逻辑触发
  → 调用 nwy_fota_get_reboot_count() (0x7E88003D)
  → 该函数位于 NON_OTA_CODE_IN_PSRAM 段
  → mini.sys.enable=1 导致该段在启动时未加载
  → PSRAM 该区域保留硬件 BIST checkerboard 填充 (0xAA/0x55)
  → CPU 执行填充数据: movs r0,#0xAA → strh r2,[r5,#4] (R5=0)
  → 访问地址 0x4 → MPU Permission fault → DataAbort → 死机
```

### 2.3 根因的三个层面

| 层面 | 问题 |
|------|------|
| **架构层面** | scatter 文件用 `*nota-nota.lib (+RO)` 通配符将整个库放入 NON_OTA，未逐模块审查 |
| **代码层面** | FOTA 重试函数放在 `nwy_at_fota.c`（属于 nota 库）而非 `download.c`（属于已加载的 PL_CODE 段） |
| **流程层面** | mini system 模式下运行的代码调用了 mini system 模式下不加载的函数，无运行时保护 |

### 2.4 mini.sys.enable=1 的设置路径

```
1. 首次 FOTA 触发（正常模式）
   → enable_mini_sys() [download.c:686-855]
   → 写 flash: mini_sys_enable=MINI_SYS_STATUS_ENABLE, mini_dfota_status=MINI_SYS_DFOTA_START
   → PM812_SW_RESET() 立即重启

2. Bootloader (ota.c:623-636)
   → 读 flash: param.mini_sys_enable==1
   → asr_property_set("mini.sys.enable", "1")
   → 提前返回，进入 mini system only 模式

3. CP 启动 (logo/main.c:944-949)
   → asr_property_get("mini.sys.enable") != 0
   → continue; // 跳过 NON_OTA_CODE_IN_PSRAM 解压加载
   → PSRAM 0x7E815000-0x7E950D7C 保留 BIST checkerboard 填充
```

## 3. 崩溃触发路径（两个场景）

**场景 A — 下载进度停滞** (`download.c:3172`):
```
1. mini system 模式启动 → fota_trigger_worker_thread 运行
2. 网络连接正常，但 FTP 服务器不可达
3. ota_download_progress 始终为 0
4. 7 次 × 4 秒 = 28 秒检测到一次停滞
5. 前 5 次停滞: retry_count++, goto trigger_again
6. 第 6 次停滞: retry_count=6 > 5
   → 调用 nwy_fota_get_reboot_count() [line 3172] → 死机
   → 总耗时: 约 168 秒
```

**场景 B — 网络不可用** (`download.c:3252`):
```
1. mini system 模式启动 → fota_trigger_worker_thread 运行
2. get_linkstas() 连续 30 次返回 0 (30 × 2 秒 = 60 秒)
3. 进入 else 分支
   → 调用 nwy_fota_get_reboot_count() [line 3252] → 死机
   → 总耗时: 约 60 秒
```

**所有受影响的调用点**（共 9 处，均在 `fota_trigger_worker_thread` 重试逻辑中）：

| 函数 | 行号 | 场景 |
|------|------|------|
| `nwy_fota_get_reboot_count()` | 3172 | 下载停滞, retry_count > 5 |
| `nwy_fota_set_reboot_count()` | 3176 | reboot_count < 3 |
| `nwy_fota_set_reboot_count()` | 3183 | reboot_count 3-6 |
| `nwy_fota_set_reboot_count()` | 3193 | reboot_count 6-10 |
| `nwy_fota_set_reboot_count()` | 3203 | reboot_count 10-20 |
| `nwy_fota_set_reboot_count()` | 3212 | reboot_count >= 20 重置 |
| `nwy_fota_get_reboot_count()` | 3252 | 网络超时 |
| `nwy_fota_set_reboot_count()` | 3254 | 网络超时递增 |
| `nwy_fota_set_reboot_count()` | 3271 | 网络超时重置 |

## 4. 函数段放置分析（根因定位）

| 符号 | 地址 | 段 | mini system 下状态 |
|------|------|----|--------------------|
| `fota_trigger_worker_thread` | 0x7E6F7255 | **PL_CODE_IN_PSRAM** | 正常加载 |
| `nwy_fota_get_reboot_count` | 0x7E88003D | **NON_OTA_CODE_IN_PSRAM** | 未加载 |
| `nwy_fota_set_reboot_count` | 0x7E87FA0B | **NON_OTA_CODE_IN_PSRAM** | 未加载 |
| `nwy_fota_read_nv` | 0x7E87F9B5 | **NON_OTA_CODE_IN_PSRAM** | 未加载 |
| `nwy_fota_write_nv` | 0x7E880783 | **NON_OTA_CODE_IN_PSRAM** | 未加载 |

**段放置根因**：scatter 文件 `Crane_DS_16M_Ram_16M_Flash_XIP_CIPSRAM_Common.sct` 中：
```
NON_OTA_CODE_IN_PSRAM (base) (size)
{
    *nota-nota.lib (+RO)      // ← 通配符将整个库放入 NON_OTA
    ...
}
```
`nwy_at_fota.o` 属于 `nota-nota.lib`，被通配符规则自动归入 NON_OTA。该文件无显式 `__attribute__((section(...)))` 注解。

## 5. 证据链

| # | 证据 | 来源 |
|---|------|------|
| 1 | DDR dump 中 `mini.sys.enable = "1"` (VA=0x7EFCB918) | com_DDR_RW.bin |
| 2 | Bootloader 设置 property: `asr_property_set("mini.sys.enable","1")` | ota.c:636 |
| 3 | CP 启动跳过 NON_OTA 加载: `asr_property_get("mini.sys.enable")!=0` → `continue` | main.c:944-949 |
| 4 | PSRAM 该区域为 checkerboard 填充 (0xAA/0x55)，不是代码 | AXF vs DDR 26 字节全部不同 |
| 5 | 破坏范围精确对齐 NON_OTA_CODE_IN_PSRAM section 边界 (0x7E815000-0x7E950D7C) | DDR dump 边界分析 |
| 6 | 0xAA/0x55 按 PSRAM bank 交替分布，过渡边界 4KB/8KB 对齐，硬件 BIST 特征 | DDR dump 统计分析 |
| 7 | `nwy_fota_get_reboot_count` 地址 0x7E88003D 在 NON_OTA 区域内 | MAP file |
| 8 | R0=0xAA 与 `movs r0,#0xAA` 指令自洽 | 寄存器 dump |
| 9 | `fota_trigger_worker_thread` 仅在 mini system 下创建，但调用的 NV 函数在 NON_OTA | download.c:6507, 3172, 3252 |
| 10 | 重试代码调用 NV 函数前无 `IsMiniSystem()` 检查 | download.c:3095-3154, 3185-3210 |

### 5.1 PSRAM 字节级对比（nwy_fota_get_reboot_count, 26 字节）

| 地址 | AXF 字节 | AXF 指令 | DDR 字节 | DDR 指令 | 匹配？ |
|------|---------|---------|---------|---------|--------|
| 0x7E88003C | `1C B5` | push {r2,r3,r4,lr} | `AA 20` | movs r0,#0xAA | 不同 |
| 0x7E88003E | `00 20` | movs r0, #0 | `AA 80` | strh r2,[r5,#4] | 不同 |
| 0x7E880040 | `00 90` | str r0, [sp, #0] | `80 0A` | lsrs r0,r0,#10 | 不同 |

26 字节全部不匹配，0xAA 占 34.7%，是 checkerboard 内存测试模式残留。

### 5.2 寄存器 Dump

```
CPSR    0x20000133    (SVC模式, Thumb, IRQ使能, FIQ使能) ← 从 SPSR_abt 复制
R0      0x000000AA    (movs r0,#0xAA 执行结果)
R5      0x00000000    (填充数据反汇编偶然副作用)
SP      0x7ECA86D8
LR      0x7E6F7453    (返回到 fota_trigger_worker_thread)
PC      0x7E880040    (已调整后的故障指令地址)
FAULT_STATUS   0x0000080D  (FSC=0x0D Permission fault, WnR=1)
FAULT_ADDRESS  0x00000004  (R5(0) + 偏移(4) = 0x4)
```

### 5.3 PMSAv7 DFSR 解码

```
DFSR = 0x0000080D = 0b 1000_0000_1101
  bit[3:0] = 1101  → FSC = 0xD
  bit[11]   = 1    → WnR = 1 (写操作)
FSC = 0x0D → Permission fault (MPU 权限错误)
```

> **关键纠错**：FSC=0x0D 在 PMSAv7 中是 Permission fault，**不是**异步外部中止（那是 VMSAv7 short-descriptor 解码错误）。参考 CMSIS `core_ca.h` (`DFSR_WnR_Pos = 11U`)、Linux kernel (`FSR_WRITE = BIT(11)`)。

### 5.4 栈分析（排除栈溢出）

```
栈范围: 0x7ECA7F10..0x7ECA870B (2044 bytes)
峰值使用: 1084/2044 (53%) — 栈溢出排除
全线程扫描: 153 线程，0xEF 水印全部完好，无栈溢出
```

## 6. 已排除的原因

| 排除项 | 证据 |
|--------|------|
| 栈溢出 | 153 线程 0xEF 水印全部完好，峰值使用 53% |
| NULL 指针 bug | 原始代码不使用 R5，R5=0 是填充数据反汇编的偶然副作用 |
| 跨周期堆碎片化 | 每次冷启动堆完全清零 |
| PSRAM 运行时覆写 | 代码从未加载不是覆写问题 |
| QSPI 总线竞争 | 0xAA/0x55 是 checkerboard 测试模式（与 `memtester.c` 中 `CHECKERBOARD1=0x55555555`/`CHECKERBOARD2=0xAAAAAAAA` 一致），不是总线错误信号；破坏范围精确对齐 section 边界 |
| 异步外部中止 | PMSAv7 中 bit[11]=WnR，FSC=0x0D 是 Permission fault |

## 7. 必现验证方法（修复前）

### 方法 A：网络不可用路径（最快，约 60 秒必现）
```
1. 正常触发一次 FOTA (AT+NEOFTPFOTA)，等系统重启进入 mini system
2. 重启后立即拔 SIM 卡（或用 AT+CFUN=0 关闭射频）
3. 等待约 60 秒 → 必现 DataAbort 死机
```

### 方法 B：下载停滞路径（约 168 秒必现）
```
1. 正常触发一次 FOTA，等系统重启进入 mini system
2. 保持网络连接，但将 FOTA URL 指向不可达的 FTP 服务器
3. 等待约 168 秒 → 必现 DataAbort 死机
```

## 8. 解决方案（已验证通过 ✅）

### 8.1 修改策略：将 NV 函数从 NON_OTA 段迁移到 PL_CODE 段

**核心思路**：在 `download.c`（编译后位于 PL_CODE_IN_PSRAM，mini system 下总是加载）中重新实现 NV 读写函数，使用 `FDI_fopen/FDI_fread/FDI_fwrite/FDI_fclose` API（位于 CrossPlatformSW → PL_CODE，同样总是加载）。从 `nwy_at_fota.c`（NON_OTA 段）中移除原实现。

**修改前（函数调用链全在 NON_OTA 段）**：
```
download.c (PL_CODE) → nwy_fota_get_reboot_count() [nwy_at_fota.c → NON_OTA]
                        └→ nwy_fota_read_nv()      [nwy_at_fota.c → NON_OTA]
                             └→ nwy_fopen/nwy_fread/nwy_fclose [nwy_interface_util.c → NON_OTA]
```

**修改后（函数调用链全在 PL_CODE 段）**：
```
download.c (PL_CODE) → nwy_fota_get_reboot_count() [download.c → PL_CODE, static]
                        └→ FDI_fopen/FDI_fread/FDI_fclose [CrossPlatformSW → PL_CODE]
```

### 8.2 修改文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `pcac/fota/src/download.c` | 新增 | 4 个 static NV 函数 (`nwy_fota_read_nv`/`nwy_fota_write_nv`/`nwy_fota_get_reboot_count`/`nwy_fota_set_reboot_count`) + 本地宏/结构体定义 |
| `hop/telephony/nwy/src/nwy_at_fota.c` | 删除+改写 | 移除上述 4 个原函数；`nwy_fota_clear_reboot_count` 改用 `nwy_fdel`（删文件，语义等价于写 0） |
| `hop/telephony/nwy/inc/nwy_at_fota.h` | 删除 | 移除 `get/set_reboot_count` 的 extern 声明 |

**关键设计点**：
- 使用 `FDI_fopen/fread/fwrite/fclose` 替代 `nwy_fopen/fread/fwrite/fclose`，前者位于 PL_CODE 段（mini system 下可执行），后者位于 NON_OTA 段
- 与同文件已有的 `nwy_fota_set_success_flag()` (line 562-579) 使用完全相同的 API
- 函数声明为 `static`，仅在本编译单元可见，不与 `nwy_at_fota.c` 中同名函数冲突（已移除）
- `nwy_fota_clear_reboot_count` 仅在 FOTA 升级成功后由主系统模式调用（NON_OTA 段已加载），不存在段未加载问题

### 8.3 为什么不用 `IsMiniSystem()` 运行时检查

重试逻辑**仅运行于** mini system 模式，`IsMiniSystem()` 检查始终为真。若在 NV 调用前加检查跳过，则 `reboot_count` 恒为 0，**阶梯延时重试策略完全失效**。因此必须把代码搬到能加载的段，而非在调用点加保护。

### 8.4 验证结果

修复后重跑方法 A（拔 SIM 卡 60s）和方法 B（不可达 FTP 168s）必现用例，**不再死机**，FOTA 重试的阶梯延时（reboot_count 递增）正常生效。

## 9. 历史分析演进（方法论价值）

> 以下保留分析思路的演进过程，是从 dump 排查沉淀 `spec-asr-dump-analyzer` 技能的原始素材。

| 版本 | 假设 | 结局 |
|------|------|------|
| v1 | 异步外部中止（FSC=0xD 误读为 VMSAv7） | ❌ 推翻：PMSAv7 中 FSC=0xD 是 Permission fault，bit[11]=WnR |
| v2 | Permission fault + PC-DFAR 矛盾（PC 处指令是合法栈操作 `str r0,[sp,#0]`，与 DFAR=0x4 不符） | 部分正确：锁定"代码段被破坏"方向 |
| v3 | PSRAM 代码段被大面积破坏（0xAA/0x55 主导） | 部分正确：识别出破坏，但误判为"运行时破坏" |
| v4 | QSPI 总线竞争（Flash 与 PSRAM 共享总线） | ❌ 推翻：0xAA/0x55 是 checkerboard 测试模式，破坏精确对齐 section 边界，非随机损坏 |
| **v5** | **mini.sys.enable=1 导致 NON_OTA 未加载** | **✅ 核心突破**：DDR dump 搜到 `mini.sys.enable="1"`，确认启动跳过加载；0xAA/0x55 是 BIST 测试残留而非破坏 |
| **v6** | **设计矛盾深入分析（最终结论）** | **✅ 最终**：重试逻辑仅运行于 mini system，调用的 NV 函数却在 mini system 下不加载的段；追溯 scatter `*nota-nota.lib (+RO)` 通配符根因 |

### 平台分析对比

平台厂商从 DDR Dump 反汇编得出 `strh r2, [r5, #4]` (R5=0)，结论为 R5=NULL 指针 bug。**但 DDR Dump 中该地址是 BIST 填充数据，原始代码（AXF）不使用 R5**。平台分析了被填充数据覆盖后的代码，得出错误结论 —— 这正是"必须先做代码完整性校验（AXF vs DDR）再反汇编"的教训。

> **方法论提炼（已固化进 `spec-asr-dump-analyzer`）**：FOTA 排查走过的"代码完整性校验 → 寄存器解读 → 反汇编对比 → 段归属分析"四步，是通用流程，下次任意死机都能复用；而"mini.sys.enable 跳过 NON_OTA 段"是这条问题的特例答案，不固化进技能。

## 10. 相关文件

| 文件 | 作用 |
|------|------|
| `pcac/fota/src/download.c` | FOTA 重试逻辑、NV 函数新位置（修复点） |
| `hop/telephony/nwy/src/nwy_at_fota.c` | NV 函数原位置（已移除） |
| `hop/telephony/nwy/inc/nwy_at_fota.h` | NV 函数声明（已移除 get/set） |
| `startup/logo/src/main.c` | NON_OTA 加载跳过逻辑 (line 944-949) |
| `startup/bootloader/src/ota.c` | Bootloader 设置 mini.sys.enable property (line 623-641) |
| `hop/BSP/src/main.c` | `IsMiniSystem()` 实现 (line 3814-3837) |
| `Crane_DS_16M_Ram_16M_Flash_XIP_CIPSRAM_Common.sct` | scatter 文件 `*nota-nota.lib (+RO)` 规则（根因） |
