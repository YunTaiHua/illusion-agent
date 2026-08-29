# User Tab Claiming

- To control an already-open in-app browser page, call `browser.user.openTabs()`, match the visible title and URL,
  and pass that returned object to `browser.user.claimTab(info)`.
- Claiming returns a controllable `Tab`. Reuse it within the current validated operation batch; before a later batch,
  list controlled tabs again and rebind the intended target.
- Do not pass an `openTabs()` id to `browser.tabs.get()`: `tabs.get()` only binds a tab already controlled by the
  current Browser Use session.
- Conversely, `browser.tabs.list()` returns controlled `TabInfo` metadata, not a controllable object. Restore it
  with `const tab = await browser.tabs.get(info.id)`.
- When an action may open a popup/new tab and the source tab does not show the expected effect, read
  `browser.tabs.list()` and `browser.user.openTabs()` unconditionally in the same observation cell. Return
  `{ controlledTabs, userTabs }` as that cell's final result so the model makes one decision from both lists, then
  claim the matching user tab in the next cell.
- Prefer claiming the matching visible page over opening another tab with the same URL.
