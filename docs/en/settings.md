# Settings & Credentials

## Table of Contents

- [Configuration Overview](#configuration-overview)
- [Credentials File (credentials.json)](#credentials-file-credentialsjson)
- [Global Configuration (settings.json)](#global-configuration-settingsjson)
  - [working_directory](#working_directory)
  - [Environment Configuration (EnvConfig)](#environment-configuration-envconfig)
  - [API Format Configuration Examples](#api-format-configuration-examples)
  - [Permission Configuration](#permission-configuration)
  - [Environment Variables](#environment-variables)
  - [Memory System Configuration](#memory-system-configuration)
  - [Auto Title Configuration](#auto-title-configuration)
  - [Notification Toggles (Toast & Sound)](#notification-toggles-toast--sound)
  - [Sandbox Configuration](#sandbox-configuration)

---

## Configuration Overview

| File | Location | Scope | Purpose |
|------|----------|-------|---------|
| `settings.json` | `~/.illusion/settings.json` | Global | Main settings: API config, permissions, hooks, etc. |
| `credentials.json` | `~/.illusion/credentials.json` | Global | Secure credential storage (API keys) |

Environment variable overrides: `ILLUSION_CONFIG_DIR` replaces `~/.illusion/`, `ILLUSION_DATA_DIR` replaces `~/.illusion/data/`, `ILLUSION_LOGS_DIR` replaces `~/.illusion/logs/`.

### Configuration Priority

1. **CLI Arguments** — highest priority
2. **Configuration Files** — `~/.illusion/settings.json`
3. **Default Values** — built-in defaults

---

## Credentials File (credentials.json)

Located at `~/.illusion/credentials.json`, managed by `illusion auth login`. Credentials are stored by `env_N` groups.

```json
{
  "env_1": {
    "api_key": "sk-ant-xxxxx"
  },
  "env_2": {
    "api_key": "sk-xxxxx"
  }
}
```

**API Key Storage Options:**

| Method | Location | Advantage |
|--------|----------|-----------|
| **Secure mode** | `credentials.json` (managed by `illusion auth login`) | Keys separated from config, file permissions protected |
| **Convenient mode** | `env_N.api_key` in `settings.json` | All config in one file |

Runtime priority: `env_N.api_key` > `credentials.json`.

> **File Permission 600**: On Unix/Linux, file is set to `rw-------` (owner only). Silently skipped on Windows.

---

## Global Configuration (settings.json)

### Format

Uses `env_N` grouped format. Each `env_N` is an independent environment config (EnvConfig). The `model` field references `env_N.model_N`.

```json
{
  "env_1": {
    "api_format": "anthropic",
    "base_url": null,
    "model_1": "claude-sonnet-4-6",
    "model_2": "claude-opus-4-6"
  },
  "env_2": {
    "api_format": "openai",
    "base_url": "https://api.openai.com/v1",
    "model_1": "gpt-5.4"
  },
  "model": "env_1.model_1",
  "context_window": 200000
}
```

### Complete Configuration Structure

```json
{
  "env_1": {
    "api_format": "anthropic",
    "base_url": null,
    "model_1": "claude-sonnet-4-6",
    "model_2": "claude-opus-4-6"
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
    "max_files": 5,
    "max_entrypoint_lines": 200
  },
  "title": {
    "enabled": false,
    "model": "env_1.model_1"
  },
  "notifications": {
    "enabled": true,
    "sound": true
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
    "enable_weaker_network_isolation": false,
    "mandatory_deny_search_depth": 3,
    "allow_git_config": false,
    "ripgrep": null
  },
  "enabled_plugins": {},
  "mcp_servers": {},
  "working_directory": null,
  "ui_language": "en-US",
  "effort": "medium"
}
```

### Configuration Field Description

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `env_N` | object | - | Environment config group (EnvConfig) |
| `model` | string | "env_1.model_1" | Active model reference: `env_N.model_N` |
| `context_window` | int | 200000 | Context window size in tokens |
| `max_tokens` | int | 16384 | Maximum output tokens |
| `max_turns` | int | 200 | Maximum conversation turns |
| `ui_language` | string | "" | UI language (empty triggers first-login prompt; fallback zh-CN) |
| `effort` | string | "medium" | Reasoning effort: low/medium/high/xhigh/max |
| `notifications.enabled` | bool | true | Master toggle for toast notifications (task completion/termination, questions, permission reminders); when off the backend stops emitting toast events |
| `notifications.sound` | bool | true | Toast sound-effect toggle (only effective while `notifications.enabled` is on) |
| `working_directory` | string | - | Fixed working directory (optional) |

---

## working_directory

Fixed working directory. If set, illusion-agent will automatically switch to this directory on startup.

**How to set:**
- Via `illusion set [path]` command (recommended)
- Guided setup during first `illusion auth login`
- Direct edit of `settings.json`

**Type:** String (optional)

**Default:** Not set or empty

**Example:**

```json
{
  "working_directory": "E:\\Projects\\my-project"
}
```

**Behavior:**
- If the field exists and is not empty, automatically switches to the specified directory on startup
- If the field does not exist or is empty, uses the current directory at startup
- If the specified directory does not exist, `illusion set` will automatically create it
- If directory validation fails on startup (e.g., insufficient permissions), logs a warning and uses the current directory

### Workspaces (Web Multi-Directory)

`working_directory` serves as the **default workspace** on the Web UI. The Web UI can run concurrently across multiple directory workspaces; each directory keeps its own session history and project-level config (`<dir>/.illusion/` permissions, skills, rules, MCP, plugins, hooks), while models and API environments are shared globally.

- The registry is stored at `~/.illusion/workspaces.json` (the default workspace is not duplicated into the file; it is injected dynamically)
- Web Settings → "Workspaces" tab can add / remove / set-default directories; **removing a directory deletes all of its sessions** (removal is blocked while any session in that directory is running)
- The session list is grouped by directory; the directory button in the input box (always visible on the welcome screen) creates a new session in the chosen directory
- Each directory's runtime bundle is built lazily and evicted when idle, avoiding resource duplication

---

### Environment Configuration (EnvConfig)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `api_format` | string | Yes | API format: `anthropic` / `openai` / `copilot` / `codex` |
| `base_url` | string\|null | No | Custom API endpoint, null uses default |
| `api_key` | string | No | API key (standard `x-api-key` auth) |
| `auth_token` | string | No | Bearer Token auth (for providers like LongCat using `Authorization: Bearer`) |
| `model_N` | string | No | Model name: `model_1`, `model_2`, ... |

### Multi-Model Configuration

```json
{
  "env_1": {
    "api_format": "openai",
    "base_url": "https://integrate.api.nvidia.com/v1",
    "model_1": "stepfun-ai/step-3.5-flash",
    "model_2": "minimaxai/minimax-m2.7",
    "model_3": "meta/llama-3.1-405b-instruct"
  },
  "model": "env_1.model_1"
}
```

**Switching models:**
```bash
/model                          # Interactive switch
illusion -m env_1.model_2       # CLI parameter
```

---

### API Format Configuration Examples

#### 1. Anthropic Claude API

```json
{
  "env_1": {
    "api_format": "anthropic",
    "base_url": null,
    "model_1": "claude-sonnet-4-6",
    "model_2": "claude-opus-4-6"
  },
  "model": "env_1.model_1"
}
```

#### 2. OpenAI API

```json
{
  "env_1": {
    "api_format": "openai",
    "base_url": "https://api.openai.com/v1",
    "model_1": "gpt-5.4"
  },
  "model": "env_1.model_1"
}
```

#### 3. Custom Format

Select "Custom format" in `illusion auth login`, enter API format, endpoint, API key, and model name.

#### 4. GitHub Copilot

```bash
illusion auth login  # Select GitHub Copilot
```

After GitHub authorization in browser, auto-configured. Auth stored in `~/.illusion/copilot_auth.json`.

```json
{
  "env_1": {
    "api_format": "copilot",
    "base_url": "https://api.githubcopilot.com",
    "model_1": "gpt-5.5"
  }
}
```

#### 5. OpenAI Codex (ChatGPT Subscription)

```bash
illusion auth login   # Select OpenAI Codex
```

Uses ChatGPT subscription auth via Device Code flow. Auth stored in `~/.illusion/codex_oauth_auth.json`.

```json
{
  "env_1": {
    "api_format": "codex",
    "base_url": "https://chatgpt.com/backend-api",
    "model_1": "codex-mini"
  }
}
```

#### 6. LongCat (Bearer Token Authentication)

LongCat uses `Authorization: Bearer` authentication, configured via the `auth_token` field (not `api_key`).

```json
{
  "env_1": {
    "api_format": "anthropic",
    "base_url": "https://api.longcat.chat/anthropic",
    "auth_token": "ak_your_longcat_api_key",
    "model_1": "LongCat-2.0"
  }
}
```

#### 7. Multi-Format Mixed Configuration

```json
{
  "env_1": {
    "api_format": "anthropic",
    "base_url": null,
    "model_1": "claude-sonnet-4-6"
  },
  "env_2": {
    "api_format": "openai",
    "base_url": "https://api.openai.com/v1",
    "model_1": "gpt-5.4"
  },
  "env_3": {
    "api_format": "copilot",
    "base_url": "https://api.githubcopilot.com",
    "model_1": "gpt-5.5"
  },
  "model": "env_1.model_1"
}
```

---

### Permission Configuration

#### Permission Modes

| Mode | Value | Description |
|------|-------|-------------|
| Default | `default` | Modification tools require user confirmation |
| Plan | `plan` | Block all modification tools |
| Full Auto | `full_auto` | Constrained by the sandbox and blocks high-risk ops (HIGH requires confirmation) |
| YOLO | `yolo` | **Bypass the sandbox entirely** with no sandbox restrictions |

```json
{
  "permission": {
    "mode": "default",
    "allowed_tools": ["read_file", "grep", "glob"],
    "denied_tools": ["bash"],
    "path_rules": [
      {"pattern": "src/**", "allow": true},
      {"pattern": "secrets/**", "allow": false}
    ],
    "denied_commands": ["/init", "/memory"]
  }
}
```

#### LLM Auto-Review in auto mode

In `full_auto` (auto) mode there are two kinds of requests that require confirmation: **high-risk operations** (HIGH, e.g. `rm` / `git reset --hard`, plus the built-in high-risk command set: deletions, destructive git operations, formatting / block-device writes, PowerShell deletion / formatting commands, compound-command segments) and **sandbox-blocked regular operations** (e.g. reads/writes outside the workspace). Both default to a manual confirmation dialog. Enable **LLM auto-review** so a review model decides on the user's behalf:

```json
{
  "permission": {
    "mode": "full_auto",
    "auto_review": true,
    "review_model": "env_2.model_1"
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `permission.auto_review` | `false` | Only affects `full_auto`: when enabled, high-risk operations and sandbox-blocked access (outside-workspace reads/writes) are released by the LLM review instead of a manual confirmation dialog; disabled keeps the manual flow. `yolo` / `plan` / `default` are unaffected |
| `permission.review_model` | unset | Review model (`env_N.model_M`). Unset inherits the current session model |

- The review is a single-turn subagent with fixed `effort = high` and up to `8192` output tokens; API failures / unparseable output are retried 3 times and ultimately fail closed (denied), never silently allowed
- Review activity is logged to `~/.illusion/logs/permission_review.log` (override with `ILLUSION_LOGS_DIR`)
- **Web UI**: Settings → General → "Permission LLM Auto-Review" section: toggle the switch and pick the review model
- **Terminal**: `/permissions auto on|off|toggle|status`, `/permissions model show|set REF|set inherit`, `/permissions` to show the current state

#### Permission Confirmation Timeout (Web & Terminal, Unified Across All Sessions)

Permission confirmations raised by **all sessions** (main conversation and subagents) on the Web and Terminal are guarded by a waiting timeout of about **285s** (deliberately less than the subagent idle timeout of 300s, so the timeout reason surfaces before a generic "Agent timed out"): on timeout the request fails with a reason (e.g. "permission request timed out"), the error flows back as a tool result (the task continues), and any leftover confirmation dialog is cleared automatically. Plain **ask_user_question** dialogs (not sandbox permissions) are treated differently: after a 15-minute timeout they do not error — they return a "(no response)" placeholder answer instructing the agent to pick the best-fitting option and continue.

#### Channel Permission Mode

Channel agents (Feishu / QQ / WeChat) run in **`yolo`** permission mode: no permission confirmation is raised in the channel conversation (neither sandbox / outside-workspace "regular" requests nor high-risk ones). Explicit deny rules still apply (`denied_tools`, path deny rules, `denied_commands`). Explicit questions via `ask_user_question` still send a message into the channel and wait for a reply.

---

### Environment Variables

| Variable | Description |
|----------|-------------|
| `ILLUSION_CONFIG_DIR` | Override configuration directory (default: `~/.illusion/`) |
| `ILLUSION_DATA_DIR` | Override data directory (default: `~/.illusion/data/`) |
| `ILLUSION_LOGS_DIR` | Override logs directory (default: `~/.illusion/logs/`) |

> **Note:** API keys, model names, and other runtime settings are managed exclusively through `settings.json` and `credentials.json`. Use `illusion auth login` to configure credentials.

---

### Memory System Configuration

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

---

### Auto Title Configuration

After the first turn, a lightweight sub-agent runs in the background to generate a concise session title from the user's first real message, writing it to the `title` field of the session's `meta.json` (shown in `/resume`, `/delete` lists and the web sidebar). It does not block the ongoing conversation.

```json
{
  "title": {
    "enabled": false,
    "model": "env_1.model_1"
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | false | Whether to enable auto title (off by default) |
| `model` | empty (inherit current) | Model used by the title sub-agent (`env_N.model_M`); empty inherits the current session model |

- Runs only on the first turn, using only the user's first real message; if the first message is a `/goal` command, it falls back to the current goal objective.
- If the session was already renamed manually (meta already has a `title`), auto title will not overwrite it.
- Title generation is a background task that automatically retries on occasional empty results; activity is logged to `~/.illusion/logs/title.log`.

---

### Notification Toggles (Toast & Sound)

Controls toast notifications on the Web / Desktop clients. The backend emits toasts on four kinds of events: **task completion, task termination, questions awaiting an answer, and permission requests**. The frontend decides how to present them based on whether the user is present:

| User state | Behavior |
|------------|----------|
| Supervising the app UI (page visible and focused) | Fully silent — no toast, no sound, no system notification (the running state and pending confirmation dialogs are directly visible) |
| Page visible but unfocused | In-app toast + sound effect + **system-level notification** |
| Page hidden (switched tab / minimized to tray) | Sound effect + system-level notification. No in-app toast card is shown or replayed — the task result has already been delivered via the system notification, so returning to the app shows nothing twice |

System-level notifications are Electron notifications in the desktop shell (click one to return to the app), or Web Notifications in plain browsers. Both are **minimal two-part** banners — a fixed short title localized per event type plus a one-line plain-text summary (Markdown in the body is flattened automatically so it never clashes with native banner typography); the full result stays in the in-app card. **Browsers require a one-time grant**: the permission request is triggered by your first click/keypress (background-tab requests are blocked by browsers); if denied, only in-app reminders remain, re-enableable in the site settings of your browser.

```json
{
  "notifications": {
    "enabled": true,
    "sound": true
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | true | Master toggle for toasts. When off, the backend stops emitting any toast events (including pass-through system notifications) |
| `sound` | true | Toast sound-effect toggle |

Besides editing settings.json directly, these toggles are also available in the **Web settings dialog → Settings → Notifications** and take effect immediately after saving.

> **Coupling rule**: the two toggles are stored independently, but **the sound is processed only while the toast master toggle is on** — with `enabled=false` nothing plays regardless of `sound`.

---

### Sandbox Configuration

The sandbox system provides OS-level isolation for shell commands. Supports two platforms:

| Platform | Mechanism | Dependencies |
|----------|-----------|--------------|
| Linux / WSL | bubblewrap (bwrap) + optional seccomp | `bwrap`, `socat` |
| macOS | Apple Seatbelt (sandbox-exec) | Built-in |

**Native Windows does not support OS-level sandboxing** (bwrap/sandbox-exec are POSIX-only; commands on Windows always run unsandboxed). Windows isolation relies on the permission layer: command risk levels (high-risk interception/confirmation) plus the filesystem allowlist (`filesystem.allow_write`, etc.) — both apply consistently on every platform.

#### Basic Configuration

```json
{
  "sandbox": {
    "enabled_platforms": [],
    "excluded_commands": []
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
      "allow_unix_sockets": [],
      "allow_all_unix_sockets": false,
      "allow_local_binding": false,
      "http_proxy_port": null,
      "socks_proxy_port": null
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

> **Filesystem restriction semantics**: writes are **deny-by-default** — only paths inside the `allow_write` whitelist are writable, and `deny_write` overrides the whitelist; reads are **allow-by-default** — only restricted by `deny_read`. These restrictions also apply to file tools (Write/Edit/Read, etc.), aligning with OS-level sandbox behavior: writes outside the working directory (`"."`) are sandbox-blocked or require confirmation.

#### Advanced Options

```json
{
  "sandbox": {
    "enable_weaker_nested_sandbox": false,
    "enable_weaker_network_isolation": false,
    "mandatory_deny_search_depth": 3,
    "allow_git_config": false,
    "ripgrep": {
      "command": "rg",
      "args": []
    }
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `enable_weaker_network_isolation` | false | macOS: allow access to trustd (needed for Go tool TLS verification; **reduces network isolation**, data exfiltration risk) |
| `enable_weaker_nested_sandbox` | false | Docker: skip `--proc /proc` |
| `allow_git_config` | false | Allow writing `.git/config` |
| `ripgrep` | null | Bundled ripgrep command and args |

#### Excluded Commands

```json
{
  "sandbox": {
    "excluded_commands": [
      "npm test",
      "make:*",
      "git status"
    ]
  }
}
```

#### High-Risk Operation Levels

Operations are classified by risk level; **high-risk operations outrank reads**:

- **HIGH**: destructive commands (`rm`, `Remove-Item`, `git restore`, `git clean`, `git reset --hard`, etc.). Even if a path was already **session-allowed**, a high-risk operation re-prompts for confirmation, preventing "allowed access" from being abused as a delete/restore pass.
- **MEDIUM**: general mutations (write, edit, etc.).
- **LOW**: read-only operations (read files, query commands, etc.).

**Risk-level rules are built-in** (LOW/MEDIUM/HIGH):

- `dangerous_bash_patterns` (HIGH): high-risk bash command regexes; matching triggers confirmation
- `dangerous_powershell_patterns` (HIGH): high-risk powershell command regexes
- `read_only_commands` (LOW): read-only command prefixes, allowed directly
- `medium_risk_tools` (MEDIUM): mutation tools, require confirmation by default

To allow a command that would otherwise be blocked as high-risk, configure a **command allowlist** in `permission.allowed_shell_commands` under `settings.json` (works for both bash and powershell commands):

- **Non-high-risk commands**: a prefix match allows them (e.g. `git push` allows `git push origin main`).
- **High-risk commands**: they are only allowed when an allowlist entry **fully lists** the high-risk command head — i.e. that entry is itself a high-risk pattern. A plain prefix does **not** exempt its high-risk sub-commands (e.g. `git push` does not allow `git push --force`; `rm` does not allow `rm -rf`). To exempt a high-risk command, configure its full command head, such as `git push --force`, `rm -rf`, or `Remove-Item`.

```json
{
  "permission": {
    "mode": "default",
    "allowed_shell_commands": ["git push --force", "rm -rf", "Remove-Item"]
  }
}
```

#### Relationship Between Sandbox Config and Risk Levels

The `sandbox` config and the built-in risk levels (LOW/MEDIUM/HIGH) are **two independent dimensions** that run in sequence during permission checks:

| Dimension | Nature | Decides | Located |
|-----------|--------|---------|---------|
| Sandbox config (`sandbox.*`) | Runtime isolation | "Which paths/domains a command may touch" | The `sandbox` section of `settings.json` |
| Risk levels | Decision classification | "How dangerous this operation is, whether to prompt" | Built-in (`risk.py`), read-only |

- **Sandbox config** (`filesystem.*`, `network.*`, `excluded_commands`) constrains the OS sandbox's actual behavior; it does not directly decide whether to prompt.
- **Risk levels** (`dangerous_bash_patterns` / `read_only_commands` / `medium_risk_tools`) decide whether to prompt for confirmation.
- **Execution order**: sandbox path restrictions (`filesystem`) are checked first, then risk levels. Hitting a sandbox deny or HIGH triggers confirmation.
- **Key overlap**: a high-risk operation (HIGH) re-prompts for confirmation even if the path was already session-allowed, preventing "allowed access" from being used as a delete/restore pass.

**How each permission mode consumes the two dimensions:**

| Mode | Sandbox config | Risk levels |
|------|----------------|-------------|
| `default` | Fully applied (filesystem/network/excluded) | Fully consumed: LOW allowed / MEDIUM confirm / HIGH must-confirm |
| `full_auto` | Subject to sandbox filesystem restrictions | Only HIGH is blocked; everything else allowed |
| `plan` | Plan file exempt; other mutations blocked | Not by level; blocked by "is it a mutation tool" |
| `yolo` | Bypassed entirely | Ignored; only explicit tool/path denies remain |
