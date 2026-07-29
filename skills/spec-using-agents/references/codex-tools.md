# Codex Tool Mapping

Skills use Claude Code tool names. When you encounter these in a skill, use your platform equivalent:

| Skill references | Codex equivalent |
|-----------------|------------------|
| `Task` tool (dispatch subagent) | `spawn_agent` |
| Multiple `Task` calls (parallel) | Multiple `spawn_agent` calls |
| Task returns result | `wait_agent` |
| Task completes automatically | `close_agent` to free slot |
| `TodoWrite` (task tracking) | `update_plan` |
| `Skill` tool (invoke a skill) | Skills load natively — just follow the instructions |
| `Read`, `Write`, `Edit` (files) | Use your native file tools |
| `Bash` (run commands) | Use your native shell tools |
| `Grep` (search content) | Use your native search tools |
| `Glob` (find files) | Use your native file tools |

## Knowledge Base Access in Codex

Codex does not have a native skill tool. Retrieve knowledge via **progressive loading** (no vector DB / no CLI search):

1. Read `~/.spec-embedded-iot/knowledge/wiki/INDEX.md` — global catalog of all entries.
2. Read `~/.spec-embedded-iot/knowledge/wiki/entries/*.md` — refined per-entry pages.
3. Fall back to `~/.spec-embedded-iot/knowledge/raw/platform/{platform}/...` — original documents.

To read a knowledge base entry directly:

```bash
cat ~/.spec-embedded-iot/knowledge/raw/platform/{platform}/bug-solutions/{filename}.md
```

To archive `.spec/` documents to the raw area:

```bash
python ../spec-knowledge-archiver/scripts/kb.py archive
```

## Subagent Dispatch

To use subagent-based skills (like bug analysis with parallel verification), add to your Codex config (`~/.codex/config.toml`):

```toml
[features]
multi_agent = true
```
