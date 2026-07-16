---
name: spec-qcx216-dump-analyzer
description: QCX216 / N706D（Unisoc ARM Cortex-M3 + FreeRTOS）平台 crash dump 分析技能。从 Unisoc DTools 抓取的 RamDumpData_*.bin 重建死机现场：excepInfoStore 异常存储解析、ASSERT(Func/Line/Val)/HardFault 识别、PC/LR→源码行映射（pyelftools，无需 ARM 工具链）、FreeRTOS 当前任务与栈溢出扫描、调用链还原。当用户说「spec 分析dump」「QCX216 死机」「N706D 崩溃」「Unisoc ARM dump」「excepInfoStore」「OsaCreateFastSignal assert」「AP assert」「interrupt assert」「RamDumpData」「设备死机需要分析 QCX216/N706D dump」时使用——只要意图是「定位 QCX216/N706D 平台的死机根因」，即使没明说「分析dump」也应触发。即使用户只是粘贴了一组寄存器值或提到设备死机需要分析，也应触发。仅适用于 QCX216/N706D（Unisoc Cortex-M3 + FreeRTOS）；ASR(Cortex-R) 用 spec-asr1603-dump-analyzer，EC(Cortex-M+EC工具链) 用 spec-ec626-dump-analyzer，UIS8852/N706C(RISC-V) 用 spec-uis8852-dump-analyzer。
---

# QCX216 / N706D Dump 分析

分析 **QCX216 / N706D**（Unisoc ARM Cortex-M3 + FreeRTOS）平台的死机 dump。从
Unisoc DTools 抓取的 `RamDumpData_*.bin` 重建死机现场，定位根因。

## 平台识别（先确认再用本技能）

本技能**仅**适用于 QCX216 / N706D。判定证据（满足任一即可）：
- 构建产物 `ap_at_command.elf` 为 `ELF 32-bit LSB, ARM, EABI5`，且 `e_machine = EM_ARM(0x28)`
- 诊断工具是 Unisoc 套件：`EPAT.log` / `SigLogger.log` / `UnilogViewer.log` / `comdb.txt`
- 平台宏 `PLAT_QCX216`、型号 `NWY_MODEL "N706D"`、SoC `NWY_SOC_MODEL "QCX216"`
- RTOS 为 FreeRTOS（`OS = freertos`、`PLAT/os/freertos`、`libfreertos.a`）

> **边界**：同为 Cortex-M 但用 EC 工具链/excep_store 的（EC626/EC616）→ `spec-ec626-dump-analyzer`；
> ASR(Cortex-R + TRACE32) → `spec-asr1603-dump-analyzer`；UIS8852/N706C(RISC-V) → `spec-uis8852-dump-analyzer`。

## 输入要求

用户提供 dump 文件所在目录或文件路径。文件识别：

| 文件 | 用途 | 必需 |
|------|------|------|
| `RamDumpData_*.bin` | RAM 完整转储（base=0x0，偏移==物理地址，含 Flash 代码 + RAM） | 是 |
| `ap_at_command.elf` | **崩溃固件** ELF（符号表 + DWARF 调试信息），必须与崩溃时刻版本一致 | 是 |
| `ap_bootloader.elf` | bootloader ELF | 可选 |
| `comdb.txt` | Unisoc 日志 ID 映射表 | 可选 |
| `*.map` | GCC MAP（本技能以 ELF 的 .symtab 为准，MAP 非必需） | 可选 |

> **版本一致性是地址映射的前提**：务必使用 **dump 同目录下的 ELF**（崩溃固件编译产物），
> 不要用 `PLAT/gccout/` 下当前编译的 ELF——若代码有变动，符号地址会不匹配，定位全错。
> 校验方法：对比 dump 目录 ELF 与 gccout ELF 的修改时间/MD5，应不同（dump 的更旧）。

## 工具链约束（重要）

QCX216 开发机通常**没有** ARM binutils（`arm-none-eabi-addr2line/nm/objdump`）。本技能**不依赖任何
专有工具（Unisoc DTools/Catcher 等）**，仅需 Python + pip 包：
- 地址→符号/源码行：**pyelftools** 读 ELF `.symtab` + `.debug_line`（`pip install pyelftools`，替代 nm/addr2line）
- **反汇编**：**优先用 capstone**（`pip install capstone`，替代 objdump，完整解 Thumb-2 含 ITE 条件块/
  MSR/宽指令，连续反汇编保留 IT 上下文）；**capstone 不可用时降级到内置纯 Python Thumb-2 反汇编器**
  （覆盖调试高频指令，罕见指令降级 `.short/.word`，但**漏解 ITE 条件块**——曾因此误判 poolId，见下）。
  → **强烈建议装 capstone**：`pip install pyelftools capstone`，反汇编更准、避免条件指令误判。
- 因此**必须**用含 DWARF 信息的 ELF（dump 目录里的 `ap_at_command.elf` 自带完整 `.debug_line`）。

> ⚠️ **为何推荐 capstone**：纯 Python 反汇编器漏解 `ITE LS` 条件执行块（把 `MOVLS/MOVHI` 当普通
> MOV），曾导致误判 `OsaCreateFastSignal` 的 poolId（误为 2，实为 1）。capstone 正确解条件指令，
> 已集成（检测到即用，连续反汇编显示条件后缀）。

## 执行流程

### Step 1：定位文件

确认 dump 路径与**崩溃固件 ELF** 路径（dump 同目录）。若用户只给了目录，按上表识别。

### Step 2：一键全流程分析（核心步骤）

```bash
SKILL_DIR="<本技能 scripts 目录>"
PY="/c/Users/20220715012/AppData/Local/Programs/Python/Python312/python"
"$PY" "$SKILL_DIR/qcx216_dump_analyzer.py" full-analyze <RamDumpData_*.bin> --elf <ap_at_command.elf>
```

`full-analyze` 自动执行：平台符号定位 → excepInfoStore 解析 → 异常类型识别 →
ASSERT(Func/Line/Val)/调用链解码 → 当前任务 → 栈溢出扫描 → 根因小结。

### Step 3：按异常类型解读

`full-analyze` 输出的 `>> Exception Type` 决定分析方向：

| 类型 | 判据 | 分析重点 |
|------|------|---------|
| **ASSERT** | excepInfoStore 含 assert 文本（`Func:/Line:/Val:`） | 看 `Func`（多为 Unisoc OSA/协议栈 API）+ `Val` + 调用链候选地址；`context: interrupt` 表示发生在中断里 |
| **HardFault** | magic 有效但无 assert 文本 | 解读寄存器快照区代码地址作为 PC/LR/调用链；fault status 需结合 Cortex-M 指南（见 references） |
| **Unknown** | 无有效 magic / 无 assert | 可能是静默复位 / 看门狗 / 无异常数据，需结合 EPAT `Communicatios.log`(UTF-16LE，grep 前先 `iconv`) 排查 |

### Step 4：调用链与源码定位

`### Code addresses captured in exception store` 列出异常时刻寄存器快照区里捕获的
Flash 代码地址（已过滤函数指针表噪音），按 `store+off` 排列。其中：
- 含 assert `Func` 名的地址 = **触发点**（如 `OsaCreateFastSignal+0x60`）
- 中断入口类地址（如 `XXX_interruptHandler`）= 证实 `interrupt` 上下文

用 `resolve` 单独解码任意地址：
```bash
"$PY" "$SKILL_DIR/qcx216_dump_analyzer.py" resolve 0x0041BB05 0x00003306 --elf <elf>
```

### Step 5：栈与任务

`### Task stack overflow scan` 给出所有任务栈的使用率与溢出判定：
- `OVERFLOW (sentinel corrupted)` = 栈底 0xA5A5A5A5 被踩，**栈溢出确认**
- `HIGH RISK (>95%)` / `WARNING (>80%)` = 使用率告警
- `NO SENTINEL (non-task/MSP?)` = 该栈未用 0xA5 填充（如 MSP 主栈 `__mcu_stack`），**无法用哨兵判定，不是溢出**
- `OK` = 排除栈溢出

### Step 6：根因定位 + 报告归档

按决策树（见 `references/qcx216-platform-reference.md`）定根因，输出复现路径。

**报告路径**：`.spec/bug/{工作项ID}_{问题描述}/Dump分析.md`（模板见 `references/dump-report-template.md`）。
**Dump 归档**：复制 `RamDumpData_*.bin` + 崩溃 ELF 到 `.spec/bug/{工作项ID}_*/dump/`。
**工作项 ID**：用户未提供时必须询问（常为飞书 ID，如目录名 `7031160371`）。

## 脚本速查

```bash
PY="/c/Users/20220715012/AppData/Local/Programs/Python/Python312/python"
"$PY" <SKILL_DIR>/qcx216_dump_analyzer.py <子命令> [选项]
```

| 子命令 | 用途 |
|--------|------|
| `full-analyze <dump> --elf <elf>` | 一键全流程（异常+触发点反汇编+任务+栈+根因） |
| `parse-excep <dump> --elf <elf>` | 仅解析 excepInfoStore + 根因小结 |
| `resolve <addr>... --elf <elf>` | 地址 -> 符号 / 源码行（替代 addr2line） |
| `scan-stacks <dump> --elf <elf>` | 任务栈溢出扫描 |
| `scan-osa-pool <dump> --elf <elf>` | OSA 协议栈专用内存池扫描（signal 池耗尽/泄漏） |
| `disasm <addr> --dump <dump> --elf <elf>` | 反汇编地址附近指令（capstone 优先；超 dump 范围如 0x8Cxxxx 协议栈段自动回退 ELF section） |
| `run_all <dump> <elf> <out_dir>` | **一键归档**：跑全部分析→`<out_dir>/analysis/NN_*.txt` + `_meta.json` + `INDEX.md` + dump 反向指针（留痕/核对，参考 uis8852 设计） |

### 脚本内部 API（深挖时直接 import 调用）

需要自定义深挖（如查特定符号、读任意地址、批量反汇编）时，可直接 import 各模块：

```python
from qcx216_common import DumpReader, u32, u16, u8   # u32/u16/u8(data, off) 模块级 helper
from qcx216_elf import ElfReader
from qcx216_excep import parse_excep, format_excep
from qcx216_tasks import enumerate_tasks, backtrace, analyze_stack
from qcx216_disasm import ThumbDisasm
from qcx216_heap import analyze_heap
from qcx216_fault import decode_cfsr, decode_hfsr, parse_exception_frame
```

| 对象 | 常用方法 |
|------|---------|
| `DumpReader(path, base=0)` | `.data`(bytes)、`.u32(addr)`/`.u16`/`.u8`（按物理地址读）、`.read(addr,size)`、`.size` |
| `ElfReader(elf_path)` | `.find_symbol(name)`→`Symbol(addr,size,is_func)`；`.sym_at(addr)`→`Symbol`；`.locate(addr)`→dict(symbol/sym_offset/file/line/is_code)；`.is_code(addr)`/`.is_ram(addr)`；`.find_symbols(regex)`；`.platform_config()` |
| `Symbol` | `.name`/`.addr`/`.size`/`.is_func` |
| `ThumbDisasm(mem)` | `mem`=按地址读 u8 的函数（`DumpReader.u8` 即可）；`.disasm_one(addr)`→(size,text)；`.format_around(center, sym_resolver=elf.locate)` |

> 注意：`ElfReader` 的 DWARF 行号表是**懒加载**（首次 `line_at`/`locate` 才构建，约 25s）；
> 只需符号解析的场景（如 `disasm`）用 `sym_at`/`find_symbol` 可秒级返回。

## 平台关键事实（分析时务必记住）

1. **dump base=0x0**：`RamDumpData_*.bin` 是从物理地址 0 开始的统一地址空间转储
   （向量表 + Flash 代码 + RAM），**偏移 == 物理地址**。开头第一个 u32 是初始 SP，
   后续是 Cortex-M 向量表（NMI/HardFault/... 桩地址）。
2. **excepInfoStore** 在 RAM（符号 `excepInfoStore`，348 字节），首字 `magic1=0xEC112013`
   表示有效异常转储。结构：magic + header + SP 快照(+0x20/+0x40) + ISR 入口(+0x34) +
   PC(+0x48) + 内存区段(+0x90) + 时间戳(+0xC4) + assert 文本(+0xCC)。
3. **调用链靠栈扫描**：Cortex-M 默认无帧指针，从异常 SP 向栈顶扫描代码地址还原调用链。
   中断上下文 SP 在 MSP（`__StackLimit`~`__StackTop`）；ASSERT 是软件触发无硬件栈帧。
   QCX216 是 AP/CP 双核，常见触发源：**ACIpcAlone1Isr/IpcC2AMsg2Errc**（CP→AP 核间通信）。
4. **OSA API 多为二进制库**：`OsaCreateFastSignal`/`OsaCreateIsrSignal` 等位于
   `PLAT/os/osa/`，但 `osasig.c` 源码不在仓内（仅 `PLAT/prebuild/PLAT/inc/osasig.h`），
   DWARF 行号仍能映射到构建时的 `.c` 文件名:行号，用于定位但不能看源码。
5. **FreeRTOS TCB**：`pxTopOfStack`=TCB+0、`pxStack`=TCB+0x30、`pcTaskName`=TCB+0x34；
   `pxCurrentTCB` 是指针变量需先解引用。全任务枚举靠扫描 RAM（栈底 `0xA5A5A5A5` 哨兵 +
   ASCII 名特征）。典型任务：`Ccm/Cemm/Cerrc(RRC)/Ceup`（协议栈）、`Uicc*`（SIM）、
   `tcpip_thread`、`lfs`、`Ccio*`（串口收发）、`nwy_*`（Neoway）。
6. **Heap 分析靠 TLSF 遍历**（不是 psSlp2）：主应用堆是 TLSF（`pool_group.node[0]`），
   MM_DEBUG 块头含 `alloc_owner = funcPtr|taskNum`，遍历物理块可得 used/free/碎片化/归属TOP。
   ⚠️ `psSlp2FreeBytesRemaining` 是 **sleep/retention 另一堆**，非主堆，**勿当主堆 free 误读**
   （曾导致「94.4% 紧张」的错误结论；主堆实测 53% 健康）。判断「不足 vs 碎片化」：
   used%≥95 不足；总 free 够但最大连续块小(碎片化>50%)则碎片化；都不达标才健康。
7. **Reset/WDT**：`ramRstReason`(复位原因，异常后可能被覆盖成 magic)、`gWdtDataBase`、
   `hibresetcnt`、`gPendingReset`。SCB fault 寄存器(CFSR/HFSR @0xE000EDxx)**不在 dump**，
   HardFault 的 fault 值须从 excepInfoStore 转储找。
8. **AP/CP 双核**：本技能分析 AP 核（`ap_at_command`）dump；CP 核协议栈日志在 `comdb.txt`/SigLogger。
9. **OSA 协议栈专用池（`OsaCreate*Signal` assert 必查）**：`OsaCreateFastSignal` 等不走主 TLSF 堆，
   而调 `OsaMemPoolIdAlloc` 从 `osaMemPoolDescList[3]` 专用池分配。**poolId 动态选择**（`ITE LS`：
   `sigBodySize+4≤36`→pool[1] 36B 小信号池；`>36`→pool[2] 132B）。block 布局 `+0 magic(0xD5E9,固定·非free标记)
   +2 poolId +3 flag(1=free/2=alloc) +4 sigId +6 sigBodyLen`，stride=blksize+4，满判据看 desc 簿记(freeHead/used)。
   `scan-osa-pool`/`full-analyze` 自动读 desc 判满 + 按块归类堆积 sigId。`osasig.c:146` assert =
   `OsaMemPoolIdAlloc` 返回 NULL（freeHead==NULL）。⚠️ 根因定位见 `references` §6「OSA signal 全链路分析」：
   sigId 可由 IPC msgId 动态计算（搜不到立即数时反汇编创建函数如 `IpcC2AMsg2Cms`）；销毁路径反汇编目标任务
   （常超 dump，用 `disasm` 的 ELF 回退）；销毁正确→生产/消费速率失配（非泄漏），漏调→泄漏。

## 输出示例（ASSERT in OsaCreateFastSignal）

```
## Exception Store @0x004232F4
  >> Exception Type: ASSERT
  ### ASSERT info  Func:OsaCreateFastSignal  Line:146  Val:0xc,0x0,0x0  Context:interrupt
  ### SP candidates: 0x0046388C/0x00463858 (MSP)
== Root-Cause Summary ==
  Trigger : 0x0041BB05 -> OsaCreateFastSignal+0x61  [osasig.c:146]

## Disassembly around trigger
     0x0041BB00:  BL 0xA7ADB0  ; excepHardFaultHandler
  >> 0x0041BB04:  B 0x41BB04   ; OsaCreateFastSignal   ← B . 死循环（触发点）

## ASSERT Failure Point (inferred)      ← 自动推理 assert 真实失败点
  BL 0x41BCE8 @0x0041BAE4 -> OsaMemPoolIdAlloc @line 146 ★ 本次触发
    判定: CBNZ r0, 0x41BB06  (r0==0 进入 assert)

## Call Chain (stack backtrace)
    0  MPDMA_interruptHandler  [dma.c]   1  IpcC2AMsg2Errc [acipcapi.c]  ← CP→AP IPC
    2  ACIpcAlone1Isr          [acipcapi.c]   3  XIC_IntHandler [ic.c]
  ### Interrupt source: ACIpcAlone1Isr -> AC 核间通信(IPC) 中断1 (CP→AP 消息)

## Heap Utilization (TLSF traversal)    ← 主堆（非 psSlp2）
  Total: 213000  Used: 113596 (53.3%)  Free: 99404  Max free block: 91652  Frag: 7.8%
  >> 判定: 堆健康(53.3% used, 碎片化 7.8%)   ← 主堆不缺、不碎片化

## OSA Signal Memory Pools              ← OSA 专用池（OsaCreate*Signal 用，独立于主堆）
  OsaCreateFastSignal(sigBodySize=12) → +4=16≤36 ⇒ poolId=1
  pool[1] 36B/32槽: used=32/32 freeHead=NULL  *** 满 ***
    └ 堆积 sigId 归类 (stride=40, flag 全=2 alloc):
       sigId=0x0949 × 31  (反汇编 IpcC2AMsg2Cms: =(msgId-0x27BD), msgId=0x3106)
       sigId=0x094D × 1
  >> 根因方向：sigId=0x0949 由 IpcC2AMsg2Cms 从 CP→AP msgId=0x3106 算出；cmsTaskEntry 销毁路由
     正确(sigId∈[0x946,0x952]→OsaDestroyFastSignal) → 生产/消费速率失配（非泄漏）

## FreeRTOS Tasks & Stack Analysis
   ...18 个任务，含 CerrcTask(RRC)/UiccDrvTask(91% WARNING)/tcpip_thread/lfs...
```
→ **根因链路**：印度网络 CP 高频投递 CMS 消息 msgId=0x3106 → XIC→ACIpcAlone1Isr→`IpcC2AMsg2Cms`
  计算 sigId=0x0949 → `OsaCreateFastSignal`(poolId=1) 发 CMS 任务；`cmsProcSignal` 处理+销毁跟不上，
  **31 个 sigId=0x0949 堆积占满 pool[1] 32 槽** → 下一次 `OsaMemPoolIdAlloc(1)` 返回 NULL → `osasig.c:146` assert。
  **主堆 53% 健康、无关；生产/消费速率失配（销毁正确·非泄漏）；仅印度网络高频、国内无**。

## 依赖

| 依赖 | 用途 |
|------|------|
| Python 3.8+ | 必需 |
| `pyelftools` | ELF 符号表 + DWARF 行号（`pip install pyelftools`） |

## 参考文档

- `references/qcx216-platform-reference.md` — 内存布局、dump 格式、excepInfoStore 结构、FreeRTOS TCB、关键符号、根因决策树
- `references/cortex-m-exception-guide.md` — Cortex-M 异常类型、Fault Status 解码、栈帧格式（HardFault 场景）
- `references/dump-report-template.md` — 结构化崩溃分析报告模板
