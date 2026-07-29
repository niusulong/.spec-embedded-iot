# OpenCode Tool Mapping

Skills use Claude Code tool names. When you encounter these in a skill, use your platform equivalent:

| Skill references | OpenCode equivalent |
|-----------------|---------------------|
| `Skill` tool (invoke a skill) | OpenCode's native `skill` tool |
| `TodoWrite` (task tracking) | `todowrite` |
| `Task` tool (dispatch subagent) | Use OpenCode's subagent system (@mention) |
| `Read`, `Write`, `Edit` (files) | Your native file tools |
| `Bash` (run commands) | Your native shell tools |
| `Grep` (search content) | Your native search tools |
| `Glob` (find files) | Your native file tools |

## Knowledge Base Access in OpenCode

Use OpenCode's native `skill` tool to load skills:

```
use skill tool to list skills
use skill tool to load spec-bug-analyzer
```

To retrieve knowledge directly, use **progressive loading** (no vector DB / no CLI search):

1. Read `~/.spec-embedded-iot/knowledge/wiki/INDEX.md` — global catalog of all entries.
2. Read `~/.spec-embedded-iot/knowledge/wiki/entries/*.md` — refined per-entry pages.
3. Fall back to `~/.spec-embedded-iot/knowledge/raw/platform/{platform}/...` — original documents.

To archive `.spec/` documents to the raw area:

```bash
python ../spec-knowledge-archiver/scripts/kb.py archive
```
