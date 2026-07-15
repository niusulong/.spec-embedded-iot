# UIS8850 堆状态与 Trace 指南

8850 的堆管理（OSA dlmalloc 包装）与崩溃前日志（OSA trace）解读。堆耗尽是高频
死机根因，malloc trace ring 能定位堆消耗户；OSA trace 能还原崩溃前事件序列。

## 1. 堆管理（gOsiHeaps / gOsiDefaultHeap）

8850 用 Unisoc OSA 的 dlmalloc 包装。多个堆 region 记录在：

| 符号 | 含义 |
|---|---|
| `gOsiHeaps` | 堆 region 指针数组（@0x80322c60, 64B） |
| `gOsiDefaultHeap` | 默认堆指针（@0x80322c58）→ 指向堆描述符 |
| `__heap_start` / `__heap_end` | AP 堆范围（实测 0x803c4980 ~ 0x80800000，约 4.4MB） |
| `cp_heap_base` / `cp_heap_limit` | CP 堆范围 |

**堆描述符结构**（`*gOsiDefaultHeap`，实测布局，字段名匿名需反汇编确认）：
```
+0x0  = 0xaeaa0000      (dlmalloc magic / mchunkptr)
+0x4  = <heap_base>     (堆基址, = __heap_start)
+0x8  = <heap_size>     (堆总大小, = end - base)
+0xc  = <used_top>      (已用到的地址)
+0x10 = <heap_end>      (堆结束, = __heap_end)
+0x14 = <used_size>     (已用大小, 用于算使用率)
```

**使用率**：`used(+0x14) / total(+0x8 或 end-base)`。>90% 高度疑似堆耗尽。
> 字段偏移随编译选项可能变。`heap_state.py` dump 描述符前 0x20 字节供人工核对；
> 精确字段需反汇编 `osiHeapGetUsed`/`osiHeapSize` 确认。

## 2. malloc trace ring（gOsiMemRecords）

OSA 记录每次 malloc 的调用者（采样），环形 buffer：

| 符号 | 含义 |
|---|---|
| `gOsiMemRecords` | trace ring buffer（@0x80322cdc, 8192B） |
| `gOsiMemRecordPos` | 当前写入位置（@0x80322cd8） |
| `gOsiMemRecordCount` | (相关计数) |

每条记录 8 字节：`{caller(u32), ptr(u32)}`。`caller` 存储时可能 `>>1`（读出还原）。
按 caller（调用者函数）统计频次 → 堆消耗户排名。

> ⚠️ **ring 末尾不是崩溃时刻的 alloc**。若崩溃发生在 `osiMalloc→dlmalloc` 内部，
> 该次 alloc 未返回、记录未写入。ring 末尾是崩溃前**上一次成功**的 alloc。崩溃时刻
> 真正的调用者要从栈回溯（`unwind.py`）确定。但高频 caller 反映崩溃前最活跃的堆
> 分配路径，对定位堆耗尽/大流量场景有指导意义。

本案例实测（FTP 大流量下载，非堆耗尽）：
```
__tgl00_13fillSigHeader  : 522   (信号头填充)
__memset_veneer          : 225
pbuf_free                : 71    (lwip pbuf 释放)
ipc_free_heap_buf_from_peer : 64
_gprs_ps_data_to_lwip    : 42    (GPRS 数据→lwip)
rab01_22PDCP_DATA_IND_LTE: 40    (LTE PDCP 数据指示)
```
→ PS 数据通路（LTE PDCP → lwip → FTP）的 buffer 分配/释放，印证 FTP 大流量下载上下文。

## 3. OSA trace（gTraceBuf）—— 已可解码

OSA trace 是崩溃前的系统事件流（类似 8852 的 SLOG）。`gTraceBuf` 是**二进制** ring，
但 record 格式可从代码逆向，`trace_decode.py` 自动解码为可读事件流。

| 符号 | 含义 |
|---|---|
| `gTraceBuf` | trace ring buffer（@0x80324d40, 192KB = 24×8KB sub-buffer） |
| `gTraceCtx` | trace 上下文（@0x80354d40：24 个 sub-buffer 描述符，含 buf 指针/写位置） |

### 3.1 record 格式（从 osiTraceBufInit/Put/prvTraceIdEx 逆向）

每条 trace record 布局（header 24B + payload）：
```
偏移   字段
+0x0   counter   (u32)  全局序列号 (单调递增, [0x80324cdc]++)
+0x4   total_len (u16)  record 总长
+0x6   0x0098    (u16)  magic1
+0x8   0x9198    (u16)  magic2  ← record 边界标记
+0xa   len-8     (u16)  = total_len - 8 (一致性校验)
+0xc   tick      (u32)  时间戳 (来自 osiTraceTick)
+0x10  trace_id  (u32)  trace 点 hash | 0x80000000 (IdBasic 置最高位)
+0x14  payload          fmtid/格式串 + 参数 (见下两种格式)
```

> **源码依据**（`components/kernel/src/osi_log.c`）：header 是 `osiTraceHeader_t`（DIAG 模式）=
> `osiDiagPacketHeader_t{seq_num,len,type=0x98,subtype=0x00} + osiDiagLogHeader_t{type=0x9198,length} + tick + tag`。
> `prvFillTraceHeader`(L192) 逐字段填充，与上表 100% 吻合。

### 3.1b 两种 record 格式（源码 osi_log.c 确认）—— 决定能否解码

**`tag` 的 bit31（TRACE_TDB_FLAG = 1<<31）是关键区分位**：

| 类型 | 触发函数 | tag.bit31 | payload 布局 | 能否解码 |
|---|---|---|---|---|
| **明文 trace** | `osiTraceEx`/`osiTraceVprintf` → `prvTraceEx`(L486) | **0** | `[格式串明文(strlen+1,4对齐)][参数]` | ✅ 直接读文本 |
| **带ID trace** | `osiTraceIdBasic`/`osiTraceIdEx` → `prvTraceIdBasic`(L466)/`prvTraceIdEx`(L511) | **1** (tag\|=0x80000000) | `[fmtid(4B)][参数]` | ❌ 需 TDB |

**实战判定**：record 的 tag（+0x10）≥ 0x80000000 → 带 ID 的纯二进制 trace，提取不出文本；< 0x80000000 → 明文 trace，payload 含可读格式串。

本案例：`0x34081020`(nwy_malloc)/`0x3415e343`(SIM API) tag<0x80000000 → 明文，已解码；`0xb85123c4`/`0xc8f064c4`/`0xc8f064c4` tag≥0x80000000 → 带 ID，需 TDB。

### 3.1c 参数布局（partype → 参数大小，源码 prvParamBuf L287）

payload 参数按调用方传入的 `partype`（每 4-bit 一个类型）逐个排列：

| partype 值 | 类型宏 | 参数大小 |
|---|---|---|
| 1 | `__OSI_LOGPAR_I` (int32) | 4B |
| 2 | `__OSI_LOGPAR_D` (int64) | 8B |
| 3 | `__OSI_LOGPAR_F` (double) | 8B |
| 4 | `__OSI_LOGPAR_S` (string) | 变长(含\0,4对齐) |
| 5 | `__OSI_LOGPAR_M` (mem dump) | ptr(4B)+size(4B)+data |

> ⚠️ **partype 不落盘**：record 只存参数数据，不存 partype。故带 ID trace 的参数边界
> 只能靠 TDB（trace_id→格式串映射，Unisoc Catcher/coolhost 的 .tdb 数据库）。明文 trace
> 因 payload 含格式串，参数边界可从格式串的 `%d/%s/...` 推断。

### 3.2 严格 record 识别（关键）

**0x9198 不仅出现在 record header，也出现在 payload 数据里**（trace 内容偶然含此字节），
单靠 0x9198 锚会误识别。必须同时满足：
1. `rs+6 == 0x0098` **且** `rs+8 == 0x9198`（magic 对）
2. `[rs+0xa] == total_len - 8`（len 字段一致性）
3. `0x18 <= total_len <= 0x400`、`counter <= 0x100000`、`tick <= 2000000`（合理性）

满足后误识别率极低（本案例 1066 条 record，干净）。

### 3.3 文本提取

payload 内嵌**格式化后的 trace 文本**（`[文件:行 函数]消息`）+ 二进制参数。提取方法：
拼接 payload 中所有 ≥4 字符的可打印 ASCII run。文本常含源码位置（如
`NWY_FRM:nwy_platform.c [1357] [nwy_modem_mem_alloc]nwy malloc 2720`）。

### 3.4 本案例解码示例（崩溃前事件流）

```
tick=35383 [nwy_sock_cb_adpt_rda] socket event sockfd: 2
tick=35385 [nwy_dss_read] sockfd=2 ret=2720 errn...
tick=35385 [nwy_modem_mem_alloc] nwy malloc 2720     ← 按读取量分配
tick=35385 [nwy_ftp_data.cpp:206 notify_status] download status=9   ← FTP 下载中
tick=35386 [nwy_check_and_handle_socket_status] ...
tick=35386 [nwy_modem_mem_free] nwy free
(循环 ret=8192/malloc 8192, ret=1920/malloc 1920 ...)
```
→ 清晰还原 FTP 下载 socket 读循环，与栈溢出根因（nwy_ftp_ctrl 深调用栈）完全吻合。

### 3.5 局限

- **部分 trace_id 无文本**：纯二进制 trace（如 `0x385123c4`/`0x48f064c4`，各 ~400 次）
  payload 是结构化参数非格式串，本技能提取不出文本（需该 trace 点的格式定义）。
- **trace_id 是 hash**：不能直接映射到函数名，需 trace 点定义表（频次+样本文本可推断语义）。
- **ring 回绕**：192KB 约存数千条 record，只保留最近事件；长时间运行的早期事件被覆盖。

## 4. 堆耗尽判定树

```
osiPanic 调用链含 osiMalloc / dlmalloc / malloc 内 OS_ASSERT?
├── 是 → 疑似堆耗尽
│   ├── gOsiDefaultHeap 使用率 >90% → 堆耗尽 (主因)
│   │   └── gOsiMemRecords 高频 caller = 堆消耗户
│   ├── 物理遍历发现 chunk size 异常 → 堆被越界写粉碎
│   └── 物理完整但使用率低 → free-list 链接损坏 (use-after-free)
└── 否 → 非堆问题, 转栈溢出/逻辑/硬件异常
```

## 5. 关键符号速查（堆 + trace + 运行时长）

| 符号 | 含义 |
|---|---|
| `gOsiDefaultHeap` / `gOsiHeaps` | 堆描述符 |
| `gOsiMemRecords` / `gOsiMemRecordPos` | malloc trace ring |
| `gTraceBuf` / `gTraceCtx` | OSA trace（二进制） |
| `cp_heap_base` / `cp_heap_limit` | CP 堆范围 |
| `xTickCount` | FreeRTOS 系统滴答（运行时长，1ms/tick） |
| `__heap_start` / `__heap_end` | AP 堆范围 |
