---
name: spec-uis8850-dump-analyzer
description: UIS8850 / N706-STD-B41F (Unisoc, ARM Cortex-R + FreeRTOS) 平台 crash dump 分析技能。从 DTools 抓取的 ramdump（按地址命名的 .bin 内存区 + AP ELF + .map）重建死机现场：gBlueScreenRegs ARM 寄存器现场、osiPanic(udf) 蓝屏机制、gBlueScreenAbortType、FreeRTOS 任务栈溢出检测(0xa5a5a5a5 magic)、CP 核 assert 经 IPC 上报、ARM Thumb 栈回溯、堆状态、pxCurrentTCB/TCB 解析、ARM addr2line/objdump 源码映射。当用户说 "spec 分析dump"、"UIS8850 死机"、"N706 死机"、"8850 crash"、"AP PANIC"、"栈溢出"、"CP Assert"、"gBlueScreenRegs"、"osiPanic"、"vApplicationStackOverflowHook"、"FreeRTOS 栈溢出"、"0xa5a5a5a5"、"FOTA 死机" 时使用——只要意图是"定位 UIS8850/N706-STD 平台的死机根因"，即使没明说 "分析dump" 也应触发。即使用户只是粘贴了 dtools.log 或一组崩溃寄存器、或提到"设备死机需要分析 8850 dump"，也应触发。仅适用于 Unisoc UIS8850/N706-STD (ARM) 平台；UIS8852/N706C (RISC-V) 用 spec-uis8852-dump-analyzer，ASR (Cortex-R) 用 spec-asr1603-dump-analyzer，EC (Cortex-M) 用 spec-ec626-dump-analyzer。
version: 1.0
---

通过 DTools 抓取的 ramdump 文件，分析 UIS8850 / N706-STD-B41F（Unisoc ARM Cortex-R + FreeRTOS）AP 核的死机根因。

## 平台特征（与 UIS8852 RISC-V 的关键差异）

| 项 | UIS8850 (本技能) | UIS8852 (另一技能) |
|---|---|---|
| 架构 | **ARM (EM_ARM, Cortex-R)** | RISC-V RV32 |
| RTOS | FreeRTOS | RT-Thread |
| 蓝屏入口 | `osiPanic` 用 `udf #255` 触发未定义指令异常 | `osAssertHandler` 用 `ecall` 陷入 |
| 蓝屏现场 | `gBlueScreenRegs` (ARM r0-r15 + cpsr + 各模式 sp/lr/spsr, 444B) | `g_osException` + `rt_hw_stack_frame` (32 RISC-V regs) |
| abort 类型 | `gBlueScreenAbortType` (u8, 如 0xFE/0xCA) | `gBlueScreenAbortType` (0xFE=ASSERT) |
| 内存布局 | **从 MAP MEMORY + ELF PT_LOAD 读，自动注册所有 .bin** | 硬编码 8852 regions |
| 栈溢出 | FreeRTOS `0xa5a5a5a5` magic 检测 (vTaskSwitchContext) | dlmalloc assert |
| 工具链 | `arm-none-eabi-addr2line/objdump` | `riscv64-unknown-elf-*` |
| CP 核 | ARM Cortex-R，assert 经 IPC(drv_md_ipc.c) 上报 AP | (不同) |

> ⚠️ **不要套用 8852 的符号**：8850 没有 `g_osErrorLog`/`g_osAssert`/`g_osException`/`g_osIrqNo`/`g_osApSystemMem`。用 `gBlueScreenRegs`/`gBlueScreenAbortType`/`gIsPanic`/`pxCurrentTCB` 等。

## 适用场景

- 设备死机后用 DTools 抓取 ramdump（dump 目录含按地址命名的 `.bin` + `*.elf` + `*.map` + `dtools.log`）
- 需要区分根因：**任务栈溢出** / CP 核 assert / 硬件异常(UND/Data/Prefetch Abort) / 看门狗 / 协议栈逻辑
- 有 PC/LR/SP/cpsr 地址，要解码到具体函数和源码行
- 需要确定是**哪个任务**栈溢出、**哪个中断/函数**触发崩溃、**CP 是否并发 assert**

## 不适用场景

- 没有 ramdump（只有 AT/串口日志）→ 用 `spec-bug-analyzer`
- 已确认内存泄漏且要精确定位 → 用 `spec-memory-leak-analyzer`
- UIS8852(RISC-V)/ASR/EC 平台 → 用对应技能
- **CP 核崩溃根因**：本技能定位 AP 侧根因；CP assert 经 IPC 上报的信息可解析，但 CP PC=0x508069xx 在 aon_iram，代码不在 AP ELF，需 CP/Modem 符号文件单独分析

## 输入要求

| 文件 | 用途 | 必需 |
|---|---|---|
| `*.elf`（如 `8850BM_cat1bis_plus.elf`） | AP ELF（含 `.symtab` + DWARF），符号/源码定位 | 是 |
| `*.map` | 链接器 MEMORY 配置（读内存布局）+ 符号降级 | 推荐 |
| `80000000.bin` | PSRAM 转储（8MB，含堆/BSS/全局变量/任务栈） | 是 |
| `00100000.bin` | AP IRAM（16KB，含 osiPanic 等 IRAM 代码 + 部分全局量） | 是 |
| `50800000.bin` | aon_iram（CP/AON 代码区） | CP 分析时 |
| `10100000.bin` | cp_iram | CP 分析时 |
| `dtools.log` | 抓取日志，含版本校验/内存区段映射 | 强烈推荐 |
| 两版本 ELF | FOTA 升级场景判断死机时实际运行版本 | FOTA 时 |

## 内存布局：从版本文件获取（核心原则，不硬编码）

**同一平台不同项目裁剪后内存布局会变**，必须从版本文件读，不能硬编码 region 列表。三个来源交叉验证：

1. **MAP `MEMORY` 配置**（最权威）：MAP 文件开头 `Memory Configuration` 段，列 `Name Origin Length Attributes`（flash/ram/sram）。
2. **ELF `PT_LOAD` 段**：`readelf -l` 或 pyelftools 读 program headers，得运行时加载地址（`.text`/`.data`/`.bss` 落点）。
3. **dump 目录 `.bin` 文件名**：DTools 约定文件名 = hex 基址，文件大小 = 区段长度。

**读取全局量的方法**：自动注册 dump 目录所有 hex 命名 `.bin`（`Mem(dump_dir, regions=[], scan_all_peripherals=True)`），按 `[base, base+len)` 建索引。这完全平台/项目无关，任何裁剪都能工作。符号地址从 ELF `.symtab` 读，落在哪个 `.bin` 就从哪个读。

8850 典型布局（003 版本实测，仅作示例，实际以版本文件为准）：
```
MAP MEMORY:  flash 0x60020000 len=0x24c000 (XIP .text)  ram 0x802ec000 len=0x514000 (PSRAM 数据/堆/BSS)  sram 0x00100000 len=0x4000 (IRAM)
ELF PT_LOAD: .text @0x60027340  .data @0x802fdfe0  .bss @0x803099e0
dump .bin:   80000000.bin(PSRAM 8MB)  00100000.bin(IRAM 16KB)  50800000.bin(aon_iram)  10100000.bin(cp_iram)
```

> AP ram 区从 `0x802ec000` 起，**不是** `0x80000000`——`0x80000000~0x802ec000` 是 CP/共享区。硬编码会踩坑。

## 快速开始

**一键跑全部分析脚本，输出自动归档 + INDEX.md**：
```bash
SKILL_DIR="<本技能目录>"
python "$SKILL_DIR/scripts/run_all.py" <dump_dir> <ap.elf> <bug_out_dir> [--elf2 <另一版本elf>] [--map <map>]
# 例: python scripts/run_all.py .spec/logs/8850_xxx/ 8850BM_cat1bis_plus.elf .spec/bug/fota_xxx/ --elf2 002.elf
```

**或单步起步**（先定崩溃性质）：
```bash
python "$SKILL_DIR/scripts/uis8850_analyze.py" <dump_dir> <ap.elf> [--elf2 <另一版本elf>]
```
一键输出：版本判定（双 ELF + PSRAM 搜串）、gIsPanic、gBlueScreenAbortType、gBlueScreenRegs(ARM 现场)、osiPanic 确认、栈溢出检测点、pxCurrentTCB/任务名。这是分析**起点**。

## 执行流程

> **场景路由**（按 `gBlueScreenAbortType` + `gIsPanic` + AP PC 决定重点）：
>
> | 场景 | 触发信号 | 重点步骤 |
> |---|---|---|
> | **AP PC=osiPanic** | PC 在 `osiPanic`(0x1006xx) | 2→3→4→5→6（栈回溯找 osiPanic 调用者 → 定根因） |
> | **栈溢出** | 调用者是 `vTaskSwitchContext`/`vApplicationStackOverflowHook` | **6(栈溢出专项)** → 7(任务列表) |
> | **堆耗尽** | 调用者含 `osiMalloc`/`dlmalloc`/`OS_ASSERT(0)` 在 malloc 内 | **7b(堆分析 heap_state)** → gOsiHeaps 使用率 + gOsiMemRecords 消耗户 |
> | **CP assert 上报** | PSRAM 含 "CP Assert" | **8(CP assert 专项)** → CP 栈回溯 |
> | **硬件异常** | abort=UND/DataAbort/PrefetchAbort 码, cpsr 模式=ABT/UND | 2→3→5（反汇编崩溃指令） |
> | **看门狗/复位** | gIsPanic=0 或 WDT int_sts 置位 | **9(wdt_reset)** → 7(任务列表找死锁/饿死) |
> | **上电即死** | xTickCount 很小（<5s） | 排查初始化阶段 |
>
> 通用必做：Step 1（版本校验）、Step 2（现场）、Step 5（源码定位）、Step 10（报告）。

### Step 1：文件扫描 + 版本判定

- Glob 扫 dump 目录，识别 .bin/.elf/.map/dtools.log
- **版本判定（FOTA 场景关键）**：dtools.log 若有 `gBuildRevision in _elf_ vs board` 比对则直接用；否则：
  - PSRAM 全文搜版本串前缀（如 `8850BM_cat1bis_plus`）—— 不依赖 ELF，最可靠
  - 用每个候选 ELF 读 `gBuildRevision` 地址，读出的串即板上实际版本
  - **降级方案（gBuildRevision 不可区分时，重要）**：两版 ELF 的 gBuildRevision 可能映射到同一 PSRAM 地址、串完全相同（无法区分）。此时用**代码字节差异法**——崩溃返回地址（如 osiPanic 调用者 sp+4）必须匹配运行版本的代码布局：用两版 ELF 分别反汇编 caller-4 处，看 bl 目标是否在崩溃路径上（如 `vApplicationStackOverflowHook`）。`uis8850_analyze.py --elf2` 自动做此交叉验证并判定运行版本。
- ⚠️ **dtools.log 多会话混杂**：dtools.log 常累积多次抓取会话（不同平台/项目/时间），必须按**本次 dump 目录名的时间戳/平台**过滤对应会话段，勿被历史会话（如其他项目的版本校验/蓝屏信息）误导。
- 读 dtools.log 的 cfg 段（`[cfg:N] name=psram addr=... size=...`）作内存布局交叉验证

### Step 2：解析蓝屏现场（gBlueScreenRegs + gBlueScreenAbortType）

```bash
python "scripts/uis8850_analyze.py" <dump_dir> <ap.elf>
```
脚本提取：
- `gBuildRevision`（板上版本串）、`gIsPanic`、`gBlueScreenAbortType`（u8）
- `gBlueScreenRegs`（444B，DWARF 读 struct 偏移）：`r[16]`(+0x0) + `d[]`(+0x40) + `cpsr`(+0x140) + `fpscr`/`epsr` + 各模式 `sp/lr/spsr`(usr/hyp/svc/abt/und/mon/irq/fiq)
- **AP PC/LR/SP/cpsr** → 判断是 osiPanic(软件) 还是硬件异常
- `osiPanic` 反汇编确认（`udf #255` = 0xdeff 结尾）

> ⚠️ **gBlueScreenRegs 结构从 DWARF 读**（`struct_offsets`），不要硬编码偏移——不同编译选项字段数会变。关键字段：`r`@+0x0（r0-r15）、`cpsr`@+0x140。
>
> ⚠️ **cpsr 解码**：M[4:0] 模式（0x13=SVC/0x1f=SYS/0x17=ABT/0x1b=UND/0x12=IRQ/0x11=FIQ）、T(bit5)=Thumb、I(bit7)/F(bit6) 中断屏蔽、A(bit8)。osiPanic 现场 cpsr 常为 SYS+Thumb+I=1。

### Step 3：abort 类型路由 + osiPanic 机制

`gBlueScreenAbortType` 路由：
- AP PC 在 `osiPanic` → 软件主动 panic（非硬件异常），根因在**调用者**（Step 4 回溯）
- AP PC 在某业务函数 + abort=异常码 → 硬件异常（UND/DataAbort/PrefetchAbort），反汇编崩溃指令

**osiPanic 机制**（8850 蓝屏核心）：
```
某代码 bl osiPanic
  → osiPanic: push{r3,lr}; bl osiTrace; bl osiProfileCode; udf #255
    → udf 触发未定义指令异常
      → 异常向量保存完整现场到 gBlueScreenRegs, 设置 gBlueScreenAbortType/gIsPanic=1
        → osiBlueScreen 死循环蓝屏
```
故 gBlueScreenRegs.PC = osiPanic 内 `udf` 指令地址（如 0x1006c6）。**调用者 LR 在 osiPanic 的 push 帧（sp+4）**，不在 gBlueScreenRegs.lr（已被覆盖）。

### Step 4：ARM Thumb 栈回溯（找 osiPanic 调用者）

```bash
python "scripts/unwind.py" <dump_dir> <ap.elf>
```
- 从 gBlueScreenRegs.SP 出发，osiPanic `push {r3,lr}` → 调用者 LR 在 `sp+4`
- 逐帧上溯：对每帧 objdump prologue 提取 frame size + lr 保存偏移（ARM Thumb: `push {...,lr}` + `sub sp,#N`）
- **手动验证 call site**：每个候选返回地址 V，反汇编 V-4/V-2 处必须是 `bl`/`blx`/`b` 指令

> ⚠️ ARM 不强制 frame pointer，纯启发式"栈上找代码地址"有噪声。**永远用反汇编确认 call site**。栈上代码地址双向核对："栈地址 A 的值 V → V 的函数调用了 A 处帧的所有者"。

### Step 5：符号 + 源码定位

```bash
arm-none-eabi-addr2line -f -e <ap.elf> 0xADDR1 0xADDR2 ...
```
必须解析：AP PC、LR、栈帧所有代码地址、gBlueScreenRegs 里的代码地址。**Thumb 地址低位=1，addr2line 前 `& ~1`**。

### Step 6：栈溢出专项（8850 高频根因）

若 osiPanic 调用者是 `vTaskSwitchContext`/`vApplicationStackOverflowHook` → **FreeRTOS 栈溢出**。

```bash
python "scripts/threads.py" <dump_dir> <ap.elf>
```
- 反汇编 `vTaskSwitchContext` 找 `cmp r?,#0xa5a5a5a5`（栈底 magic 检测点）
- 确认 `vApplicationStackOverflowHook` → `osiPanic`（常为 tail-call `b.w __osiPanic_veneer`）
- 读 `pxCurrentTCB` → 当前任务 TCB → 任务名(TCB+0x34)、`pxTopOfStack`(TCB+0)、`pxStack`(TCB+0x30, 栈底)
- **判定**：`pxTopOfStack < pxStack` → 栈顶越过栈底边界 = **该任务自己栈溢出**；栈底 magic 区被破坏的具体值（常含越界写入的代码地址/数据）

> FreeRTOS TCB 结构（DWARF 确认）：`pxTopOfStack`@+0、`xStateListItem`@+4、`xEventListItem`@+0x18、`uxPriority`@+0x2c、**`pxStack`@+0x30**、`pcTaskName`@+0x34。
>
> 栈溢出检测机制：任务创建时栈填充 `0xa5a5a5a5`，`vTaskSwitchContext` 切换时检查栈底若干字是否仍为 magic。被覆盖 → `vApplicationStackOverflowHook` → `osiPanic`。

### Step 7：任务列表 + 栈水位

```bash
python "scripts/threads.py" <dump_dir> <ap.elf>
```
- 扫描 PSRAM 堆区找所有 TCB（特征：pxTopOfStack 合法 + pcTaskName ASCII + 邻接 magic 栈）
- 每任务：名、优先级、`pxStack`/`pxTopOfStack`、栈水位、栈底 magic 是否完好
- 找栈水位最低 / magic 被破坏的任务

### Step 7b：堆分析（堆耗尽根因）

```bash
python "scripts/heap_state.py" <dump_dir> <ap.elf>
```
- 读 `gOsiDefaultHeap` → 堆描述符（base/end/size/used），算使用率（>90% 高度疑似堆耗尽）
- 读 `gOsiMemRecords`（malloc trace ring, 8192B）+ `gOsiMemRecordPos`，按调用者统计堆消耗户 Top N
- `cp_heap_base`/`cp_heap_limit` 看 CP 堆范围
- **判定**：osiPanic 调用链含 `osiMalloc`/`dlmalloc`/malloc 内 assert + 使用率>90% → 堆耗尽；物理遍历发现 chunk 异常 → 堆被越界写粉碎

> malloc trace ring 末尾不是崩溃时刻的 alloc（崩溃在 malloc 内未返回）。高频调用者反映崩溃前堆分配最频繁的路径。若含协议栈数据通路（pbuf/PDCP/lwip/PS data）+ 大流量下载，结合使用率判断是否堆耗尽。

### Step 8：CP assert 专项（8850 特有）

若 PSRAM 含 "CP Assert"/"AP PANIC" 文本 → CP 核 assert 经 IPC 上报 AP。

```bash
python "scripts/cp_assert.py" <dump_dir> <ap.elf>
```
- 全文搜 `CP Assert reg:` 提取 CP 寄存器 r0-r17（r13=SP/r14=LR/r15=PC/r16/r17=CPSR/SPSR）
- 读 `g_CpMdVersion`（CP 固件 svn 版本号，如 0xed91=60817）
- 反汇编 CP PC（@ aon_iram 0x50800000 区，ARM Thumb，对齐 `& ~1`）—— 常是 CP 异常处理读 CP15（SCTLR/TTBR0/DFSR/FAR/IFSR/IFAR）
- 上报路径：CP assert → IPC → AP `drv_md_ipc.c`(`ipc_notify_cp_assert`/`ipc_show_cp_assert`) → AP 记录 "AP PANIC"

> CP 代码在 aon_iram/cp_iram，**不在 AP ELF**，无法符号化 CP 函数。只能反汇编裸字节看崩溃指令类型。CP 根因需 CP/Modem ELF。

### Step 9：复位原因 + WDT（复位/看门狗场景）

```bash
python "scripts/wdt_reset.py" <dump_dir> <ap.elf>
```
- `gIsPanic=1` → 蓝屏 panic（本次崩溃，WDT 寄存器是历史）；`gIsPanic=0` → 设备已复位
- 解析 WDT 寄存器（`50039000.bin`：ctrl 使能位、int_sts 触发位、val 计数）；WDT 寄存器全 0 = WDT 未启用
- 复位原因位掩码（`gResetReson` 若存在）：bit0 POR / bit1 EXT / bit2 SW / bit3 APSS_WDT / bit4 CPSS_WDT
- WDT 寄存器全 0 且 gIsPanic=1 → 排除 WDT 复位（本次是软件 panic）

> 8850 实测常无 `gResetReson` 全局（复位原因在 sysnv/PCU 寄存器）。WDT 寄存器全 0 是 panic 场景的典型特征（蓝屏前常停 WDT）。

### Step 9b：运行时长 + 初始化阶段判断

`uis8850_analyze.py` 读 `xTickCount`（FreeRTOS 系统滴答，通常 1ms）：
- <5s → 上电/初始化阶段即死，排查初始化流程
- 较久 → 运行态死机（栈溢出/堆耗尽/逻辑错误等）

> 注：xTickCount 是 32 位，约 49 天回绕。压测场景设备可能多次重启，tick 反映本次启动后时长。
>
> ⚠️ **xTickCount 可能与 trace tick 矛盾**：panic 路径可能改写 xTickCount，或多次重启使 tick 复位，导致 xTickCount 偏小（误判"上电即死"）。若 xTickCount 与 trace 事件 tick（`trace_decode.py` 输出的 record tick）不一致，**以 trace tick 为准**——trace tick 是每条事件记录时的真实滴答，更可信。xTickCount 仅作弱参考。

### Step 9c：OSA trace 解码（所有场景的辅助利器）

```bash
python "scripts/trace_decode.py" <dump_dir> <ap_elf> [--last 60] [--no-kw]
```
解码 `gTraceBuf`（192KB 二进制 trace ring）为**崩溃前事件流**。record 格式（从 osiTraceBufInit/Put/prvTraceIdEx 逆向）：
```
[counter(u32) | total_len(u16) | 0x0098 | 0x9198 | len-8(u16) | tick(u32) | trace_id(u32) | payload]
```
payload 内嵌格式化 trace 文本（`[文件:行 函数]消息`）+ 二进制参数。脚本用 magic 对(0x0098+0x9198)+len 一致性严格识别 record，按 tick 排序输出崩溃前事件。

> **trace 是定位根因的强辅助**：即使栈溢出/堆耗尽等根因已定，trace 能还原崩溃前最后的调用活动（哪个 socket/文件/函数在跑），印证根因方向。trace_id 是 trace 点 hash，频次反映最活跃代码路径。本案例实测还原出崩溃前 FTP 下载 socket 读循环（`nwy_dss_read sockfd=2 ret=2720/8192/1920` → `malloc 对应大小` → `nwy_ftp_data.cpp:206 download status=9`），与栈溢出根因完全吻合。详见 [[heap-and-trace-guide]] 的 trace 解码章节。

### Step 10：报告

用 `references/bug-report-template.md` 模板，输出到 `.spec/bug/{工作项ID}_{问题描述}/Dump分析.md`。

**归档**：脚本输出归档到 bug 目录 `analysis/`（编号前缀 + `INDEX.md` 含 dump 绝对路径与核对指引 + `_meta.json`）；dump 目录写 `_analysis_pointer.txt` 反向链接。

## 工具使用规则

### 脚本路径
所有脚本在本技能 `scripts/` 下。系统加载技能时提供 "Base directory"，执行时用完整路径：
```bash
SKILL_DIR="<Base directory for this skill>"
python "$SKILL_DIR/scripts/uis8850_analyze.py" <dump_dir> <ap.elf>
```

### 脚本速查

| 脚本 | 用途 | 关键产出 |
|---|---|---|
| `common.py` | 公共模块：Mem(自动注册 .bin)、Symbols(symtab+DWARF)、ARM 工具链查找、addr2line/objdump | 不直接运行 |
| `run_all.py` | **一键跑全部** + 编号归档 + INDEX.md + _meta.json | 全套证据 |
| `uis8850_analyze.py` | **起点**：版本判定 + gBlueScreenRegs + osiPanic + 栈溢出检测点 + pxCurrentTCB | 崩溃定性 |
| `unwind.py` | ARM Thumb 栈回溯（prologue 解析） | osiPanic 调用链 |
| `threads.py` | FreeRTOS 任务列表(TCB扫描) + 栈水位 + magic 检查 | 栈溢出任务定位 |
| `cp_assert.py` | CP assert 上报解析 + CP 寄存器 + CP PC 反汇编 + CP 栈回溯 | CP 并发 assert |
| `heap_state.py` | 堆描述符(gOsiDefaultHeap)使用率 + malloc trace ring(gOsiMemRecords) 消耗户统计 | 堆耗尽根因 |
| `wdt_reset.py` | gIsPanic 判蓝屏/复位 + WDT 寄存器(50039000.bin) + 复位原因位掩码 | WDT 超时/复位 |
| `trace_decode.py` | **OSA trace 二进制解码**(gTraceBuf) → 崩溃前事件流(record格式逆向) | 还原崩溃前调用活动/源码位置 |
| `map_lookup.py` | .map 符号降级（ELF 缺失时） | 地址→函数名 |

### 工具链
ARM 工具链在项目 `prebuilts/<os>/gcc-arm-none-eabi/.../bin/`（`arm-none-eabi-addr2line.exe`/`objdump.exe`），脚本从 dump 目录向上查找。pyelftools 读 ELF symtab/DWARF。

## 输出规范

- 报告路径：`.spec/bug/{工作项ID}_{问题描述}/Dump分析.md`
- 脚本输出归档：`<bug_dir>/analysis/` 下 `01_*.txt`…`NN_*.txt` + `INDEX.md`（含 dump 绝对路径 + 核对指引）+ `_meta.json`
- dump 反向 link：`<dump_dir>/_analysis_pointer.txt`
- 所有地址解析标注来源（ELF 符号 / addr2line 行号 / 反汇编）
- 区分"已验证"与"推断"，证据不足标注"待验证"
- 关键结论附反汇编/脚本输出作证据

## 参考文档

- `references/uis8850-memory-map.md` — 内存布局从 MAP/ELF 获取方法、8850 区段、关键全局符号
- `references/arm-bluescreen-guide.md` — osiPanic(udf)/gBlueScreenRegs/gBlueScreenAbortType 蓝屏机制、cpsr 解码、TCB 结构
- `references/freertos-stack-overflow.md` — 0xa5a5a5a5 magic 检测、vTaskSwitchContext/vApplicationStackOverflowHook、栈溢出判定树
- `references/arm-stack-unwind-guide.md` — ARM Thumb prologue 解析、栈回溯、call site 验证
- `references/cp-assert-guide.md` — CP assert IPC 上报机制、CP 寄存器解读、CP15 故障寄存器
- `references/heap-and-trace-guide.md` — 堆状态(gOsiHeaps)、malloc trace ring(gOsiMemRecords)、OSA trace(gTraceBuf 二进制)
- `references/bug-report-template.md` — 8850 dump 分析报告模板
