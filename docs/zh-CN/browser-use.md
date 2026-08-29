# Browser Use

IllusionAgent 内置浏览器自动化运行时：内置受管 Chromium，默认使用「空白」配置档案，
可通过参数切换为用户真实浏览器数据。启用后以插件式注入 `node_repl` MCP 服务器、
Browser Use skills 与文档清单，模型即可在会话中打开、浏览、检查、点击、填写、
截图和验证网页（含本地开发服务器）。

## 启用

在 `~/.illusion/settings.json` 中加入（缺失该键时首次加载会自动写入默认值）：

```json
{
  "browser": {
    "enabled": true,
    "profile": "blank",
    "headless": true
  }
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 总开关。关闭时整个子系统零副作用（不注入 MCP/skills、不启动 broker、不拉起浏览器） |
| `profile` | `"blank"` | `"blank"` = 一次性临时用户数据目录（干净环境）；`"user"` = 用户真实浏览器数据（含登录态/Cookie） |
| `user_data_dir` | `""` | `profile="user"` 时的自定义数据目录；为空时自动探测（如 `%LOCALAPPDATA%\Google\Chrome\User Data`） |
| `headless` | `true` | 无头运行（终端场景静默）；`false` 弹出浏览器窗口 |
| `channel` | `"auto"` | 渠道探测：auto/chrome/edge/brave/chromium |
| `executable_path` | `""` | 显式指定浏览器可执行文件（优先级最高） |
| `cdp_url` | `""` | 连接已有浏览器的 CDP 端点（如 `http://127.0.0.1:9222`），设置后跳过受管启动 |
| `viewport` | `1280×720` | 默认视口（CSS 像素） |
| `keep_alive_minutes` | `30` | 浏览器空闲自动回收时间；0 表示不回收 |
| `stream_interval_ms` | `800` | Web 端实时画面的最小采样间隔 |
| `screenshot_quality` | `60` | 实时画面 JPEG 质量（1-100） |

依赖（可选）：

```bash
pip install "illusion-agent[browser]"
python -m playwright install chromium   # 无系统 Chrome 时使用内置 Chromium
```

未安装 Playwright 时功能自动降级关闭（告警日志），不影响其他功能。

## CLI 会话参数

```bash
# 本会话使用用户浏览器数据（需先完全退出正在运行的浏览器）
illusion --browser-profile user

# 本会话启用并带窗口运行
illusion --browser-use headed

# 本会话禁用（覆盖 settings.json 的 enabled=true）
illusion --browser-use off
```

- `--browser-use off|auto|headless|headed`：会话级启用与模式覆盖
- `--browser-profile blank|user`：会话级配置档案覆盖
- 两个参数仅作用于当前进程（不持久化），优先级高于 settings.json

## 启用后注入了什么

| 注入项 | 说明 |
|--------|------|
| `node_repl` MCP 服务器 | stdio 子进程，提供 `mcp__node_repl__js`、`mcp__node_repl__js_reset`、`mcp__node_repl__js_add_node_module_dir` 三个工具；模型在全新 Node 内核中执行 JavaScript 并通过 `agent.browsers` 驱动浏览器 |
| `browser-use:control-browser` skill | 浏览器操作工作流引导（快照 → locator → 动作 → 观察） |
| `browser-use:web-gui-tester` skill | 纯 GUI 黑盒测试工作流（叠加在 control-browser 之上） |
| 文档清单 | `agent.documentation` / `browser.documentation()` 按能力过滤的完整 API 文档 |
| broker | Python 宿主侧的回环 TCP 端点（令牌鉴权），转发浏览器命令到 Playwright |

工具与 skill 在**下次会话构建时**注册（运行中会话的工具注册表不支持热重建）；
通过 Web 端设置面板开关时，当前会话的浏览器服务与实时画面立即可用。

## 模型侧用法（摘要）

每次 `js` 调用都是全新内核，需要先执行引导代码：

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

之后即可：

```js
const browser = await agent.browsers.getDefault();   // 或 getForUrl(url) / get("cdp")
const tab = await browser.tabs.new();
await tab.goto("https://example.com");
await tab.playwright.waitForLoadState({ state: "domcontentloaded" });
nodeRepl.write(await tab.playwright.domSnapshot());   // ARIA 树快照（默认观察手段）
```

完整 API 见会话内的 `browser.documentation()`；截图需经
`nodeRepl.emitImage(await tab.screenshot())` 以图片内容块回传模型。

## 终端 / Web 行为

- **终端**：静默执行。浏览器在后台（默认无头）运行，终端只显示常规工具调用行，
  不展示任何浏览器画面。
- **Web**：右栏「用量」页签提供「浏览器」实时画面卡片（活动标签页 JPEG 帧 +
  URL + 标签页列表），默认关闭，点开后按内容变化推送。会话未启用 Browser Use
  或浏览器尚未启动时显示占位提示。

## 架构

```
模型 ── mcp__node_repl__js ──▶ node_repl MCP 服务器（Node 子进程）
                                   │  vm 内核（每次 js 调用全新上下文）
                                   │  agent.browsers 桥接（browser-client.mjs）
                                   ▼
                              回环 TCP broker（令牌鉴权，JSON-lines）
                                   ▼
                        BrowserUseService（Python 宿主）
                                   │  BrowserCommandExecutor（协议命令 → Playwright）
                                   ▼
                        受管 Chromium（空白档案 / 用户档案 / CDP attach）
```

协议与官方 ZCode browser-use 插件（0.4.1，MIT）保持兼容：命令信封、结果结构、
`agent.browsers` 桥接形状与 skill 引导代码均一致（桥接 symbol 同时注册
illusion 与 zcode 两个名称），vendored 的 `browser-client.mjs` 与文档清单
无需修改即可对接 IllusionAgent 宿主。

## 故障排查

| 现象 | 处理 |
|------|------|
| `playwright 未安装` 日志，功能降级 | `pip install "illusion-agent[browser]"` |
| 未找到可用的 Chromium 系浏览器 | 安装 Chrome 或 `python -m playwright install chromium`，或配置 `browser.executable_path` |
| 用户档案启动失败 | 完全退出正在运行的浏览器（含后台进程），或改用 `profile="blank"` |
| `agent.browsers` 为 undefined | 确认会话已启用（settings 或 CLI 参数），且每次 js 调用先执行引导代码 |
| 快照句柄失效（ref_not_found） | 页面已刷新，重新执行 `snapshot` / `get_visible_dom` |
