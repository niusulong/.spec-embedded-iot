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

To search the knowledge base directly:

```bash
python ../spec-knowledge-archiver/scripts/embed_search.py "{query}" --platform {platform} --top 5
```
