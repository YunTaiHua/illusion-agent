# Tab Lifecycle Marks

- Agent-created tabs persist in the current IllusionAgent process until the model explicitly calls `tab.close()`, the user
  closes the tab/window, or the process exits. Claimed user tabs return to the user when released.
- `tab.markDeliverable()` keeps a user-facing result visible and releases it from browser control at turn cleanup.
- `tab.markHandoff()` keeps unfinished work visible and controllable by this session in a later turn.
- `browser.tabs.finalize({ keep })` changes only the tabs listed in `keep`. Unlisted active/handoff tabs stay open;
  absence from `keep` is never an implicit close request.
- `turnEnded` cancels pending requests and releases explicit deliverables or unmarked claimed user tabs, but it never
  closes a tab; an explicit handoff remains controlled. `closeSession` releases surviving tabs back to the owning
  conversation without closing their views. Released tabs never become visible to a different conversation.
- `browser.user.openTabs()` only returns the current conversation's non-empty user tabs. Empty URLs and exact
  `about:blank` placeholders are intentionally omitted.
- Closing every visible in-app browser tab in the current conversation requires both sources: close controlled tabs from
  `browser.tabs.list()`, then claim and close user tabs returned by `browser.user.openTabs()`. Other conversations remain
  inaccessible.
