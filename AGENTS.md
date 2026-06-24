# Agents — Embedded IoT Skills & Knowledge Base

Read and follow CLAUDE.md for full instructions.

## Quick Reference

This plugin provides specialized skills for embedded software development on Neoway IoT platforms:

- **Bug Analysis** — Root-cause analysis from AT logs, AP logs, with knowledge base retrieval
- **Crash Dump Analysis** — ARM Cortex-R (TRACE32) and Cortex-M (EC platform) dump analysis
- **Code Summarization** — Module-level code implementation analysis
- **Knowledge Base** — Persistent cross-project knowledge with vector semantic search
- **Coding Standards** — Neoway C coding standards reference
- **Requirement Management** — Requirement generation, splitting, and structuring

## How to Use Skills

Use your native skill/tool system to invoke skills. See the tool mapping references for your platform:

- Codex: `references/codex-tools.md`
- OpenCode: `references/opencode-tools.md`

## Knowledge Base Access

Search the knowledge base via command line:

```bash
python ~/.agents/skills/spec-knowledge-archiver/scripts/embed_search.py "{query}" --platform {platform} --top 5
```

Knowledge base path: `~/.spec-embedded-iot/knowledge/platform/{platform}/`
