---
name: spec-init
description: >-
  Spec 工作流环境初始化器。创建 .spec 基础目录结构，并确保中央知识库
  (~/.spec-embedded-iot/knowledge，独立 git 仓库) 就位——不存在时自动克隆。
  在首次使用工作流前或需要重置环境时调用。
  当用户说"使用spec 技能初始化环境"、"spec 初始化"、"spec 准备环境"时使用。
version: 2.1
author: niusulong
---

## 核心原则
1. **非破坏性**：初始化前检查已存在的目录，经用户确认后才执行；知识库路径已存在但非 git 仓库时不擅自覆盖
2. **幂等性**：多次执行初始化是安全的，不会重复创建或破坏已有结构；知识库已存在则 `git pull` 更新到最新
3. **按需创建**：只创建基础目录，功能目录由各技能按需创建
4. **容错降级**：知识库克隆失败不阻塞 `.spec/` 项目目录的创建，仅告警并提示手动补救

## 执行流程

### Step 1：检查环境状态
检查 `.spec` 目录是否已存在。如已存在，询问用户是否重新初始化。

### Step 2：确保知识库就位（不存在则克隆，存在则更新到最新）

中央知识库位于插件目录下的 `~/.spec-embedded-iot/knowledge/`，是一个独立 git 仓库（remote: `https://github.com/niusulong/knowledge.git`），**不在主仓库追踪**。新机器/新用户首次初始化时本地还没有它，需克隆；已存在则 `git pull` 同步到最新。

**判断与操作**（用 Bash 检查 `knowledge/.git` 是否存在）：

```bash
# 知识库路径（Windows bash 下 ~ 展开为 C:\Users\<用户>\）
KB=~/.spec-embedded-iot/knowledge

if [ -d "$KB/.git" ]; then
    # 已存在 → 拉取最新（仅快进，避免意外 merge commit）
    git -C "$KB" pull --ff-only
elif [ -e "$KB" ]; then
    # 路径存在但不是 git 仓库——不擅自覆盖，交给用户决策
    echo "警告: $KB 已存在但不是 git 仓库，跳过（请手动确认该目录内容）"
else
    # 不存在 → 克隆
    git clone https://github.com/niusulong/knowledge.git "$KB"
fi
```

**容错（重要）**：
- 克隆或拉取失败（无网络 / 无 git / 远程不可达 / 鉴权失败）**不中断**初始化——`.spec/` 项目目录的创建不依赖知识库。
- 失败时给一句警告，并提示用户稍后手动操作：
  `git clone https://github.com/niusulong/knowledge.git ~/.spec-embedded-iot/knowledge`（首次）或
  `git -C ~/.spec-embedded-iot/knowledge pull`（更新）。
- **`git pull --ff-only` 失败时降级**：知识库存在本地改动或与远程分叉导致无法快进时，不自动 merge/rebase（避免污染用户本地状态），仅告警提示用户手动处理（如 `git stash` 后再 pull）。
- 知识库内的 `vector_db/`（向量索引）是生成产物，**不入 git**，克隆后为空。首次使用语义检索前需手动构建：`python ~/.spec-embedded-iot/skills/spec-knowledge-archiver/scripts/embed_indexer.py build`（需下载约 450MB 嵌入模型）。

> 路径边界：`~/.spec-embedded-iot/` 是插件根目录（skills/hooks 等都在此），通常已随插件安装存在；本步真正检查并按需拉取的是它下面的 `knowledge/` 子目录。

### Step 3：创建基础目录

```powershell
New-Item -ItemType Directory -Force -Path ".spec" | Out-Null
New-Item -ItemType Directory -Force -Path ".spec/logs" | Out-Null
```

**目录说明**：
| 目录 | 用途 |
|------|------|
| `.spec/` | 根目录 |
| `.spec/logs/` | 日志文件（用户手动放置） |
| `.spec/bug/` | Bug 分析报告（由 spec-bug-analyzer、spec-asr1603-dump-analyzer、spec-ec-dump-analyzer 按需创建） |

**中央知识库**（跨项目持久化，独立于代码仓库）：
```
~/.spec-embedded-iot/knowledge/platform/{平台名}/
  ├── 项目概览.md                        -- 由 spec-project-overview 生成
  └── code-summary/{模块名}/代码总结.md   -- 由 spec-code-summary 生成
```

**项目功能目录**（由各技能按需创建，不在此预创建）：
```
.spec/bug/{工作项ID}_{问题描述}/     -- 由 spec-bug-analyzer、spec-asr1603-dump-analyzer、spec-ec-dump-analyzer 按需创建
  Bug分析.md / Dump分析.md
  logs/                   -- 相关日志归档
  dump/                   -- dump 文件归档
.spec/requirement/{项目ID}_{功能名}/          -- 由 spec-requirement-generator 等技能按需创建
  需求.md
  实现方案.md
  任务规划.md
  ...
```

### Step 4：输出初始化报告

```
✓ Spec 环境初始化完成

项目级目录：
  .spec/
  ├── logs/          (日志文件)
  └── bug/           (Bug 分析报告，按需创建)

中央知识库（跨项目持久化，独立 git 仓库）：
  ~/.spec-embedded-iot/knowledge/platform/{平台名}/
  ├── 项目概览.md
  └── code-summary/  (模块代码总结，按需创建)
  状态：<已是最新 / 本次克隆成功 / 更新到最新 / 失败，需手动执行: git -C ~/.spec-embedded-iot/knowledge pull>

项目功能目录（如 .spec/requirement/6974423486_MQTT_SSL双向认证/）和 Bug 目录（如 .spec/bug/6974423486_UDP链路未关闭/）由各技能按需创建。
工作项 ID 必须向用户确认，未提供时需询问。
现在可以开始使用 Spec 工作流了。
```

## 交互规则
- 目录不存在 → 直接创建，无需确认
- 目录已存在 → 询问用户是否继续
- 用户选择取消 → 中止执行
- 知识库已存在（`knowledge/.git` 在）→ `git pull --ff-only` 更新到最新，无需确认
- 知识库路径存在但非 git 仓库 → **不覆盖**，告警并跳过，提示用户手动确认
- 知识库克隆/拉取失败（网络/鉴权/本地分叉）→ **不中断**，继续 `.spec/` 初始化，报告里给出手动命令
