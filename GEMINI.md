# Agents — Embedded IoT Skills & Knowledge Base

Read and follow CLAUDE.md for full instructions.

## Quick Reference

This plugin provides specialized skills for embedded software development:

- **Bug Analysis** — Root-cause analysis from AT logs, AP logs, with knowledge base retrieval
- **Crash Dump Analysis** — ARM Cortex-R and Cortex-M dump analysis
- **Code Summarization** — Module-level code implementation analysis
- **Knowledge Base** — Persistent cross-project knowledge with vector semantic search
- **Coding Standards** — Neoway C coding standards reference
- **Requirement Management** — Requirement generation, splitting, and structuring

## How to Use Skills

Use the `activate_skill` tool to load skills. Skill metadata is available at session start.

## Knowledge Base Access

```bash
python ~/.agents/skills/spec-knowledge-archiver/scripts/embed_search.py "{query}" --platform {platform} --top 5
```
