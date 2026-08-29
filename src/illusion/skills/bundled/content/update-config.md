---
name: update-config
description: Configure Illusion Agent via settings.json, permissions.json, project-level directories, and instruction files. Use for permissions, hooks, env vars, MCP servers, skills, plugins, sandbox, and other settings. Examples: "allow npm commands", "set DEBUG=true", "add a hook to format code after writes", "disable a skill for this project".
---

# Update Config Skill

Modify Illusion Agent configuration across three layers: global `settings.json`, project-level `.illusion/` directory, and AI instruction files.

## CRITICAL: Read Before Write

**Always read the existing settings file before making changes.** Merge new settings with existing ones - never replace the entire file.

## CRITICAL: Use `ask_user_question` for Ambiguity

When the user's request is ambiguous, use `ask_user_question` to clarify:
- Which layer to modify (global / project / instruction file)
- Specific values when multiple options exist
- Whether to add to existing arrays or replace them

## Configuration Layers (Priority: Project > Global > Defaults)

| Layer | File / Directory | Scope | Purpose |
|-------|------------------|-------|---------|
| **Global config** | `~/.illusion/settings.json` | All projects | API config, permissions, hooks, memory, sandbox, MCP, plugins |
| **Global credentials** | `~/.illusion/credentials.json` | All projects | API keys (managed by `illusion auth login`) |
| **Project permissions** | `<project>/.illusion/permissions.json` | Current project | Deny-list for skills/hooks/plugins/MCP/memory/rules/tools |
| **Project MCP** | `<project>/.illusion/mcp/*.json` | Current project | Project-specific MCP servers |
| **Project skills** | `<project>/.illusion/skills/` | Current project | Project-specific skills (override global) |
| **Project plugins** | `<project>/.illusion/plugins/` | Current project | Project-specific plugins |
| **Project rules** | `<project>/.illusion/rules/*.md` | Current project | Project-specific AI rules |
| **Instruction files** | `<project>/CLAUDE.md` / `ILLUSION.md` / `AGENTS.md` | Current project | AI instructions (merged into system prompt) |
| **Instruction files** | `<project>/.claude/CLAUDE.md` | Current project | AI instructions (alternate location) |
| **Instruction files** | `<project>/.illusion/CLAUDE.md` / `ILLUSION.md` / `AGENTS.md` | Current project | AI instructions (alternate location) |
| **Rules files** | `<project>/.claude/rules/*.md` | Current project | Additional AI rules (sorted by filename) |

Environment variable overrides: `ILLUSION_CONFIG_DIR` replaces `~/.illusion/`, `ILLUSION_DATA_DIR` replaces `~/.illusion/data/`, `ILLUSION_LOGS_DIR` replaces `~/.illusion/logs/`.

Configuration priority: CLI arguments > project-level > global settings.json > built-in defaults.

## When to Use Which Layer

| Request | Layer |
|---------|-------|
| "Allow npm commands globally" | Global `settings.json` → `permission.allowed_tools` |
| "Disable a skill for this project" | Project `.illusion/permissions.json` → `denied_skills` |
| "Add an MCP server for this project" | Project `.illusion/mcp/<name>.json` |
| "Add project-specific instructions" | `<project>/CLAUDE.md` |
| "Add a rule for Python style" | `<project>/.illusion/rules/python-style.md` |
| "Add a project-specific skill" | `<project>/.illusion/skills/<name>/SKILL.md` |
| "Set API key" | `illusion auth login` (writes `credentials.json`) |
| "Configure hooks globally" | Global `settings.json` → `hooks` |

## When Hooks Are Required

If the user wants something to happen automatically in response to an EVENT, they need a **hook**.

**These require hooks:**
- "After writing files, run prettier" → `post_tool_use` hook with matcher `write_file|edit_file`
- "Before running bash commands, validate them" → `pre_tool_use` hook with matcher `bash`
- "When session starts, show a greeting" → `session_start` hook

**Hook events (only 4):**
- `session_start` — When session starts
- `session_end` — When session ends
- `pre_tool_use` — Before tool execution (can block)
- `post_tool_use` — After tool execution

---

## Layer 1: Global Configuration (settings.json)

Located at `~/.illusion/settings.json`. Loaded by `load_settings()`.

### Complete Configuration Structure

```json
{
  "env_1": {
    "api_format": "anthropic",
    "base_url": null,
    "api_key": "",
    "model_1": { "name": "claude-sonnet-4-6", "capabilities": ["image"] },
    "model_2": { "name": "claude-opus-4-6", "capabilities": [] }
  },
  "model": "env_1.model_1",
  "context_window": 200000,
  "max_tokens": 16384,
  "max_turns": 200,
  "permission": {
    "mode": "default",
    "allowed_tools": [],
    "denied_tools": [],
    "path_rules": [],
    "denied_commands": []
  },
  "hooks": {},
  "memory": {
    "enabled": true,
    "auto_extract": false,
    "extract_model": null,
    "dream_model": null,
    "directory": null,
    "max_files": 5,
    "max_entrypoint_lines": 200,
    "max_entrypoint_bytes": 25000,
    "extract_interval": 1,
    "dream_min_hours": 24,
    "dream_min_sessions": 5
  },
  "sandbox": {
    "enabled_platforms": [],
    "excluded_commands": [],
    "network": {
      "allowed_domains": [],
      "denied_domains": [],
      "allow_unix_sockets": [],
      "allow_all_unix_sockets": false,
      "allow_local_binding": false,
      "http_proxy_port": null,
      "socks_proxy_port": null
    },
    "filesystem": {
      "allow_read": [],
      "deny_read": [],
      "allow_write": ["."],
      "deny_write": []
    },
    "ignore_violations": {},
    "enable_weaker_nested_sandbox": false,
    "mandatory_deny_search_depth": 3,
    "allow_git_config": false
  },
  "enabled_plugins": {},
  "mcp_servers": {},
  "working_directory": null,
  "ui_language": "en-US",
  "theme": "light",
  "show_thinking": true,
  "effort": "medium",
  "title": {
    "enabled": false,
    "model": null
  }
}
```

### Top-Level Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `env_N` | object | - | Environment config group (EnvConfig) |
| `model` | string | "env_1.model_1" | Active model reference: `env_N.model_N` |
| `context_window` | int | 200000 | Context window size in tokens |
| `max_tokens` | int | 16384 | Maximum output tokens |
| `max_turns` | int | 200 | Maximum conversation turns |
| `ui_language` | string | "" | UI language ("en-US" / "zh-CN"); empty triggers the first-login prompt |
| `theme` | string | "light" | Web UI theme: light / dark / system |
| `show_thinking` | bool | true | Show thinking process |
| `effort` | string | "medium" | Reasoning effort: low/medium/high/xhigh/max |
| `working_directory` | string\|null | null | Fixed working directory (auto-switch on startup, auto-create if missing) |
| `enabled_plugins` | object | {} | Plugin enable/disable map |
| `mcp_servers` | object | {} | MCP server configurations |

### Memory Configuration (`memory`)

File-based memory system (aligned with Claude Code Auto Memory), stored at `~/.illusion/memory/<project-name>-<hash>/`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | true | Enable the memory system (prompt injection, search, background extraction, auto dream) |
| `auto_extract` | bool | false | Allow background LLM extraction/consolidation. false (default) = manual-only mode: memory is written directly in conversation when requested, no background sub-agents |
| `extract_model` | string\|null | null | Model reference (`env_N.model_M`) for the extraction sub-agent. null inherits the current model |
| `dream_model` | string\|null | null | Model reference (`env_N.model_M`) for the Auto Dream consolidation sub-agent. null inherits the current model |
| `directory` | string\|null | null | Custom memory directory (absolute path or `~/` prefix). null uses the default per-project directory |
| `max_files` | int | 5 | Max relevant memory files injected into context |
| `max_entrypoint_lines` | int | 200 | Max MEMORY.md lines loaded (truncation warning beyond) |
| `max_entrypoint_bytes` | int | 25000 | Max MEMORY.md bytes loaded (truncation warning beyond) |
| `extract_interval` | int | 1 | Background memory extraction interval (turns) |
| `dream_min_hours` | int | 24 | Auto Dream min interval (hours) |
| `dream_min_sessions` | int | 5 | Auto Dream min sessions since last consolidation |

### Environment Configuration (env_N)

Each `env_N` is an independent API provider config. Models are referenced as `env_N.model_N`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `api_format` | string | Yes | API format: `anthropic` / `openai` / `response` / `copilot` / `codex` |
| `base_url` | string\|null | No | Custom API endpoint, null uses default |
| `api_key` | string | No | API key (standard `x-api-key` auth) |
| `auth_token` | string | No | Bearer Token auth (for providers like LongCat using `Authorization: Bearer`) |
| `model_N` | object | No | Model declaration: `{"name": "...", "capabilities": [...]}`, e.g. `model_1`, `model_2`, ... |

`capabilities` declares the model's multimodal (image input) ability; valid value is `"image"`. Models without a declaration are treated as vision-less (fail-closed) — `read_file` on images returns an explicit error for them.

```json
{
  "env_1": {
    "api_format": "anthropic",
    "base_url": null,
    "model_1": { "name": "claude-sonnet-4-6", "capabilities": ["image"] },
    "model_2": { "name": "claude-opus-4-6", "capabilities": [] }
  },
  "env_2": {
    "api_format": "openai",
    "base_url": "https://api.openai.com/v1",
    "model_1": { "name": "gpt-5.4", "capabilities": [] }
  },
  "model": "env_1.model_1"
}
```

**API key storage priority:** `env_N.api_key` (in settings.json) > `credentials.json` (managed by `illusion auth login`).

### Permissions (Global)

```json
{
  "permission": {
    "mode": "default",
    "allowed_tools": ["bash(npm:*)", "read_file", "grep"],
    "denied_tools": ["bash(rm -rf:*)"],
    "denied_commands": ["git push --force"],
    "path_rules": [
      {"pattern": ".env*", "allow": false},
      {"pattern": "src/**", "allow": true}
    ]
  }
}
```

**Permission modes (4):**
| Mode | Value | Description |
|------|-------|-------------|
| Default | `default` | Modification tools require user confirmation |
| Plan | `plan` | Block all modification tools |
| Full Auto | `full_auto` | Allow all operations automatically (still sandboxed) |
| YOLO | `yolo` | Bypass the sandbox entirely, run fully |

> **Note:** `accept_edits` and `dont_ask` modes do NOT exist. Use `full_auto` for automatic execution (still sandboxed), or `yolo` to bypass the sandbox entirely.

**Tool names** (lowercase, used in matchers):
- `bash` — Shell commands
- `read_file` — Read file contents
- `edit_file` — Edit existing file
- `write_file` — Write/create file
- `grep` — Search file contents
- `glob` — Find files by pattern

**Permission Rule Syntax:**
- Exact match: `"bash(npm run test)"`
- Prefix wildcard: `"bash(git:*)"` - matches `git status`, `git commit`, etc.
- Tool only: `"read_file"` - allows all read_file operations

### Hooks (Global)

```json
{
  "hooks": {
    "pre_tool_use": [
      {
        "type": "command",
        "command": "echo 'Tool called'",
        "timeout_seconds": 30,
        "matcher": "bash",
        "block_on_failure": false
      }
    ],
    "post_tool_use": [
      {
        "type": "command",
        "command": "prettier --write $FILE",
        "timeout_seconds": 30,
        "matcher": "write_file|edit_file"
      }
    ]
  }
}
```

#### Hook Types (4)

**1. Command Hook** — Runs a shell command:
```json
{
  "type": "command",
  "command": "prettier --write $FILE",
  "timeout_seconds": 30,
  "matcher": "write_file|edit_file",
  "block_on_failure": false
}
```

**2. Prompt Hook** — Uses LLM to evaluate a condition:
```json
{
  "type": "prompt",
  "prompt": "Is this command safe? $ARGUMENTS",
  "model": "env_1.model_1",
  "timeout_seconds": 30,
  "matcher": "bash",
  "block_on_failure": true
}
```

**3. HTTP Hook** — Sends event payload to an HTTP endpoint:
```json
{
  "type": "http",
  "url": "https://example.com/webhook",
  "headers": {"Authorization": "Bearer token"},
  "timeout_seconds": 30,
  "matcher": "write_file|edit_file",
  "block_on_failure": false
}
```

**4. Agent Hook** — Uses an agent for deep validation:
```json
{
  "type": "agent",
  "prompt": "Verify this change is safe: $ARGUMENTS",
  "model": "env_1.model_1",
  "timeout_seconds": 60,
  "matcher": "write_file|edit_file",
  "block_on_failure": true
}
```

#### Hook Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | string | required | Hook type: `command`, `prompt`, `http`, `agent` |
| `command` | string | (command) | Shell command to execute |
| `prompt` | string | (prompt/agent) | Prompt for LLM evaluation |
| `url` | string | (http) | HTTP endpoint URL |
| `headers` | object | `{}` | HTTP headers |
| `model` | string | null | Model override as `env_N.model_N` (prompt/agent) |
| `timeout_seconds` | int | 30/60 | Timeout in seconds |
| `matcher` | string | null | Tool name pattern to match (lowercase) |
| `block_on_failure` | bool | varies | Block execution on failure |

#### Hook Input (stdin JSON)

Hooks receive JSON on stdin:
```json
{
  "session_id": "abc123",
  "tool_name": "write_file",
  "tool_input": { "file_path": "/path/to/file.txt", "content": "..." },
  "tool_response": { "success": true }
}
```

#### Hook Output

Command hooks can output JSON to control behavior:
```json
{
  "blocked": true,
  "reason": "Command not allowed",
  "output": "Detailed explanation"
}
```

- `blocked` — Set to `true` to block the tool execution
- `reason` — Message shown when blocking
- `output` — Output text (displayed to user or injected as context)

### Memory (Global)

```json
{
  "memory": {
    "enabled": true,
    "max_files": 5,
    "max_entrypoint_lines": 200
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | true | Enable memory function |
| `max_files` | 5 | Maximum number of memory files |
| `max_entrypoint_lines` | 200 | Maximum lines for MEMORY.md entry file |

### Sandbox (Global)

The sandbox provides OS-level isolation for shell commands. Supports Linux (bubblewrap) and macOS (seatbelt). Native Windows does not support OS-level sandboxing (bwrap/sandbox-exec are POSIX-only); Windows isolation relies on the permission layer (risk levels + filesystem allowlist).

```json
{
  "sandbox": {
    "enabled_platforms": [],
    "excluded_commands": [],
    "network": {
      "allowed_domains": [],
      "denied_domains": [],
      "allow_unix_sockets": [],
      "allow_all_unix_sockets": false,
      "allow_local_binding": false,
      "http_proxy_port": null,
      "socks_proxy_port": null
    },
    "filesystem": {
      "allow_read": [],
      "deny_read": [],
      "allow_write": ["."],
      "deny_write": []
    },
    "ignore_violations": {},
    "enable_weaker_nested_sandbox": false,
    "mandatory_deny_search_depth": 3,
    "allow_git_config": false
  }
}
```

#### Network Configuration
```json
{
  "sandbox": {
    "network": {
      "allowed_domains": ["api.anthropic.com", "*.github.com"],
      "denied_domains": ["malicious.example.com"],
      "allow_local_binding": false
    }
  }
}
```

#### Filesystem Configuration
```json
{
  "sandbox": {
    "filesystem": {
      "allow_write": [".", "./output"],
      "deny_write": [".git/hooks", ".env"],
      "deny_read": ["./secrets"],
      "allow_read": ["./secrets/public"]
    }
  }
}
```

#### Excluded Commands
```json
{
  "sandbox": {
    "excluded_commands": ["npm test", "make:*", "git status"]
  }
}
```

### MCP Servers (Global)

```json
{
  "mcp_servers": {
    "server-name": {
      "type": "stdio",
      "command": "node",
      "args": ["server.js"],
      "env": {},
      "enabled": true
    }
  }
}
```

> `mcpServers` (camelCase) is also accepted for backward compatibility and auto-mapped to `mcp_servers`.

MCP server types: `stdio` (command, args, env, cwd), `http` (url, headers). All support `enabled` field (default `true`).

### Plugins (Global enable/disable)

```json
{
  "enabled_plugins": {
    "my-plugin": true,
    "disabled-plugin": false
  }
}
```

---

## Layer 2: Project-Level Configuration

Project-level config lives in `<project>/.illusion/`. It overrides or supplements global config for the current project only.

### Project Permissions (`.illusion/permissions.json`)

Controls deny-lists for the current project. **Highest priority** — overrides global settings.

```json
{
  "denied_tools": ["bash"],
  "denied_skills": ["dangerous-skill"],
  "denied_hooks": ["pre_tool_use"],
  "denied_plugins": ["unwanted-plugin"],
  "denied_mcp_servers": ["external-server"],
  "denied_memory": false,
  "denied_rules": ["rule-name"]
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `denied_tools` | list | `[]` | Tools always denied |
| `denied_skills` | list | `[]` | Disabled skill names, `["*"]` = all |
| `denied_hooks` | list | `[]` | Disabled hook events, `["*"]` = all |
| `denied_plugins` | list | `[]` | Disabled plugin names, `["*"]` = all |
| `denied_mcp_servers` | list | `[]` | Disabled MCP server names, `["*"]` = all |
| `denied_memory` | bool | false | Disable memory function |
| `denied_rules` | list | `[]` | Disabled rule names, `["*"]` = all |

**Priority:** Project `permissions.json` > Global `settings.json` > Defaults

### Project MCP Servers (`.illusion/mcp/*.json`)

Scan all `*.json` files in `.illusion/mcp/`. Two formats supported:

**Single server** (filename = server name, e.g. `filesystem.json`):
```json
{
  "type": "stdio",
  "command": "python",
  "args": ["server.py"],
  "enabled": true
}
```

**Multiple servers** (any filename, use `mcpServers` key):
```json
{
  "mcpServers": {
    "filesystem": {
      "type": "stdio",
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

### Project Skills (`.illusion/skills/`)

Two formats:
- **Directory format** (preferred): `.illusion/skills/<skill-name>/SKILL.md`
- **File format**: `.illusion/skills/<skill-name>.md`

Project skills override global skills with the same name.

### Project Rules (`.illusion/rules/*.md`)

Each `.md` file is an independent rule, sorted by filename. Also scanned: `.claude/rules/*.md`.

`/init` command generates default rules: `python-style.md`, `testing.md`, `project-structure.md`.

### Project Plugins (`.illusion/plugins/`)

Each subdirectory must contain `plugin.json` or `.claude-plugin/plugin.json`.

### Memory (user-level, no project-level)

Memory is stored at `~/.illusion/memory/{project-name}-{hash}/` (or the custom directory from `settings.json` → `memory.directory`). There is NO project-level memory directory — see the `memory` section in Layer 1 for configuration.

---

## Layer 3: AI Instruction Files

Instruction files provide project-specific context to the AI. They are merged into the system prompt as `# Project Instructions`. Each file is limited to 12,000 characters (truncated if exceeded).

### Discovery Locations (current working directory only, NOT in `~/.illusion/`)

1. **Project root**: `{cwd}/CLAUDE.md`, `{cwd}/AGENTS.md`, `{cwd}/ILLUSION.md`
2. **`.claude/` directory**: `{cwd}/.claude/CLAUDE.md`
3. **`.illusion/` directory**: `{cwd}/.illusion/CLAUDE.md`, `{cwd}/.illusion/AGENTS.md`, `{cwd}/.illusion/ILLUSION.md`

All three names (`CLAUDE.md`, `ILLUSION.md`, `AGENTS.md`) are **equivalent** — IllusionAgent recognizes them interchangeably.

### Usage

```markdown
# Project Description

This is a Python Web project using the FastAPI framework.

## Code Standards

- Use Python 3.10+ features
- Follow PEP 8 code style
- Use type hints

## Notes

- Do not modify files in the tests/ directory
- Run pytest before committing
```

### Rules Files (additional)

Also scanned (sorted by filename, each file is an independent rule):
- `{cwd}/.claude/rules/*.md`
- `{cwd}/.illusion/rules/*.md`

---

## Common Patterns

### Auto-format after writes (global hook)
```json
{
  "hooks": {
    "post_tool_use": [{
      "type": "command",
      "command": "jq -r '.tool_input.file_path // .tool_response.filePath' | { read -r f; prettier --write \"$f\"; } 2>/dev/null || true",
      "matcher": "write_file|edit_file",
      "timeout_seconds": 30
    }]
  }
}
```

### Log all bash commands (global hook)
```json
{
  "hooks": {
    "pre_tool_use": [{
      "type": "command",
      "command": "jq -r '.tool_input.command' >> ~/.illusion/bash-log.txt",
      "matcher": "bash"
    }]
  }
}
```

### Block dangerous commands (global hook)
```json
{
  "hooks": {
    "pre_tool_use": [{
      "type": "command",
      "command": "jq -r '.tool_input.command' | grep -qE 'rm -rf|drop table' && echo '{\"blocked\": true, \"reason\": \"Dangerous command blocked\"}' || true",
      "matcher": "bash",
      "block_on_failure": false
    }]
  }
}
```

### Allow specific bash commands (global permission)
```json
{
  "permission": {
    "allowed_tools": ["bash(npm:*)", "bash(git:*)", "read_file", "grep", "glob"]
  }
}
```

### Deny destructive commands (global permission)
```json
{
  "permission": {
    "denied_tools": ["bash(rm -rf:*)"],
    "denied_commands": ["git push --force", "git reset --hard"]
  }
}
```

### Protect sensitive files (global permission)
```json
{
  "permission": {
    "path_rules": [
      {"pattern": ".env*", "allow": false},
      {"pattern": "secrets/**", "allow": false},
      {"pattern": "src/**", "allow": true}
    ]
  }
}
```

### Disable a skill for this project (project permissions)
```json
{
  "denied_skills": ["dangerous-skill"]
}
```
Save to `<project>/.illusion/permissions.json`.

### Disable all MCP servers for this project (project permissions)
```json
{
  "denied_mcp_servers": ["*"]
}
```
Save to `<project>/.illusion/permissions.json`.

### Add a project-specific MCP server
Save to `<project>/.illusion/mcp/my-server.json`:
```json
{
  "type": "stdio",
  "command": "python",
  "args": ["server.py"],
  "enabled": true
}
```

### Add project instructions
Create `<project>/CLAUDE.md`:
```markdown
# Project Instructions

This project uses Python 3.10+ with FastAPI.
Run tests with: pytest tests/
Do not modify files in src/generated/.
```

---

## Workflow

1. **Clarify intent** — Ask which layer to modify if ambiguous
2. **Read existing file** — Use Read tool on the target config file
3. **Merge carefully** — Preserve existing settings, especially arrays
4. **Edit file** — Use Edit tool (if file doesn't exist, create it first)
5. **Validate** — Check JSON syntax
6. **Confirm** — Tell user what was changed and which layer

## Merging Arrays (Important!)

When adding to permission arrays or hook arrays, **merge with existing**, don't replace:

**WRONG** (replaces existing):
```json
{ "permission": { "allowed_tools": ["bash(npm:*)"] } }
```

**RIGHT** (preserves existing + adds new):
```json
{
  "permission": {
    "allowed_tools": [
      "bash(git:*)",
      "read_file",
      "bash(npm:*)"
    ]
  }
}
```

## Troubleshooting

If a hook isn't running:
1. Check the settings file exists and has valid JSON
2. Verify the event name is correct (lowercase with underscores: `pre_tool_use`, `post_tool_use`, `session_start`, `session_end`)
3. Check the matcher matches the tool name (lowercase: `bash`, `write_file`, `edit_file`, etc.)
4. Check hook type is one of: `command`, `prompt`, `http`, `agent`
5. Test the command manually
6. Check `timeout_seconds` isn't too low
7. Check project `.illusion/permissions.json` hasn't denied the hook event

If permissions aren't working:
1. Verify mode is one of: `default`, `plan`, `full_auto`, `yolo`
2. Check tool names are lowercase: `bash`, `read_file`, `edit_file`, `write_file`, `grep`, `glob`
3. Check rule syntax: `bash(command:*)` for prefix match, `bash(exact command)` for exact match
4. Check project `.illusion/permissions.json` hasn't denied the tool

If a skill/plugin/MCP isn't loading:
1. Check project `.illusion/permissions.json` hasn't denied it
2. For MCP: check `.illusion/mcp/*.json` file format
3. For skills: check `.illusion/skills/<name>/SKILL.md` or `.illusion/skills/<name>.md`
4. For plugins: check `.illusion/plugins/<name>/plugin.json`

### If plan approval is stuck

If plan mode approval seems stuck (agent can't proceed):

1. Check for stale `pending-plan-approval-*.json` files in `<project>/.illusion/sessions/`
2. Delete the stale file to reset: `rm <project>/.illusion/sessions/pending-plan-approval-*.json`
3. Restart the session with `illusion -c -p "approve"` or start a new session
