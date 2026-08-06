# Installing spec-embedded-iot for Pi (Pi Coding Agent)

[Pi](https://pi.dev) is a terminal coding agent extended via TypeScript extensions, skills, prompt templates, and themes — distributed as **Pi packages**. This repo is a Pi package: it ships 17 embedded-IoT skills (auto-discovered from `skills/`) plus a session-start extension that auto-injects the `spec-using-agents` meta-skill.

## Prerequisites

- Pi installed: `npm install -g --ignore-scripts @earendil-works/pi-coding-agent` (or the installer at https://pi.dev)
- Python 3 (for the analysis scripts: pcap parser, dump parsers, knowledge archiver)

## Install

```bash
# Global (writes to ~/.pi/agent/settings.json)
pi install git:github.com/niusulong/.spec-embedded-iot.git

# Project-level (writes to .pi/settings.json — share with your team)
pi install -l git:github.com/niusulong/.spec-embedded-iot.git

# Try without installing (current run only)
pi -e git:github.com/niusulong/.spec-embedded-iot.git
```

Pi clones the package to `~/.pi/agent/git/github.com/niusulong/.spec-embedded-iot/` and runs `npm install`. The only declared dependency is the **optional** peer dep `@earendil-works/pi-coding-agent` (provided by Pi itself at runtime), so there are no extra packages to download.

## 本地安装（开发推荐）

如果你在本地开发这个包，用**本地路径**安装——Pi 不会复制文件，直接指向当前工作树，你改的 `skills/` / `extensions/` 立即生效（配合 `/reload` 热重载）。这是开发本仓库时最顺手的安装方式：

```bash
# 绝对路径（推荐，从任意目录执行）
pi install /c/Users/20220715012/.spec-embedded-iot          # Git Bash / MSYS 路径
pi install "C:/Users/20220715012/.spec-embedded-iot"        # 或 Windows 原生路径（正斜杠）

# 相对路径（cwd 为仓库父目录时）
pi install ./.spec-embedded-iot

# 项目级（写 .pi/settings.json，随项目共享；默认写 ~/.pi/agent/settings.json 全局）
pi install -l /c/Users/20220715012/.spec-embedded-iot
```

要点：

- **不复制文件**：Pi 把本地路径记进 settings，资源直接从原目录加载。改源码无需重装。
- **热重载**：改完 `SKILL.md` / `.ts` 扩展后，在 Pi 里执行 `/reload` 重新加载扩展、技能、模板、主题。
- 路径指向**目录**时按包规则加载（`pi` 字段 + 约定目录）；指向**单个 `.ts` 文件**时只加载那一个扩展（适合临时试验）。

## Python dependencies (analysis scripts)

Pi does **not** install Python deps. Install them once:

```bash
pip install -r https://raw.githubusercontent.com/niusulong/.spec-embedded-iot/master/requirements.txt
```

## What gets loaded

- **17 skills** — auto-discovered from `skills/*/SKILL.md`, surfaced in Pi's system prompt. Invoke any with `/skill:<name>`.
- **`spec-session-start` extension** — on the first turn of each session, injects the `spec-using-agents` routing rules as hidden context (mirrors the Claude Code SessionStart hook).
- **Knowledge base** — `~/.spec-embedded-iot/knowledge/` is independent of Pi; clone/init it via `spec-init` or the knowledge repo.

## Tool mapping

Skills are written with Claude Code tool names. Pi equivalents (all lowercase built-ins):

| Skill (Claude Code) | Pi |
|---------------------|----|
| `Skill` | `/skill:<name>` |
| `Read` / `Write` / `Edit` | `read` / `write` / `edit` |
| `Bash` | `bash` |
| `Grep` / `Glob` | `grep` / `find` / `ls` |

Full mapping: [`skills/spec-using-agents/references/pi-tools.md`](../skills/spec-using-agents/references/pi-tools.md).

## Verify

Start Pi in any project and ask:

```
列出可用的技能
```

Or invoke a skill directly:

```
/skill:spec-bug-analyzer
```

## Update / Uninstall

```bash
pi update --extensions                                  # reconcile packages to pinned refs
pi remove git:github.com/niusulong/.spec-embedded-iot.git
```

## Troubleshooting

- **Skills not listed**: run `/reload`; confirm the package appears in `pi list`.
- **Meta-skill not auto-injected**: the extension reads `skills/spec-using-agents/SKILL.md` relative to itself; if you filtered skills via `pi config`, make sure `spec-using-agents` is enabled. Skills still work without the injection — it only pre-loads routing rules.
- **`ExtensionAPI` type warnings in your editor**: the import is type-only and erased at runtime; install `@earendil-works/pi-coding-agent` for IDE types, but it is not required to run.
