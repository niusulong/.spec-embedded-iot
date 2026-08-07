# spec-bug-analyzer 平台 Profile 架构设计

> 日期：2026-08-06
> 作者：niusulong × Claude（brainstorming 协同）
> 状态：待审（spec review）
> 范围：仅 `skills/spec-bug-analyzer/`，不涉及 5 个 dump 分析器内部
> 注（2026-08-07 更新）：文中"`pcap_analyzer.py`（协议解码 single source）"等描述已于 v1.16 失效——pcap 分析后端改为内嵌 TShark2MCP MCP server（封装 tshark）。正文保留作历史设计参考，现行方法见 `skills/spec-bug-analyzer/references/pcap-analyzer-guide.md`。

---

## 1. 背景与问题

`spec-bug-analyzer` 定位为**通用** bug 分析器：从 AT/AP/pcap 日志出发，沿调用链追溯根因，设计初衷是多平台适用。实际使用暴露三类痛点：

| 痛点 | 表现 | 根因 |
|------|------|------|
| **平台差异大** | 日志格式、关键串描述、定位方法、代码实现各平台不同 | 当前平台知识极薄：`analysis-patterns.md` 只有 EC/ASR 段，**Unisoc 三平台（QCX216/UIS8850/UIS8852）完全缺失** |
| **大仓/多子仓难定位** | 一个业务跨子仓调用，AI 全仓 grep 走弯路、需人工纠正 | 现成的"代码路径地图"（`项目概览.md` 目录树、`code-summary` 模块路径）**没被强制作为定位前置索引** |
| **业务路由缺失** | 协议层（MQTT/FTP/CoAP）问题无法按业务注入专属参考 | `raw/protocols/` 7 个目录全空；`concepts/` 有 9 篇但未与"业务识别"挂钩 |

**核心诊断**：用户想要的"判断平台/业务 → 给不同参考文档"，地基大多已存在（`raw/platform/`、`项目概览`、`code-summary`、`concepts`）。真正缺的是**"探测 → 路由 → 按需注入"的机制**，以及把现有产物串起来的**可约束 profile 契约**。

---

## 2. 设计目标

1. **多平台可适用**：加平台不改流程（OCP），平台差异通过可插拔 profile 注入。
2. **大仓定位不走弯路**：分析前强制加载平台"代码地图"锚点，避免盲全仓 grep。
3. **输出准确、不臃肿**：profile 只保留与通用基线的**差异**，启动 context 增量最小化。
4. **可约束**：profile 用 schema 约束，可 lint，保证每平台输出结构一致、模型理解负担低。
5. **单一职责**：只做日志现象诊断与根因追溯，dump 分析完全交专门技能。

---

## 3. 设计原则映射（约束实现的总纲）

> 用户要求"参考代码设计原则约束技能实现"。每个决策映射到具体原则，使"约束"可验证。

| 原则 | 在本设计的体现 |
|------|---------------|
| **SRP（单一职责）** | ① `SKILL.md` 只做"通用流程编排 + 探测 + 路由"；② profile 只放"该平台差异化的分析方法"；③ 不碰 dump（边界声明） |
| **OCP（开闭）** | 加平台 = 新增 `profiles/{平台}.md`，`SKILL.md` 探测/路由逻辑零修改 |
| **DIP（依赖倒置）** | `SKILL.md` 依赖"profile 契约（schema）"，不依赖任何具体平台内容 |
| **DRY（不重复）** | ① profile 禁止抄通用基线（通用模式/通用手法/通用串）；② 平台命名引用 `section0-spec.md` 单一真理源；③ 案例只指针挂接、不拷贝原文 |
| **KISS** | 单层 profile（不建独立业务层）；6 段中仅"代码地图"必填，余者无差异则整段省略 |
| **YAGNI** | 不建 `protocols/` 业务层（靠 `concepts/` 兜底）；不自动同步案例；先建高频平台 |

---

## 4. 架构总览（职责切分）

```
技能内（自包含 · 可 lint · 克隆即用）─────────────────
  SKILL.md              通用流程 + 平台探测 + profile 路由 + 边界声明（稳定）
  profiles/
    _schema.md          profile 契约（必须段 + 差异约束）
    {平台}.md           该平台差异化的分析方法（薄）
  references/           通用基线（analysis-patterns 等，全平台共享）
  scripts/              log_analyzer.py / pcap_analyzer.py / check_profile.py(新)

知识库（增量增强 · 有则用、无不影响主流程）────────────
  raw/platform/{平台}/  code-summary §8 / bug-solutions 原文（L1/L3 按需回溯）
  wiki/concepts/        跨平台业务/问题模式（L2 按需）
```

**统一依赖哲学**：技能核心（平台分析方法 + 解码脚本）自包含且充分；领域知识与历史案例都是知识库的**增量**——缺了不影响分析，有了更准。

### 4.1 与 `knowledge/schema.md` 的边界

`schema.md` 管的是**知识库层**（raw/wiki/page-types/frontmatter），**不管技能仓内的 profile**。两者职责不重叠：

| 维度 | `knowledge/schema.md` | 本设计 profile schema |
|------|----------------------|----------------------|
| 管辖 | 知识库（raw/wiki） | 技能仓 `profiles/` |
| 内容 | 案例精炼页、概念页、检索入口 | 平台分析方法（差异） |
| 谁写 | archiver 自动 + agent 维护 | 人 + LLM 协同（随代码基线） |

**唯一交汇点**：profile 的平台命名必须遵循 `section0-spec.md`（平台命名单一真理源），保证"profile 名 = 知识库平台目录名 = 仓库名映射输出"三名一致，回溯不落空。

---

## 5. 核心决策（地基）

| # | 决策 | 理由 |
|---|------|------|
| D1 | **平台事实 + 代码地图进技能** `profiles/{平台}.md` | 属"分析能力"，跟代码基线走，自包含可 lint；无其它技能产出它 |
| D2 | **案例原文留知识库**，profile 只放指针 | 案例由 archiver 持续归档（高频），技能是发版物（低频），搬运必漂移 |
| D3 | **业务知识留知识库** `concepts/`，技能**不建业务层** | 协议解码靠 `pcap_analyzer.py`（single source），业务经验靠 concepts（增量）；技能不背回码表 |
| D4 | **profile 只保留差异**，禁止重复通用基线 | 通用方法已在 `SKILL.md`/`references/`，profile 是"差异补丁"，不臃肿 |
| D5 | **dump 相关全部剔除**，保留一句边界声明 | dump 是另一专业领域，本技能只做日志现象诊断（SRP） |

---

## 6. Profile Schema（核心契约）

### 6.1 通用基线（profile 禁止重复）

| 通用基线 | 现位置 |
|---------|--------|
| 8 步分析流程、证据标注、熔断、反驳自检闸 | `SKILL.md` |
| 通用问题模式 8 类 | `references/analysis-patterns.md` 通用段 |
| 通用分析手法 6 种（时间戳间隔统计 / count 驱动批量识别 / 多复现点对比 / 报文字节验证 / 枚举重构回归 / heap 三数字判别） | `references/analysis-patterns.md` 手法段 |
| log/pcap 脚本用法 | `references/*-guide.md` |

### 6.2 Profile 六段（每段带差异约束）

| 段 | 内容 | 差异约束 | 必填 |
|----|------|---------|------|
| **根注解** | 架构/RTOS/工具链，1-2 行 | 仅当它解释了下面的差异时才写 | 否 |
| **日志差异** | 时间戳格式 / tag 约定 / 默认可见性 | 只列与通用假设不同的；相同则整段省 | 否 |
| **代码地图** | 子仓结构 + 关键模块路径锚点 | 天然平台特异，是"怎么找代码"的依据 | **是** |
| **检索清单** | 平台特有/高频关键串与错误码 | 通用串（ERROR/fail/timeout）禁止列 | 否 |
| **差异化定位手法** | 本平台独有的诊断手法 | 通用 6 手法禁止抄 | 否 |
| **平台专属问题模式** | 本平台常踩的坑（现象-根因对） | 通用 8 模式禁止重复 | 否 |

> **只有「代码地图」必填**（每次分析都要定位代码），其余"无差异则整段省略"。多数平台 profile 因此很薄。

### 6.3 落地样例：`profiles/UIS8852.md`（差异最鲜明，验证"差异纯度"）

```markdown
# UIS8852 分析方法（仅差异）

## 根注解
RISC-V RV32 + RT-Thread（非 ARM、非 FreeRTOS）——下面所有差异源于此。

## 日志差异
- 任务名是 rt_thread（非 xTask）；串 g_osAssert / g_osException
- 当前中断号打印为 g_osIrqNo

## 代码地图
- AP 业务逻辑：ap/ 子目录
- 堆实现：dlmalloc（非 FreeRTOS heap），关键结构在 components/...
- 锚点：业务跨 ap/ 与协议栈子仓调用，定位时先查这两处

## 检索清单（仅平台独有，通用串不列）
dlmalloc | g_osAssert | g_osException | g_osIrqNo | cachedIntPrints

## 差异化定位手法（仅平台独有，通用 6 手法不抄）
- dlmalloc 堆物理遍历：沿 chunk 链遍历，区分"耗尽"vs"损坏"
- 系统/中断栈溢出溅射检测：RISC-V 无硬件栈溢出，靠 magic 溅射
- DWARF CFI 帧感知回溯：RISC-V 无 ARM Thumb 自动回溯，必须靠 .eh_frame

## 平台专属问题模式
- LPM 中断死机（中断中调度）
- PSRAM 堆耗尽（dlmalloc 池被踩）
```

> 注：样例无一个通用手法/通用串——时间戳统计、count 驱动、ERROR/fail 全不见，全是 RISC-V/RT-Thread/UIS8852 独有。这是"只保留差异"的标尺。

---

## 7. SKILL.md 流程改动

### 7.1 探测机制简化（单一映射）

dump 剔除后，平台识别从"两套映射（检索平台 + dump 分派）"收成**单一映射**：

```
git 仓库名  →  平台名  →  profiles/{平台}.md
```

- 保留：取仓库名/分支命令、仓库名→平台名映射表
- 删除：知识库 platform 目录复杂校验、dump 分派表、日志线索交叉印证段
- 校验简化：`ls profiles/` 确认 `{平台}.md` 存在；不存在 → 退化为纯通用基线分析 + 提示

### 7.2 新执行流程（profile 注入点）

```
Step 0  平台探测 → 加载 profiles/{平台}.md（仅差异）     ← 新增，强制
Step 1  获取参考文档（日志文件）
Step 2  日志分析
        参考 = 通用基线 + profile(日志差异/检索清单) + code-summary§8(如存在)
        ┃ 遇 dump/寄存器 → 【边界声明】转 dump 技能，本技能续做日志层分析
Step 3  对比分析（正常+异常两组时）
Step 4  根因定位
        参考 = 通用模式 + profile(差异化定位手法/专属问题模式)
Step 5  代码交叉验证
        用 profile【代码地图】锚点定位（见 §8 过期应对）
Step 6  知识库检索（穷尽后询问）→ concepts(业务模式) + raw 案例原文
Step 7  生成报告
Step 8  确认工作项ID + 归档
```

变化只有三点：① 新增 Step 0；② Step 2/4/5 平台参考来源统一收敛到 profile；③ dump 分派段换成边界声明。**8 步主干、核心原则、证据标注/熔断全不变。**

### 7.3 渐进加载四层

| 层 | 何时加载 | 内容 | 缺失行为 |
|----|---------|------|---------|
| **L0 强制** | Step 0 探测后立即 | `profiles/{平台}.md`（仅差异，薄） | 无 → 纯通用基线 + 提示 |
| **L1 按需** | Step 2/4 涉及具体模块 | `code-summary §8` 字段字典 | 无 → 靠 profile 检索清单 + 通用串 |
| **L2 按需** | Step 4 识别出业务 | `concepts/{业务}.md` | 无 → 跳过，靠通用模式 |
| **L3 兜底** | Step 6 询问且用户同意 | `raw/platform/.../bug-solutions` 原文 | 无匹配 → 告知无案例，熔断或继续 |

**仅 L0 强制且薄** → 启动 context 增量最小，不臃肿。L1-L3 全是"有则用、无不影响"的增量。

### 7.4 一致性约束（单一来源）

`profile 文件名` = `raw/platform/{平台}/ 目录名` = `仓库名映射输出的平台名`，三者必须一致。权威来源：`skills/spec-knowledge-archiver/references/section0-spec.md`（平台命名单一真理源）。由 `check_profile.py` 校验。

---

## 8. 代码地图过期应对

代码地图锚点写进 profile，代码重构后锚点过期。对策：在现有 Step 5「先窄后宽/子仓要查/闭源降级」纪律**前**加一层"profile 锚点优先"，过期时无缝衔接现有降级链（复用，不重造）。

```
Step 5 代码定位
  profile 代码地图锚点（glob 探测）
    ├─ 命中 → 沿锚点定位（最优路径，直击"大仓全仓 grep 走弯路"）
    └─ 扑空 → ① 不静默：提示"锚点 X 未命中，profile 可能过期，建议更新 profiles/{平台}.md"
             ② 回退现有降级链：项目概览(如有) → 子仓窄 grep → 全仓 grep
             ③ 仍扑空 → 闭源降级 [基于日志推断]
```

**三条规则**：① 探测优先；② 扑空降级不卡死；③ 扑空必提示（每次一次，克制）。

**职责区分（防误解）**：静态 lint（`check_profile.py`）只查 schema 合规，**查不了过期**（脚本访问不到实际项目代码）；过期检测只能靠运行时 Step 5 的 glob 探测。

---

## 9. Dump 边界声明（SRP 守护）

删除全部 dump 分析能力与 dump 路由映射表，仅保留一句职责边界声明（放 `SKILL.md` 核心原则之后）：

> **职责边界**：本技能只做日志（AT/AP/pcap）现象诊断与根因追溯，**不解析 crash dump、不解读 PC/LR/SP/堆栈寄存器、不做异常反汇编**——那是专门 dump 分析技能的职责（按平台选择）。分析中遇 dump 内容或寄存器解读需求，转交 dump 技能，本技能续做日志层时序/现象分析与其互补。

**映射表不在此处**——仍由 `spec-using-agents`/CLAUDE.md 单一来源维护。

### 9.1 需删除的 dump 残留

| 位置 | 删除内容 |
|------|---------|
| `SKILL.md` description | "crash dump 改用各平台 dump 分析器"边界罗列 |
| `SKILL.md` 平台识别 §2 | 「确定 dump 分派架构大类」整节 |
| `SKILL.md` Step 2 | 「死机/崩溃的分派」段 |
| `references/analysis-patterns.md` | EC/ASR 段中"转 dump 分析器"行 |
| profile schema | （已在 §6 移除「转交边界」段） |

---

## 10. 范围与非目标（YAGNI）

| 不做 | 理由 |
|------|------|
| 不建 `profiles/protocols/` 业务层 | 协议解码靠脚本、业务经验靠 concepts，避免 8 平台重复抄回码表 |
| 不把案例搬进技能 | 持续归档 vs 发版物，搬运必漂移（D2） |
| 不自动维护/同步 profile | profile 是人 + LLM 协同产物，随代码基线低频更新；过期靠 Step 5 探测提示 |
| 不改 5 个 dump 分析器 | 本设计仅 `spec-bug-analyzer`；dump 分析器已有的平台深度知识与本 profile 不重叠（一个管 dump 现场、一个管日志现象） |
| 不一次铺全 8 平台 profile | 先建高频平台（EC626/UIS8852/ASR1603…），其余按需 |

---

## 11. 落地清单（供 writing-plans 展开）

**新建**：
- `skills/spec-bug-analyzer/profiles/_schema.md`（profile 契约 + 差异约束）
- `skills/spec-bug-analyzer/profiles/{平台}.md`（先建 2-3 个高频平台，如 UIS8852/EC626）
- `skills/spec-bug-analyzer/scripts/check_profile.py`（schema 合规 + 一致性校验）

**修改**：
- `skills/spec-bug-analyzer/SKILL.md`：新增 Step 0、收敛 Step 2/4/5 参考来源、简化探测、删 dump 段加边界声明
- `skills/spec-bug-analyzer/references/analysis-patterns.md`：删除"转 dump 分析器"行（平台专属段保留）

**不动**：8 步主干、核心原则、证据标注/熔断规则、log/pcap 脚本、报告模板、归档流程。

---

## 12. 验收标准

1. 加新平台 = 仅新增 `profiles/{新平台}.md`，`SKILL.md` 零改动（OCP 验证）。
2. `check_profile.py` 能检出：代码地图段空、检索清单含通用串黑名单、profile 名与知识库平台目录不一致。
3. 启动 context 增量 = 单个薄 profile（L0）；L1-L3 全可选、缺失不阻塞。
4. `SKILL.md` 全文无 dump 分析能力/路由映射，仅一句边界声明。
5. 任一 profile 不含通用 6 手法 / 通用 8 模式 / 通用串的重复抄录。
