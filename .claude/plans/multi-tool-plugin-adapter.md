# 多工具插件适配方案（参照 superpowers 架构）

## Context

当前项目 `~/.agents/` 包含 12 个 skills 和一个按芯片平台组织的知识库。目标是按照 superpowers 插件的多工具架构，将项目改造为同时支持 Claude Code、Codex、OpenCode 的标准插件。

## 参照架构：superpowers v5.1.0

superpowers 通过以下机制实现多工具支持：
- `.claude-plugin/plugin.json` — Claude Code 插件清单
- `.codex-plugin/plugin.json` — Codex 插件清单（含 skills/agents/commands 路径和 interface 元数据）
- `.opencode/plugins/superpowers.js` — OpenCode ESM 插件模块（通过 config hook 注册 skills 路径，通过 chat.messages.transform 注入 bootstrap）
- `package.json` — 指向 OpenCode 插件入口
- `hooks/hooks.json` — Claude Code SessionStart 钩子，注入 bootstrap 技能内容
- `hooks/session-start` — bash 脚本，读取 bootstrap SKILL.md 并输出为 JSON additionalContext
- `hooks/run-hook.cmd` — 跨平台 polyglot（batch+bash）钩子运行器
- `skills/using-superpowers/SKILL.md` — bootstrap 技能，教 agent 如何发现和调用其他技能
- `AGENTS.md` — Codex/Copilot 指令文件（内容引用 CLAUDE.md）
- `GEMINI.md` — Gemini CLI 指令文件

## 改造方案

### 最终目录结构

```
~/.agents/
├── .claude-plugin/
│   └── plugin.json              # Claude Code 插件清单
├── .codex-plugin/
│   └── plugin.json              # Codex 插件清单
├── .opencode/
│   ├── INSTALL.md               # OpenCode 安装说明
│   └── plugins/
│       └── agents.js            # OpenCode ESM 插件入口
├── .cursor-plugin/
│   └── plugin.json              # Cursor 插件清单（可选）
├── hooks/
│   ├── hooks.json               # Claude Code hooks 配置
│   ├── hooks-cursor.json        # Cursor hooks 配置（可选）
│   ├── run-hook.cmd             # 跨平台 polyglot 钩子运行器
│   └── session-start            # SessionStart 钩子脚本
├── skills/
│   ├── using-agents/            # 新增：bootstrap 技能
│   │   ├── SKILL.md
│   │   └── references/
│   │       ├── codex-tools.md
│   │       └── opencode-tools.md
│   ├── skill-creator/           # 现有技能（不变）
│   ├── spec-bug-analyzer/
│   ├── spec-code-summary/
│   ├── spec-dump-analyzer/
│   ├── spec-ec-dump-analyzer/
│   ├── spec-init/
│   ├── spec-knowledge-archiver/
│   ├── spec-neoway-coding-standards/
│   ├── spec-project-overview/
│   ├── spec-requirement-generator/
│   ├── spec-requirement-splitter/
│   └── esafenet-file-io/
├── knowledge/                   # 知识库（不变）
│   ├── README.md
│   ├── platform/
│   └── vector_db/
├── package.json                 # OpenCode 插件入口声明
├── AGENTS.md                    # Codex/Copilot 指令（引用 CLAUDE.md）
├── CLAUDE.md                    # 主指令文件（插件说明 + 知识库路径）
├── gemini-extension.json        # Gemini CLI 扩展清单（可选）
└── docs/                        # 现有文档（不变）
```

### 实施步骤

#### Step 1: 创建 CLAUDE.md 主指令文件

`~/.agents/CLAUDE.md` — 插件的核心指令文件，所有工具共用：

内容包括：
- 插件简介（嵌入式 IoT 开发技能库 + 知识库）
- 知识库路径说明（`~/.agents/knowledge/platform/{平台}/`）
- 可用技能列表（名称 + 触发条件摘要）
- 知识库使用规则（向量搜索命令、归档流程）
- 平台检测规则（从项目路径推断芯片平台）

#### Step 2: 创建 AGENTS.md

`~/.agents/AGENTS.md` — Codex/Copilot 指令文件：

```markdown
# Agents — Embedded IoT Skills & Knowledge Base

Read and follow CLAUDE.md for full instructions.

## Quick Reference

This plugin provides specialized skills for embedded software development:
- Bug analysis, crash dump analysis, code summarization
- Knowledge base with vector semantic search
- Coding standards and requirement management

Use your native skill/tool system to invoke skills.
See references/codex-tools.md for tool name mapping.
```

#### Step 3: 创建 Claude Code 插件清单

`~/.agents/.claude-plugin/plugin.json`:
```json
{
  "name": "agents",
  "description": "嵌入式 IoT 开发技能库：Bug 分析、Dump 分析、代码总结、知识库检索，按芯片平台组织",
  "version": "1.0.0",
  "author": { "name": "niusulong" },
  "homepage": "https://github.com/niusulong/agents",
  "repository": "https://github.com/niusulong/agents",
  "license": "Apache-2.0",
  "keywords": ["embedded", "iot", "bug-analysis", "knowledge-base", "neoway"]
}
```

#### Step 4: 创建 Codex 插件清单

`~/.agents/.codex-plugin/plugin.json`:
```json
{
  "name": "agents",
  "version": "1.0.0",
  "description": "嵌入式 IoT 开发技能库：Bug 分析、Dump 分析、代码总结、知识库检索",
  "author": { "name": "niusulong" },
  "keywords": ["embedded", "iot", "bug-analysis", "knowledge-base"],
  "skills": "./skills/",
  "interface": {
    "displayName": "Agents",
    "shortDescription": "Embedded IoT development skills and knowledge base",
    "longDescription": "Specialized skills for embedded software: bug root-cause analysis, crash dump analysis, code summarization, knowledge base with vector semantic search, coding standards, and requirement management.",
    "developerName": "niusulong",
    "category": "Embedded",
    "capabilities": ["Read", "Write", "Interactive"]
  }
}
```

#### Step 5: 创建 OpenCode 插件

`~/.agents/.opencode/plugins/agents.js` — ESM 模块，参照 superpowers.js：

```javascript
import path from 'path';
import fs from 'fs';
import os from 'os';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export const AgentsPlugin = async ({ client, directory }) => {
  const homeDir = os.homedir();
  const agentsRoot = path.resolve(__dirname, '../..');
  const skillsDir = path.join(agentsRoot, 'skills');

  // 读取 bootstrap 技能内容
  const getBootstrapContent = () => {
    const skillPath = path.join(skillsDir, 'using-agents', 'SKILL.md');
    if (!fs.existsSync(skillPath)) return null;
    const content = fs.readFileSync(skillPath, 'utf8');
    const body = content.replace(/^---\n[\s\S]*?\n---\n/, '');
    return `<EXTREMELY_IMPORTANT>
You have access to embedded IoT development skills and knowledge base.

**The using-agents skill content is included below. It is ALREADY LOADED.**

${body}

**Tool Mapping for OpenCode:**
- Skill tool → OpenCode's native skill tool
- Read, Write, Edit, Bash → Your native tools
- Knowledge base search → use bash to run: python ~/.agents/skills/spec-knowledge-archiver/scripts/embed_search.py "query"
</EXTREMELY_IMPORTANT>`;
  };

  let _bootstrapCache = undefined;

  return {
    config: async (config) => {
      config.skills = config.skills || {};
      config.skills.paths = config.skills.paths || [];
      if (!config.skills.paths.includes(skillsDir)) {
        config.skills.paths.push(skillsDir);
      }
    },

    'experimental.chat.messages.transform': async (_input, output) => {
      if (_bootstrapCache === undefined) _bootstrapCache = getBootstrapContent();
      if (!_bootstrapCache || !output.messages.length) return;
      const firstUser = output.messages.find(m => m.info.role === 'user');
      if (!firstUser || !firstUser.parts.length) return;
      if (firstUser.parts.some(p => p.type === 'text' && p.text.includes('EXTREMELY_IMPORTANT'))) return;
      const ref = firstUser.parts[0];
      firstUser.parts.unshift({ ...ref, type: 'text', text: _bootstrapCache });
    }
  };
};
```

`~/.agents/package.json`:
```json
{
  "name": "agents",
  "version": "1.0.0",
  "type": "module",
  "main": ".opencode/plugins/agents.js"
}
```

`~/.agents/.opencode/INSTALL.md`:
```markdown
# Installing Agents for OpenCode

Add to your opencode.json:
{
  "plugin": ["agents@git+https://github.com/niusulong/agents.git"]
}
Restart OpenCode.
```

#### Step 6: 创建 Hooks（Claude Code SessionStart 注入）

`~/.agents/hooks/hooks.json`:
```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|clear|compact",
        "hooks": [
          {
            "type": "command",
            "command": "\"${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.cmd\" session-start",
            "async": false
          }
        ]
      }
    ]
  }
}
```

`~/.agents/hooks/run-hook.cmd` — 跨平台 polyglot（直接复用 superpowers 的模式）。

`~/.agents/hooks/session-start` — bash 脚本，读取 `skills/using-agents/SKILL.md` 并输出为 JSON additionalContext。

#### Step 7: 创建 using-agents Bootstrap 技能

`~/.agents/skills/using-agents/SKILL.md`:

内容包括：
- 技能发现规则（引用 superpowers 的 "1% chance" 规则）
- 可用技能列表和触发条件
- 知识库访问方法（向量搜索命令、路径规则）
- 平台检测方法
- 工具映射引用（references/codex-tools.md, references/opencode-tools.md）
- 与 superpowers 的优先级关系说明

`references/codex-tools.md` — Codex 工具名映射（参照 superpowers 的 codex-tools.md）。
`references/opencode-tools.md` — OpenCode 工具名映射。

#### Step 8: Cursor 支持（可选）

`~/.agents/.cursor-plugin/plugin.json` — Cursor 插件清单。
`~/.agents/hooks/hooks-cursor.json` — Cursor hooks（使用 `sessionStart` 而非 `SessionStart`）。
`~/.agents/gemini-extension.json` — Gemini CLI 扩展清单（可选）。

## 文件清单

| 文件 | 用途 | 优先级 |
|------|------|--------|
| `.claude-plugin/plugin.json` | Claude Code 插件清单 | P0 |
| `.codex-plugin/plugin.json` | Codex 插件清单 | P0 |
| `.opencode/plugins/agents.js` | OpenCode 插件入口 | P0 |
| `package.json` | OpenCode 插件入口声明 | P0 |
| `hooks/hooks.json` | Claude Code SessionStart 钩子 | P0 |
| `hooks/run-hook.cmd` | 跨平台钩子运行器 | P0 |
| `hooks/session-start` | SessionStart 脚本 | P0 |
| `skills/using-agents/SKILL.md` | Bootstrap 技能 | P0 |
| `skills/using-agents/references/codex-tools.md` | Codex 工具映射 | P1 |
| `skills/using-agents/references/opencode-tools.md` | OpenCode 工具映射 | P1 |
| `CLAUDE.md` | 主指令文件 | P0 |
| `AGENTS.md` | Codex/Copilot 指令 | P1 |
| `.opencode/INSTALL.md` | OpenCode 安装说明 | P2 |
| `.cursor-plugin/plugin.json` | Cursor 插件清单 | P2 |
| `hooks/hooks-cursor.json` | Cursor hooks | P2 |
| `gemini-extension.json` | Gemini 扩展清单 | P2 |

## 验证方式

1. **Claude Code**：将 `~/.agents` 注册为本地插件（或发布到 GitHub 后通过 marketplace 安装），验证 SessionStart 时 bootstrap 技能被注入，`/spec-bug-analyzer` 等技能可正常调用
2. **Codex**：在 `~/.codex/config.toml` 中配置插件路径，验证 AGENTS.md 被加载、技能可发现
3. **OpenCode**：在 `opencode.json` 中配置 `"plugin": ["~/.agents"]`，验证插件加载、skills 路径注册、bootstrap 注入
4. **知识库**：在三个工具中分别运行 `embed_search.py` 测试向量搜索，结果应一致
