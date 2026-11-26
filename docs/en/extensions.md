# Extensions: MCP, Rules, Plugins, Skills & Hooks

## Table of Contents

- [Overview](#overview)
- [MCP Server Configuration](#mcp-server-configuration)
- [Rules Configuration](#rules-configuration)
- [Plugin System](#plugin-system)
- [Skill System](#skill-system)
- [Hook System](#hook-system)

---

## Overview

IllusionAgent resolves resources from a user configuration root, the current project, built-in content, and enabled plugins.

- `<user-config>` is selected by `ILLUSION_CONFIG_DIR`. It defaults to `~/.illusion`.
- Project-level resources live under `<project>/.illusion`.
- Resource discovery reads existing files and directories. Missing resource directories and files are skipped without being created. Explicit write, install, or initialization commands create their destinations when needed.

The loader order depends on the resource type. Project skills override user skills with the same name, while commands use first discovery so user commands take precedence over project and plugin commands. Plugin resources are loaded only for enabled plugins.

---

## MCP Server Configuration

### Configuration Types

| Type | Fields | Description |
|------|--------|-------------|
| `stdio` | command, args, env, cwd, log_file, enabled | Standard I/O communication |
| `http` | url, headers, enabled | HTTP protocol (Streamable HTTP; aliases: `streamable-http`/`streamableHttp`/`streamable_http`/`streamablehttp`) |
| `sse` | url, headers, enabled | Server-Sent Events protocol |

All types support `enabled` field (default `true`). Set it to `false` to disable a server without removing its configuration.

The `type` field is optional. When omitted, the server is treated as `stdio`:

```json
{
  "command": "python",
  "args": ["server.py"]
}
```

### Standard File Locations

| Source | Standard location | Notes |
|--------|-------------------|-------|
| User-level MCP | `<user-config>/mcp.json` | `<user-config>` is selected by `ILLUSION_CONFIG_DIR`, default `~/.illusion` |
| Project-level MCP | `<project>/.illusion/mcp.json` (preferred) or `<project>/.illusion/.mcp.json` | The dot-file is used only when the preferred file is absent |
| Settings MCP | `<user-config>/settings.json` → `mcp_servers` | The structured settings source |
| Plugin MCP | Plugin root `.mcp.json`, the manifest `mcp_file` value, then plugin root `mcp.json` | Plugin servers are registered as `{plugin_name}:{server_name}` |

The standalone file is named `mcp.json` at both scopes. The legacy `<user-config>/mcp/` and `<project>/.illusion/mcp/` directories are ignored. To migrate `mcp/filesystem.json`, put its server object under `mcpServers.filesystem` in `mcp.json` at the same scope. If an old file already contains a named map, merge its entries into `mcpServers` under their existing names. This migration is manual and leaves the old files intact. Resource discovery skips missing paths.

Plugin manifests may add MCP file paths or inline definitions through `mcpServers` / `mcp_servers`. Manifest paths are relative to the plugin root and cannot escape it.

### File Formats

A standard MCP file can contain one server object:

```json
{
  "command": "python",
  "args": ["server.py"],
  "enabled": true
}
```

It can contain a named server map under `mcpServers` or `mcp_servers`:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "./data"]
    },
    "remote-api": {
      "type": "http",
      "url": "https://api.example.com/mcp",
      "headers": {"Authorization": "Bearer token"}
    }
  }
}
```

An unwrapped map such as `{ "filesystem": { ... } }` is also accepted. The nested `mcp.servers`, `mcp.mcpServers`, and `mcp.mcp_servers` forms are accepted as well.

### Merge Order

The effective order is project file, user file, `settings.json`, then plugin servers. Project entries override same-named user entries, and user entries override same-named settings entries. Plugin servers use a `{plugin_name}:{server_name}` namespace and do not replace an existing non-plugin entry.

CLI management:

```bash
illusion mcp list
illusion mcp add <name> <config>
illusion mcp remove <name>
```

### Source Reference

- Config loading: `src/illusion/mcp/config.py`
- Path resolution: `src/illusion/config/paths.py`
- Type definitions: `src/illusion/mcp/types.py`

---

## Rules Configuration

Rules are `.md` files that provide project-specific instructions to the AI.

### Discovery Locations

Rules are discovered from:
1. `{cwd}/.claude/rules/*.md` — sorted by filename
2. `{cwd}/.illusion/rules/*.md` — sorted by filename
3. AI instruction files (`CLAUDE.md`, `ILLUSION.md`, `AGENTS.md`) in the project root, `.claude/`, and `.illusion/` directories

### Rule File Format

Each `.md` file is an independent rule. The filename determines the sort order:

```
.claude/rules/
├── 01-python-style.md
├── 02-testing.md
└── 03-git-workflow.md
```

### Initialization

The `/init` command generates default rules at `.illusion/rules/`:
- `python-style.md` — Python code style rules
- `testing.md` — Testing framework and conventions
- `project-structure.md` — Project structure guide

### Source Reference

- Discovery logic: `src/illusion/prompts/claudemd.py` — `discover_claude_md_files()`
- Rule generation: `src/illusion/commands/init/generation/rules.py`

---

## Plugin System

### Plugin Directories

1. **User-level**: `<user-config>/plugins/`
2. **Project-level**: `<project>/.illusion/plugins/`

`<user-config>` is selected by `ILLUSION_CONFIG_DIR`, default `~/.illusion`. Discovery reads these roots when they already exist. It does not create missing plugin directories.

### Plugin Discovery

Each visible immediate subdirectory of a plugin root is considered only when it contains the first existing manifest from this ordered list:

1. `.illusion-plugin/plugin.json`
2. `.claude-plugin/plugin.json`
3. `.codex-plugin/plugin.json`
4. `.zcode-plugin/plugin.json`
5. `plugin.json`

The first existing candidate determines the manifest. A malformed first candidate causes that plugin to be skipped. Hidden plugin directories are not discovered by the root scan.

### Plugin Manifest

The manifest is JSON. Resource paths declared in it are relative to the plugin root and are rejected when they escape that root.

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "My custom plugin",
  "enabled_by_default": true,
  "skills_dir": "skills",
  "hooks_file": "hooks.json",
  "mcp_file": "mcp.json",
  "commands": "commands",
  "agents": "agents",
  "skills": "skills",
  "hooks": "hooks/hooks.json",
  "mcpServers": ".mcp.json"
}
```

Full manifest fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | required | Plugin name |
| `version` | string | `"0.0.0"` | Plugin version |
| `description` | string | `""` | Plugin description |
| `enabled_by_default` | bool | true | Initial enabled state |
| `skills_dir` | string | `"skills"` | Default skills entry |
| `hooks_file` | string | `"hooks.json"` | Default hook file candidate |
| `mcp_file` | string | `"mcp.json"` | Default MCP file candidate |
| `commands` | string \| list \| dict | null | Command directory, paths, or inline mappings |
| `agents` | string \| list \| dict | null | Agent directory, paths, or inline mappings |
| `skills` | string \| list | null | Skill directory or paths. When omitted, `skills_dir` is used |
| `hooks` | string \| dict \| list | null | Hook paths or inline definitions |
| `mcpServers` / `mcp_servers` | string \| list \| dict | null | MCP paths or inline definitions |
| `settings` | dict | null | Plugin default settings |
| `user_config` / `userConfig` | dict | null | Plugin user configuration metadata |

### Plugin Resource Defaults

When a manifest does not replace them with explicit entries, the loader checks:

- `skills/` for skills
- `commands/` for commands
- `agents/` for agents
- `hooks/hooks.json`, the `hooks_file` value, and `hooks.json` for hooks
- `.mcp.json`, the `mcp_file` value, and `mcp.json` for MCP

All missing resource paths are skipped without creating directories.

### Plugin Directory Structure

```text
my-plugin/
├── .illusion-plugin/plugin.json  # or another manifest candidate above
├── skills/
│   ├── my-skill/
│   │   └── SKILL.md
│   └── another-skill.md
├── commands/                     # Slash commands (.md files)
├── agents/                       # Agent definitions (.md files)
├── hooks/
│   └── hooks.json                # Hook definitions
├── .mcp.json                     # MCP server configuration
└── settings.json                 # Plugin default settings
```

### Plugin Enable/Disable

The Web plugin switch and the terminal commands `/plugin enable <name>` and `/plugin disable <name>` save the override under the manifest's `name`. The installation location determines the file.

| Plugin installation | File containing `enabled_plugins` |
|---|---|
| User or built-in, under `<user-config>/plugins/` | `<user-config>/settings.json` |
| Project, under `<project>/.illusion/plugins/` | `<project>/.illusion/permissions.json` |

For example, to enable a project installation of `academic-research-skills`, merge this entry into the project's `permissions.json`:

```json
{
  "enabled_plugins": { "academic-research-skills": true }
}
```

An absent override uses `manifest.enabled_by_default`. Project installations read only their own project's override, including when a global key already exists. A matching `denied_plugins` entry (or `"*"`) in this project's permissions takes precedence for plugins from every source. An ordinary toggle preserves that policy.

Plugin commands, skills and hooks refresh when the switch changes. The workspace loads plugin MCP servers when it builds its runtime. Restart the Web server or terminal process to apply a change to those connections.

### Plugin Skill Naming

All plugin skills are registered with format `{plugin_name}:{skill_name}` to avoid conflicts.

### Plugin Hook Variables

Hook commands support `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` variable substitution.

### CLI Management

```bash
illusion plugin list
illusion plugin install <source>
illusion plugin uninstall <name>
```

In the terminal session, use `/plugin enable <name>` and `/plugin disable <name>`.

### Source Reference

- Plugin loader: `src/illusion/plugins/loader.py`
- Manifest schema: `src/illusion/plugins/schemas.py`
- Plugin types: `src/illusion/plugins/types.py`

---

## Skill System

### Skill Sources (priority order)

1. **Builtin skills**: `src/illusion/skills/bundled/content/*.md`
2. **User skills**: `<user-config>/skills/` with `<name>/SKILL.md`, `<name>.md`, or YAML skill files
3. **Project skills**: `<project>/.illusion/skills/` with `<name>/SKILL.md`, `<name>.md`, or YAML skill files
4. **Plugin skills**: from the skill entries of enabled plugins

`<user-config>` is selected by `ILLUSION_CONFIG_DIR`, default `~/.illusion`. Missing skill directories are skipped without being created. Skills register in this order, so later project and plugin skills can replace an earlier same-name skill.

### Command Sources

Slash commands are separate from skills and are loaded from:

1. User commands: `<user-config>/commands/**/*.md`
2. Project commands: `<project>/.illusion/commands/**/*.md`
3. Enabled plugin command entries, including the default `commands/` directory and manifest mappings

Relative subdirectories become colon-separated command names. Command registration uses first discovery, so a user command keeps precedence over a project or plugin command with the same name. Missing command directories are skipped without being created.

### Agent Sources

Agent definitions are loaded from:

1. Built-in agent definitions
2. User Markdown files: `<user-config>/agents/*.md`
3. Project Markdown files: `<project>/.illusion/agents/*.md`
4. Agent entries from enabled plugins, including the default `agents/` directory and manifest mappings

The later source replaces an earlier definition with the same agent name. Missing agent directories are skipped without being created.

### SKILL.md Format

Supports YAML frontmatter:

```markdown
---
description: What this skill does
allowed-tools: Bash, Read, Write
model: claude-sonnet-4-6
context: fork
effort: high
hooks:
  PreToolUse:
    - matcher: Bash
      hooks:
        - type: command
          command: echo check
---

Skill content in markdown...
```

#### Frontmatter Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Skill name (auto-derived from filename if omitted) |
| `description` | string | Skill description (shown in skill list) |
| `allowed-tools` | string\|list | Comma-separated or list of allowed tool names |
| `model` | string | Model override for this skill |
| `context` | string | `inline` (expand into conversation) or `fork` (sub-agent) |
| `effort` | string | Reasoning effort override |
| `hooks` | object | Hooks to register when skill is invoked |
| `agent` | string | Agent type to use |
| `disable_model_invocation` | bool | Disable model invocation |
| `skill_root` | string | Root directory for skill resources |

### Built-in Skills

| Skill | Description |
|-------|-------------|
| `debug` | Systematic debugging workflow |
| `verify` | Code change verification |
| `loop` | Recurring task execution |
| `batch` | Batch operations |
| `remember` | Memory management |
| `simplify` | Code review for reuse and quality |
| `skillify` | Convert patterns into skills |
| `stuck` | Break through blockers |
| `update-config` | Configure settings.json |

### Source Reference

- Skill loader: `src/illusion/skills/loader.py`
- Skill types: `src/illusion/skills/types.py`
- Skill registry: `src/illusion/skills/registry.py`

---

## Hook System

### Supported Events (27 events)

| Event | Matcher | Description |
|-------|---------|-------------|
| `PreToolUse` | tool_name | Before tool execution |
| `PostToolUse` | tool_name | After tool execution |
| `PostToolUseFailure` | tool_name | After tool execution fails |
| `PermissionDenied` | tool_name | After auto mode classifier denies |
| `Notification` | notification_type | When notifications are sent |
| `UserPromptSubmit` | — | When user submits a prompt |
| `SessionStart` | source | New session started |
| `SessionEnd` | reason | Session ending |
| `Stop` | — | Before Claude concludes response |
| `StopFailure` | error | Turn ends due to API error |
| `SubagentStart` | agent_type | Subagent started |
| `SubagentStop` | agent_type | Subagent concludes |
| `PreCompact` | trigger | Before compaction |
| `PostCompact` | trigger | After compaction |
| `PermissionRequest` | tool_name | Permission dialog displayed |
| `Setup` | trigger | Repo setup |
| `ConfigChange` | source | Configuration file changes |
| `InstructionsLoaded` | load_reason | Instruction file loaded |
| `WorktreeCreate` | — | Create worktree |
| `WorktreeRemove` | — | Remove worktree |
| `CwdChanged` | — | Working directory changes |
| `FileChanged` | — | Watched file changes |
| `TaskCreated` | — | Task being created |
| `TaskCompleted` | — | Task being completed |
| `TeammateIdle` | — | Teammate about to idle |
| `Elicitation` | mcp_server_name | MCP elicitation request |
| `ElicitationResult` | mcp_server_name | After user responds to elicitation |

### Hook Types

| Type | Required | Optional | Description |
|------|----------|----------|-------------|
| `command` | `command` | `if`, `shell`, `timeout`, `statusMessage`, `once`, `async` | Execute shell command |
| `prompt` | `prompt` | `if`, `model`, `timeout`, `statusMessage`, `once` | Use LLM for verification |
| `http` | `url` | `if`, `timeout`, `headers`, `allowedEnvVars`, `statusMessage`, `once` | Send HTTP POST |
| `agent` | `prompt` | `if`, `model`, `timeout`, `statusMessage`, `once` | Use Agent for verification |

### Hook Configuration Format

Matcher-based structure:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "echo 'Tool: $ARGUMENTS' >> /tmp/tool.log",
            "timeout": 30
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "http",
            "url": "https://hooks.example.com/tool-complete",
            "headers": {"Authorization": "Bearer token"}
          }
        ]
      }
    ]
  }
}
```

### Matcher Patterns

| Pattern | Example | Behavior |
|---------|---------|----------|
| Empty / `*` | `""` | Matches everything |
| Exact match | `"Bash"` | Matches exact tool name |
| Pipe-separated | `"Write\|Edit"` | Matches any in list |
| Regex | `"^git .*"` | Regex match |

### Common Hook Options

| Option | Type | Description |
|--------|------|-------------|
| `if` | string | Permission rule syntax filter (e.g. `"Bash(git *)"`) |
| `timeout` | int | Timeout in seconds |
| `once` | bool | Hook runs once then auto-removes |
| `statusMessage` | string | Custom spinner message |

### Command Hook Environment Variables

| Variable | Description |
|----------|-------------|
| `CLAUDE_PROJECT_DIR` | Current working directory |
| `CLAUDE_SESSION_ID` | Current session ID |
| `CLAUDE_PLUGIN_ROOT` | Plugin installation directory (if from plugin) |
| `CLAUDE_PLUGIN_DATA` | Plugin data directory |
| `CLAUDE_ENV_FILE` | Write bash exports here to apply env to subsequent commands |

Use `$ARGUMENTS` in the command string to inject the hook input JSON.

### Hook Registration Sources

Hooks are loaded from:

1. `settings.json` → `hooks`
2. User-level local JSON files: `<user-config>/hooks.json`, `<user-config>/hooks/hooks.json`, and other `<user-config>/hooks/*.json`
3. Project-level local JSON files: `<project>/.illusion/hooks.json`, `<project>/.illusion/hooks/hooks.json`, and other `<project>/.illusion/hooks/*.json`
4. Enabled plugin hook entries, including the default `hooks/hooks.json`, the manifest `hooks_file` value, `hooks.json`, explicit paths, and inline definitions

`<user-config>` is selected by `ILLUSION_CONFIG_DIR`, default `~/.illusion`. Hook discovery reads existing files and does not create the configuration root or `hooks` directories.

### Hook Result Aggregation

Multiple hooks on the same event are aggregated with priority: `deny` > `ask` > `allow`.

### Source Reference

- Hook loader: `src/illusion/hooks/loader.py`
- Hook events: `src/illusion/hooks/events.py`
- Hook schemas: `src/illusion/hooks/schemas.py`
- Hook types: `src/illusion/hooks/types.py`
