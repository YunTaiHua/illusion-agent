# Browser use overview

IllusionAgent ships a built-in browser driven by the `browser_*` tools and mirrored live in the right-side browser panel.

- **Backend modes**: `desktop` (Electron in-app webview — live, user-interactive) and `managed` (headless Playwright Chromium; panel shows screenshot frames). The host selects the mode; tools behave identically.
- **Kernel resolution (managed mode)**: Playwright Chromium → system Chrome → system Edge. First use can auto-install (`browser.auto_setup`).
- **Continuity**: tools are stateless, browser tabs persist for the session. Always re-list tabs (`browser_tabs action="list"`) before acting on one.
- **Reading pages**: `browser_snapshot` returns an ARIA tree with `[ref=eN]` markers; act via refs. Screenshots are for vision/aiming only.
- **Scope**: `http:`, `https:`, `about:blank` only. Viewport 320-3840 x 320-2160 (default 1280x720).

See also: workflow.md, safety.md, viewport.md, screenshot.md.
