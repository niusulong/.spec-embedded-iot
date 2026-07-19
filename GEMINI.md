# Agents — Embedded IoT Skills & Knowledge Base

Read and follow CLAUDE.md for full instructions.

## Quick Reference

This plugin provides specialized skills for embedded software development:

- **Bug Analysis** — Root-cause analysis from AT logs, AP logs, with knowledge base retrieval and pcap parsing
- **Crash Dump Analysis** — Multi-platform: ARM Cortex-R (ASR1603/UIS8850), Cortex-M (EC626/QCX216), RISC-V (UIS8852)
- **Memory Leak Analysis** — call-stack tracking with MAP→source mapping
- **Code Summarization** — Module-level code implementation analysis
- **Knowledge Base** — Persistent cross-project knowledge with vector semantic search
- **Coding Standards** — Neoway C coding standards reference
- **Requirement Management** — Requirement generation, splitting, solution design, and implementation planning

## Available Skills

| Skill | Trigger | Description |
|-------|---------|-------------|
| `spec-bug-analyzer` | spec 分析bug、spec 诊断问题 | Bug root-cause analysis (logs + KB retrieval + pcap) |
| `spec-asr1603-dump-analyzer` | spec 分析dump、crash dump | ASR1603 crash dump (Cortex-R + ThreadX) |
| `spec-ec626-dump-analyzer` | EC dump、EC626崩溃 | EC626 crash dump (Cortex-M + FreeRTOS) |
| `spec-qcx216-dump-analyzer` | QCX216 死机、N706D 崩溃 | QCX216/N706D crash dump (Unisoc Cortex-M3 + FreeRTOS) |
| `spec-uis8850-dump-analyzer` | UIS8850 死机、N706-STD 崩溃 | UIS8850/N706-STD crash dump (Unisoc Cortex-R + FreeRTOS) |
| `spec-uis8852-dump-analyzer` | UIS8852 死机、N706C 崩溃 | UIS8852/N706C crash dump (Unisoc RISC-V + RT-Thread) |
| `spec-memory-leak-analyzer` | 分析内存泄漏、memory leak | Memory leak localization (call-stack tracking) |
| `spec-code-summary` | spec 模块实现、spec 代码分析 | Single module code implementation analysis |
| `spec-project-overview` | spec 项目概览 | Project overview document generation |
| `spec-init` | spec 初始化 | .spec workflow environment init + auto-clone knowledge base |
| `spec-knowledge-archiver` | 归档bug、同步知识库 | Archive documents to persistent knowledge base |
| `spec-neoway-coding-standards` | spec 编码规范 | Neoway C coding standards reference |
| `spec-requirement-generator` | spec 整理需求 | Transform loose requirements into structured docs |
| `spec-requirement-splitter` | spec 拆分需求 | Split complex requirements into smaller units |
| `spec-solution-designer` | spec 设计方案、spec 技术方案 | Requirement → embedded technical solution |
| `spec-implementation-planner` | spec 实施计划、spec 排期 | Solution → code-level plan (delegates to superpowers:writing-plans) |
| `spec-using-agents` | (auto-loaded at session start) | Meta-skill: skill discovery + usage rules + KB access |

## How to Use Skills

Use the `activate_skill` tool to load skills. Skill metadata is available at session start.

## Knowledge Base Access

```bash
python ~/.spec-embedded-iot/skills/spec-knowledge-archiver/scripts/embed_search.py "{query}" --platform {platform} --top 5
```
