# Installing Agents for OpenCode

## Prerequisites

- [OpenCode](https://opencode.ai) installed

## Installation

Add agents to the `plugin` array in your `opencode.json` (global or project-level):

```json
{
  "plugin": ["agents@git+https://github.com/niusulong/agents.git"]
}
```

Restart OpenCode. The plugin installs through OpenCode's plugin manager and registers all skills.

Verify by asking: "列出可用的技能" or "What skills are available?"

## Usage

Use OpenCode's native `skill` tool:

```
use skill tool to list skills
use skill tool to load spec-bug-analyzer
```

## Tool Mapping

When skills reference Claude Code tools:
- `Skill` tool → OpenCode's native `skill` tool
- `Read`, `Write`, `Edit` → your native file tools
- `Bash` → your native shell tools
- Knowledge base search → `python ~/.agents/skills/spec-knowledge-archiver/scripts/embed_search.py "query"`

## Troubleshooting

### Plugin not loading

1. Check logs: `opencode run --print-logs "hello" 2>&1 | grep -i agents`
2. Verify the plugin line in your `opencode.json`
3. Make sure you're running a recent version of OpenCode

### Skills not found

1. Use `skill` tool to list what's discovered
2. Check that the plugin is loading (see above)
