# Command System

## Main Command-Line Options

The `illusion` main command supports the following options, grouped by function:

### Session

| Option | Short | Description |
|--------|-------|-------------|
| `--continue` | `-c` | Continue the most recent conversation in the current directory |
| `--resume <SESSION_ID>` | `-r` | Resume a conversation by session ID (ID required) |
| `--name <NAME>` | `-n` | Set a display name for this session (stored in `tool_metadata.session_name`) |

### Model & Effort

| Option | Short | Description |
|--------|-------|-------------|
| `--model <MODEL>` | `-m` | Model ID in `env_N.model_N` format (e.g. `env_1.model_2`), persists to settings.json |
| `--effort <LEVEL>` | `-e` | Effort level: `low` / `medium` / `high` / `xhigh` / `max`, persists to settings.json |
| `--max-turns <N>` | `-t` | Maximum agentic turns, persists to settings.json |

### Output

| Option | Short | Description |
|--------|-------|-------------|
| `--print <PROMPT>` | `-p` | Non-interactive print mode: execute a single prompt and exit |
| `--output-format <FORMAT>` | - | Output format for `--print` mode: `text` (default) / `json` / `stream-json` |

### Permissions

| Option | Description |
|--------|-------------|
| `--permission-mode <MODE>` | Permission mode: `default` / `plan` / `full_auto` / `yolo`, persists to settings.json |
| `--dangerously-skip-permissions` | Bypass all permission checks (equivalent to `--permission-mode full_auto`, only for sandboxed environments) |

### Global

| Option | Short | Description |
|--------|-------|-------------|
| `--version` | `-v` | Show version and exit |
| `--help` | `-h` | Show help and exit |

### Run Modes

`illusion` supports three main run modes:

#### 1. Interactive Session Mode (default)

```bash
illusion                            # Start interactive session
illusion -m env_1.model_2           # Start with a specific model
illusion --permission-mode full_auto  # Start with auto permission mode
illusion -e high                    # Start with high effort (persists to settings)
```

#### 2. Non-Interactive Print Mode

```bash
illusion -p "Analyze the project structure"
illusion -p "say hi" --output-format json
illusion -p "refactor this" -t 10
illusion -e high -p "Analyze code"  # Persist effort and execute
```

#### 3. Session Resume Mode

```bash
illusion -c -p "Continue analysis"           # Continue the most recent session (requires -p)
illusion -r <session-id> -p "Continue"       # Resume a specific session (requires -p)
illusion -c -p "Continue" --name "feature-work"  # Continue and name the session
```

Note: `-c`/`-r` now require `-p`; otherwise an error is raised. The `--resume` picker mode (no value) has been removed (non-backend-only path).

### Parameter Pass-Through

Core command options (model/effort/max_turns/permission_mode/name/continue/resume) are fully passed through to the React terminal frontend (`launch_react_tui` → `build_backend_command`) and the structured backend host (`run_backend_host` → `build_runtime`), ensuring they take effect in interactive mode, the `--backend-only` subprocess mode, and `-c`/`-r` session resume mode.

### Common Combinations

```bash
# Model + permission mode
illusion -m env_1.model_2 --permission-mode plan

# High effort + print mode (persists effort)
illusion -e high -p "Analyze performance bottlenecks in this code"

# Limit turns + print mode (persists max_turns)
illusion -t 5 -p "Quick syntax check"

# Continue session + print mode
illusion -c -p "Continue the previous task"

# Name a session
illusion --name "debug-auth-issue"
```

## Subcommands

```bash
# Web UI
illusion web                     # Launch Web UI in browser (default port 3000)
illusion web --port 8080         # Launch with custom port
illusion web --trusted-host nas.example  # Declare a trusted host (for LAN access to /ws in non-loopback deployments)

# Authentication management
illusion auth login              # Interactive provider setup (first login guides working directory setup)
illusion auth status             # View credential status for all environments
illusion auth logout [env_N]     # Clear environment credentials
illusion auth switch [env_N]     # Switch active environment
illusion add model [env_N]       # Add model(s) to an existing environment (supports multiple input)

# Working directory management
illusion set                      # Show current working directory
illusion set "E:\Projects\myapp"  # Set working directory (creates if missing)

# MCP management
illusion mcp list                # List MCP servers
illusion mcp add <name> <config> # Add server
illusion mcp remove <name>       # Remove server

# Plugin management
illusion plugin list             # List plugins
illusion plugin install <source> # Install plugin
illusion plugin uninstall <name> # Uninstall plugin

# Channel management (Feishu/WeChat/QQ messaging)
illusion channel login           # Interactive channel setup (select channel → configure credentials)
illusion channel serve           # Run channel daemon in foreground (listen for messages)
illusion channel status          # View channel status (enabled/connected/PID)
illusion channel enable feishu   # Enable a channel
illusion channel disable feishu  # Disable a channel
illusion channel logout feishu   # Clear channel credentials

# Scheduled tasks
illusion cron start              # Start scheduler
illusion cron stop               # Stop scheduler
illusion cron status             # View status
illusion cron serve              # Run cron daemon in foreground (daemon entry point)
illusion cron list               # List tasks
illusion cron toggle <name> <true|false>  # Enable/disable task
illusion cron run <name>         # Manually trigger task
illusion cron history            # View execution history
illusion cron logs               # View scheduler logs

# Self-update
illusion update                  # Check for and install updates from PyPI
illusion update --deps           # Also update project dependencies
```

## Interactive Slash Commands

In interactive sessions, you can use the following commands:

| Category | Command Examples | Description |
|----------|------------------|-------------|
| Session Management | `/help`, `/clear`, `/exit`, `/rewind`, `/delete` | Manage session state |
| Memory Snapshots | `/memory`, `/resume`, `/export`, `/rules` | Memory and session management |
| Configuration | `/config`, `/model`, `/permissions`, `/thinking` | Adjust runtime configuration |
| Reasoning Control | `/effort`, `/max-tokens`, `/turns` | Effort, token limit, turn count control |
| Plugin Extensions | `/skills`, `/hooks`, `/mcp`, `/plugin` | Manage extension features |
| Project Init | `/init` | Initialize project IllusionAgent files |
| Multi-Agent | `/continue`, `/agent` | Agent collaboration and management |

### Non-Interactive Mode (Print Mode) Available Parameters

Use `-p` / `--print <PROMPT>` to enter non-interactive mode: execute a single prompt and exit, suitable for scripts and automation. The following parameters can be used with `-p`:

| Parameter | Short | Description | Persists |
|-----------|-------|-------------|----------|
| `--print <PROMPT>` | `-p` | Enter print mode, PROMPT is the prompt text | No |
| `--output-format <FORMAT>` | - | Output format: `text` (default) / `json` / `stream-json` | No |
| `--model <MODEL>` | `-m` | Model alias or full model ID | Yes (writes `settings.model`) |
| `--effort <LEVEL>` | `-e` | Effort level: `low` / `medium` / `high` / `xhigh` / `max` | Yes (writes `settings.effort`) |
| `--max-turns <N>` | `-t` | Maximum agentic turns | Yes (writes `settings.max_turns`) |
| `--permission-mode <MODE>` | - | Permission mode: `default` / `plan` / `full_auto` / `yolo` | Yes (writes `settings.permission.mode`) |
| `--continue` | `-c` | Continue the most recent session in the current directory (requires `-p`) | No |
| `--resume <SESSION_ID>` | `-r` | Resume a specific session by ID (requires `-p`) | No |
| `--name <NAME>` | `-n` | Set a display name for this session | No |
| `--dangerously-skip-permissions` | - | Bypass all permission checks (equivalent to `--permission-mode full_auto`) | No |

**Interactive Behavior**:

- **Permission confirmation**: Print mode uses a cross-turn Y/N callback — in `default` mode, tools requiring permission do not execute directly; instead the permission request is persisted and the program exits with code 2, stderr shows `Permission request: {tool}. Use illusion -c -p "Y" to allow once / "N" to deny`:
  1. **Turn 1**: `illusion -p "write a file"` → tool requires permission → persisted to `pending-permission-<session_id>.json` → exit code **2**
  2. **Turn 2**: `illusion -c -p "Y"` → detects pending permission → injects approval result → resumes execution

  **Approval input format** (case-insensitive):
  - **Y** / **yes** / **approve**: Allow once (not persisted, effective only for the current tool call)
  - **N** / any other input: Deny (LLM receives denial message, may try alternative approaches)

  Use `--permission-mode full_auto` to skip permission confirmation entirely; `plan` mode blocks all mutation tools.
- **Sandbox permission confirmation (two options)**: In print mode, a **sandbox restriction** uses a dedicated **two-option** cross-turn confirmation (allow / deny), distinct from the general Y/N flow, and never offers "always allow":
  1. **Turn 1**: `illusion -p "..."` → tool hits a sandbox restriction → persisted to `pending-sandbox-<session_id>.json` → exit code **2**, stderr shows `Sandbox permission request: {tool}. Use illusion -c -p "Y" to allow / "N" to deny`
  2. **Turn 2**: `illusion -c -p "Y"` → allows that single sandboxed operation and resumes; `illusion -c -p "N"` → denies it.

  **High-risk operations**: destructive commands (e.g. `rm`, `git restore`, `Remove-Item`) rank above reads. Even if a path was already allowed for the session, destructive operations on it still trigger sandbox confirmation.
- **ask_user_question interaction**: When the LLM calls the ask_user_question tool, print mode uses a **cross-turn non-interactive** pattern:
  1. **Turn 1**: `illusion -p "do something"` → agent calls ask_user_question during execution → tool persists the question to `pending-question-<session_id>.json`, returns a special marker as tool_result → agent ends the turn → program exits with **exit code 2** (indicating waiting for user answer)
  2. **Turn 2**: `illusion -c -p "<answer>"` → detects pending question → injects the answer as tool_result (replacing the marker) → calls `continue_pending` to resume agent execution

  This design allows illusion agent to be controlled by other agents: each `-p` invocation is an atomic request-response, without waiting for interactive input within the same turn. Exit code semantics:
  - `0`: Normal completion
  - `1`: Error
  - `2`: Waiting for user answer (answer with `-c -p` next time)

  **Multi-question answer format** (agent-friendly):
  - **Single question**: Enter the answer text directly. For `multiSelect`, separate with commas, e.g. `optionA,optionB`
  - **Multiple questions**: Use JSON format, where keys are the headers shown in brackets:
    ```bash
    illusion -c -p "{\"Fruit\": \"strawberry\", \"OS\": \"Windows\", \"Emoji\": \"less\"}"
    ```
    `multiSelect` values use arrays: `{"Fruit": ["strawberry", "mango"]}`. Non-JSON input is passed as-is to the LLM (backward compatible)
- **Plan approval interaction**: When the LLM calls `exit_plan_mode` in print mode, it uses the same cross-turn pattern as ask_user_question:
  1. **Turn 1**: `illusion -p "implement feature X"` → agent enters plan mode, writes plan file, calls `exit_plan_mode` → plan persisted to `pending-plan-approval-<session_id>.json` → exit code **2**
  2. **Turn 2**: `illusion -c -p "approve"` → detects pending plan approval → injects approval result → resumes execution

  Exit code semantics: 0=normal completion, 1=error, 2=waiting for user input (ask_user_question, plan approval, or permission confirmation).

  **Approval input format**: Input "approve"/"yes"/"y" (case-insensitive) means approved; any other input is treated as rejection with the input as feedback. For example, `illusion -c -p "need more test cases"` is parsed as reject + feedback.
- **Persistence timing**: Parameters marked "Persists" are written to `settings.json` before executing the prompt, so persistence takes effect even if subsequent execution fails.

**Examples**:

```bash
# Basic usage
illusion -p "Analyze the project structure"

# Specify model + JSON output
illusion -m env_1.model_2 -p "List TODO comments" --output-format json

# High effort + limit turns (both persist)
illusion -e high -t 10 -p "Refactor this function"

# Full auto permissions + persist
illusion --permission-mode full_auto -p "Run tests"

# Continue previous session
illusion -c -p "Continue the previous task"

# Resume a specific session
illusion -r <session-id> -p "Continue"

# Combined: model + permission + effort + turns + session resume
illusion -m env_1.model_2 -e max -t 20 --permission-mode full_auto -c -p "Complete this feature"
```
