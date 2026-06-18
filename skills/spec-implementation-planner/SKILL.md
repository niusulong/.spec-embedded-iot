---
name: spec-implementation-planner
description: >-
  实施计划生成器（委托 superpowers:writing-plans）。读取技术方案（方案.md），委托 writing-plans 产出代码级、
  可执行的实施计划（内嵌代码步骤、no-placeholders、self-review），写入 .spec/requirement/{项目ID}_{核心功能}/计划.md。
  两点硬约束：① 代码符合 spec-neoway-coding-standards；② 输出到 spec 项目路径（覆盖 writing-plans 默认路径）。
  去除 writing-plans 的 TDD 假设——验证步骤改嵌入式（编译/烧录/AT/抓包/长稳/功耗）。硬依赖 writing-plans，不存在则终止（无 fallback）。
  当用户说"spec 实施计划"、"spec 编写计划"、"spec 开发计划"、"spec 排期"、"spec 任务拆解"、
  "把这个方案拆成开发任务"、"方案怎么落地"时使用——意图是"基于 方案.md 做开发计划/任务清单"即触发。
  边界：还在做技术选型 → 回 spec-solution-designer（以 需求.md 为输入）。
version: 2.0
author: niusulong
---

## 核心原则
1. **委托不重造**：复用 superpowers:writing-plans 的成熟方法论（file-structure-first、任务拆解、内嵌代码的分钟级步骤、interfaces Consumes/Produces、no-placeholders、self-review），不自行实现。
2. **两点硬约束（必须）**：① 计划中所有代码符合 spec-neoway-coding-standards（命名/注释/风格/宏控）；② 输出到 `.spec/requirement/{项目ID}_{核心功能}/计划.md`，覆盖 writing-plans 默认路径。
3. **去 TDD**：writing-plans 的步骤示例是 pytest 测试先行——嵌入式不适用。验证步骤改为嵌入式手段（编译/烧录/AT 命令测试/抓包/长时间运行/功耗测试），但**保留 no-placeholders**（每步仍是可验证的具体动作 + 预期结果）。
4. **硬依赖、无 fallback**：本技能依赖 superpowers:writing-plans。当前可用技能列表里没有它 → 直接终止并告知用户。

## 执行流程

### Step 1：定位并读取方案 + 确认项目ID/路径
1. 在 `.spec/requirement/{项目ID}_{核心功能}/` 下查找 `方案.md`（+`需求.md`）；缺失 → 转交 spec-solution-designer。
2. 识别当前平台。继承方案的宏控命名 / 接口决策（方案阶段已按 spec-neoway-coding-standards 定好，直接复用，写入计划）。
3. 确定输出路径：`.spec/requirement/{项目ID}_{核心功能}/计划.md`。

### Step 2：校验依赖
检查当前可用技能列表是否含 `superpowers:writing-plans`。**没有 → 直接终止**：
> 本技能依赖 superpowers:writing-plans，当前环境未提供，无法生成 计划.md。请启用 superpowers 后重试。

### Step 3：委托 writing-plans 生成计划（注入硬约束）

**委托前先做 baseline 核查**：grep 目标文件，确认相关代码是否已部分就位（如 handler / 宏 / gperf 项 / 接口是否已存在）。把"已就位 / 需新增"结论带入委托——已就位的任务设计成"核对 / 补位"双分支，避免重复新增已有代码。

然后调用 `superpowers:writing-plans`，把以下作为它的工作输入与约束：

- **spec / 需求来源**：本项目的 `方案.md`（给出路径）+ `需求.md` + baseline 核查结论。
- **Global Constraint（写入计划 header）**：所有代码符合 `spec-neoway-coding-standards`——命名/注释/风格/宏控；功能宏按 `FEATURE_NWY_[AT|OPEN]_FUNC_[FUNC1]_[BZ]`、不含项目名（详见该技能 §3.4）。
- **输出路径（最高优先级，覆盖默认 docs/superpowers/plans/）**：`.spec/requirement/{项目ID}_{核心功能}/计划.md`。**产出后用 `ls` 校验**文件确实落在该路径、未落在 writing-plans 默认路径；落错就移动到正确路径并告知用户。
- **去 TDD（硬约束，明禁五段式）**：writing-plans 默认会套"写失败测试→跑红→实现→跑绿→commit"五段式——**禁用**。任务步骤改用"**动作→Expected**"两段式（动作 = Run 命令 / 操作；Expected = 具体输出 / 退出码 / AT 响应），验证用嵌入式手段（编译通过 / 烧录后 AT 命令测试 / 抓包 / 长时间运行 / 功耗测试）。**保留 no-placeholders**——每步仍须可验证并给出预期结果，不得空话。
- 其余（file-structure-first、任务拆解、内嵌代码步骤、interfaces、self-review）完全按 writing-plans 执行。

### Step 4：交付与执行转交
writing-plans 产出的计划已落到 `计划.md`。向用户展示后，按 writing-plans 的原生 handoff 提供执行选项（由用户决定，本技能不强制）：

- **`superpowers:subagent-driven-development`**（writing-plans 推荐）：每任务派独立子会话执行 + 两段评审，隔离性好；
- **`superpowers:executing-plans`**：本会话内联执行 + 检查点，更简单。

执行完成后，可选调 `spec-neoway-coding-standards` 的"代码检查"对产出代码做一次规范符合性复核（复用已有技能，确认命名/注释/风格/宏控达标）。

## 交互规则
- 方案文档缺失 → 转交 spec-solution-designer。
- writing-plans 不可用 → 终止（无 fallback）。
- 技术选型/架构争议 → 回 spec-solution-designer。
