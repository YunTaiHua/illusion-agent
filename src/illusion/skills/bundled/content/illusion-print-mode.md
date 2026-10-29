---
name: illusion-print-mode
description: Guide for using IllusionAgent's non-interactive print mode (-p) for automation and agent control. Invoke when user asks about print mode, non-interactive usage, scripting illusion, or controlling illusion from another agent.
---

# Print Mode: Non-Interactive Automation

Print mode (`-p`) lets you run IllusionAgent as a non-interactive command: submit a prompt, stream the response, then exit. Designed for scripts, CI, and controlling IllusionAgent from other agents.

## Core Principle

**Print mode is fully non-interactive.** Every `-p` invocation is an atomic request-response — no waiting for interactive input within the same turn. This lets other agents control IllusionAgent programmatically.

## Basic Usage

```bash
illusion -p "<prompt>"
```

- `-p` value (the prompt) **must be the last argument** — typer parses it greedily
- Streams response to stdout; status/errors go to stderr
- Exits with code 0 (success), 1 (error), or 2 (waiting for user answer)

## Permission Modes (Critical)

Print mode uses cross-turn callbacks for permissions. Choose explicitly:

| Mode | Behavior | Use When |
|------|----------|----------|
| `default` (omit) | Mutating tools trigger **cross-turn Y/N approval** | Interactive-ish workflows, selective approval |
| `full_auto` | All tools execute (still sandboxed) | Fully autonomous, writing files, running commands |
| `yolo` | Bypass the sandbox entirely, run fully | Fully autonomous with sandbox disabled |
| `plan` | All mutation tools blocked | Planning only |

> **Note**: In print mode, `plan` mode uses cross-turn approval. When the agent calls `exit_plan_mode`, the plan is persisted and the process exits with code 2. Resume with `illusion -c -p "approve"` (or any feedback text to reject).
> **Note**: In print mode, sandbox permission confirmations offer **two options** (allow / deny) instead of the interactive three options.

### `default` mode: Cross-Turn Permission Approval (Y/N)

In `default` mode, when a tool requires permission, print mode does NOT block or directly deny. Instead:

1. **Turn 1**: `illusion -p "write a file"` → tool needs permission → persisted to `pending-permission-<session_id>.json` → exit code **2**, stderr shows guidance with tool name and Y/N commands
2. **Turn 2**: `illusion -c -p "Y"` → detects pending permission → injects approval → resumes execution

**Approval input** (case-insensitive):

| Input | Meaning | Persistence |
|-------|---------|-------------|
| `Y` / `yes` / `approve` | Allow once | None — only this tool call |
| `N` / other | Deny | None — LLM receives denial, may try alternatives |

```bash
# Read-only — safe, no side effects (no permission needed)
illusion -p "Analyze the project structure"

# Allow writes/commands — must be explicit
illusion --permission-mode full_auto -p "Fix the failing tests"

# default mode with Y/N approval
illusion -p "Write a test file"
# → exit 2, stderr: "Permission request: write_file. Use Y/N..."
illusion -c -p "Y"   # allow once
illusion -c -p "N"   # deny
```

### Sandbox Permission: Two-Option Cross-Turn Approval (Y/N)

When a tool is blocked by the **sandbox** in print mode, the confirmation uses a dedicated **two-option** flow (allow / deny) — distinct from the general Y/N permission flow. It never offers "always allow" for sandbox, so allowing one sandboxed operation cannot become a blanket pass for later destructive access.

1. **Turn 1**: `illusion -p "..."` → tool hits a sandbox restriction → persisted to `pending-sandbox-<session_id>.json` → exit code **2**, stderr: `Sandbox permission request: <tool>. Use Y/N...`
2. **Turn 2**: `illusion -c -p "Y"` → allows that single sandboxed operation and resumes; `illusion -c -p "N"` → denies it.

```bash
# sandbox two-option approval
illusion -p "read /etc/config"
# → exit 2, stderr: "Sandbox permission request: read_file. Use illusion -c -p \"Y\" / \"N\""
illusion -c -p "Y"   # allow once
illusion -c -p "N"   # deny
```

> **High-risk operations**: destructive commands (e.g. `rm`, `git restore`, `Remove-Item`) are ranked above reads. Even if a path was already allowed for the session, a destructive operation on it still triggers this sandbox confirmation.

## ask_user_question: Cross-Turn Non-Interactive Pattern

When the LLM calls `ask_user_question`, print mode does NOT block waiting for input. Instead:

1. **Turn 1**: `illusion -p "do something"` → LLM calls `ask_user_question` → question persisted to `pending-question-<session_id>.json` → program exits with **code 2**
2. **Turn 2**: `illusion -c -p "<answer>"` → detects pending question → injects answer → resumes execution

### Answer Formats

| Scenario | Format | Example |
|----------|--------|---------|
| Single question | Plain text | `strawberry` |
| Single question (multiSelect) | Comma-separated | `strawberry,mango` |
| Multiple questions | JSON, keys = headers | `{"Fruit": "strawberry", "OS": "Windows"}` |
| Multiple questions (multiSelect) | JSON with arrays | `{"Fruit": ["strawberry", "mango"]}` |

Headers are shown in brackets during Turn 1 output (e.g., `[Fruit] Which fruit?`). Non-JSON input for multi-question is passed as-is to the LLM (backward compatible).

```bash
# Single answer
illusion -c -p "strawberry"

# Multi-question JSON answer (escape quotes in shell)
illusion -c -p "{\"Fruit\": \"strawberry\", \"OS\": \"Windows\"}"
```

## Plan Approval: Cross-Turn Pattern

Identical to ask_user_question's cross-turn mechanism:

1. **Turn 1**: `illusion -p "implement X"` → agent enters plan mode → writes plan file → calls `exit_plan_mode` → plan persisted to `pending-plan-approval-<sid>.json` → exit code **2**
2. **Turn 2**: `illusion -c -p "approve"` → detects pending plan approval → injects approval result → resumes execution

**Approval input**: "approve"/"yes"/"y" (case-insensitive) → approved. Any other input → rejected with input as feedback.

**Note**: Plan content is NOT output to stderr. The agent receives the plan file path in the ToolResult and can read it with the `read_file` tool.

## Exit Codes

| Code | Meaning | Next Action |
|------|---------|-------------|
| 0 | Normal completion | Read stdout for result |
| 1 | Error | Check stderr for details |
| 2 | Waiting for user input (ask_user_question, plan approval, or permission approval) | Answer with `illusion -c -p "<answer>"` (answer for question, `"approve"` for plan, `"Y"`/`"F"`/`"N"` for permission) |

## Session Resume

| Flag | Description |
|------|-------------|
| `-c` / `--continue` | Continue most recent session in current directory |
| `-r <ID>` / `--resume <ID>` | Resume specific session by ID |

Both require `-p`. Combine with answer to continue after a pending question:

```bash
illusion -c -p "the answer"
illusion -r abc123 -p "the answer"
```

## Persistent Parameters

These persist to `settings.json` (survive across sessions):

| Flag | Description |
|------|-------------|
| `-m <env_N.model_N>` / `--model` | Model selection |
| `-e <LEVEL>` / `--effort` | Effort: `low`/`medium`/`high`/`max` |
| `-t <N>` / `--max-turns` | Max agentic turns |
| `--permission-mode <MODE>` | Permission mode |

Non-persistent: `-c`, `-r`, `-n`, `--dangerously-skip-permissions`

## Output Formats

`--output-format <FORMAT>`:

| Format | Description |
|--------|-------------|
| `text` (default) | Plain text to stdout, status to stderr |
| `json` | Single JSON object `{"type": "result", "text": "..."}` at end |
| `stream-json` | One JSON object per line (events: `assistant_delta`, `tool_started`, `tool_completed`, `assistant_complete`, `error`, `status`, `system`) |

## Common Patterns

### Read-only analysis (safe default)
```bash
illusion -p "Find all TODO comments in the codebase"
```

### Execute changes (explicit auto)
```bash
illusion --permission-mode full_auto -p "Run the test suite and fix failures"
```

### Structured output for programmatic consumption
```bash
illusion -p "List all public functions in src/" --output-format json
```

### Multi-turn agent conversation
```bash
# Turn 1: ask
illusion -p "Plan a refactor of auth.py"
# Returns exit code 2 with a question about approach

# Turn 2: answer
illusion -c -p "{\"Approach\": \"JWT\", \"Scope\": \"full\"}"
# Continues execution with the answer injected
```

### Combined flags
```bash
illusion -m env_1.model_2 -e high -t 20 --permission-mode full_auto -c -p "Complete this feature"
```

## Integration Tips for Controlling Agents

1. **Always check exit code**: 2 means a question is pending — you must answer it
2. **Parse stderr for questions**: Turn 1 prints questions to stderr with headers in brackets
3. **Use JSON output for parsing**: `--output-format json` gives clean structured result
4. **Prefer `full_auto` for autonomous tasks**: Avoids permission denials mid-execution
5. **Persist model/effort once**: Set `-m` and `-e` in an initial setup call, they persist
6. **Session ID continuity**: `-c` resumes the most recent session; `-r <ID>` for specific ones

## Anti-Patterns

- **Don't** expect interactive prompts — print mode never waits
- **Don't** put `-p` value before other flags — it must be last
- **Don't** forget `--permission-mode full_auto` when writes are needed and you want to skip confirmation
- **Don't** ignore exit code 2 — it means a question, plan approval, or permission approval needs answering
- **Don't** use `--dangerously-skip-permissions` — prefer `--permission-mode full_auto` (explicit intent)
- **Don't expect plan approvals to be automatic** in `default` print mode. If you want auto-approval, use `--permission-mode full_auto`.
- **Don't** assume `default` mode denies all tools — it now uses cross-turn Y/N approval; respond with `Y`/`N` on exit code 2.
