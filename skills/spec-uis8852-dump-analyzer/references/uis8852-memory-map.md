# UIS8852 / N706C 内存映射与关键符号

UIS8852 是 Unisoc Cat.1 bis LTE 模块芯片，AP 核为 RISC-V RV32（RVC + Zc 压缩指令，soft-float），运行 RT-Thread。CP 核处理 LTE 协议栈。dump 通常只抓 AP（CP 死机另有流程）。

## 内存区映射（AP 核视图）

DTools 按"文件名 = 十六进制基址"命名 dump 文件：

| 文件 | 基址 | 别名 | 大小 | 区段 | 内容 |
|------|------|------|------|------|------|
| `80000000.bin` | `0x80000000` | `0x40000000` | 4MB | PSRAM | 堆、BSS、全局变量、PSRAM 代码（`__PSRAM_CODE`） |
| `c0200000.bin` | `0xc0200000` | `0x10200000` | 512KB | IRAM | IRAM 代码（`__IRAM_CODE`）；**`g_osAssert`/`g_osException` 解引用后指向这里** |
| `00008000.bin` | `0x00008000` | — | 16KB | AP ITCM | ITCM 代码（`__ITCM_CODE`，热路径：osInterruptDispatch、osAssertHandler 等） |
| `00010000.bin` | `0x00010000` | — | 16KB | AP DTCM | DTCM 数据；**dlmalloc `av_` bin header 数组、`g_osIrqNo`、`g_osInterruptNest`** |
| `c8000000.bin` | `0xc8000000` | — | 4MB | SPI flash XIP | XIP 代码（`flash_*`、SLOG、驱动） |

### 别名（重要）

- **PSRAM** 有 `0x80000000` 和 `0x40000000` 两种地址形式，解析时都要识别
- **IRAM** 有 `0xc0200000` 和 `0x10200000` 两种形式。`g_osAssert`/`g_osException` 解引用常得到 `0x10206000` 这种别名地址——它就是 IRAM
- 内存读取脚本必须同时注册两种地址形式（`common.py` 的 `UIS8852_REGIONS` 已处理）

### 未 dump 的区（分析时勿浪费时间查找）

- **ECLIC 中断控制器寄存器**（基址 `0x00420000`，含 pending/active IRQ 状态）— DTools 默认不抓，"哪些中断挂起未处理"无法从 dump 看。改用 `g_osIrqNo`（当前 IRQ）+ `g_osInterruptNest`。
- **CP 核内部状态**：dump 含 `f5000000.bin`(CP ITCM)/`f5008000.bin`(CP DTCM)，但本技能仅分析 AP。若怀疑 CP 引发 AP 异常需单独做 CP 侧分析（本技能不支持）。
- 外设寄存器（WDT `0xe0002000`、PCU `0xf1005000`、PMU `0xf1001000`、GPIO/UART/DMA 等）**会被 DTools 抓取**（按地址命名 `.bin`），`wdt_reset.py` 等用 `scan_all_peripherals=True` 自动读取。

### 区段归属速判

分析地址时先判区段：
- `0x00008xxx` / `0x0001xxxx` → ITCM/DTCM（ITCM 代码 + 关键 ISR 全局量）
- `0x801xxxxx` / `0x802xxxxx` / `0x803xxxxx` → PSRAM（堆 `0x8019ace0+`、BSS 全局变量）
- `0xc026xxxx` / `0xc02xxxxx` → IRAM 代码（dlmalloc、协议栈热路径）
- `0xc803xxxx` / `0xc804xxxx` / `0xc805xxxx` → flash XIP（SLOG、驱动、CFW）

## 关键全局符号

地址从 ELF `.symtab` 动态读取（不硬编码）。下表是含义与所在区段：

### 异常 / 蓝屏

| 符号 | 类型 | 所在区段 | 含义 |
|------|------|---------|------|
| `gBuildRevision` | `char[]` | PSRAM BSS | 固件版本字符串（版本校验用） |
| `gIsPanic` | `uint32_t` | PSRAM BSS | =1 表示发生 panic |
| `gBlueScreenAbortType` | `uint8_t` | PSRAM BSS | `0xFE`=ASSERT；其他=EXCEPTION(mcause code) |
| `g_osErrorLog` | `char[]` | PSRAM BSS | 蓝屏摘要字符串（如 `AP Assert. File: dlmalloc.c, Line: 539, PC: 0x...`） |
| `g_osAssert` | **指针** → `osAssert_t` | PSRAM BSS | 指向 IRAM 中的 osAssert_t 结构（**需二次解引用**） |
| `g_osException` | **指针** → `osException_t` | PSRAM BSS | 指向 IRAM 中的 osException_t 结构（**需二次解引用**） |

### 中断 / 调度

| 符号 | 类型 | 所在区段 | 含义 |
|------|------|---------|------|
| `g_osIrqNo` | `uint8_t` | DTCM | **当前正在处理的内部 IRQ 号**（`osInterruptDispatch` 写入）。注意是**内部** IRQ；映射到中断源需 `ext_num = irq - 19` 再查 `AP_INT_NUM_<ext_num>`。三个 u8 全局在 DTCM 连续：`g_osIrqNo` → `g_osExceptionNest` → `g_osInterruptNest`（irq.c:23-27） |
| `g_osInterruptNest` | `uint8_t` | DTCM | 中断嵌套深度（≥1 = ISR 上下文） |
| `g_osCurrentThread` | 指针 → `osThread` | DTCM | 被中断的任务（ISR 上下文时） |
| `g_irq_table` | 数组 | PSRAM BSS | `g_irq_table[irq].handler` = ISR 函数指针 |

### 堆 / malloc trace

| 符号 | 类型 | 所在区段 | 含义 |
|------|------|---------|------|
| `g_osApSystemMem` | `osDlmalloc_t` | PSRAM BSS | AP 系统堆（base/total/used/free/top_size） |
| `gOsiMemRecords` | `osiMemRecord_t[]` | PSRAM BSS | malloc/free 操作环形 trace（每条 `{caller, ptr}`；`caller` 的 **bit31=alloc(1)/free(0) 标志**，`[30:0]`=调用者地址`>>1`） |
| `gOsiMemRecordCount` | `uint32_t` | PSRAM BSS | ring 容量（通常 1024） |
| `gOsiMemRecordPos` | `uint32_t` | PSRAM BSS | ring 写入位置 |

### SLOG 日志

| 符号 | 类型 | 含义 |
|------|------|------|
| `g_slogBufPool` | `SLOG_BufferPool` | 日志 buffer 池；`cachedIntPrints`（ISR 日志队列）在偏移 `+0xa8` |
| `g_slogIsrLogTotalLen` | `int32_t` | ISR 日志累计字节数（限流阈值 `SLOG_ISR_LOG_MAX_SIZE=1024`） |
| `g_slogIsrLogHisMaxLen` | `int32_t` | ISR 日志历史峰值 |
| `g_slogExpLogTotalLen` | `int32_t` | panic 期间日志累计（**无限流**，可继续吃堆） |

## 结构体

### `osAssert_t`（@ IRAM，g_osAssert 解引用后）

| 偏移 | 字段 | 类型 |
|------|------|------|
| 0 | core | uint32（1=AP, 2=CP） |
| 4 | file | `const char*`（指向 .rodata 文件名） |
| 8 | line | uint32（`__LINE__`） |
| 12 | pc | uint32（assert 宏调用点返回地址） |

### `osException_t`（@ IRAM，g_osException 解引用后）

| 偏移 | 字段 | 类型 | 含义 |
|------|------|------|------|
| 0 | core | uint32 | 1=AP, 2=CP |
| 4 | trace | uint32 | → `rt_hw_stack_frame`（保存的 32 寄存器） |
| 8 | mcause | uint32 | RISC-V mcause CSR |
| 12 | mdcause | uint32 | 平台扩展：损坏原因（PMP/Bus/NICE） |
| 16 | mepc | uint32 | 异常指令地址 |
| 20 | mtval | uint32 | 异常值（访问地址等） |

### `rt_hw_stack_frame`（@ trace 指向的地址，32 × uint32）

字段顺序（与 `cpuport.c` 的 `struct rt_hw_stack_frame` 一致；index × 4 = 字节偏移）：

| idx | 字段 | idx | 字段 | idx | 字段 |
|-----|------|-----|------|-----|------|
| 0 | epc | 11 | a1 | 22 | s6 |
| 1 | ra | 12 | a2 | 23 | s7 |
| 2 | mstatus | 13 | a3 | 24 | s8 |
| 3 | gp | 14 | a4 | 25 | s9 |
| 4 | tp | 15 | a5 | 26 | s10 |
| 5 | t0 | 16 | a6 | 27 | s11 |
| 6 | t1 | 17 | a7 | 28 | t3 |
| 7 | t2 | 18 | s2 | 29 | t4 |
| 8 | s0_fp | 19 | s3 | 30 | t5 |
| 9 | s1 | 20 | s4 | 31 | t6 |
| 10 | a0 | 21 | s5 | | |

（a0-a7 在 idx 10-17，s2-s11 在 idx 18-27，t3-t6 在 idx 28-31）

> 注意：**不含 sp**（sp 由 trap 入口用其他方式保存）。trap 帧的 `ra`(idx1) 在 ASSERT 场景会被 `osAssertHandler` 覆盖（常等于 epc），不可直接当调用者返回地址（见 `stack-unwind-guide.md`）。
