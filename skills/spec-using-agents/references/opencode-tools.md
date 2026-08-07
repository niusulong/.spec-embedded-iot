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

## TShark2MCP MCP server（pcap 分析后端）

`spec-bug-analyzer` 的 pcap 报文解析由内嵌的 TShark2MCP MCP server（封装 tshark）提供。Claude Code 经插件 `.mcp.json` 自动注册；**OpenCode 需手动注册**到全局配置 `~/.config/opencode/opencode.json`（per-project 配置有已知不生效问题，用全局）：

```json
{
  "mcp": {
    "tshark": {
      "type": "local",
      "command": ["python", "-m", "tshark_mcp"],
      "enabled": true
    }
  }
}
```

前置（一次性）：
1. 插件子模块已 init：`git submodule update --init vendor/TShark2MCP`
2. 依赖已装：`pip install -r requirements.txt`（含 `-e vendor/TShark2MCP`，自动拉 `mcp` + `pydantic`；Windows 下 tshark 随子模块自带，无需另装 Wireshark）

注册后 `tshark` server 提供 5 个工具：`get_pcap_overview` / `list_conversations` / `extract_packets` / `extract_stream` / `get_statistics`。用法与避坑见 `skills/spec-bug-analyzer/references/pcap-analyzer-guide.md`。
