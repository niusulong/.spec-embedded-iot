# Pi Tool Mapping

Skills use Claude Code tool names. When you encounter these in a skill, use your Pi equivalent:

| Skill references | Pi equivalent |
|-----------------|----------------|
| `Skill` tool (invoke a skill) | `/skill:<name>` slash command — Pi loads all `skills/*/SKILL.md` into your context natively; just invoke the name |
| `Task` tool (dispatch subagent) | No built-in subagent tool; run steps in-session, or install a subagent extension |
| `TodoWrite` (task tracking) | No built-in task tool; use a `todo` extension or track in a scratch file |
| `Read`, `Write`, `Edit` (files) | `read`, `write`, `edit` built-ins (lowercase) |
| `Bash` (run commands) | `bash` built-in (exposes `PI_SESSION_ID`, `PI_SESSION_FILE`, `PI_PROVIDER`, `PI_MODEL`) |
| `Grep` (search content) | `grep` built-in |
| `Glob` (find files) | `find` / `ls` built-ins |

## Skill Access in Pi

Pi auto-discovers every `skills/*/SKILL.md` and surfaces them in the system prompt. Invoke one with:

```
/skill:spec-bug-analyzer
```

The `spec-using-agents` meta-skill is **auto-injected** at the first turn of each session by the `extensions/spec-session-start.ts` extension (mirrors the Claude Code SessionStart hook), so its routing rules are already in your context — you do not need to load it manually.

## Knowledge Base Access in Pi

Retrieve knowledge via **progressive loading** (no vector DB / no CLI search):

1. Read `~/.spec-embedded-iot/knowledge/wiki/INDEX.md` — global catalog of all entries.
2. Read `~/.spec-embedded-iot/knowledge/wiki/entries/*.md` — refined per-entry pages.
3. Fall back to `~/.spec-embedded-iot/knowledge/raw/platform/{platform}/...` — original documents.

To archive `.spec/` documents to the raw area:

```bash
python ~/.spec-embedded-iot/skills/spec-knowledge-archiver/scripts/kb.py archive --project {project-root} --type bug --all
```

## Notes

- Tool names are **lowercase** in Pi (`read`, not `Read`).
- Pi's `bash` tool runs with full system permissions. The Python analysis scripts (pcap parser, dump parsers, knowledge archiver) work unchanged — but install their Python deps yourself (Pi does not install Python dependencies).
- Built-in tools truncate large output at **50 KB / 2000 lines**; oversized dump/log output is auto-truncated and the full version is written to a temp file.
