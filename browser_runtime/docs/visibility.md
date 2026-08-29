# Browser Visibility Guidance

- Creating an IAB tab automatically opens and activates the right browser pane so the user can see browser use in progress.
- Keep the pane visible during normal browser work unless the task explicitly calls for hiding it.
- Use visibility controls to hide the pane or show it again; callers do not need to call `set(true)` after `tabs.new()`.
- Show or hide it with `await (await browser.capabilities.get("visibility")).set(true | false)`; read the current state with `get()`.
