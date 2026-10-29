# Getting Started

## Requirements

- Python >= 3.10
- Supports Windows, macOS, Linux
- Windows users: Auto-detect Git, no manual PATH configuration needed
- Node.js 18+: Only required for source installs; `pip install illusion-agent` does not need Node.js

## Installation

### Recommended: pip install from PyPI

The simplest way to install IllusionAgent. Automatically installs both frontends and registers the `illusion` command globally. **No Node.js required** — frontend assets are pre-built and included in the package.

```bash
pip install illusion-agent
```

No git clone required — everything is included in the package.

### Alternative: pip install from source

Clone the repository and install locally. Triggers the hatch build hook to build frontends automatically.

```bash
git clone https://github.com/YunTaiHua/illusion-agent.git
cd illusion-agent
pip install .
```

Requires Node.js 18+ (for frontend build).

### Alternative: pip install -e . (editable, from source)

Editable install from source. Like `pip install .`, it triggers the hatch build hook to build frontends automatically and registers `illusion` globally. Like `uv sync`, source code changes take effect immediately without reinstalling.

```bash
git clone https://github.com/YunTaiHua/illusion-agent.git
cd illusion-agent
pip install -e .
```

Requires Node.js 18+ (for frontend build).

> **When to use**: Best for developers who want both an editable install (live code changes) and the `illusion` command available globally. Unlike `uv sync`, no manual frontend build or `uv run` wrapper is needed.

### Alternative: uv sync (for development)

`uv sync` creates an editable install within the project directory. It does **not** trigger the hatch build hook, so you must build frontends manually. This is recommended for developers who want to modify the source code.

```bash
git clone https://github.com/your-repo/illusion-agent.git
cd illusion-agent
uv sync

# Build frontends manually (required after uv sync)
python scripts/build_frontend.py              # Build both
python scripts/build_frontend.py --terminal   # Terminal TUI only
python scripts/build_frontend.py --web        # Web UI only
```

> **Note**: `uv sync` does NOT register `illusion` globally. To use it:
>
> ```bash
> # Option 1: Use uv run from the project directory
> cd illusion-agent
> uv run illusion
>
> # Option 2: Activate the virtual environment
> # Windows
> .venv\Scripts\activate
> # macOS / Linux
> source .venv/bin/activate
> illusion
>
> # Option 3: Install globally with pip (recommended)
> pip install .
>
> # Option 4: Editable install with pip (global + live code changes)
> pip install -e .
> ```

### Manual frontend build (for source installs only)

If you installed from source and need to rebuild frontends (e.g., after updating frontend code). PyPI users do not need this step.

**Build script (recommended)**

```bash
python scripts/build_frontend.py              # Build both
python scripts/build_frontend.py --terminal   # Terminal TUI only
python scripts/build_frontend.py --web        # Web UI only
```

**npm directly**

```bash
# Terminal TUI (esbuild → dist/index.mjs)
cd frontend/terminal
npm install --no-fund --no-audit
npm run build
cd ../..

# Web UI (Vite → dist/)
cd frontend/web
npm install --no-fund --no-audit
npm run build
cd ../..
```

### Key differences

| | `pip install illusion-agent` | `pip install .` | `pip install -e .` | `uv sync` |
|---|---|---|---|---|
| Source | PyPI | Local git clone | Local git clone | Local git clone |
| Frontend build | Pre-built (included) | Automatic (hatch hook) | Automatic (hatch hook) | Manual |
| Node.js required | **No** | Yes (18+) | Yes (18+) | Yes (18+) |
| `illusion` command | Global | Global | Global | Project-only (via `uv run` or venv activation) |
| Install type | Standard | Standard | Editable | Editable |
| Code changes take effect | Reinstall needed | Reinstall needed | Immediately | Immediately |
| Best for | End users | Contributors | Developers (global + editable) | Developers |

## Basic Usage

> **First-time setup**: Run `illusion auth login` first to configure your API credentials. Without authentication (or if the model is unavailable), the program may exit with an error code.

```bash
# First-time: configure authentication
illusion auth login

# Start interactive session (recommended)
illusion

# Launch Web UI in browser
illusion web

# Web UI with custom port
illusion web --port 8080

# Non-interactive print mode: read-only analysis (safe in default permission mode)
illusion -p "Analyze the structure of this project"

# Print mode with explicit auto-approval for writes/commands
illusion --permission-mode full_auto -p "Fix the failing tests"

# Specify model
illusion -m env_1.model_2

# Continue most recent session (use with -p)
illusion -c -p "Continue the previous session"

# Restore specific session (use with -p)
illusion -r <session-id> -p "Continue"

# Answer a pending permission/question/plan after exit code 2
illusion -c -p "Y"

# Set permission mode
illusion --permission-mode full_auto

# Set effort level (persists to settings)
illusion -e high
```

> **Note**: The terminal interface (`illusion`) and the Web UI (`illusion web`) are two independent, first-class interfaces. They share the same backend runtime, settings, and session storage — use whichever fits your workflow. The terminal is great for keyboard-driven workflows; the Web UI is ideal for mouse-based browsing, long outputs, or side-by-side use.

---

## Print Mode Details

`-p` / `--print` runs a single prompt non-interactively and exits. It is designed for scripts, CI, and controlling IllusionAgent from other agents.

### Important rules

- The `-p` value must be the **last argument** on the command line because typer parses it greedily.
- Use `--permission-mode full_auto` when the task needs to write files or run commands without manual approval.
- In default permission mode, mutating tools exit with code **2** and persist a pending approval. Resume with `illusion -c -p "Y"` (allow once), `"F"` (always allow), or `"N"` (deny).

### Exit codes

| Code | Meaning | Next action |
|------|---------|-------------|
| 0 | Success | Read stdout for the result |
| 1 | Error | Check stderr for details |
| 2 | Waiting for cross-turn input | Answer with `illusion -c -p "<answer>"` |

### Common patterns

```bash
# Read-only analysis (safe)
illusion -p "Find all TODO comments in the codebase"

# Autonomous execution
illusion --permission-mode full_auto -p "Run the test suite and fix failures"

# Structured JSON output for downstream scripts
illusion -p "List all public functions in src/" --output-format json

# Multi-turn conversation
illusion -p "Plan a refactor of auth.py"                 # exits 2 with a question
illusion -c -p '{"Approach": "JWT", "Scope": "full"}'    # continues with the answer
```

---

## Development & Testing

```bash
# Install development dependencies
uv sync --dev

# Run tests
pytest
```

---

## License

This project is open-sourced under the [MIT](../LICENSE) license.

---

## Contributing

Welcome to submit Issues and Pull Requests!
