# Project Instruction & Memory Files

## Project plugin permissions

`<project>/.illusion/permissions.json` stores the `enabled_plugins` map for plugins installed in that project. Its `denied_plugins` list blocks named plugins from any source within the workspace, and `["*"]` blocks all plugins. A deny takes precedence over a `true` switch. The Web switch and terminal `/plugin enable` or `/plugin disable` commands choose the settings file by installation scope. See [Plugin Enable/Disable](extensions.md#plugin-enabledisable).

## AI Instruction Files (CLAUDE.md / ILLUSION.md / AGENTS.md)

`CLAUDE.md`, `ILLUSION.md`, and `AGENTS.md` are **equivalent** AI instruction files. IllusionAgent recognizes all three names interchangeably.

### Discovery Locations

The system scans for these files in the following locations (within the current working directory only, **not** in `~/.illusion/`):

1. **Project root**: `{cwd}/CLAUDE.md`, `{cwd}/AGENTS.md`, `{cwd}/ILLUSION.md`
2. **`.claude/` directory**: `{cwd}/.claude/CLAUDE.md`
3. **`.illusion/` directory**: `{cwd}/.illusion/CLAUDE.md`, `{cwd}/.illusion/AGENTS.md`, `{cwd}/.illusion/ILLUSION.md`

All discovered files are merged into a single `# Project Instructions` section in the system prompt. Each file is limited to 12,000 characters (truncated if exceeded).

### Rules Files

In addition to the instruction files above, the system also scans:

- `{cwd}/.claude/rules/*.md` — sorted by filename, each file is an independent rule

### Usage

Create any of these files in your project root to provide project-specific context and instructions:

```markdown
# Project Description

This is a Python Web project using the FastAPI framework.

## Code Standards

- Use Python 3.10+ features
- Follow PEP 8 code style
- Use type hints

## Directory Structure

- src/api: API routes
- src/models: Data models
- src/services: Business logic

## Notes

- Do not modify files in the tests/ directory
- Run pytest before committing
```

### Source Reference

File discovery logic: `src/illusion/prompts/claudemd.py` — `discover_claude_md_files()` function.

---

## Memory Files (MEMORY.md)

The memory system provides project knowledge persistence through `MEMORY.md` and associated memory files.

### Storage Locations

Memory uses a **single user-level storage**:

1. **Default**: `~/.illusion/memory/{project_name}-{sha1_hash_prefix}/`
2. **Custom**: when `settings.json` → `memory.directory` is set, that directory is used (absolute path or `~/` prefix)

### Directory Layout (type-based subdirectories)

Memory files are stored in type-specific subdirectories next to MEMORY.md, keeping the root clean:

```
~/.illusion/memory/{project}-{hash}/
├── MEMORY.md                  ← entry index
├── user/                      ← user-type memories
│   └── user_role.md
├── feedback/                  ← feedback-type memories
│   └── feedback_testing.md
├── project/                   ← project-type memories
│   └── project_plan.md
└── reference/                 ← reference-type memories
    └── reference_linear.md
```

Legacy root-layout files (pre-migration) are still scanned for compatibility. MEMORY.md index entries use paths relative to the memory directory (with the type subdirectory prefix, e.g. `- [Title](user/user_role.md) — hook`).

### MEMORY.md Entry File

`MEMORY.md` is the entry point file that serves as an index. Each entry is a one-line pointer:

```markdown
- [Title](user/user_role.md) — one-line description
- [Another Topic](project/roadmap.md) — another description
```

**Limits:**
- Maximum 200 lines / 25000 bytes (controlled by `memory.max_entrypoint_lines` / `memory.max_entrypoint_bytes` in settings.json), truncated with a warning beyond
- Maximum 5 relevant memory files injected into context (controlled by `memory.max_files` in settings.json)

### Memory File Format

Each memory file uses frontmatter format, stored in the subdirectory matching its `type`:

```markdown
---
name: short-kebab-case-slug
description: One-line summary for relevance matching
type: user|feedback|project|reference
---

Content of the memory entry. For feedback/project types, structure as:
- Rule/fact
- **Why:** reason
- **How to apply:** when/where this guidance applies
```

### Memory Types

| Type | Purpose | Subdirectory |
|------|---------|--------------|
| `user` | User role, goals, preferences, knowledge level | `user/` |
| `feedback` | Guidance on how to approach work (corrections and confirmations) | `feedback/` |
| `project` | Ongoing work, goals, initiatives, bugs, incidents | `project/` |
| `reference` | Pointers to external systems (Linear, Slack, Grafana, etc.) | `reference/` |

### Memory Reinforcement (Background Extraction + Auto Dream)

The system maintains memory quality automatically:

- **Background extraction** : after every `memory.extract_interval` conversation turns, a background sub-agent analyzes new messages and proactively saves durable facts (user preferences, corrections, project context). The sub-agent can only read and write inside the memory directory (including type subdirectories). The model can be configured via `memory.extract_model` (`env_N.model_M` format; unset inherits the current model).
- **Auto Dream consolidation** : when more than `memory.dream_min_hours` (default 24h) have passed since the last consolidation and `memory.dream_min_sessions` (default 5) sessions have elapsed, a background sub-agent merges duplicates, updates stale content, resolves conflicts, and prunes low-value entries. The model can be configured via `memory.dream_model` (`env_N.model_M` format; unset inherits the current model).

### Manual Mode (default, background LLM calls off)

`memory.auto_extract` is **disabled by default** (false): memory is enabled by default, but background LLM summarization (background extraction + Auto Dream) does not run. Set it to `true` to enable automatic extraction/consolidation. Memory is then recorded manually: when the user explicitly asks to remember something, the main-conversation LLM writes the memory file directly via Write/Edit tools (into the type subdirectory, updating the MEMORY.md index) — zero extra LLM consumption.

### Memory Management

Memory entries can be managed through:
- The `/memory` slash command in interactive sessions
- The `remember` skill (review and propose reorganization)
- Direct file editing in the memory directory

### Enable/Disable & Custom Directory

- `settings.json` → `memory.enabled: false` fully disables the memory system (no prompt injection, no search, no background extraction)
- Project `permissions.json` → `denied_memory: true` disables memory for a single project
- `settings.json` → `memory.directory: "~/my-memory"` sets a custom memory directory (also configurable in the web settings dialog)
- `memory.extract_model` / `memory.dream_model`: model references (`env_N.model_M` format) for the extraction / consolidation sub-agents; unset inherits the current model

### Initialization

The `/init` command creates an initial `MEMORY.md` template in the memory directory.

### Source Reference

- Path resolution: `src/illusion/memory/paths.py`
- Prompt building: `src/illusion/memory/memdir.py`
- Management: `src/illusion/memory/manager.py`
- Background extraction: `src/illusion/memory/extract.py`
- Auto Dream consolidation: `src/illusion/memory/auto_dream.py`
