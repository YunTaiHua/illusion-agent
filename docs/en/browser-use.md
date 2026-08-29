# Browser Use

IllusionAgent's built-in browser automation runtime: a managed Chromium shipped
with the agent, defaulting to a clean ("blank") browser profile with an optional
parameter to use the user's real browser data. When enabled, it injects the
`node_repl` MCP server, Browser Use skills, and documentation manifests
plugin-style, so the model can open, navigate, inspect, click, fill, screenshot,
and verify web pages (including local dev servers) inside a session.

## Enable

Add the following to `~/.illusion/settings.json` (missing keys are materialized
with defaults on first load):

```json
{
  "browser": {
    "enabled": true,
    "profile": "blank",
    "headless": true
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `false` | Master switch. When off, the subsystem has zero side effects (no MCP/skill injection, no broker, no browser) |
| `profile` | `"blank"` | `"blank"` = disposable temp user-data dir (clean environment); `"user"` = the user's real browser data (logins/cookies) |
| `user_data_dir` | `""` | Custom data dir for `profile="user"`; auto-detected when empty (e.g. `%LOCALAPPDATA%\Google\Chrome\User Data`) |
| `headless` | `true` | Headless (silent, recommended for terminal); `false` opens a visible window |
| `channel` | `"auto"` | Browser channel: auto/chrome/edge/brave/chromium |
| `executable_path` | `""` | Explicit browser executable (highest priority) |
| `cdp_url` | `""` | Attach to an existing browser via CDP (e.g. `http://127.0.0.1:9222`); skips managed launch |
| `viewport` | `1280×720` | Default viewport (CSS pixels) |
| `keep_alive_minutes` | `30` | Idle auto-recycle for the browser; 0 disables recycling |
| `stream_interval_ms` | `800` | Minimum sampling interval for the web live view |
| `screenshot_quality` | `60` | JPEG quality of live-view frames (1-100) |

Optional dependency:

```bash
pip install "illusion-agent[browser]"
python -m playwright install chromium   # used when no system Chrome is found
```

Without Playwright the feature degrades to off (with a warning log); everything
else keeps working.

## CLI Session Overrides

```bash
# Use the user's real browser data for this session (close the browser first)
illusion --browser-profile user

# Enable for this session with a visible window
illusion --browser-use headed

# Disable for this session (overrides settings.json enabled=true)
illusion --browser-use off
```

- `--browser-use off|auto|headless|headed`: session-level enable + mode override
- `--browser-profile blank|user`: session-level profile override
- Both flags affect the current process only (not persisted) and take priority
  over settings.json

## What Gets Injected When Enabled

| Injection | Description |
|-----------|-------------|
| `node_repl` MCP server | stdio subprocess exposing `mcp__node_repl__js`, `mcp__node_repl__js_reset`, `mcp__node_repl__js_add_node_module_dir`; the model runs JavaScript in fresh Node kernels and drives the browser through `agent.browsers` |
| `browser-use:control-browser` skill | Browser operation workflow guidance (snapshot → locator → act → observe) |
| `browser-use:web-gui-tester` skill | Pure GUI black-box testing workflow layered on control-browser |
| Documentation manifests | Capability-filtered API docs served via `agent.documentation` / `browser.documentation()` |
| Broker | Loopback TCP endpoint on the Python host (token-authenticated) forwarding browser commands to Playwright |

Tools and skills register at **next session build** (a running session's tool
registry cannot be hot-rebuilt); when toggled from the web settings panel, the
current session's browser service and live view become available immediately.

## Model-Facing Usage (Summary)

Every `js` call runs in a fresh kernel — bootstrap first:

```js
const browserPluginRoot =
  process.env.ILLUSION_PLUGIN_ROOT ?? process.env.ZCODE_PLUGIN_ROOT ?? process.env.CLAUDE_PLUGIN_ROOT;
const { join } = await import("node:path");
const { pathToFileURL } = await import("node:url");
const { setupBrowserRuntime } = await import(
  pathToFileURL(join(browserPluginRoot, "browser-client.mjs")).href
);
await setupBrowserRuntime({ globals: globalThis });
```

Then:

```js
const browser = await agent.browsers.getDefault();   // or getForUrl(url) / get("cdp")
const tab = await browser.tabs.new();
await tab.goto("https://example.com");
await tab.playwright.waitForLoadState({ state: "domcontentloaded" });
nodeRepl.write(await tab.playwright.domSnapshot());   // ARIA tree snapshot (default observation)
```

See `browser.documentation()` in-session for the full API; screenshots must be
emitted via `nodeRepl.emitImage(await tab.screenshot())` to reach the model as
image content blocks.

## Terminal / Web Behavior

- **Terminal**: fully silent. The browser runs in the background (headless by
  default); the terminal only shows ordinary tool-call lines, never a browser view.
- **Web**: the right-panel **Usage** tab hosts a live "Browser" card (active-tab
  JPEG frames + URL + tab list), off by default, pushed on content change once
  opened. A placeholder explains when Browser Use is disabled or the browser
  hasn't started yet.

## Architecture

```
model ── mcp__node_repl__js ──▶ node_repl MCP server (Node subprocess)
                                   │  vm kernel (fresh context per js call)
                                   │  agent.browsers bridge (browser-client.mjs)
                                   ▼
                              loopback TCP broker (token-auth, JSON-lines)
                                   ▼
                        BrowserUseService (Python host)
                                   │  BrowserCommandExecutor (protocol → Playwright)
                                   ▼
                        Managed Chromium (blank profile / user profile / CDP attach)
```

The protocol stays compatible with the official ZCode browser-use plugin
(0.4.1, MIT): command envelopes, result structures, the `agent.browsers` bridge
shape, and skill bootstrap code are identical (the bridge is registered under
both the illusion and zcode symbol names), so the vendored `browser-client.mjs`
and documentation manifests work against the IllusionAgent host unchanged.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `playwright not installed` log, feature degraded | `pip install "illusion-agent[browser]"` |
| No Chromium-family browser found | Install Chrome, run `python -m playwright install chromium`, or set `browser.executable_path` |
| User profile launch fails | Fully exit the running browser (including background processes), or switch to `profile="blank"` |
| `agent.browsers` is undefined | Confirm the session is enabled (settings or CLI flags) and bootstrap in every js call |
| Stale snapshot handles (ref_not_found) | The page refreshed; re-run `snapshot` / `get_visible_dom` |
