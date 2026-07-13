---
name: spec-uis8852-dump-analyzer
description: >
  UIS8852 / N706C (Unisoc, RISC-V RV32 + RT-Thread) 平台 crash dump 分析技能。
  从 DTools 抓取的 ramdump（按地址命名的 .bin 内存区 + AP.elf + .map）重建死机现场：
  g_osAssert/g_osException 结构、rt_hw_stack_frame 寄存器、当前中断 IRQ（g_osIrqNo）、
  帧感知栈回溯、dlmalloc 堆物理遍历（耗尽 vs 损坏判定）、malloc trace ring 定位堆消耗户、
  cachedIntPrints ISR 日志源定位、RISC-V addr2line/objdump 源码映射。
  当用户说 "spec 分析dump"、"UIS8852 死机"、"N706C 崩溃"、"8852 crash"、"AP Assert"、
  "dlmalloc 断言"、"RISC-V 异常"、"mcause ecall"、"g_osIrqNo"、"RT-Thread 死机"、
  "PSRAM 堆耗尽"、"LPM 中断死机" 时使用——只要意图是"定位 UIS8852/N706C 平台的死机根因"，
  即使没明说 "分析dump" 也应触发。即使用户只是粘贴了 dtools.log 或一组崩溃寄存器、
  或提到 "设备死机需要分析 8852 dump"，也应触发。
  仅适用于 Unisoc UIS8852/N706C (RISC-V) 平台；ASR (Cortex-R) 用 spec-asr1603-dump-analyzer，
  EC (Cortex-M) 用 spec-ec626-dump-analyzer。
---

通过 DTools 抓取的 ramdump 文件，分析 UIS8852 / N706C（Unisoc RISC-V + RT-Thread）AP 核的死机根因。

## 适用场景

- 设备死机后用 DTools 抓取了 ramdump（dump 目录含按地址命名的 `.bin` + `*.elf` + `*.map` + `dtools.log`）
- 需要区分根因：堆耗尽 / 堆元数据损坏（dlmalloc assert）/ 空指针 / 栈溢出 / 看门狗 / 协议栈逻辑错误
- 有 PC/RA/mepc/mcause 地址，要解码到具体函数和源码行
- 需要确定是**哪个中断**（IRQ 号 + ISR）、**哪个函数**触发了崩溃

## 不适用场景

- 没有 ramdump（只有 AT 日志 / 串口日志）→ 用 `spec-bug-analyzer`
- 已确认是内存泄漏且要精确定位泄漏点（埋点追踪）→ 用 `spec-memory-leak-analyzer`
- ASR (Cortex-R) / EC (Cortex-M) 平台 → 用对应平台的 dump 分析技能
- **CP 核崩溃**：本技能仅分析 AP 核。dump 虽含 `f5000000.bin`(CP ITCM)/`f5008000.bin`(CP DTCM)，但无 CP ELF 无法解析 CP 符号；需 CP ELF 单独分析

## 输入要求

用户提供 dump 文件所在目录路径。典型目录内容：

| 文件 | 用途 | 必需 |
|------|------|------|
| `*.elf`（如 `8852_cat1bis_op_mdl_4M.elf`） | AP ELF（含 `.symtab` + DWARF），符号/源码定位 | 是 |
| `*.map` | 链接器符号映射。**主脚本要求 ELF（带 `.symtab`+DWARF，读 struct 偏移）**。ELF 缺失/被 strip 时可用 `map_lookup.py` 降级（仅地址→函数名，无 struct/源码行/static 函数可能缺失） | 可选 |
| `80000000.bin` | PSRAM 完整转储（堆/BSS/全局变量都在这） | 是 |
| `c0200000.bin` | IRAM 转储（IRAM 代码 + g_osAssert/osException struct 实际存这里） | 是 |
| `00008000.bin` / `00010000.bin` | AP ITCM / DTCM（DTCM 含 `av_` bin header、g_osIrqNo 等） | 是 |
| `c8000000.bin` | SPI flash XIP 转储 | 推荐 |
| `dtools.log` | DTools 抓取日志，含**版本校验**、内存区段映射、蓝屏信息 | 强烈推荐 |
| `ap.cmm` / `cp.cmm` / `loadbin.cmm` | TRACE32 脚本（备用） | 可选 |

## 内存区映射（关键 — 所有地址解析的基础）

UIS8852 AP 核视图（dump `.bin` 文件名即基址）：

| 文件 | 基址 | 别名 | 区段 |
|------|------|------|------|
| `80000000.bin` | `0x80000000` | `0x40000000` | PSRAM（4MB，含堆、BSS、全局变量、PSRAM 代码） |
| `c0200000.bin` | `0xc0200000` | `0x10200000` | IRAM（512KB，IRAM 代码） |
| `00008000.bin` | `0x00008000` | — | AP ITCM（16KB） |
| `00010000.bin` | `0x00010000` | — | AP DTCM（16KB，含 `av_` 数组、ISR 上下文全局量） |
| `c8000000.bin` | `0xc8000000` | — | SPI flash XIP（4MB） |

> **别名很重要**：`g_osAssert`/`g_osException` 是**指针**，解引用后指向的 struct 实际存在 IRAM（解析常得到 `0x10206000` 别名地址，需识别为 IRAM）。PSRAM 也有 `0x40000000` 别名。脚本会同时注册两种地址形式。

## 快速开始

**推荐：一键跑全部 7 个分析脚本，输出自动归档 + 生成 INDEX.md**（便于追溯对比）：
```bash
SKILL_DIR="<Base directory for this skill>"
python "scripts/run_all.py" <dump_dir> <ap.elf> <bug_out_dir> --pc <crash_PC>
# 例：python scripts/run_all.py .spec/.../dump/ 8852.elf .spec/bug/<id>_desc/ --pc 0xc026cb94
```
产出 `<bug_out_dir>/analysis/` 下：每个脚本的 `<name>.txt` 完整输出 + `INDEX.md`（脚本功能 + 自动提取的关键结论）。

**或单步起步**（先定崩溃性质）：
```bash
python "scripts/uis8852_analyze.py" <dump_dir> <ap.elf>
```
一键输出：版本、gIsPanic、abort 类型、g_osErrorLog、g_osAssert/osException 完整字段、rt_hw_stack_frame 32 个寄存器、当前 IRQ（`g_osIrqNo`）、启发式栈扫描回溯 + addr2line 源码定位。这是分析的**起点**；后续按下方流程深入。

## 执行流程

> **场景路由**（按 `gBlueScreenAbortType` + `gIsPanic` + `gResetReson` 决定重点步骤）：
>
> | 场景 | 触发信号 | 重点步骤 |
> |------|---------|---------|
> | **ASSERT (`0xFE`)** | gBlueScreenAbortType=0xFE | 2→4→5→7→8→9→10（assert 位置 + 中断 + 堆） |
> | **EXCEPTION (硬件异常)** | abort=mcause code (1/2/5/7) | 2→4→5→**6**→**代码完整性**→9→10（反汇编崩溃指令 + 查代码损坏） |
> | **WDT/看门狗超时** | gResetReson bit3/4 置位 + 无 panic | **复位/WDT** → 4 → **任务列表**（找死锁/饿死） |
> | **栈溢出/死锁** | 某任务栈水位 >90% 或全 SUSPEND | **任务列表** → 4（找溢出任务/持锁者） |
> | **设备重启无 panic** | gIsPanic=0 | **复位/WDT**（复位原因 forensics） |
>
> 通用必做：Step 1（版本校验）、Step 2（现场）、Step 4（中断身份）、Step 9（根因）、Step 10（报告）。

---

### Step 1：文件扫描 + 版本校验

- Glob 扫描 dump 目录，按上表识别文件
- 读 `dtools.log`：`gBuildRevision in _elf_` vs `in board` 必须**完全一致**，否则后续结论标注"版本不匹配"警告
- 从 `dtools.log` 可直接看到蓝屏摘要（`g_osErrorLog`、`gBlueScreenAbortType`），作为 Step 2 的交叉验证

### Step 2：解析异常现场（g_osAssert / g_osException / rt_hw_stack_frame）

```bash
python "scripts/uis8852_analyze.py" <dump_dir> <ap.elf>
```

脚本提取：
- `gIsPanic`、`gBlueScreenAbortType`（在 PSRAM，符号地址从 ELF 读）
- `g_osErrorLog`（蓝屏字符串，如 `AP Assert. File: dlmalloc.c, Line: 539, PC: 0xc026cb94`）
- `g_osAssert` → **指针，二次解引用** → `{core, file, line, pc}`（struct 在 IRAM）
- `g_osException` → **指针，二次解引用** → `{core, trace, mcause, mdcause, mepc, mtval}`
- `rt_hw_stack_frame` @ `g_osException->trace`：32 个寄存器，字段顺序见 `references/riscv-exception-guide.md`

> ⚠️ **关键陷阱**：`g_osAssert`/`g_osException` 是**指针**（存在 PSRAM BSS），指向的 struct 实际在 IRAM（解析常得到 `0x10206xxx` 别名）。必须二次解引用。`osThread.name` 同理。
>
> ⚠️ **trap 帧的 ra 不可信**：`osAssertHandler` 执行 `ecall` 主动陷入时，`rt_hw_stack_frame.ra` 被覆盖（常等于 epc，或被改成数据指针）。**不要从 trap 帧的 ra 直接回溯**——它是 `osAssertHandler` 的现场，不是 assert 点的调用者。调用者要从栈上 `do_check_*` 帧的 ra 找（Step 4）。

### Step 3：abort 类型路由

| `gBlueScreenAbortType` | 路由 | 含义 |
|---|---|---|
| `0xFE` | **ASSERT**（软件断言） | 代码主动 `OS_ASSERT` 失败，PC 指向 assert 宏调用点（如 dlmalloc 一致性检查） |
| 其他值 | **EXCEPTION**，值 = `mcause & 0x1f` | 硬件异常码（见 `references/riscv-exception-guide.md`） |

**`mcause` 解读**：
- bit31=0 → exception；bit31=1 → interrupt
- `code=11`（Machine ECALL from M-mode）= **`osAssertHandler` 主动 `ecall` 陷入**保存蓝屏现场，**不是硬件错误**。这是本平台 ASSERT 的标准机制
- `code=2/4/5/6/7` = 非法指令/地址错误/访问错误（真硬件异常）

**ISR 上下文判断**：`g_osInterruptNest`（@ DTCM，u8）≥ 1 → 崩溃发生在中断上下文。被中断任务的栈不能直接用，要走 Step 4 的中断链回溯。

### Step 4：中断识别 + 帧感知回溯（UIS8852 的核心难点）

#### 4.1 确定当前中断（权威方法 — 读 `g_osIrqNo`）

```bash
python "scripts/unwind.py" <dump_dir> <ap.elf>
```

- `g_osIrqNo`（DTCM，**uint8_t**）= **当前正在处理的内部 IRQ 号**（`osInterruptDispatch` 写入）。三个 u8 全局在 DTCM 连续：`g_osIrqNo` → `g_osExceptionNest` → `g_osInterruptNest`
- IRQ → ISR：查 `osInterruptInstall` 注册关系（源码），或从 `g_irq_table[irq].handler`（@ PSRAM）读函数指针
- IRQ → 中断源：`g_osIrqNo` 是**内部号**；外部/源号 `ext = irq - 19`（`OS_EXT_IRQ_TO_IRQ(ext)=ext+19`），再用 ext 查 `chip_int_num.h` 的 `AP_INT_NUM_<ext>`。**勿直接用 irq 匹配 AP_INT_NUM_***

> 例：`g_osIrqNo=0x27(39)`（内部）→ ext=20 → `AP_INT_NUM_20` → `LTE_LPM5_INT` → "LTE lpm timer5 子帧中断" → ISR=`LPM_Isr`。

#### 4.2 帧感知栈回溯（**不要用纯启发式扫描**）

RISC-V 默认不用 frame pointer，启发式"栈上找代码地址"会有**大量噪声**（本次案例中 `osiWorkEnqueue`/`SHA224`/`DMA_Irq` 都被误判）。正确做法：

```bash
python "scripts/unwind.py" <dump_dir> <ap.elf>   # 自动 prologue 解析 + 帧步进
```

脚本逻辑：
1. 从 trap 帧 SP（`g_osException->trace + 128`）出发
2. 对链上每个函数，objdump 其 prologue，提取 frame size + ra 保存偏移（处理 Zcm `cm.push`/`c.addi16sp`/`sw ra,k(sp)`）
3. `sp += frame_size`，读 `sp + ra_off` 得上一层调用者，逐层上溯到 `osInterruptDispatch` 和被中断任务

**手动验证 call site**（必须做，避免误判）：对每个候选返回地址 V，反汇编 V-4/V-2 处必须是一条 `jal`/`jalr`/`c.jal`/`c.jalr` 指令。详见 `references/stack-unwind-guide.md`。

> ⚠️ **常见误判**：栈扫描会把栈上的数据（恰好落在某函数地址范围）当成返回地址。**永远用反汇编确认 call site**，再用语义（"栈地址 A 的值 V → V 的函数调用了 A 处帧的所有者"）双向核对。

### Step 5：符号 + 源码定位

```bash
# 工具链在 idh.code/prebuilts 下，脚本自动查找
riscv64-unknown-elf-addr2line -f -e <ap.elf> 0x ADDR1 0x ADDR2 ...
```

必须解析的地址：assert PC、mepc、栈帧中所有代码段地址（完整调用链）。脚本内置批量 addr2line。

### Step 6：反汇编确认崩溃指令（仅 EXCEPTION / 需确认 assert 分支）

```bash
riscv64-unknown-elf-objdump -d -C --start-address=<addr-0x10> --stop-address=<addr+0x20> <ap.elf>
```

用途：
- **ASSERT 场景**：确认是哪个 `OS_ASSERT`（同一函数常有多个，`li a1, <line>` 立即数 = `__LINE__`，对照 `g_osAssert.line` 双重确认）。本次案例：do_check_chunk 反汇编显示 `li a1,539` → 确认是 539 行 `p >= heap->base`
- **EXCEPTION 场景**：确认崩溃指令类型（`lw`/`sw`/`ld`/`sd` 等），区分空指针/栈溢出/野指针
- **call site 验证**：确认某地址是否真为函数调用返回点（Step 4）

### Step 7：堆分析（UIS8852 死机高频根因 — 堆耗尽 / 元数据损坏）

#### 7.1 堆状态

```bash
python "scripts/heap_state.py" <dump_dir> <ap.elf>
```

读 `g_osApSystemMem`（PSRAM BSS）：`base`/`total`/`used`/`free`/`top_size`。**使用率 > 95% → 堆耗尽高危**。

#### 7.2 堆物理遍历（**判定"耗尽" vs "元数据损坏"的关键**）

```bash
python "scripts/heap_walker.py" <dump_dir> <ap.elf>
```

从 `base` 出发按 `chunksize = p->size & ~3`（`SIZE_BITS=PREV_INUSE|IS_MMAPPED`）逐 chunk 物理步进至 `end`，校验每个 chunk 的 size 合法性，统计 inuse/free 数量、top chunk 大小、最大 free 块。

| 结果 | 判定 |
|---|---|
| 所有 chunk size 合法、覆盖 gap=0、top 极小（如 <64B） | **纯耗尽**（堆物理完整） |
| 某个 chunk size 异常 / 越界 | **堆内存被越界写粉碎**（定位破坏点） |
| 物理完整但 free chunk 的 fd/bk 指向 DTCM `0x10000~0x10400` | **正常**（dlmalloc bin header 在 DTCM `av_` 数组，`bin_at(i)=av_+8i`） |
| 物理完整但某 fd/bk 指向 `< base` 的非法地址 | **free-list 链接损坏**（窄带 4 字节级，疑似 use-after-free） |

> ⚠️ **dlmalloc 机制**：free chunk 的 fd/bk 循环指回 bin header（在 DTCM `av_` 数组），**这是正常的，不是损坏**。本次案例初扫把 204 个 free chunk 的 DTCM fd/bk 误报为 corruption，实际是 bin header 地址。`bin_at(i) = av_ + 8*i`，间距 8 字节，`@0x10010 fd=<top>` 正是 `top(n)=bin_at(n,0)->fd`。

#### 7.3 堆耗尽户定位（malloc trace ring）

```bash
python "scripts/heap_state.py" <dump_dir> <ap.elf>   # 内含 trace ring 统计
```

`gOsiMemRecords`（PSRAM，环形 buffer，count 通常 1024）每条 `{caller, ptr}`，`caller` 存储时 `>>1`（读出需 `<<1` 还原）。按 caller 分布统计 → 找出堆消耗最大的调用者。

> ⚠️ **关键陷阱**：trace ring **最后一条不是崩溃时刻的 alloc**。崩溃发生在 `osMalloc→dlMalloc` 内部，`dlMalloc` 未返回，记录未写入。ring 末尾是崩溃前**上一次成功**的 alloc。崩溃时刻的真正调用者要从栈回溯（Step 4）确定。本次案例：ring 末尾全是 `SLOG_GetCommBuffer`，但崩溃时刻真正执行 osMalloc 的是栈上的 `Ps_LpmCallback(osMalloc 801)`。

#### 7.4 ISR 日志源定位（SLOG 相关崩溃）

`g_slogBufPool`（PSRAM）含多个 SLOG_List 链表。`cachedIntPrints`（ISR 日志队列，偏移 `+0xa8`）记录 ISR 内打印的日志 buffer。遍历链表解码日志内容，可定位"哪条打印最频繁"。`g_slogIsrLogTotalLen`（@ PSRAM）若顶满 `SLOG_ISR_LOG_MAX_SIZE`(1024) → ISR 日志生产 > 消费。

### Step 8：dlmalloc assert 专项（若 `g_osErrorLog` 含 `dlmalloc.c`）

dlmalloc DEBUG 一致性检查失败是本平台高频死机点。关键 assert：

| 行 | 函数 | 失败含义 |
|---|---|---|
| 536 | `do_check_chunk` | chunk 被 mmap（罕见） |
| 539 | `do_check_chunk` | **`p < heap->base`**：遍历到非堆地址（free-list bk 被改 / victim 野指针） |
| 542 | `do_check_chunk` | `p+sz > top`：chunk 越过 top（堆顶附近 size 被改） |
| 558 | `do_check_free_chunk` | free chunk 标志位/链表不自洽 |
| 1138 | `dlMalloc` | top chunk 剩余 < MINSIZE（**堆耗尽**触发 `dlmallocPrint`+`OS_ASSERT(0)`） |

进入 `dlmallocPrint` 的两条路径：
1. `dlMalloc` line 1138：top chunk 不足（堆耗尽）
2. `osMallocTrace` line 1848：采样 dump 检查（`g_osApSystemMemDumpRate` 触发）

`dlmallocPrint` 内部遍历 free bin 会调用 `do_check_free_chunk → do_check_chunk`——所以**耗尽也会触发 539 assert**（遍历撞见链表异常），不要一见 539 就断定"独立 corruption"，先看堆使用率。

### Step 8b：任务列表 + 栈水位（栈溢出 / 死锁 / 调度异常）

```bash
python "scripts/threads.py" <dump_dir> <ap.elf>
```

枚举所有 `osThread_t`（按 TCB 特征**扫描法**，不依赖链表完整性）+ 每任务栈水位（RT-Thread magic 填充法）。

| 发现 | 判定 |
|---|---|
| 某任务水位 > 90% | **栈溢出高风险**（可能破坏相邻堆/TCB） |
| 被中断任务水位 > 90% | 栈溢出很可能就是根因 |
| 全部任务 SUSPEND（无 READY） | **死锁**（找持锁者 / 关中断者） |
| 高优先级任务长时间 RUNNING | **饿死**低优先级任务 |
| 水位都正常 | 排除栈溢出，聚焦堆/逻辑 |

> 本次 dump 实测（用脚本扫描，任务数随扫描阈值/版本略有差异，关注水位与状态而非绝对数量）：最高水位 RTC 65%，无任务 >90% → 排除栈溢出。

### Step 8c：代码完整性（EXCEPTION 场景必查 — 排除代码损坏）

```bash
python "scripts/code_compare.py" <dump_dir> <ap.elf> --pc <crash_PC>
```

对比 ELF `.itcm` / `.iram2` / `.psram` 代码段与 dump 对应地址（仅在 ELF 非零字节处计不一致，排除 padding/BSS 误报），并 spotlight crash PC 处指令：

| 结果 | 判定 |
|---|---|
| 全部 INTACT | 代码完好 → 崩溃非代码损坏（logic/指针/堆） |
| crash PC 处 ELF≠dump | **CPU 执行了损坏代码**（PSRAM/IRAM 代码被破坏） |
| 整段全不一致（非零位置） | 代码段未加载（XIP/PSRAM 未填充） |

> mcause=2（非法指令）/1/5（取指/访问错误）落在代码段时**必须**跑此步，否则可能把代码损坏误判成 logic bug。

### Step 8d：复位原因 + WDT（复位/看门狗场景）

```bash
python "scripts/wdt_reset.py" <dump_dir> <ap.elf>
```

- `gIsPanic=1` → 蓝屏（`gResetReson` 是历史，非本次崩溃因）；`gIsPanic=0` → 设备已重启（`gResetReson` 是本次复位因）
- `gResetReson` 位掩码：bit0 POR5(上电) / bit1 EXT(复位键) / bit2 G_SW(软件) / **bit3 APSS_WDT** / **bit4 CPSS_WDT**
- WDT：`gSysnvSysWdtEnable`、`gSysnvSysWdtFeedPeriod`（喂狗周期 ms）、`g_hardWdtInfo` + WDT 寄存器（`0xe0002000`）

> WDT 复位场景：`gResetReson` bit3/4 置位 → 找死锁/关中断过久的任务（结合 Step 8b 任务列表 + Step 8e 切换历史）。

### Step 8e：任务/中断切换历史（WDT/死锁的时序证据）

```bash
python "scripts/trace_history.py" <dump_dir> <ap.elf>
```

读内核环形 buffer（`kservice.c`，需编译开 `OS_USING_THREAD_TRACE`/`OS_USING_IRQ_TRACE`）：
- `osThreadSwapArray[100] {thread, time}`：每次任务切换入
- `osIrqSwapArray[100] {irq, in, out}`：每次中断进/出

静态快照看不出的问题，靠它还原时序：

| 发现 | 判定 |
|---|---|
| 最后 switch-in ≠ g_osCurrentThread | 崩溃发生在 ISR/调度器内（非任务上下文） |
| 某任务运行间隔（gap）特别大 | 该任务占用 CPU 久 → **WDT 超时嫌疑** |
| 某任务从不出现 | 该任务**饿死**（被高优先级抢占）或死锁 |
| 全是 idle（无任务切换） | 任务全阻塞 → 死锁或等待同一事件 |
| 某 IRQ 在 IRQ 频率榜 >30% | **IRQ 风暴**（硬件抖动/错误重试） |
| IRQ 的 in→out 耗时长 | ISR 执行过久 → 关中断太久致 WDT |

> 本次 dump 实测：最后 switch-in = NAS_TASK（= g_osCurrentThread，自洽）；idle 39%/tcpip 24%/L2_TASK 16%（正常分布），无任务饿死。

### Step 9：根因定位

综合 Step 1-8，按决策树定位根因：

```
gBlueScreenAbortType?
├── 0xFE (ASSERT)
│   ├── dlmalloc.c assert
│   │   ├── 堆使用率 >95% + top <64B  →  堆耗尽（主因）
│   │   │   └── 查 malloc trace ring 最大户 + 栈上 osMalloc 调用者
│   │   ├── 物理遍历发现 size 异常 chunk  →  堆内存被越界写（定位破坏点）
│   │   └── 物理完整 + 539 assert       →  free-list 链接损坏（疑似 use-after-free）
│   ├── 协议栈/驱动 assert（file=ps_*/drv_*.c）→  逻辑错误（assert_file:line）
│   └── WDT_EXPIRED  →  看门狗（某任务死锁/关中断过久）
├── EXCEPTION (mcause code)
│   ├── code=11 (ecall M-mode)  →  其实是 ASSERT（osAssertHandler 陷入）
│   ├── code=2 (非法指令)       →  跳转到数据区/指令损坏/PSRAM 代码损坏
│   ├── code=4/6 (地址未对齐)   →  异常指针
│   └── code=5/7 (访问错误)     →  空指针/野指针/PMP 违例
└── 堆耗尽 + ISR 内 osMalloc  →  审查 ISR 中的动态分配（应改预分配/静态）
```

**输出复现路径**：根因确定后必须给出前置条件 + 触发路径。证据不足时输出已知条件 + 推测，标注"待验证"。

### Step 10：报告

使用 `references/bug-report-template.md` 模板，输出到 `.spec/bug/{工作项ID}_{问题描述}/Dump分析.md`。

**工作项 ID**：未提供时询问用户；或按内容命名目录（如 `ap_heap_exhaust_xxx`）。

**归档**：将分析脚本和 `dtools.log` 复制到 bug 目录的 `scripts/`、`dump/` 子目录（大体积 `.bin`/`.elf` 保留原位，报告中记录路径）。

## 工具使用规则

### 脚本路径（重要）

所有脚本在技能目录下 `scripts/`。系统加载技能时提供 "Base directory for this skill"，执行 Bash 命令时**必须用完整路径**：

```bash
SKILL_DIR="<Base directory for this skill>"
python "$SKILL_DIR/scripts/uis8852_analyze.py" <dump_dir> <ap.elf>
```

### 脚本速查

| 脚本 | 用途 | 关键产出 |
|------|------|---------|
| `common.py` | 公共模块（Mem 内存区+外设扫描、Symbols 符号+DWARF struct 偏移、工具链查找、addr2line/objdump） | 不直接运行 |
| `run_all.py` | **一键跑全部 8 个分析脚本** + 编号归档 + `INDEX.md` + `_meta.json` + dump 反向 link | 全套证据 + 核对闭环 |
| `uis8852_analyze.py` | **起点**：panic 现场 + 寄存器 + IRQ + 启发式回溯 + 源码定位 | 崩溃定性 |
| `unwind.py` | 帧感知栈回溯（prologue 解析）+ 当前 IRQ 权威确认 | 真实调用链、中断身份 |
| `threads.py` | 任务列表（扫描法）+ 每任务栈水位 | 栈溢出 / 死锁 / 调度异常 |
| `trace_history.py` | **任务/中断切换历史**（崩溃前调度序列 + IRQ 风暴） | WDT/死锁/饿死的时序证据 |
| `heap_state.py` | 堆状态（使用率）+ malloc trace ring + SLOG ISR 日志 | 耗尽度 + 堆消耗户 |
| `heap_walker.py` | **堆物理遍历** + av_ bin-header 完整性 | 耗尽 vs 损坏判定 |
| `wdt_reset.py` | 复位原因（gResetReson 位掩码）+ WDT 状态/寄存器 | 蓝屏 vs 真复位 / WDT 超时 |
| `code_compare.py` | **代码完整性**（ELF 代码段 vs dump）+ crash PC spotlight | 排除/确认代码损坏（EXCEPTION 必查） |
| `map_lookup.py` | **.map 符号解析**（ELF 缺失时的降级） | 仅地址→函数名（无 struct/源码行） |

### 工具链

RISC-V 工具链在 `idh.code/prebuilts/` 下（`riscv64-unknown-elf-addr2line.exe`/`objdump.exe`），脚本从 dump 目录向上查找。pyelftools 用于读 ELF `.symtab`/DWARF（脚本不依赖系统装 pyelftools 时需 `pip install pyelftools`）。

## 输出规范

- 报告路径：`.spec/bug/{工作项ID}_{问题描述}/Dump分析.md`
- **脚本输出归档**（结果主存在 bug 目录，持久）：`<bug_dir>/analysis/` 下：
  - `01_uis8852_analyze.txt` … `08_code_compare.txt`（**编号前缀**=按分析流程顺序，`ls` 自动排序）
  - `INDEX.md` — 人工索引：头部"📌 核对原始数据"段含 **dump 绝对路径** + "如何核对"指引（每结论对照哪个 `.bin` 的哪个 offset）
  - `_meta.json` — 运行元数据（dump 路径/ELF/固件版本/crash PC/时间/脚本清单），结果可追溯回原始 dump
- **dump 目录反向 link**：`<dump_dir>/_analysis_pointer.txt` 轻量指向 bug 目录结果（从原始数据侧核对时能反向找到结论）
- **覆盖更新**：同 bug 重跑 `run_all.py` 直接覆盖 `analysis/`（保持最新；多次分析可复制 bug 目录或传不同 `out_dir`）
- **跨 dump/bug 对比**：`diff <bug1>/analysis/06_heap_walker.txt <bug2>/analysis/06_heap_walker.txt` 快速看差异
- 所有地址解析标注来源（ELF 符号 / addr2line 行号 / 反汇编）
- 区分"已验证"与"推断"，证据不足标注"待验证"
- 关键结论附**反汇编/脚本输出**作为证据

## 参考文档

- `references/uis8852-memory-map.md` — 内存区/别名、关键全局符号、结构体（osAssert_t/osException_t/rt_hw_stack_frame/osThread_t/osDlmalloc_t）
- `references/riscv-exception-guide.md` — mcause/mdcause/mepc/mtval 解码、rt_hw_stack_frame 字段、abort 类型
- `references/stack-unwind-guide.md` — 帧感知回溯方法、Zcm prologue 解析、启发式扫描陷阱
- `references/heap-corruption-guide.md` — 堆物理遍历、dlmalloc chunk/bin 机制、耗尽 vs 损坏判定树、malloc trace ring
- `references/bug-report-template.md` — UIS8852 dump 分析报告模板
