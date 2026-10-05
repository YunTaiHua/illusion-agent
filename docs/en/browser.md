# Built-in Browser (browser-use)

IllusionAgent ships a built-in browser that lets you (and the agent) open,
browse, operate and verify web pages directly — for agent- and user-driven web automation. The right-panel "Browser" section is the entry point; the browser
itself opens as a tab in the **file preview card** on the right, side by side
with file/diff previews.

## Contents

- [Quick start](#quick-start)
- [Layout](#layout)
- [Two run modes](#two-run-modes)
- [How the agent uses the browser](#how-the-agent-uses-the-browser)
- [Configuration](#configuration)
- [Plugin and switches](#plugin-and-switches)
- [Browser data management](#browser-data-management)
- [FAQ](#faq)

---

## Quick start

1. Expand the right panel, find the "Browser" section, click "Open browser".
2. The browser opens in the preview card on the right (a tab next to file
   previews).
3. Type a URL in the address bar and press Enter — or just ask the agent in
   chat, e.g. "open example.com and take a screenshot".

Prerequisites (web/terminal mode only):

- Playwright is a required dependency of illusion-agent (ships with the
  package) — nothing extra to install.
- Chromium kernel: with system Chrome/Edge present it is used automatically;
  otherwise run `illusion browser setup` once (~130MB download).

The desktop app uses its bundled engine — nothing to install.

## Layout

The browser tab in the preview card has two parts:

**Toolbar**

- Back / forward / refresh buttons and the address bar (Enter to navigate;
  bare domains get `https://`, local addresses get `http://`);
- Responsive viewport toggle: adds a viewport toolbar with width × height
  inputs, fit-to-window / 50–200% zoom, and drag handles on all edges and
  corners (zoom freezes at the current percentage while dragging);
- Element picker: click a page element to attach it to the chat input;
- "⋯" menu: open in the system browser / open page DevTools;
- Agent-operation indicator (lights up while the agent drives the page).

**Canvas**

- Desktop mode: live view — click, scroll and type natively;
- Web mode: screenshot stream — clicks/wheel/keys are forwarded to the real
  page: "click to focus → type directly → Enter to submit", with a focus
  hint below the canvas.

## Two run modes

| Mode | Where | Canvas | Interaction |
|------|-------|--------|-------------|
| `desktop` (in-app browser) | Desktop app | Electron `<webview>` live view | Native, direct |
| `managed` (managed browser) | Web / terminal | Screenshot stream from a Playwright-managed Chromium | Click/wheel/key forwarding |

Managed kernel resolution order: Playwright Chromium → system Chrome →
system Edge (pinnable in settings, see Configuration). Each backend tab is a
tab in the preview-card strip: click to switch, hover-X to close, "+" to
create (with an optional URL), trash to close others, minus to collapse the
card (tabs and the live view stay warm). The desktop in-app browser follows
the app's light/dark theme.

## How the agent uses the browser

With the browser-use plugin enabled, the agent gets two skills and a set of
`browser_*` tools:

- **`browser-use:control-browser`**: the main browse/operate/verify workflow
  (navigate → ARIA snapshot → click/type by ref → screenshot to verify);
- **`browser-use:web-gui-tester`**: GUI black-box testing methodology
  (plan → execute → cross-validate → report).

Tools: `browser_navigate`, `browser_snapshot`, `browser_screenshot`,
`browser_click`, `browser_type`, `browser_press_key`, `browser_scroll`,
`browser_evaluate`, `browser_tabs`, `browser_resize`, `browser_wait`,
`browser_close`.

Notes:

- The agent's primary way to read pages is `browser_snapshot` (ARIA tree with
  `[ref=eN]` refs); screenshots are reserved for when vision matters;
- Page content is treated as untrusted data, used only for locating elements;
- Only `http:` / `https:` / `about:blank` are navigable;
- When the agent starts operating, the browser view pops open automatically
  and the toolbar shows an operation indicator;
- Screenshots appear both in the chat tool card and in the browser canvas.

## Configuration

Browser settings live in the `browser` section of
`~/.illusion/settings.json`, editable in **Settings → Extensions** (branched
by runtime: web/terminal shows the managed options below, desktop shows
browser data management):

```jsonc
{
  "browser": {
    // Managed kernel: auto (default, resolution chain) / chromium / chrome / msedge
    "kernel": "auto",
    // Headless for managed mode (the desktop in-app view is always visible)
    "headless": true,
    // Default viewport (320-3840 × 320-2160)
    "viewport_width": 1280,
    "viewport_height": 720,
    // Proxy: auto (default; detects Windows system proxy and HTTP(S)_PROXY) /
    // off / explicit URL (e.g. "http://127.0.0.1:7890")
    "proxy": "auto"
  }
}
```

## Plugin and switches

The browser capability is carried by the built-in **browser-use** plugin
(enabled by default):

- **Settings → Extensions**: toggle switch, hot-reloads the current session;
- Right-panel plugin list: the same switch inline;
- Slash commands: `/plugin enable browser-use`,
  `/plugin disable browser-use`.

Disabling immediately unregisters the 12 `browser_*` tools and both skills;
re-enabling restores them instantly. The plugin is seeded to
`~/.illusion/plugins/browser-use/` and updated with package versions.

## Browser data management

The desktop app offers two data-management actions in
**Settings → Extensions** (not shown on web/terminal — managed browser data
dies with the process):

- **Clear all browser data**: clears cookies, cache and site data; signs out
  of every site;
- **Clear browser cache**: clears cached files only; keeps logins and site
  data.

## FAQ

**The browser opens blank?**
Type a URL in the address bar and press Enter — the blank state guides you
there. You can also just ask the agent to open pages.

**Clicks on the canvas don't respond?**
In web mode clicks are forwarded to the real page and a fresh screenshot
comes back (~hundreds of ms). Make sure the browser is running (the
right-panel section shows the current page).

**Navigation rejected?**
Only `http:` / `https:` / `about:blank` are allowed; `file:`, `data:` and
`javascript:` are always rejected (security constraint).

**Desktop vs Web?**
Desktop uses the Electron-bundled engine with a live, natively interactive
view (smoothest). Web uses a managed Chromium screenshot stream with
input forwarding. Agent-driven behavior is identical in both.

**External sites (GitHub etc.) won't load?**
Headless managed Chromium does not inherit the Windows system proxy.
`browser.proxy` defaults to `auto`: it auto-detects the system proxy
(Clash/v2rayN etc.) and `HTTP(S)_PROXY` and passes it to Chromium
explicitly; you can also set an explicit address in
Settings → Extensions, or `off` to disable. If the proxy app
isn't running or the node is down, external sites still fail — the
navigation error is shown on the browser canvas. The desktop webview uses
the system proxy natively.

**Ports / security?**
The desktop browser control server listens on `127.0.0.1` only with a random
per-launch token; the managed browser runs inside the backend process and
opens no extra port.
