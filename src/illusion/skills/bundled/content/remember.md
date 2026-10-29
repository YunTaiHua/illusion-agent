---
name: remember
description: Review memory entries and propose promotions, cleanup, or reorganization. Detects outdated, conflicting, and duplicate entries.
---

# Memory Review

## Goal
Review the user's memory landscape and produce a clear report of proposed changes, grouped by action type. Do NOT apply changes — present proposals for user approval.

## Memory System Overview

IllusionAgent uses a file-based memory system (aligned with Claude Code Auto Memory):
- **Memory directory**: `~/.illusion/memory/<project-name>-<hash>/` (or the custom directory configured via `settings.json` → `memory.directory`)
- **Entry point**: `MEMORY.md` (index file with one-line pointers to memory files, format `- [Title](user/user_role.md) — one-line hook`, paths relative to the memory directory)
- **Type subdirectories**: memory files are stored in `user/`, `feedback/`, `project/`, `reference/` subdirectories matching their `type` field (root-layout legacy files are also scanned)

### Memory File Format
```markdown
---
name: short-kebab-case-slug
description: One-line summary for relevance matching
type: user|feedback|project|reference
---

Memory content here. For feedback/project types, structure as:
- Rule/fact
- **Why:** Reason
- **How to apply:** When/where this guidance applies
```

### Memory Types
- **user**: User role, goals, preferences, knowledge → `user/`
- **feedback**: Guidance on how to approach work (corrections and confirmations) → `feedback/`
- **project**: Ongoing work, goals, initiatives, bugs → `project/`
- **reference**: Pointers to external resources → `reference/`

## Steps

### 1. Gather all memory layers
- Read the `MEMORY.md` entry point from the memory directory
- Read all `.md` files in the memory directory and its type subdirectories (user/feedback/project/reference, excluding MEMORY.md itself)
- Check the `settings.json` `memory` section for custom directory / enabled / auto_extract flags

**Success criteria**: You have the contents of all memory files and can compare them.

### 2. Classify each memory entry
For each substantive memory entry, evaluate its current placement:

| Check | What to look for |
|-------|------------------|
| **Staleness** | Is the information still accurate? Does it reference things that no longer exist? |
| **Duplicates** | Is the same information captured in multiple places? |
| **Conflicts** | Do any entries contradict each other? |
| **Type accuracy** | Is the `type` field correct (user/feedback/project/reference)? |
| **Description quality** | Does the description accurately summarize the content for relevance matching? |

**Success criteria**: Each entry has been evaluated for the above checks.

### 3. Identify cleanup opportunities
Scan across all memory files for:
- **Outdated entries**: Information contradicted by newer entries or current codebase state
- **Duplicates**: Same information in multiple files
- **Conflicts**: Contradictions between entries → propose resolution, noting which is more recent
- **Orphaned entries**: Memory files not linked from MEMORY.md
- **Missing descriptions**: Files without proper `description` field in frontmatter

**Success criteria**: All issues identified.

### 4. Present the report
Output a structured report grouped by action type:

1. **Updates** — entries that need content or metadata corrections
   - Include: file name, what needs to change, why
2. **Cleanup** — duplicates, outdated entries, conflicts to resolve
   - Include: file names involved, recommendation
3. **Reorganization** — entries that should be split, merged, or retyped
   - Include: current file, proposed changes
4. **No action needed** — brief note on entries that are fine as-is

If memory is empty, say so and offer to help create initial memory entries.

**Success criteria**: User can review and approve/reject each proposal individually.

## Proposing Changes

When proposing changes, use the memory file format:

```markdown
---
name: proposed-slug
description: One-line summary
type: user|feedback|project|reference
---

Proposed content here.
```

## Rules
- Present ALL proposals before making any changes
- Do NOT modify files without explicit user approval
- Do NOT create new files unless the target doesn't exist yet
- Preserve the YAML frontmatter format when creating or updating files
- Use `add_memory_entry` to create new memory files (handles slug generation and MEMORY.md indexing)
- Use `remove_memory_entry` to delete memory files (handles MEMORY.md cleanup)
