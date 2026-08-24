# Project Architecture

```
illusion-agent/
├── src/illusion/           # Main source code
│   ├── api/                # API clients (Anthropic, OpenAI, etc.)
│   ├── auth/               # Authentication management
│   ├── commands/           # Slash command system
│   ├── config/             # Configuration system
│   ├── coordinator/        # Multi-agent coordinator
│   ├── engine/             # Core conversation engine
│   ├── hooks/              # Hook system
│   ├── mcp/                # MCP client
│   ├── memory/             # Memory system
│   ├── permissions/        # Permission system
│   ├── plugins/            # Plugin system
│   ├── prompts/            # Prompt system
│   ├── skills/             # Skill system
│   ├── tasks/              # Task management
│   ├── tools/              # Toolset (30 base tools + 4 channel tools)
│   ├── ui/                 # User interface
│   │   ├── web/            # Web backend (FastAPI + WebSocket)
│   │   └── ...
│   └── cli.py              # CLI entry point
├── frontend/
│   ├── terminal/           # React Ink TUI frontend
│   └── web/                # React Web frontend (Vite + Tailwind)
├── tests/                  # Test suite
└── pyproject.toml          # Project configuration
```

---

## Core Modules

### API Client Layer

Supports multiple AI providers:

| Provider | API Format | Authentication |
|----------|------------|----------------|
| Anthropic Claude | anthropic | API Key |
| OpenAI / Compatible | openai | API Key |
| GitHub Copilot | copilot | OAuth Device Flow |
| OpenAI Codex | codex | OAuth Device Flow |
| Custom Format | anthropic / openai | API Key |

### Tool System

Provides 30 base tools, covering:

- **File Operations**: `file_read`, `file_write`, `file_edit`
- **Command Execution**: `bash`, `powershell`
- **Search**: `glob`, `grep`, `web_fetch`, `web_search`
- **Task Management**: `task_output`, `task_stop`
- **Agent Collaboration**: `agent`, `send_message`, `team_create`, `team_delete`
- **Mode Switching**: `enter_plan_mode`, `exit_plan_mode`
  - `exit_plan_mode` triggers plan approval: terminal/Web shows an approval card, print mode uses cross-turn approval (exit code 2), channel sends plan content and waits for reply
- **Session Control**: `enter_worktree`, `exit_worktree`, `todo_write`, `sleep`
- **Config & Debug**: `config`, `lsp`, `mcp_auth`, `skill`
- **MCP Resources**: `list_mcp_resources`, `read_mcp_resource`
- **Interaction**: `ask_user_question`
- **Scheduled Tasks**: `cron` (unified tool with status/list/add/update/remove/run actions)

### Scheduled Tasks & Delivery Pipeline

The cron subsystem is composed of three cooperating modules:

- `services/cron.py` — CronJob data model and persistence (`cron.json`)
- `services/cron_scheduler.py` — scheduler process; runs the prompt in a subprocess and delivers the result to a channel based on the `deliver_to` field
- `channels/delivery.py` — delivery module; `parse_deliver_to` parses the target, `deliver_to_channel` dispatches to Feishu/WeChat/QQ `_deliver_*` functions

Delivery targets accept `channel:chat_id` (fully qualified) or a bare channel name (combined with the `chat_id` field). Failed jobs include stderr in the delivered text so users can see the error. See [Channels doc](channels.md#cron-job-result-delivery) for details.

### Permission System

Four permission modes:

| Mode | Description |
|------|-------------|
| `default` | Modification tools require user confirmation |
| `plan` | Block all modification tools |
| `full_auto` | Without sandbox it equals `yolo`; with sandbox it is constrained by the sandbox and blocks high-risk ops |
| `yolo` | Bypass the sandbox entirely with no sandbox restrictions |

### Multi-Agent Coordinator

Built-in 7 specialized Agents:

| Agent | Purpose |
|-------|---------|
| `general-purpose` | General research and multi-step tasks |
| `explore` | File search and code exploration expert |
| `plan` | Architecture design and implementation planning expert |
| `verification` | Adversarial verification expert |
| `worker` | Implementation-oriented Worker |
| `statusline-setup` | Shell PS1 converter |
| `illusion-guide` | Illusion Agent / SDK / API documentation expert |

---

## Frontend Tech Stack

### Terminal TUI (Ink)

| Technology | Version | Purpose |
|------------|---------|---------|
| React | 18.3.1 | UI framework |
| Ink | 5.1.0 | Terminal UI component library |
| TypeScript | 5.7.3 | Type safety |

### Web UI

| Technology | Version | Purpose |
|------------|---------|---------|
| React | 18.x | UI framework |
| Vite | 6.x | Build tool and dev server |
| Tailwind CSS | 3.x | Utility-first CSS framework |
| TypeScript | 5.x | Type safety |
| FastAPI | - | Backend API framework |
| WebSocket | - | Real-time bidirectional communication |

### Web Multi-Workspace (Directory Spaces)

The Web UI can run sessions concurrently across multiple directory workspaces, each with its own scope:

- **Backend**: `WebBackendHost` holds an independent `RuntimeBundle` per workspace (api_client / tool_registry / mcp / hooks initialized per directory; project-level `.illusion/` config applies per directory). The default workspace builds eagerly (startup flow unchanged); other workspaces are **built lazily** (on first session in that directory) and **evicted when idle** (60s grace period). The registry persists at `~/.illusion/workspaces.json`
- **Sessions**: stored per directory (`~/.illusion/data/sessions/{dir}-{sha}/`) and grouped by directory in the UI; new sessions can target a directory (welcome-screen directory button); restore routes by owning directory
- **cron**: jobs carry their own `cwd` (required, must be a registered directory on create/update); web delegation matches the session's owning directory; `/api/cron/sessions?cwd=` filters per directory
- **Channels**: channel config includes `working_directory` (required to enable); channel agents run anchored in that directory; `channel enable/login --working-directory <dir>` specifies and auto-registers the workspace
- **Zero impact on terminal/print modes**: `build_runtime(cwd=)` defaults to the process directory

---

## Main Dependencies

| Dependency | Purpose |
|------------|---------|
| anthropic | Anthropic SDK |
| openai | OpenAI SDK |
| rich | Rich text output |
| prompt-toolkit | Advanced input processing |
| typer | CLI framework |
| pydantic | Data validation |
| httpx | HTTP client |
| mcp | MCP protocol |
| fastapi | Web backend API framework |
| uvicorn | ASGI server for Web backend |
