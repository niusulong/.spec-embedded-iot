---
name: spec-qcx216-dump-analyzer
version: 1.3
author: niusulong
description: QCX216 / N706D（Unisoc ARM Cortex-M3 + FreeRTOS）crash dump 分析：从 RamDumpData_*.bin 重建死机现场，定位 ASSERT/HardFault 根因（excepInfoStore 解析、Cortex-M 异常帧还原、objdump 反汇编+源码行、调用链、堆/栈/OSA池/代码完整性）。当用户要定位 QCX216/N706D 死机根因时使用——即使只粘贴一组崩溃寄存器、只说"设备死机"、或没明说"分析dump"也应触发。仅适用 QCX216/N706D；其它平台用对应 dump 分析器：ASR(Cortex-R)→spec-asr1603-dump-analyzer、EC626/EC616→spec-ec626-dump-analyzer、UIS8850/N706-STD→spec-uis8850-dump-analyzer、UIS8852/N706C→spec-uis8852-dump-analyzer；无 dump 仅 AT/串口日志→spec-bug-analyzer，精确定位内存泄漏→spec-memory-leak-analyzer。
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

| 文件 | 用途 | 必需 |
|------|------|------|
| `RamDumpData_*.bin` | RAM 完整转储（base=0x0，偏移==物理地址，含 Flash 代码 + RAM） | 是 |
| `ap_at_command.elf` | **崩溃固件** ELF（符号表 + DWARF 调试信息），必须与崩溃时刻版本一致 | 是 |
| `ap_bootloader.elf` | bootloader ELF | 可选 |
| `comdb.txt` | Unisoc 日志 ID 映射表 | 可选 |

> **版本一致性是地址映射的前提**：务必使用 **dump 同目录下的 ELF**（崩溃固件编译产物），
> 不要用 `PLAT/gccout/` 下当前编译的 ELF——代码变动后符号地址不匹配，定位全错。

## 工具链（重要 — 已有 ARM 工具链，优先用）

QCX216 仓库自带 GCC 10 工具链 `PLAT/tools/gcc/arm-none-eabi/bin/`（`objdump.exe` /
`nm.exe` / `readelf.exe`，文件名不带 `arm-none-eabi-` 前缀）。**反汇编优先用 objdump**：
权威 Thumb-2（正确解 ITE 条件块 / MSR / 宽指令）、自带 DWARF 源码行（替代 addr2line）、
零 pip 依赖。脚本自动查找（`qcx216_toolchain.find_toolchain`），找不到降级 capstone/纯 Python。

| 能力 | 优先 | 降级 |
|------|------|------|
| 反汇编崩溃指令 | **objdump -d -l**（带源码行） | capstone → 纯 Python（`qcx216_disasm`） |
| 符号/源码行查询 | pyelftools（`ElfReader.locate`，已缓存） | objdump/nm 回退 |
| section 代码/数据判定 | pyelftools（`is_code`/`is_ram`） | readelf -S |

详见 `references/arm-toolchain-guide.md`。

## 执行流程

### Step 0：一键全流程（推荐起点）

```bash
SKILL_DIR="<本技能 scripts 目录>"
PY="/c/Users/20220715012/AppData/Local/Programs/Python/Python312/python"
"$PY" "$SKILL_DIR/qcx216_dump_analyzer.py" full-analyze <RamDumpData_*.bin> --elf <ap_at_command.elf>
```
`full-analyze` 自动：异常解析 → 触发点 objdump 反汇编 → 调用链 → 中断源 → heap → OSA池 → 全任务+栈水位。
头部 `Toolchain:` 行确认工具链就绪。

### Step 1：异常定性 + 场景路由

`full-analyze` 的 `>> Exception Type` 决定方向：

| 类型 | 判据 | 路由（重点步骤） |
|------|------|-----------------|
| **ASSERT** | excepInfoStore 含 `Func:/Line:/Val:` 文本 | Step 2(A) → 反汇编触发点 → Val/调用链 |
| **HardFault** | magic 有效但无 assert 文本 | Step 2(B) → 异常帧还原 → code-compare + 堆完整性/崩溃寄存器块状态 |
| **Unknown** | 无有效 magic | 静默复位/WDT：查 reset 原因 + EPAT 日志（UTF-16LE） |

通用必做：Step 0（现场）、heap（排除耗尽）、threads（栈溢出）、报告（Step 6，含确定性分级）。

### Step 2(A)：ASSERT 深度（Func/Line/Val + 调用链）
- `Func` 多为 Unisoc OSA/协议栈 API（`OsaCreateFastSignal` 等，二进制库，源码不在仓内）。
- `Val` 含义见 `references/qcx216-platform-reference.md` §7（如 `0xc`=sigBodySize=12）。
- **触发点反汇编**：`disasm <addr> --dump <dump> --elf <elf>`（objdump 优先）。确认 assert 分支
  （`B .` 死循环 / `CBZ r0` 失败点）。
- **调用链**：从异常 SP 扫描栈代码地址还原（`full-analyze` 已含）。`context: interrupt` → 查 ISR
  近期事件（常见 `ACIpcAlone1Isr`/`IpcC2AMsg2Errc` CP→AP IPC）。
- **OSA signal 池满**（`OsaCreate*Signal` assert 必查）：`scan-osa-pool`，看 `freeHead==NULL`/满池 +
  堆积 sigId 归类（`references/qcx216-platform-reference.md` §6 全链路分析）。

### Step 2(B)：HardFault 深度（核心，含异常帧还原 + 链表损坏取证）

HardFault 是 QCX216 难点（CFSR/HFSR 不在 dump，靠异常帧 PC/LR + 反汇编指令语义定位）。

**B1. 还原异常帧**（excepInfoStore 含 EXC_RETURN + PSP，`references/cortex-m-exception-guide.md`）：
- 找 `EXC_RETURN`（`0xFFFFFFFD`=Thread/PSP 任务里崩 / `0xFFFFFFF9`=Thread/MSP / `0x...1`=Handler）。
- 用对应 SP 读 8 字异常帧 `{R0,R1,R2,R3,R12,LR,PC,xPSR}`（R0 低地址、xPSR 高地址）。
- 校验三重约束：PC∈代码段 + xPSR bit24=1 + LR 是合法调用返回点。**PC=崩溃指令，LR=调用者**。

**B2. 反汇编崩溃指令**：`disasm <PC> --dump <dump> --elf <elf>`（objdump）。看指令语义：
- `STR/LDR R?,[R?,#?]` + 某 R=0/野值 → 空指针/野指针/链表损坏（如 `STR R1,[R2,#8]` R2=0）。
- `BL/B` 跳到非代码 → 函数指针损坏 / 栈被踩。

**B3. 代码完整性**（排除代码损坏，HardFault 必查）：
```bash
"$PY" "$SKILL_DIR/qcx216_dump_analyzer.py" code-compare <dump> --elf <elf> --pc <PC>
```
RAM 代码段（0x004xxxxx）被堆越界写踩坏时这里会发现 CORRUPTED。INTACT → 排除代码损坏。

**B4. 堆完整性 + 链表损坏取证**（链表操作崩溃如 `vListInsert`/`uxListRemove` 必查）：
- **堆完整性**：`full-analyze` 的 `Heap integrity` 段自动校验 head_bound（`0xBEAFDEAD`）；
  遍历中断于坏块 → 堆元数据损坏（越界写 / double-free / UAF 破坏物理连续性）。
- **崩溃寄存器块状态**：`full-analyze` 的 `Crash registers -> block status` 段自动查崩溃帧
  R0-R3 指向地址的块状态（FREE 块 → 悬空/use-after-free；USED → 看分配者）。底层是
  `qcx216_heap.find_enclosing_block`（可手动查任意地址）。
- **链表悬空节点取证**（特定模式，非通用 double-free 检测）：**QCX216 无 malloc/free trace**，
  静态 dump 无法通用检测"同一地址释放两次"。链表损坏需临时脚本——用
  `qcx216_heap.find_enclosing_block` 遍历目标 List（如 `pxCurrentTimerList`）节点，
  找"被链表引用但内存已释放"的悬空节点。见 `references/heap-corruption-guide.md`。

> ⚠️ **避免过度推断**：FreeRTOS 正常删除是"先摘链后释放"，单次删除不会残留悬空节点。链表损坏
> 根因多为并发 double-free / use-after-free / 越界写（间接堆损坏）。静态 dump 能确认"已损坏"
> （堆完整性 + 悬空节点），但"哪条路径产生"常需埋点复测——报告必须区分（见 Step 6 确定性分级）。

### Step 3：栈溢出 / 死锁
```bash
"$PY" "$SKILL_DIR/qcx216_dump_analyzer.py" threads <dump> --elf <elf>
```
任务列表 + 栈水位判定表 + 风险摘要。当前/被中断任务 OVERFLOW/HIGH RISK → 栈溢出可能是根因。

### Step 4：调用链 / 地址解码
```bash
"$PY" "$SKILL_DIR/qcx216_dump_analyzer.py" resolve 0x<addr> 0x<addr> --elf <elf>
```
地址→符号+偏移+源码行（pyelftools，objdump 行号回退）。

### Step 5：归档（一键跑全套 + INDEX）
```bash
"$PY" "$SKILL_DIR/run_all.py" <dump> <elf> .spec/bug/<工作项ID>_<问题>/
```
产出 `analysis/NN_*.txt`（编号归档）+ `INDEX.md`（核对原始数据段）+ `_meta.json` + dump 反向指针。

### Step 6：报告 + 确定性分级（重要）

报告路径 `.spec/bug/{工作项ID}_{问题描述}/Dump分析.md`（模板 `references/dump-report-template.md`）。
**必须区分结论的确定性**（避免把根因机制具体化到 dump 无法支撑的精度）：

| 级别 | 含义 | 示例 |
|------|------|------|
| **铁证** | dump 直接证明、可复核 | 崩溃指令 PC/LR、寄存器值、反汇编、链表损坏节点 |
| **合理推测** | 强证据支撑的根因域，但非唯一 | "NWY 定时器生命周期缺陷→堆损坏" |
| **待验证** | 静态 dump 无法唯一确定 | "哪条释放路径产生了悬空节点" → 需埋点复测 |

> 把铁证当铁证写，推测标推测，存疑标"待验证 + 复测方法"。不要把机制具体化到 dump 无法支撑的精度。

## 脚本速查

```bash
PY="/c/Users/20220715012/AppData/Local/Programs/Python/Python312/python"
"$PY" <SKILL_DIR>/qcx216_dump_analyzer.py <子命令> [选项]
```

| 子命令 | 用途 |
|--------|------|
| `full-analyze <dump> --elf <elf>` | 一键全流程（异常帧+objdump反汇编+调用链+heap完整性+崩溃寄存器块状态+OSA池+任务） |
| `parse-excep <dump> --elf <elf>` | 仅解析 excepInfoStore |
| `disasm <addr> --dump <d> --elf <e>` | 反汇编地址附近（**优先 objdump**，降级 capstone/纯Python） |
| `resolve <addr>... --elf <elf>` | 地址→符号/源码行（pyelftools + objdump 回退） |
| `code-compare <dump> --elf <elf> [--pc X]` | **代码完整性**：ELF 代码段 vs dump（HardFault 必查） |
| `frame <dump> --elf <elf>` | **Cortex-M 异常帧**（崩溃 PC/LR/寄存器，HardFault 快查） |
| `threads <dump> --elf <elf>` (= `scan-stacks`) | 任务列表 + 栈水位 + 风险摘要 |
| `wdt-reset <dump> --elf <elf>` | 复位原因 + WDT 状态（蓝屏 vs 真复位 / WDT 超时） |
| `scan-osa-pool <dump> --elf <elf>` | OSA 协议栈信号池（OsaCreate*Signal assert 必查） |
| `run_all <dump> <elf> <out_dir>` | 一键归档（编号输出 + INDEX.md + _meta.json） |

**反汇编后端优先级**：objdump（仓库工具链，权威+源码行）> capstone > 纯 Python。`disasm`/`full-analyze`
自动选择，输出标注 `backend:`。

## 平台关键事实（分析时记住）

1. **dump base=0x0**：`RamDumpData_*.bin` 从物理地址 0 起的统一地址空间（向量表+Flash+RAM），**偏移==物理地址**。
2. **excepInfoStore** 在 RAM（符号 `excepInfoStore`），首字 `magic1=0xEC112013` 表示有效。结构：magic + header + SP/EXC_RETURN 快照 + ISR入口 + PC + assert 文本。HardFault 时含 EXC_RETURN（+0x50）+ PSP（+0x58），见 `cortex-m-exception-guide.md`。
3. **ASSERT 文本**：`Func:/Line:/Val:`（DWARF 映射 `.c:行`，源码多不在仓内）。`context: interrupt` = 中断里触发。
4. **调用链靠栈扫描**：Cortex-M 无帧指针，从异常 SP 扫代码地址还原。中断上下文 SP 在 MSP。
5. **OSA signal 池**（`OsaCreate*Signal` assert 必查）：`osaMemPoolDescList[3]` 专用池（pool[1] 36B/32槽 小信号池常见满），block `+0 magic(0xD5E9) +2 poolId +3 flag +4 sigId`，满判据 `freeHead==NULL`。poolId 由 sigBodySize 动态选（`ITE LS`，objdump 解）。详见 `qcx216-platform-reference.md` §6。
6. **堆是 TLSF+MM_DEBUG**：块头 `+4 head_bound(0xBEAFDEAD) +8 alloc_owner +C size(bit0=free)`。⚠️ `psSlp2FreeBytesRemaining` 是 sleep 另一堆，非主堆 free。主堆统计靠 `walk_tlsf` 物理遍历。
7. **FreeRTOS TCB**：`pxTopOfStack=TCB+0`、`pxStack=TCB+0x30`、`pcTaskName=TCB+0x34`；`pxCurrentTCB` 需解引用。栈底 `0xA5A5A5A5` 哨兵判溢出（MSP 主栈不填哨兵，`NO SENTINEL` 非溢出）。
8. **CFSR/HFSR 不在 dump**：HardFault 靠异常帧 PC/LR + 反汇编指令语义定位，不靠 fault status 寄存器。
9. **AP/CP 双核**：分析 AP 核（`ap_at_command`）；CP 核日志在 `comdb.txt`/SigLogger。

## 依赖

| 依赖 | 用途 |
|------|------|
| Python 3.8+ | 必需 |
| `pyelftools` | ELF 符号 + DWARF（`pip install pyelftools`） |
| 仓库 `PLAT/tools/gcc/arm-none-eabi/bin` | **objdump/nm/readelf**（反汇编权威后端，自带） |
| capstone（可选） | objdump 不可用时的反汇编降级（`pip install capstone`） |

## 参考文档

- `references/arm-toolchain-guide.md` — objdump/nm/readelf 命令、与 pyelftools 分工、Python API、常见坑
- `references/cortex-m-exception-guide.md` — 异常类型、**异常栈帧还原（R0 低/xPSR 高）、EXC_RETURN、HardFault 现场**
- `references/heap-corruption-guide.md` — TLSF+MM_DEBUG 块布局、**损坏判定树（越界/double-free/UAF）、链表损坏取证（三重签名）**、运行时埋点
- `references/qcx216-platform-reference.md` — 内存布局、excepInfoStore 结构、OSA 池、TCB、关键符号、ASSERT Val 含义
- `references/dump-report-template.md` — 报告模板（含确定性分级）
