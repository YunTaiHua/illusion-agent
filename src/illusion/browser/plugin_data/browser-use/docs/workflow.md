# Browser workflow (canonical loop)

1. **List tabs** — `browser_tabs action="list"`; match target by verified id/url/title; `select` if needed.
2. **Navigate** — `browser_navigate` (reuses same-site tabs; waits for domcontentloaded). Direct URLs only from the user, visible page facts, or authoritative lookups.
3. **Observe** — `browser_snapshot` (ARIA tree + refs). This is the primary read; screenshots only when vision matters.
4. **Act** — `browser_click` / `browser_type` with a snapshot ref; `browser_press_key`; `browser_scroll`. One state-changing action per observation cycle.
5. **Verify** — the cheapest observation that answers your next question: fresh `browser_snapshot`, targeted state, or `browser_screenshot` for visual truth. An unchanged URL does not prove failure.
6. **Popups/new tabs** — re-run `browser_tabs action="list"` and match by verified facts before continuing.
7. **Cleanup** — tabs persist for the session; close only tabs you created and no longer need.

Failure handling: a failed command is not a crashed browser. Take a fresh snapshot and rebuild from snapshot-proven facts; never retry the same stale ref.
