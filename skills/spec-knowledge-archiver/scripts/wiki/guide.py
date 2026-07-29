"""spec-embedded-iot 知识库 wiki 维护指南（GUIDE_TEXT）。

这是给 LLM agent 的系统提示词，在 kb.py archive 归档完成后输出到 stdout。
agent 读到后会按照本指南维护全局 wiki。

理念借鉴自 Karpathy LLM-Wiki + lucasastorian/llmwiki 的 GUIDE_TEXT，
针对嵌入式 IoT 场景做了深度语境化（5 类知识 + 平台 + 跨案例概念）。
"""

# 目录结构约定（agent 维护 wiki 时必须遵守）
DIRECTORY_STRUCTURE = """\
## 目录结构

```
knowledge/
├── raw/                                 # ★ 统一 raw 根（所有原文，只读，不修改）
│   ├── platform/{平台}/                 # 平台特定原文
│   │   ├── bug-solutions/
│   │   │   └── {wid}_{desc}/            # 1 个 bug = 1 个目录（多文件保真）
│   │   │       ├── Bug分析.md
│   │   │       ├── Dump分析.md
│   │   │       └── 修改方案.md
│   │   ├── code-summary/{模块名}/代码总结.md  # 1 个模块 = 1 个目录
│   │   ├── requirement-solutions/{id}_{desc}/ # 1 个需求 = 1 个目录
│   │   ├── official-docs-md/cn|en/...        # 厂商官方文档（原样镜像）
│   │   └── 项目概览.md                        # 平台项目概览
│   └── protocols/{协议名}/...            # 全局协议原文（跨平台共享）
└── wiki/                                # ★ 全局唯一 wiki（跨平台、跨类型综合）
    ├── Home.md                              # 知识库入口（平台矩阵 + 导航）
    ├── INDEX.md                             # 全部条目的轻量目录（一行一条）
    ├── entries/                             # 单条目精炼页（按类型分子目录）
    │   ├── bug-solutions/{平台}_{wid}_{desc}.md
    │   ├── code-summary/{平台}_{模块名}.md
    │   ├── requirement-solutions/{标题}.md
    │   └── ...
    └── concepts/                            # 跨案例/跨平台概念页（LLM-Wiki 灵魂）
        ├── MQTT连接失败.md                  # 综合 EC626+ASR1603 的所有 MQTT bug
        ├── FreeRTOS任务调度.md              # 跨平台 RTOS 概念
        ├── 内存泄漏排查方法论.md            # 跨案例排查方法论
        └── ...
```

**关键语义**：
- `raw/` 是原文保真区，**绝对不修改**。kb.py archive 只往这里拷贝。
- `wiki/` 是你（LLM）维护的精炼层，**只有你可以写**。
- `wiki/concepts/` 跨平台、跨类型综合，是 LLM-Wiki 的核心价值。
- `wiki/entries/` 内部按类型分子目录，因为 bug 和 code-summary 是不同东西。
"""

# wiki 页 frontmatter 规范
FRONTMATTER_SPEC = """\
## Frontmatter 规范（所有 wiki/*.md 必须有）

```yaml
---
title: MQTT 连接失败的 3 种根因模式       # 必填，无 # 前缀
date: 2026-07-21                          # 必填，YYYY-MM-DD
tags: [MQTT, 连接失败, EC626, ASR1603]    # 必填，用于检索
type: concept                             # 必填：concept | entry | index | home
                                          #   concept = 跨案例概念页
                                          #   entry   = 单条目精炼页
                                          #   index   = INDEX.md
                                          #   home    = Home.md
platform: [EC626, ASR1603]                # 可选，涉及的平台
module: MQTT                              # 可选，涉及的模块
bug_type: 状态机异常                       # 可选（entry 专有），只能从 10 个规范值选一
                                          #   见 references/section0-spec.md（禁止自由发挥）
work_item_id: 6974423486                  # 可选（entry 专有）
---
```
"""

# entries/ 单条目精炼页模板
ENTRY_TEMPLATE = """\
## entries/ 模板（单条目精炼页）

文件名约定：`{类型}/{平台}_{条目标识}.md`（如 `bug-solutions/EC626_6974423486_UDP链路未关闭.md`）

**结构**：
```
---
title: UDP 链路未关闭
date: 2026-07-21
tags: [UDP, LWIP, 链路管理, EC626]
type: entry
platform: [EC626]
module: LWIP/UDP
bug_type: 时序竞争
work_item_id: 6974423486
---

# UDP 链路未关闭

## 一句话根因
AT+COPS=2/CFUN=4 后 UDP socket 未主动关闭，需手动关闭后才能重建连接。

## 调用链摘要
nwy_dsnet_deactive → lwip_close → sock_evt queue 满 → 死锁

## 关键证据（指向原文，不复制）
- 现象日志：见 [Bug分析.md §1](../../../raw/platform/EC626/bug-solutions/6974423486_UDP链路未关闭/Bug分析.md)
- Dump 现场：见 [Dump分析.md §异常信息](../../../raw/platform/EC626/bug-solutions/6974423486_UDP链路未关闭/Dump分析.md)

## 修复方案（如有）
见 [修改方案.md](../../../raw/platform/EC626/bug-solutions/6974423486_UDP链路未关闭/修改方案.md)

## 相关概念
- [[LWIP 资源管理]]
- [[Socket 生命周期]]
```

**要点**：
- 只放精炼结论，不复制原文（原文在 raw/）
- 所有引用用相对路径指向 raw/
- `[[概念名]]` wikilink 指向 concepts/（双向链接）
"""

# concepts/ 跨案例概念页模板（LLM-Wiki 灵魂）
CONCEPT_TEMPLATE = """\
## concepts/ 模板（跨案例概念页，LLM-Wiki 灵魂）

**创建时机**：当 2 个以上案例涉及同一根因模式 / 同一模块 / 同一类现象时。

文件名约定：`{概念名}.md`（概念名用中文，简洁，如 `MQTT连接失败.md`、`内存泄漏排查方法论.md`）

**结构**：
```
---
title: MQTT 连接失败的根因模式
date: 2026-07-21
tags: [MQTT, 连接失败, 根因模式, 跨平台]
type: concept
platform: [EC626, ASR1603]
module: MQTT
---

# MQTT 连接失败的根因模式

## 模式总览（3 种）

| 模式 | 典型症状 | 根本原因 | 涉及案例 |
|------|---------|---------|----------|
| 1. CONNACK 未等待 | 发送 CONNECT 立即失败 | mqttConnectWithResults 中 #if 0 跳过等待 | [[EC626_MQTT_SSL连接成功但MQTTConnect失败]] |
| 2. SSL 上下文丢失 | SSL 握手成功但 MQTT 明文发送 | ECMTOPEN/ECMTCONN 阶段 SSL 状态未传递 | [[EC626_MQTT_SSL双向认证连接失败]] |
| 3. 内存不足崩溃 | 双向认证时 ASSERT | ECC 运算内存峰值超可用堆 | [[EC626_MQTT_SSL双向认证内存分配崩溃]] |

## 通用排查思路
1. 先确认 SSL 层是否真的成功（看 mbedTLS 日志）
2. 看 MQTT CONNECT 包是否真的发出（lwip send 日志）
3. 看是否有 CONNACK 等待逻辑（grep CONNACK in code）
4. 看内存水位（双向认证特别耗内存）

## 跨平台差异
- EC626: mbedtls，双向认证 ECC 内存敏感
- ASR1603: ...（若适用）

## 相关概念
- [[SSL/TLS 握手]]
- [[内存管理]]
```

**要点**：
- 每个模式必须有"涉及案例"列，用 `[[条目页名]]` 双向链接
- 综合"共同根因 + 差异化表现 + 通用排查"，这是向量检索做不到的
- 当新案例符合已有模式 → 更新该概念页（追加行）
- 当新案例不符合任何已有模式 → 等第 2 个同类出现再新建概念页
"""

# INDEX.md 全局目录模板
INDEX_TEMPLATE = """\
## INDEX.md 模板（全局轻量目录）

这是检索的入口（agent 第一层渐进加载读它）。**保持精简**——一行一条，不要展开。

```
---
title: spec-embedded-iot 知识库索引
date: 2026-07-21
type: index
---

# 知识库索引

> 全部条目的一行摘要。检索时先读这里锁定候选，再读 entries/ 或 concepts/。

## Bug 案例（30）

### EC626（21）
| WID | 标题 | 模块 | 一句话根因 |
|-----|------|------|-----------|
| 6974423486 | UDP链路未关闭 | LWIP/UDP | XIIC=0 去激活触发三方死锁 |
| NA | MQTT SSL连接失败 | MQTT | CONNACK 等待代码被 #if 0 注释 |
| ... | ... | ... | ... |

### ASR1603（8）
...

## 代码总结（10）

| 平台 | 模块 | 文件 |
|------|------|------|
| EC626 | CoAP模块 | [entries/code-summary/EC626_CoAP模块](entries/code-summary/EC626_CoAP模块.md) |
| ... | ... | ... |

## 跨案例概念（N）

| 概念 | 涉及案例数 | 最后更新 |
|------|-----------|---------|
| [MQTT 连接失败](concepts/MQTT连接失败.md) | 3 | 2026-07-21 |
| [内存泄漏排查方法论](concepts/内存泄漏排查方法论.md) | 5 | 2026-07-15 |
| ... | ... | ... |
```

**要点**：
- 每条只占一行（一行根因摘要），让 agent 一次读完能锁定候选
- 按"类型 → 平台 → 表格"分组
- 概念页区单独列，标出涉及案例数（帮助判断重要性）
"""

# Home.md 平台矩阵
HOME_TEMPLATE = """\
## Home.md 模板（知识库入口）

```
---
title: spec-embedded-iot 知识库
date: 2026-07-21
type: home
---

# spec-embedded-iot 知识库

> 嵌入式 IoT 模组 bug 分析案例库 + 代码理解 + 协议参考。LLM-Wiki 路线，跨平台综合。

## 平台矩阵

| 平台 | 芯片/架构 | Bug 案例 | 代码总结 | 入口 |
|------|----------|---------|---------|------|
| EC626 | EigenComm ARM Cortex-M + FreeRTOS | 21 | 10 | [raw](../raw/platform/EC626/) |
| ASR1603 | ASR ARM Cortex-R + ThreadX | 8 | 0 | [raw](../raw/platform/ASR1603/) |
| UIS8850 | Unisoc Cortex-R + FreeRTOS | 0 | 0 | [raw](../raw/platform/UIS8850/) |
| UIS8852 | Unisoc RISC-V + RT-Thread | 0 | 0 | [raw](../raw/platform/UIS8852/) |

## 检索入口
- 找具体案例 → [INDEX.md](INDEX.md)
- 找某类问题共性 → [concepts/](concepts/)
- 看某平台代码结构 → 该平台 raw/code-summary/

## 高频概念（Top 10）
- [MQTT 连接失败](concepts/MQTT连接失败.md)
- [内存泄漏排查方法论](concepts/内存泄漏排查方法论.md)
- ...
```
"""

# 维护工作流
WORKFLOW = """\
## 维护工作流（kb.py archive 归档后触发）

当 kb.py archive 完成新条目归档，你（LLM）应该：

### 1. 为每个新条目生成 entries/ 精炼页
- 读 raw/ 下新归档的所有原文（Bug分析.md / Dump分析.md / 方案.md 等）
- 按 entries/ 模板生成精炼页（**不复制原文，只做精炼 + 引用**）
- 多文件场景：精炼页要综合所有文件，不是每个文件一页

### 2. 更新 INDEX.md
- 在对应类型的表格里追加新行（一行 = wid + 标题 + 模块 + 一句话根因）
- 不要展开（保持精简，让 agent 一屏读完）

### 3. 维护 concepts/ 概念页（LLM-Wiki 灵魂）
- 对每个新 bug，判断它是否符合已有 concepts/ 页的模式：
  - 符合 → 追加到该 concept 的"涉及案例"列
  - 不符合 → 暂不新建，等第 2 个同类案例出现再建
- 新建 concept 页的标准：2+ 案例共享同一根因模式

### 4. （可选）更新 Home.md
- 案例数变化、新增高频概念时更新

### 维护原则
- **raw 绝对不修改**（取证保真）
- **wiki 鼓励修改**（持续累积、提炼、综合）
- **新案例优先纳入已有 concept**，而不是新建（避免 concept 碎片化）
- **引用用相对路径**（`../../../raw/platform/EC626/...`），不要用绝对路径
"""


# 完整 GUIDE_TEXT（kb.py archive 输出给 agent 的提示词）
GUIDE_TEXT = """# spec-embedded-iot Wiki 维护任务

你是 spec-embedded-iot 知识库的 wiki 维护者。kb.py archive 刚归档了新条目到 raw/ 区，
现在需要你按本指南维护 wiki/ 区。

""" + DIRECTORY_STRUCTURE + FRONTMATTER_SPEC + ENTRY_TEMPLATE + CONCEPT_TEMPLATE + INDEX_TEMPLATE + HOME_TEMPLATE + WORKFLOW


# 归档后输出给 agent 的简短触发语（不是完整 GUIDE，是指针）
ARCHIVE_TRIGGER_TEMPLATE = """\
========================================
归档完成。请用 LLM 维护全局 wiki（~/.spec-embedded-iot/knowledge/wiki/）：

{summary_line}

{entry_list}

请执行：
1. 为每个新条目生成 wiki/entries/{{type}}/{{平台}}_{{标识}}.md（精炼页，不复制原文）
2. 更新 wiki/INDEX.md（追加一行摘要）
3. 检查 wiki/concepts/：新案例是否符合已有概念页？符合则追加，不符合暂不新建
4. （可选）更新 wiki/Home.md 的案例数

完整维护指南（目录结构 / frontmatter 规范 / 模板 / 工作流）：
{guide_path}

检索入口：wiki/INDEX.md（一行一条）→ wiki/entries/（精炼页）→ raw/（原文）
========================================\
"""

# 更新场景的简化提示（仅内容变更，无新条目）
ARCHIVE_UPDATE_TEMPLATE = """\
========================================
归档完成（仅内容更新，无新条目）。

已更新 {update_count} 个条目：
{entry_list}

可选：检查相关 wiki/entries/ 精炼页是否需要同步更新内容。
（如无新条目，通常 wiki 维护工作量小）
========================================\
"""


def get_guide_text():
    """返回完整 GUIDE_TEXT（给文档/帮助命令用）。"""
    return GUIDE_TEXT


def format_archive_trigger(archived_entries, guide_path):
    """生成归档后输出给 agent 的触发语。

    根据是否有"新增"条目选择模板：
    - 有新增 → 完整 SOP 触发语（含 entries/INDEX/concepts 维护步骤）
    - 仅更新 → 简化提示（只提示可选同步）

    archived_entries: list[dict]，每项形如：
        {"type": "bug-solutions", "platform": "EC626", "name": "...",
         "title": "...", "files": [...], "is_new": True/False}
    guide_path: GUIDE_TEXT 文件路径
    """
    new_entries = [e for e in archived_entries if e.get("is_new")]
    updated_entries = [e for e in archived_entries if not e.get("is_new")]

    # 仅更新场景：简化提示
    if not new_entries and updated_entries:
        lines = [f"  - [{e['type']}] {e.get('platform', '?')}/{e['name']}" for e in updated_entries]
        return ARCHIVE_UPDATE_TEMPLATE.format(
            update_count=len(updated_entries),
            entry_list="\n".join(lines),
        )

    # 有新条目：完整触发语
    lines = []
    for e in archived_entries:
        tag = "新增" if e.get("is_new") else "更新"
        files_str = ", ".join(e.get("files", []))
        lines.append(f"  - [{tag}][{e['type']}] {e.get('platform', '?')}/{e['name']} ({files_str})")

    summary = f"已归档 {len(new_entries)} 新 + {len(updated_entries)} 更新条目到 raw/ 区："
    if not new_entries and not updated_entries:
        summary = "本次无新/更新条目："

    return ARCHIVE_TRIGGER_TEMPLATE.format(
        summary_line=summary,
        entry_list="\n".join(lines) if lines else "  (无)",
        guide_path=guide_path,
    )
