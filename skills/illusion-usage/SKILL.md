---
name: illusion-usage
description: Dual-purpose guide for agents. Scenario A — agent autonomously drives IllusionAgent via non-interactive print mode (-p). Scenario B — agent helps its human master install and use IllusionAgent's interactive TUI/Web modes. Invoke when an agent needs to operate IllusionAgent, install it for a user, or teach a user how to use it.
---

# IllusionAgent Usage: Agent Dual-Role Guide

This skill serves two scenarios. **Detect your role first**, then follow the matching track.

> **Live documentation**: ALWAYS fetch the latest docs from GitHub before acting — do not rely on hardcoded commands in this skill. Repo: `https://github.com/YunTaiHua/illusion-agent`. Docs: `https://github.com/YunTaiHua/illusion-agent/tree/main/docs`. Use `WebFetch`, `web_fetch`, or an equivalent tool to read the URLs in [Reference URLs](#reference-urls) below.

## Role Detection

| Your situation | Track to follow |
|----------------|-----------------|
| You (the agent) will invoke `illusion -p` as a subprocess to do coding tasks autonomously | **Track A: Agent-Driven Print Mode** |
| Your human master wants to install/use IllusionAgent themselves, and you guide them | **Track B: Human-Friendly TUI & Web** |
| Both — you'll drive it AND teach your master | Read both tracks; Track A for your own use, Track B for your master |

---

# Track A: Agent-Driven Print Mode

Use print mode when YOU (the agent) operate IllusionAgent as a non-interactive subprocess. Every `-p` invocation is an atomic request-response — no waiting for interactive input within the same turn.

## A.0 Fetch Current Docs (MANDATORY before install)

Use a web fetch tool to read these in order. This skill only outlines the workflow — concrete flags, file paths, and auth providers may have changed.

1. **README** — `https://github.com/YunTaiHua/illusion-agent`
2. **Getting Started** — `https://github.com/YunTaiHua/illusion-agent/blob/main/docs/en/getting-started.md`
3. **Commands** — `https://github.com/YunTaiHua/illusion-agent/blob/main/docs/en/commands.md`
4. **Settings** — `https://github.com/YunTaiHua/illusion-agent/blob/main/docs/en/settings.md`

> If any URL 404s, fall back to PyPI (`https://pypi.org/project/illusion-agent/`) and follow its homepage link. Do NOT assume commands below are current — verify against fetched docs.

## A.1 Installation

**Recommended (no Node.js required)**:

```bash
pip install illusion-agent
illusion --version
```

**From source (developers, requires Node.js 18+)**:

```bash
git clone https://github.com/YunTaiHua/illusion-agent.git
cd illusion-agent
pip install .          # or: pip install -e .   (editable)
```

Always confirm the exact install command from the fetched Getting Started doc.

## A.2 Authentication (Interactive, One-Time)

Print mode requires prior auth. **This is the one interactive step** — complete it once via TTY before any `-p` invocation:

```bash
illusion auth login
```

Common providers (verify against fetched Settings doc):

- **Custom** — API format (anthropic/openai), base URL, API key, model name
- **Anthropic** — direct API key
- **OpenAI** — direct API key
- **GitHub Copilot** — OAuth device flow
- **OpenAI Codex** — OAuth device flow

Credentials stored in `~/.illusion/credentials.json`, grouped by `env_N`. Models are referenced as `env_N.model_N` (e.g., `env_1.model_2`).

## A.3 Verify Environment

```bash
illusion -p "Reply with: OK" --output-format json
```

Should print `{"type": "result", "text": "OK"}` and exit 0. If non-zero, check credentials/settings files.

## A.4 Print Mode Core

```bash
illusion -p "<prompt>"
```

**Critical**: `-p`'s value **must be the last argument** (typer parses it greedily).

### Exit codes (always check)

| Code | Meaning | Action |
|------|---------|--------|
| 0 | Normal completion | Parse stdout for result |
| 1 | Error | Read stderr; fix and retry |
| 2 | Waiting for user input (question, plan approval, or permission approval) | Answer with `illusion -c -p "<answer>"` (answer for question, `"approve"` for plan, `"Y"`/`"F"`/`"N"` for permission) |

### stdout vs stderr

- **stdout**: assistant text, JSON results
- **stderr**: status, permission denials, questions, errors

For programmatic parsing, use `--output-format json` (single object at end) or `--output-format stream-json` (one object per line, events: `assistant_delta`, `tool_started`, `tool_completed`, `assistant_complete`, `error`, `status`, `system`).

## A.5 Permission Modes (Critical)

Print mode uses cross-turn Y/N callback for permissions. Choose explicitly:

| Mode | Behavior | Use when |
|------|----------|----------|
| `default` (omit) | Mutating tools trigger **cross-turn Y/N approval** | Selective approval, interactive-ish |
| `full_auto` | All tools execute | Autonomous coding, writes, commands |
| `plan` | All mutation tools blocked | Planning only |

> **Plan Approval Flow**:
> - **Terminal/Web**: Approval card UI shown inline
> - **Print mode**: Cross-turn (exit code 2, resume with `illusion -c -p "approve"`)
> - **Channel**: Plan content sent as message, user replies to approve/reject

### `default` mode: Cross-Turn Permission Approval (Y/N)

In `default` mode, mutating tools trigger a cross-turn approval instead of direct denial:

1. **Turn 1**: `illusion -p "write a file"` → exit 2, stderr shows `Permission request: {tool}. Use Y/N...`
2. **Turn 2**: `illusion -c -p "Y"` → approve once; `"N"` → deny

```bash
# Read-only (no permission needed)
illusion -p "Analyze the project structure"

# Autonomous coding (skip Y/N approval)
illusion --permission-mode full_auto -p "Fix the failing tests"

# default mode with Y/N approval
illusion -p "Write a test file"
# → exit 2, stderr shows permission request with Y/N guidance
illusion -c -p "Y"   # allow once
illusion -c -p "N"   # deny
```

> **Autonomous agents**: default to `full_auto` for coding tasks to avoid interruptions.

## A.6 ask_user_question — Cross-Turn Non-Interactive

When the LLM calls `ask_user_question`, print mode persists the question and exits with code 2. Answer in the next invocation:

```bash
# Turn 1
illusion -p "Refactor auth.py"
# → exit 2, stderr prints questions with [header] markers

# Turn 2
illusion -c -p "<answer>"
```

### Answer formats

| Scenario | Format | Example |
|----------|--------|---------|
| Single question | Plain text | `strawberry` |
| Single (multiSelect) | Comma-separated | `strawberry,mango` |
| Multiple questions | JSON, keys = headers | `{"Fruit": "strawberry", "OS": "Windows"}` |
| Multiple (multiSelect) | JSON arrays | `{"Fruit": ["strawberry", "mango"]}` |

Headers shown in brackets in Turn 1 stderr (e.g., `[Fruit] Which fruit?`). Non-JSON input is passed as-is (backward compatible).

### Shell escaping for JSON

```bash
illusion -c -p "{\"Fruit\": \"strawberry\", \"OS\": \"Windows\"}"
```

If your agent passes args as a list (not shell string), no escaping needed.

### Detecting pending questions

1. Exit code 2 → question pending
2. Parse stderr for `[<header>]` lines
3. Build JSON answer from headers
4. Resume with `illusion -c -p "<json>"`

## A.7 Session Continuity

| Flag | Description |
|------|-------------|
| `-c` / `--continue` | Continue most recent session in cwd |
| `-r <ID>` / `--resume <ID>` | Resume specific session by ID |

Both require `-p`. Use `-c` for linear flows; `-r <ID>` for parallel sessions. Session files under `~/.illusion/sessions/` (verify path in docs).

## A.8 Persistent Parameters

Set once, survive across sessions:

| Flag | Description |
|------|-------------|
| `-m <env_N.model_N>` / `--model` | Model selection |
| `-e <LEVEL>` / `--effort` | Effort: `low`/`medium`/`high`/`max` |
| `-t <N>` / `--max-turns` | Max agentic turns |
| `--permission-mode <MODE>` | Permission mode |

Non-persistent: `-c`, `-r`, `-n`, `--output-format`, `--dangerously-skip-permissions` (avoid this — prefer `--permission-mode full_auto`).

## A.9 Common Agent Workflows

### Read-only analysis
```bash
illusion -p "Find all TODO comments" --output-format json
```

### Autonomous bug fix
```bash
illusion --permission-mode full_auto -p "Fix test_login_expires in tests/test_auth.py"
```

### Multi-turn clarification
```bash
illusion -p "Add a rate limiter to the API"
# exit 2 → [Algorithm] [Limit]
illusion -c -p "{\"Algorithm\": \"token bucket\", \"Limit\": \"100\"}"
```

### Parallel sessions
```bash
illusion -p "Refactor db layer" -n "db"
illusion -p "Add telemetry" -n "tel"
illusion -r <session-id> -p "Continue"
```

## A.10 Agent Integration Checklist

1. Installed — `illusion --version` works
2. Authenticated — `illusion auth login` done via TTY
3. Verified — trivial `-p "OK"` exits 0
4. Docs fetched — read latest Getting Started, Commands, Settings
5. Permission strategy — `full_auto` for coding, `default` for read-only
6. Exit code handling — wrapper checks 0/1/2
7. JSON answer builder — ready for exit 2 cases
8. Session tracking — capture/reuse session IDs

---

# Track B: Human-Friendly TUI & Web

Use this track when your human master wants to install and use IllusionAgent themselves. **Default to interactive modes** (TUI/Web) — they are far more user-friendly than print mode for humans. Guide your master step by step, and fetch the latest docs to give accurate instructions.

## B.0 Fetch Current Docs (MANDATORY before guiding)

Use `WebFetch` / `web_fetch` to read the latest docs before giving any instruction. Repo: `https://github.com/YunTaiHua/illusion-agent`.

Recommended fetch order for guiding humans:

1. **README** — `https://github.com/YunTaiHua/illusion-agent` — show your master the project overview and latest features
2. **Getting Started** — `https://github.com/YunTaiHua/illusion-agent/blob/main/docs/en/getting-started.md` (or `docs/zh-CN/getting-started.md` for Chinese) — install steps, first-run guide
3. **Commands** — `https://github.com/YunTaiHua/illusion-agent/blob/main/docs/en/commands.md` (or `docs/zh-CN/commands.md`) — slash commands for interactive sessions
4. **Settings** — `https://github.com/YunTaiHua/illusion-agent/blob/main/docs/en/settings.md` — for advanced configuration

> Match the doc language to your master's preference. Both `en/` and `zh-CN/` directories exist in the docs.

## B.1 Guide Your Master Through Installation

**Recommended for most users** (no Node.js required):

Tell your master to run in their terminal:

```bash
pip install illusion-agent
```

Then verify:

```bash
illusion --version
```

> Always confirm the exact install command from the fetched Getting Started doc — the project may have added new install methods (e.g., `uv`, `pipx`, homebrew).

**For developers** (requires Node.js 18+):

```bash
git clone https://github.com/YunTaiHua/illusion-agent.git
cd illusion-agent
pip install -e .
```

## B.2 Guide Authentication (Interactive)

Tell your master this is a one-time setup:

```bash
illusion auth login
```

This launches an interactive provider picker. Walk them through the options:

- **Custom** — for self-hosted or third-party API-compatible endpoints
- **Anthropic** — direct Claude API key
- **OpenAI** — direct OpenAI API key
- **GitHub Copilot** — OAuth device flow (no API key needed, just sign in to GitHub)
- **OpenAI Codex** — OAuth device flow

> Verify the provider list from the fetched Settings doc. Read the auth section carefully and relay the exact prompts your master will see.

## B.3 Recommend Interactive Modes (Default for Humans)

### Terminal TUI (Recommended primary mode)

```bash
illusion
```

This launches the React-based terminal interface — full-screen, mouse support, streaming responses, slash commands, todo panel, etc. **This is what most humans should use.**

> Fetch the Commands doc to learn the available slash commands (`/help`, `/clear`, `/model`, `/config`, `/permissions`, etc.) so you can teach your master.

### Web UI (Supplementary, browser-based)

```bash
illusion web
# or with custom port
illusion web --port 8080
```

Opens a browser-based interface. Good when:
- A terminal is unavailable (remote server, tablet with web browser)
- User prefers mouse-driven UI
- Sharing a session on a screen

> The Web UI is supplementary — the terminal TUI is the primary recommended mode. Confirm this from the fetched Getting Started doc.

### When to mention print mode to humans

Only mention `illusion -p` to humans for:
- One-off quick questions from scripts
- CI/automation pipelines
- Piping output to other tools

For everyday coding, steer them to `illusion` (TUI).

## B.4 Teach Basic Interactive Usage

Walk your master through their first session:

```bash
# Start
illusion

# In the session, try:
# - Type a question and press Enter
# - Type /help to see slash commands
# - Type /model to switch models
# - Type /clear to start fresh
# - Press Ctrl+C twice or /exit to quit
```

Fetch the Commands doc to give them an accurate list of slash commands and keyboard shortcuts.

## B.5 Guide Configuration (Optional, Advanced)

If your master wants to customize, point them to `~/.illusion/settings.json`:

- **Model**: `-m env_1.model_2` or `/model` in session
- **Effort**: `-e high` or `/thinking` in session
- **Permission mode**: `--permission-mode full_auto` or `/permissions` in session
- **Hooks**: automate actions after tool use (see Settings doc)
- **MCP servers**: connect external tools (see Settings doc)
- **Plugins**: extend functionality (see Settings doc)

> Fetch the Settings doc to give accurate JSON schema examples. Never hardcode config snippets from this skill — always relay the latest from the docs.

## B.6 Guide Channel Setup (Optional, Mobile Access)

If your master wants to use IllusionAgent from their phone via messaging apps:

- **Feishu/Lark** — `illusion channel login`, select Feishu
- **WeChat** — `illusion channel login`, select WeChat (iLink Bot API)
- **QQ** — `illusion channel login`, select QQ (official Bot API)

Fetch the Channels doc for setup details: `https://github.com/YunTaiHua/illusion-agent/blob/main/docs/en/channels.md` (or `docs/zh-CN/channels.md`).

## B.7 Human-Facing Anti-Patterns

- **Don't** tell humans to use `illusion -p` for everyday coding — steer them to `illusion` (TUI)
- **Don't** hardcode install steps — fetch the latest Getting Started doc
- **Don't** skip authentication — `illusion auth login` is required before first use
- **Don't** recommend `--dangerously-skip-permissions` — use `/permissions` in session or `--permission-mode full_auto`
- **Don't** assume the Web UI replaces the TUI — TUI is primary, Web is supplementary
- **Don't** give config examples from memory — fetch the Settings doc for current schema

## B.8 Human Integration Checklist

Before your master starts coding:

1. **Installed** — `illusion --version` works
2. **Authenticated** — `illusion auth login` completed
3. **Knows how to start** — `illusion` launches the TUI
4. **Knows slash commands** — at least `/help`, `/clear`, `/model`, `/exit`
5. **Knows permission modes** — `/permissions` to switch
6. **Knows where docs live** — bookmark the repo and docs directory
7. **Optional: Web UI** — `illusion web` for browser access
8. **Optional: Channels** — mobile access via Feishu/WeChat/QQ

---

# Reference URLs (verify they resolve before relying on them)

- Repository: `https://github.com/YunTaiHua/illusion-agent`
- PyPI package: `https://pypi.org/project/illusion-agent/`
- Getting Started (EN): `https://github.com/YunTaiHua/illusion-agent/blob/main/docs/en/getting-started.md`
- Getting Started (中文): `https://github.com/YunTaiHua/illusion-agent/blob/main/docs/zh-CN/getting-started.md`
- Commands (EN): `https://github.com/YunTaiHua/illusion-agent/blob/main/docs/en/commands.md`
- Commands (中文): `https://github.com/YunTaiHua/illusion-agent/blob/main/docs/zh-CN/commands.md`
- Settings (EN): `https://github.com/YunTaiHua/illusion-agent/blob/main/docs/en/settings.md`
- Settings (中文): `https://github.com/YunTaiHua/illusion-agent/blob/main/docs/zh-CN/settings.md`
- Channels (EN): `https://github.com/YunTaiHua/illusion-agent/blob/main/docs/en/channels.md`
- Channels (中文): `https://github.com/YunTaiHua/illusion-agent/blob/main/docs/zh-CN/channels.md`
- Architecture (EN): `https://github.com/YunTaiHua/illusion-agent/blob/main/docs/en/architecture.md`

If any URL above 404s, the docs may have been reorganized — start from `https://github.com/YunTaiHua/illusion-agent` and navigate to the `docs/` directory to find the current paths.
