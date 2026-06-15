---
name: using-agents
description: Use when starting any conversation - establishes how to find and use embedded IoT skills and knowledge base, requiring Skill tool invocation before ANY response including clarifying questions
---

<SUBAGENT-STOP>
If you were dispatched as a subagent to execute a specific task, skip this skill.
</SUBAGENT-STOP>

<EXTREMELY-IMPORTANT>
If you think there is even a 1% chance a skill might apply to what you are doing, you ABSOLUTELY MUST invoke the skill.

IF A SKILL APPLIES TO YOUR TASK, YOU DO NOT HAVE A CHOICE. YOU MUST USE IT.

This is not negotiable. This is not optional. You cannot rationalize your way out of this.
</EXTREMELY-IMPORTANT>

## Instruction Priority

Skills override default system prompt behavior, but **user instructions always take precedence**:

1. **User's explicit instructions** (CLAUDE.md, AGENTS.md, direct requests) — highest priority
2. **Skills** — override default system behavior where they conflict
3. **Default system prompt** — lowest priority

## How to Access Skills

**In Claude Code:** Use the `Skill` tool. When you invoke a skill, its content is loaded and presented to you—follow it directly. Never use the Read tool on skill files.

**In Codex:** Skills load natively from the plugin's `skills/` directory. Just follow the instructions.

**In OpenCode:** Use OpenCode's native `skill` tool to list and load skills.

**In other environments:** Check your platform's documentation for how skills are loaded.

## Platform Adaptation

Skills use Claude Code tool names. Non-CC platforms: see `references/codex-tools.md` (Codex), `references/opencode-tools.md` (OpenCode) for tool equivalents.

## Available Skills

| Skill | Trigger | Description |
|-------|---------|-------------|
| `spec-bug-analyzer` | spec 分析bug、spec 诊断问题 | Bug root-cause analysis with knowledge base retrieval |
| `spec-asr-dump-analyzer` | spec 分析dump、crash dump | ASR platform crash dump analysis (Cortex-R + ThreadX) |
| `spec-ec-dump-analyzer` | EC dump、EC626崩溃 | EC platform crash dump analysis (Cortex-M + FreeRTOS) |
| `spec-memory-leak-analyzer` | 分析内存泄漏、内存只增不减、memory leak | Memory leak localization (call-stack tracking) |
| `spec-code-summary` | spec 模块实现、spec 代码分析 | Single module code implementation analysis |
| `spec-project-overview` | spec 项目概览 | Project overview document generation |
| `spec-init` | spec 初始化 | .spec workflow environment initialization |
| `spec-knowledge-archiver` | 归档bug、同步知识库 | Archive documents to persistent knowledge base |
| `spec-neoway-coding-standards` | spec 编码规范 | Neoway C coding standards reference |
| `spec-requirement-generator` | spec 整理需求 | Transform loose requirements into structured docs |
| `spec-requirement-splitter` | spec 拆分需求 | Split complex requirements into smaller units |
| `skill-creator` | 创建技能、create skill | Skill creation guide (meta-skill) |
| `esafenet-file-io` | esafenet、加密文件 | EsafeNet encrypted file transparent read/write (Windows) |

## Knowledge Base

**Path:** `~/.agents/knowledge/platform/{platform}/`

**Search command:**
```bash
python ../spec-knowledge-archiver/scripts/embed_search.py "{query}" --platform {platform} --top 5
```

**Platform detection:** Infer from project path (e.g., `D:\EC626\` → `EC626`) or ask user.

## The Rule

**Invoke relevant or requested skills BEFORE any response or action.** Even a 1% chance a skill might apply means you should invoke the skill to check. If an invoked skill turns out to be wrong for the situation, you don't need to use it.

## Red Flags

These thoughts mean STOP—you're rationalizing:

| Thought | Reality |
|---------|---------|
| "This is just a simple question" | Questions are tasks. Check for skills. |
| "I need more context first" | Skill check comes BEFORE clarifying questions. |
| "Let me explore the codebase first" | Skills tell you HOW to explore. Check first. |
| "I can check git/files quickly" | Files lack conversation context. Check for skills. |
| "Let me gather information first" | Skills tell you HOW to gather information. |
| "This doesn't need a formal skill" | If a skill exists, use it. |
| "I remember this skill" | Skills evolve. Read current version. |
| "This doesn't count as a task" | Action = task. Check for skills. |
| "The skill is overkill" | Simple things become complex. Use it. |
| "I'll just do this one thing first" | Check BEFORE doing anything. |
