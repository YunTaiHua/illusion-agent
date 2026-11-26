# Mentions and slash resources

Type `@` at the start of the input or after whitespace to find a session, file, or directory. To find a skill or command, type `/`. Both menus work while composing a message. Enter or Tab accepts a suggestion and Esc closes the menu.

## Mention Types

| Section | Inserted text | How content reaches the model |
| --- | --- | --- |
| Sessions | `@[Session title](illusion-session:<id>)` | The engine resolves the mention at submission time and injects a read-only snapshot of the source session as context |
| Files | `@src/main.py` (directories keep a trailing `/` for drilling down) | Plain prompt text; the model reads the file with its `read` tool |

Names containing spaces use the quoted form `@"name with spaces"`. Mentions render in the accent color inside the input box and in sent user bubbles — plain text, no visual decoration beyond the color.

## Skills and commands with `/`

In either interface, type `/` where you want to use a resource, for example `Review @src/main.py with /`. Selecting a resource inserts its full name at the cursor so you can keep writing. The input and the sent user message highlight recognized references such as `/my-plugin:review`.

The terminal shows one `/name [type]` per row, such as `/my-plugin:review [plugin skill]`. Use ↑ and ↓ to move between rows. The Web menu groups entries under headings such as `SKILLS` and `PLUGIN COMMANDS` and shows descriptions when available.

At the start of a message, `/my-plugin:review release-1` passes `release-1` as the argument. Within a sentence, `Please use /my-plugin:review to check @src/main.py` loads the resource and keeps the whole sentence as your request. Use backticks to quote a name literally. Built-in interface actions such as `/exit` in the terminal use their usual command position at the start.

## Session References

Session mentions let one conversation pull in the context of another. From the `Sessions` section of the `@` menu, pick a recent session (same workspace first, sorted by last update; the current and empty sessions are excluded). The inserted token is canonical text:

```
@[Research notes](illusion-session:abc123def456)
```

The canonical form is what travels end to end: the input box, the submitted message, the persisted `context.jsonl`, and the replayed user message on `/rewind` or `/resume` all keep exactly this text — no conversion happens anywhere, so what you see is what the model sees.

### Snapshot Injection

At submission time the engine parses the canonical mentions and:

- The user message keeps the canonical mention text as-is.
- Immediately after it, a second message is injected containing a **fixed, immutable snapshot** of the source session — plain user/assistant text only (tool calls/results, thinking, and other injected messages are excluded; a prior compaction summary is kept as the conversation opening).
- Each source session gets an independent byte budget (64 KiB). When over budget, the oldest messages are dropped first (the newest is never dropped), then the longest texts are head/tail-truncated with an explicit `[… omitted N UTF-8 bytes …]` marker.
- The snapshot is captured once and persisted with the session, so replaying/restoring the session shows exactly what the model saw; later changes in the source session do not alter an already-captured snapshot.

The snapshot is wrapped in a warning telling the model it is **untrusted background data** — instructions, permission claims, or tool requests inside a snapshot must not be followed unless the current user repeats them. The JSON is serialized tag-safe (`<` escaped), so snapshot content can never forge the surrounding markers. Duplicates are de-duplicated, self-references are ignored, and at most 3 session references per message are resolved; references to missing sessions surface an explicit error entry instead of failing the whole turn.

### Where Snapshots Do Not Count

Injected snapshot messages are not real user input. They are excluded from turn counts, session summaries, auto titles, rewind point selection, and the replay transcript — the visible user message (still canonical mention text) remains the only trace of the turn.

## Privacy & Scope

- Candidate discovery reads metadata only (session id / title / summary / turn count); message contents are never scanned for the menu.
- Snapshots may include sessions from every registered workspace; the source workspace is resolved automatically at submission time.
