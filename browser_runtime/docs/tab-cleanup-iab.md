# Tab Cleanup

- IAB tabs persist for the lifetime of the current IllusionAgent process. Turn end, session end, an omitted finalize call,
  and omission from `keep` do not close a tab.
- Call `tab.close()` only when the model intentionally decides to close that exact tab. A user may also close tabs
  directly in the UI.
- `browser.tabs.finalize({ keep })` is a lifecycle-marking operation, not a cleanup allowlist. Listed tabs become
  `deliverable` or `handoff`; unlisted tabs retain their current lifecycle and remain visible.
- Use `deliverable` when a live page is the requested result and should be released from agent control. Use
  `handoff` when unfinished work must remain controllable by the same session.
- IllusionAgent does not restore these tabs after the IllusionAgent process exits.
