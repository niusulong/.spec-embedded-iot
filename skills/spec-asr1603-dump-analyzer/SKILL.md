---
name: spec-asr1603-dump-analyzer
description: >-
  ASR 平台 (ARM Cortex-R + ThreadX) Crash Dump 分析技能，通过 TRACE32 dump 文件定位死机根因。
  支持 AXF 反汇编、DDR 栈分析、静态栈深度分析、代码完整性检查、WDT 追踪、堆扫描。
  当用户说"spec 分析dump"、"spec 死机分析"、"spec 崩溃定位"、
  "DataAbort"、"HardFault"、"PC地址解码"、"反汇编"、"crash dump"、
  "栈溢出分析"、"寄存器解读"、"dump分析"、"看门狗超时"时使用。
  也适用于：ARM 崩溃寄存器解读、栈回溯分析、PSRAM代码完整性验证、
  Cortex-R 异常处理分析、TRACE32 dump解析、嵌入式死机定位、
  任何涉及 PC/LR/SP 地址解码的死机问题，即使用户没有明确说出"dump"。
  仅适用于 ASR 平台 (Cortex-R)，不适用于 EC 平台 (Cortex-M)。
  英文触发词: decode crash registers, analyze crash dump, disassemble AXF,
  stack overflow analysis, PSRAM corruption, Cortex-R exception, ARM fault analysis,
  trace32 dump, PC 0x7e, HardFault handler, embedded crash diagnosis, WDT timeout.
  即使用户只是粘贴了一组崩溃寄存器值或提到设备死机需要分析，也应触发此技能。
---

通过设备死机后抓取的 crash dump 文件，分析 ARM Cortex-R 嵌入式系统的死机原因。

## 适用场景

- 设备死机后通过工具抓取了 dump 文件
- dump 目录包含 `.bin`、`.axf`、`.map` 等文件
- 需要定位死机的精确原因（栈溢出、空指针、总线错误、看门狗超时等）
- 有 PC/LR/SP 地址，需要确认崩溃发生在哪个函数的哪条指令
- 需要区分根因是栈溢出、空指针、内存破坏、PSRAM 代码损坏、WDT 超时等

## 不适用场景

- 没有 crash dump 日志（无 PC/LR/SP 地址）
- 纯 RTOS 任务调度问题（无异常寄存器）

## 输入要求

用户提供 dump 文件所在目录路径。

## 快速开始

如果用户提供了完整的 dump 目录（包含 EE_Hbuf + DDR + MAP + AXF），可以先运行自动诊断获取概览：

```bash
python "scripts/dump_analyzer.py" full-analyze --dump-dir <dump_dir> --map <map_file>
```

`full-analyze` 自动执行以下步骤：版本校验、异常头解析、PC/LR 解析、DDR 栈分析、全线程扫描、堆扫描、AXF vs DDR 代码完整性检查，并输出初步根因结论。但它不执行反汇编和 PSRAM 损坏映射——这些需要按下方步骤手动执行。

## 执行流程

> **分支概览**：
> - **EE=EXCEPTION(450)**：Step 1→2→3→4→5→6(按需)→7→8→9→10
> - **EE=WDT(300)**：Step 1→2→3→4→7→8→9→10（跳过代码检查和反汇编）
> - **EE=ASSERT(350)**：Step 1→2→3→7→8→9→10（聚焦 assert 位置）
> - **代码损坏（Step 5 检测到）**：跳过 Step 6，进入 Step 5b 损坏映射

---

### Step 1：文件扫描 + 版本校验

使用 Glob 扫描目录，按扩展名和文件名识别各类 dump 文件：

| 文件 | 用途 | 必需 |
|------|------|------|
| `com_EE_Hbuf.bin` | 异常头（寄存器、FAULT_STATUS/ADDRESS、栈帧、任务名、EE 类型、PMU 复位原因） | 是 |
| `com_DDR_RW.bin` | DDR/PSRAM 完整内存转储 | 推荐 |
| `*.map` | 链接器符号映射表 | 推荐 |
| `*.axf` | 带 debug 信息的 ELF 可执行文件 | 推荐 |
| `com_wdtKICK.bin` | WDT kick 追踪（看门狗场景必需） | 可选 |
| `com_rti_tsk.bin` | ThreadX 任务切换历史（128 条记录） | 可选 |
| `com_CustVer.bin` | 固件版本字符串（版本校验） | 可选 |
| `*.cmm` / `*.xdb` | TRACE32 脚本（衍生文件，备用） | 可选 |

```bash
python "scripts/dump_analyzer.py" check-version \
  --hbuf <com_EE_Hbuf.bin> --axf <axf_file> --map <map_file> --custver <com_CustVer.bin>
```

版本不匹配时所有后续分析结论需标注警告。

### Step 2：解析异常信息

```bash
python "scripts/dump_analyzer.py" parse-hbuf <hbuf_file>
```

脚本自动提取所有寄存器、FAULT_STATUS/ADDRESS、栈帧、任务名、EE 类型、PMU 复位原因、ISR 上下文标记。如果只有 `.cmm`/`.xdb` 文件，可使用 `parse-cmm` 子命令。

CPSR 位域解读详见 `references/arm-exception-guide.md` §2。常见错误：I=0 表示 IRQ **使能**。

### Step 3：EE 类型路由

根据 parse-hbuf 输出中的 `ee_type_raw` 选择分析方向：

| ee_type_raw | 路由 | 重点步骤 |
|-------------|------|---------|
| 300 (SYS_RESET) | 检查 `PMU_reg`，若为 WDT_Reset → **看门狗分析** → Step 4 | WDT kick + 任务历史 |
| 350 (ASSERT) | 提取 `assert_file` + `assert_line` → Step 7 | DDR 分析 + assert 根因 |
| 450 (EXCEPTION) | 按异常子类型分析 → Step 4 | 完整异常流程 |
| 550 (WARNING) | 报告警告内容，通常无需深入分析 | — |

**ISR 上下文**：如果 `is_isr=True` 或 `task_name="INTRRPT"`，崩溃发生在中断上下文。SP 指向 IRQ/ABT 栈而非任务栈。需从 DDR 读取 `_tx_thread_current_ptr` 获取被抢占任务的栈范围。

**Assert desc 特殊处理**：如果 desc 含 `WDT_EXPIRED_withoutAssert` → 转入看门狗分析（同 ee_type=300）。如果 assert 在 `malloc`/`tx_byte_allocate` → 内存分配失败，检查堆碎片/泄漏。

### Step 4：符号解析

```bash
python "scripts/dump_analyzer.py" resolve --map <map_file> <PC> <LR>
```

必须解析的地址：PC → 崩溃所在函数；LR → 调用者函数；栈帧中所有代码段地址 → 完整调用链。

**失败恢复**：如果找不到匹配符号，尝试 grep 手动搜索；检查 MAP 文件版本是否匹配。

### Step 5：AXF vs DDR 代码完整性检查（仅 EXCEPTION 路径）

**在有 DDR dump 和 AXF 文件时必须执行此步骤**。如果 PSRAM 代码被损坏，反汇编会得到错误的指令。

```bash
python "scripts/ddr_code_compare.py" <axf_file> <ddr_file> \
  --pc <PC地址> --base <DDR基地址> --size 64
```

| 结果 | 后续 |
|------|------|
| AXF == DDR | 代码完整 → Step 6 分析崩溃指令 |
| AXF != DDR（局部） | 代码损坏 → **跳过 Step 6**，Step 7 之后进入 Step 5b |
| AXF != DDR（整段 BIST checkerboard） | 代码段未加载 → **跳过 Step 6**，搜索 DDR 启动配置 |

`full-analyze` 自动执行段归属分析（PC/LR 所属 ELF section name、段首/段尾采样、跨段调用检测），并在结论中区分三种状态：`CODE INTACT` / `CODE CORRUPTED` / `CODE SECTION NOT LOADED`。

#### 代码段未加载判定

当 `full-analyze` 输出 `CODE SECTION NOT LOADED` 时：

1. DDR 中该段为 BIST checkerboard (0xAA/0x55) — 硬件内存测试残留，非代码
2. 不一致范围精确对齐 ELF section 边界（段首到段尾全部不一致）
3. LR 在已加载段、PC 在未加载段（跨段调用）

手动确认方法：

```bash
# 搜索 DDR dump 中控制段加载的配置属性
python "scripts/dump_analyzer.py" ddr-search \
  --ddr <DDR> --base <DDR基地址> --string "<与段加载相关的关键词>"
```

> 常见关键词因平台而异，参考 `references/psram-corruption-mapping.md` §7。

损坏类型速查见 `references/psram-corruption-mapping.md` §6。

**关键结论**：
- 代码损坏时 → AXF 反汇编是正确指令，CPU 执行了损坏代码 → 根因是 PSRAM 代码损坏
- 代码段未加载时 → DDR 中从来就没有代码 → 根因是启动配置/段加载条件问题

### Step 5b：PSRAM 损坏范围映射（仅代码损坏时）

详细操作流程见 `references/psram-corruption-mapping.md`。多点采样 → 全段扫描 → 二分搜索边界 → 汇总输出。

### Step 6：AXF 反汇编确认崩溃指令（仅代码完整时）

```bash
python "scripts/axf_disasm.py" <axf_file> --address <PC> --size 32 --map <map_file>
```

`★` 标记崩溃指令。确认指令类型（str/ldr/push/pop 等）。Thumb 地址 bit0=1，实际代码地址需 `addr & ~1`。

崩溃指令模式识别和**矛盾分析法**（push 成功但 str 失败 → SP 被破坏）详见 `references/arm-crash-analysis-guide.md` §4。

### Step 7：DDR 栈分析

#### 7.1 确定 DDR 基地址

```bash
python "scripts/dump_analyzer.py" ddr-base \
  --ddr <com_DDR_RW.bin> --hbuf <com_EE_Hbuf.bin>
```

**失败恢复**：自动检测失败时手动推导——取栈帧中非零值在 DDR 中搜索，`base = addr - file_offset`。

#### 7.2 栈使用分析

```bash
python "scripts/dump_analyzer.py" stack-analysis \
  --ddr <com_DDR_RW.bin> --base <DDR基地址> \
  --stack-bottom <栈底> --stack-top <栈顶> --sp <SP> --map <map_file>
```

#### 7.3 全线程栈溢出扫描

ThreadX 任务栈通过 malloc 分配，任何任务栈越界会破坏相邻堆块。

```bash
python "scripts/dump_analyzer.py" scan-threads \
  --ddr <com_DDR_RW.bin> --base <DDR基地址>
```

#### 7.4 堆完整性检查

```bash
python "scripts/dump_analyzer.py" scan-heap \
  --ddr <com_DDR_RW.bin> --base <DDR基地址>
```

堆损坏与栈溢出的因果关系：如果堆损坏 + 某线程栈溢出 → 根因是栈溢出；堆损坏但无栈溢出 → 越界写入 / use-after-free。

#### 栈溢出判定标准

| 条件 | 判定 |
|------|------|
| 栈底 0xEF 被覆盖 | **栈溢出确认** |
| 栈底 0xEF 完整，使用率 > 95% | **栈溢出高风险** |
| 栈底 0xEF 完整，使用率 < 80% | **排除栈溢出** |

### Step 8：WDT 分析（仅看门狗场景）

```bash
# 解析 WDT kick 追踪
python "scripts/dump_analyzer.py" parse-wdt <com_wdtKICK.bin>

# 解析任务切换历史（最后 20~30 条）
python "scripts/dump_analyzer.py" parse-rti <com_rti_tsk.bin> --last 20
```

分析要点：
- kick 间隔是否正常（应 < WDT 超时周期的 50%）
- 最后运行的任务（rti_tsk 最后一条 T 类型记录）
- 是否有任务长时间占用 CPU（连续出现同一任务）
- 该任务栈是否溢出（Step 7.3）

### Step 9：静态栈深度分析（条件触发）

**触发条件**：栈溢出高风险（>95%）/ 崩溃指令为 push/str[sp]/ldr[sp] / 栈看似正常但高度疑似溢出。

```bash
python "scripts/stack_analysis.py" <axf_file> <map_file> --func <函数名>
# 或
python "scripts/stack_analysis.py" <axf_file> <map_file> --addr 0x7e6f7255 --size 840
```

| 峰值 vs 栈分配 | 判定 |
|---------------|------|
| `peak > allocation` | **栈溢出确认** |
| `peak < allocation` | **栈溢出排除** |

**关键区分**：崩溃时刻 SP 显示"充裕" ≠ 栈溢出不可能（异步异常下溢出可能在更早时刻发生）。

### Step 10：根因定位 + 报告

综合所有信息，按以下决策树定位根因：

```
ee_type_raw?
├── 300 (SYS_RESET) + PMU=WDT → **看门狗超时**
│   ├── kick 停止 → 某任务死锁 / 关中断过久
│   ├── kick 正常但间隔过长 → 某任务占用 CPU 过久
│   └── 最后任务栈溢出 → 栈溢出导致调度失效
├── 350 (ASSERT)
│   ├── WDT_EXPIRED → **看门狗超时**（同上）
│   ├── malloc/tx_byte_allocate → **内存分配失败**（堆碎片/损坏）
│   └── 其他 → 代码逻辑错误（assert_file:assert_line）
├── 450 (EXCEPTION)
│   ├── 代码不一致（Step 5）
│   │   ├── BIST checkerboard + 整段不一致 + 跨段调用
│   │   │   → **代码段未加载**（非损坏）
│   │   │   → 用 ddr-search 搜索启动配置属性
│   │   │   → 排查 scatter/linker script 中该段的加载条件
│   │   │   → 检查调用链为何跨越已加载/未加载段
│   │   └── 局部损坏（非整段）
│   │       → **PSRAM 代码损坏**
│   │       → Step 5b 损坏范围映射
│   ├── DataAbort（代码完整时）
│   │   ├── 崩溃指令为 str/ldr [sp] / push → **栈溢出**
│   │   ├── 崩溃指令为 ldr/str [Rn≠sp] 且 Rn≈0 → **NULL指针解引用**
│   │   └── FAULT_ADDRESS 在栈范围外 → **野指针 / 数组越界**
│   ├── PrefetchAbort → 函数指针损坏 / 返回地址被覆盖
│   └── Undefined Instruction → 跳转到数据区 / 指令对齐问题
└── 堆损坏（Step 7.4）→ **堆结构被破坏**
    ├── 有线程栈溢出 → 栈溢出覆盖了堆块标记
    └── 无栈溢出 → 越界写入 / use-after-free
```

使用 `references/bug-report-template.md` 模板生成报告到 `.spec/bug/{工作项ID}_{问题描述}/`。

**确认工作项 ID**：如果用户未提供工作项 ID（如 6974423486），必须询问用户获取。目录命名格式：`{工作项ID}_{问题描述}`（如 `6974423486_ipv6_udp_ppp_crash`）。

**合并归档**：生成报告前检查 `.spec/bug/` 下是否已有同工作项 ID 的目录（`{工作项ID}_*`）。如果已存在，复用该目录归档，避免同一问题分散到多个路径。例如 bug 日志分析已创建了 `6974423486_ipv6_udp_ppp_crash/`，dump 分析的 `Dump分析.md` 和 `dump/` 也归档到同一目录下。

**问题复现路径**：找到根因后必须输出复现路径，包含前置条件、必要状态和操作步骤。概率性问题额外标注复现概率和触发频率。证据不足以推导完整复现路径时，输出已知条件和推测路径，并标注"待验证"。

## 工具使用规则

### 脚本路径（重要）

所有脚本位于技能目录下的 `scripts/` 子目录。系统加载技能时会提供 **"Base directory for this skill"**，执行 Bash 命令时**必须用该路径拼接完整脚本路径**：

```bash
SKILL_DIR="<Base directory for this skill>"
python "scripts/dump_analyzer.py" resolve --map <map_file> <addr>
```

### 脚本速查

| 脚本 | 用途 | 关键子命令/参数 |
|------|------|---------|
| `dump_analyzer.py` | 主脚本（诊断、栈分析、线程扫描、DDR 搜索） | `parse-hbuf` `parse-wdt` `parse-rti` `check-version` `scan-heap` `ddr-base` `ddr-search` `stack-analysis` `scan-threads` `resolve` `full-analyze` |
| `ddr_code_compare.py` | AXF vs DDR 代码完整性对比（含 DFSR 解码） | `--pc` `--base` `--scan` `--decode-dfsr` |
| `axf_disasm.py` | ARM/Thumb 全功能反汇编器 | `--address` `--map` `--context` `--arch` |
| `stack_analysis.py` | 调用链峰值栈深度分析 | `--func` 或 `--addr` `--mode` `--depth` `--stack-size` |
| `map_lookup.py` | 独立 MAP 地址查找工具 | `--pc` `--lr` `--addr` `--call-stack` |
| `common.py` | 公共模块（不直接运行） | — |

**DDR dump > 1MB 时必须使用脚本**；栈帧中仅几个地址需解析时可用 Grep 手动搜索。

## 输出规范

- 报告路径：`.spec/bug/{工作项ID}_{问题描述}/Dump分析.md`
- 栈数据等中间结果在报告中作为附录
- 所有地址解析必须标注来源（map 文件行号或符号名）

**Dump 文件归档**：将分析涉及的 dump 文件（EE_Hbuf、DDR、MAP 等）复制到 `.spec/bug/{工作项ID}_{问题描述}/dump/` 目录归档，保持分析报告与原始数据的关联，便于后续复查。

```bash
mkdir -p ".spec/bug/{工作项ID}_{问题描述}/dump"
cp <dump目录>/{com_EE_Hbuf.bin,com_DDR_RW.bin,*.map,*.axf} ".spec/bug/{工作项ID}_{问题描述}/dump/"
```

## 参考文档

- `references/arm-exception-guide.md` — ARM 异常类型、CPSR 位域、FAULT_ADDRESS 模式、Thumb 寻址
- `references/arm-pmsav7-dfsr-reference.md` — PMSAv7 DFSR/FSC 权威参考（SEGGER 官方解码代码）
- `references/arm-crash-analysis-guide.md` — 崩溃指令模式识别、矛盾分析、栈帧分析、峰值判定框架
- `references/dump-analysis-patterns.md` — 11 种分析模式目录（栈溢出、空指针、PSRAM 代码损坏、ThreadX 任务等）
- `references/psram-corruption-mapping.md` — PSRAM 代码损坏范围深度映射流程
- `references/bug-report-template.md` — 结构化崩溃分析报告模板
