// IllusionAgent Browser Use runtime client.
// Vendored from the official ZCode browser-use plugin (v0.4.1, MIT License),
// https://github.com/zai-org — protocol-compatible; the runtime bridge is
// registered under both the illusion and zcode bridge symbols.
import { createRequire as __zcodeCreateRequire } from "node:module";
const require = __zcodeCreateRequire(import.meta.url);

// ../contracts/dist/interfaces/browser-control.port.js
var BROWSER_VIEWPORT_LIMITS = {
  minWidth: 320,
  maxWidth: 3840,
  minHeight: 320,
  maxHeight: 2160
};

// ../core/dist/browser-client/documentation.js
import { existsSync as existsSync2, readFileSync as readFileSync2 } from "node:fs";
import { join as join2 } from "node:path";

// ../core/dist/browser-client/manifest.js
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
var FALLBACK_MANIFEST = {
  version: 10,
  types: {
    BrowserViewportSize: "{ width: number; height: number }",
    TabInfo: "{ id: string; active?: boolean; title?: string; url?: string; viewport: BrowserViewportSize }"
  },
  objects: {
    Agent: {
      members: [
        { name: "browsers", kind: "property", signature: "browsers: Browsers" },
        { name: "documentation", kind: "property", signature: "documentation: Documentation" }
      ]
    },
    Documentation: {
      members: [{ name: "get", kind: "method", signature: "get(name: string): Promise<string>" }]
    },
    Browsers: {
      members: [
        ...["list", "get", "getDefault", "getForUrl"].map((name) => ({
          name,
          kind: "method",
          signature: `${name}(...)`
        })),
        // open() 是默认导航入口（同站复用 + 激活 + 原地跳转）；不声明会被 hideUnknown
        // 代理隐藏，REPL 里 agent.browsers.open 变 undefined（与插件 docs/api.json 同步维护）。
        {
          name: "open",
          kind: "method",
          signature: "open(url?: string, options?: { reuseTab?: boolean }): Promise<Tab>"
        }
      ]
    },
    Browser: {
      members: [
        { name: "browserId", kind: "property", signature: "browserId: string" },
        {
          name: "capabilities",
          kind: "property",
          signature: "capabilities: BrowserCapabilityCollection"
        },
        {
          name: "documentation",
          kind: "method",
          signature: "documentation(): Promise<string>"
        },
        { name: "tabs", kind: "property", signature: "tabs: Tabs" },
        { name: "user", kind: "property", signature: "user: BrowserUser" }
      ]
    },
    BrowserUser: {
      members: [
        {
          name: "claimTab",
          kind: "method",
          signature: "claimTab(tab): Promise<Tab>",
          unsupportedByDefaultIn: ["iab", "cdp"]
        },
        {
          name: "history",
          kind: "method",
          signature: "history(options): Promise<BrowserHistoryEntry[]>",
          unsupportedByDefaultIn: ["iab"]
        },
        {
          name: "openTabs",
          kind: "method",
          signature: "openTabs(): Promise<BrowserUserTabInfo[]>"
        }
      ]
    },
    Tabs: {
      members: [
        ...["get", "new", "selected"].map((name) => ({
          name,
          kind: "method",
          signature: `${name}(...)`
        })),
        {
          name: "list",
          kind: "method",
          signature: "list(): Promise<TabInfo[]>"
        },
        {
          name: "finalize",
          kind: "method",
          signature: "finalize(options): Promise<void>",
          unsupportedByDefaultIn: ["iab", "cdp"]
        }
      ]
    },
    Tab: {
      members: [
        { name: "id", kind: "property", signature: "id: string" },
        {
          name: "capabilities",
          kind: "property",
          signature: "capabilities: TabCapabilityCollection"
        },
        ...[
          "back",
          "close",
          "forward",
          "getJsDialog",
          "goto",
          "reload",
          "screenshot",
          "title",
          "url"
        ].map((name) => ({ name, kind: "method", signature: `${name}(...)` })),
        {
          name: "finalize",
          kind: "method",
          signature: "finalize(options?): Promise<void>",
          unsupportedByDefaultIn: ["iab", "cdp"]
        },
        ...["markDeliverable", "markHandoff"].map((name) => ({
          name,
          kind: "method",
          signature: `${name}(): Promise<void>`,
          unsupportedByDefaultIn: ["iab", "cdp"]
        })),
        { name: "cua", kind: "property", signature: "cua: CUAAPI" },
        { name: "dom_cua", kind: "property", signature: "dom_cua: DomCUAAPI" },
        {
          name: "playwright",
          kind: "property",
          signature: "playwright: PlaywrightAPI"
        },
        {
          name: "recording",
          kind: "property",
          signature: "recording: BrowserRecordingAPI"
        },
        {
          name: "setViewportSize",
          kind: "method",
          signature: "setViewportSize(viewportSize: { width: number; height: number }): Promise<void>",
          command: "browserViewportSet"
        },
        {
          name: "viewportSize",
          kind: "method",
          signature: "viewportSize(): { width: number; height: number } | null"
        }
      ]
    },
    BrowserRecordingAPI: {
      members: [
        {
          name: "start",
          kind: "method",
          signature: "start(options?): Promise<BrowserRecordingJob>",
          command: "recordingStart",
          unsupportedByDefaultIn: ["extension", "cdp"]
        },
        {
          name: "status",
          kind: "method",
          signature: "status(recordingId, options?): Promise<BrowserRecordingJob>",
          command: "recordingStatus",
          unsupportedByDefaultIn: ["extension", "cdp"]
        },
        {
          name: "cancel",
          kind: "method",
          signature: "cancel(recordingId): Promise<BrowserRecordingJob>",
          command: "recordingCancel",
          unsupportedByDefaultIn: ["extension", "cdp"]
        }
      ]
    },
    BrowserCapabilityCollection: {
      members: ["get", "list"].map((name) => ({
        name,
        kind: "method",
        signature: `${name}(...)`
      }))
    },
    TabCapabilityCollection: {
      members: ["get", "list"].map((name) => ({
        name,
        kind: "method",
        signature: `${name}(...)`
      }))
    },
    PlaywrightAPI: {
      members: [
        {
          name: "domSnapshot",
          kind: "method",
          signature: "domSnapshot(): Promise<string>",
          command: "playwright"
        },
        {
          name: "elementInfo",
          kind: "method",
          signature: "elementInfo(options: ElementInfoOptions): Promise<ElementInfo[]>",
          command: "playwright",
          documented: false
        },
        {
          name: "elementScreenshot",
          kind: "method",
          signature: "elementScreenshot(options: ElementScreenshotOptions): Promise<Uint8Array>",
          command: "playwright",
          documented: false
        },
        {
          name: "evaluate",
          kind: "method",
          signature: "evaluate(pageFunction, arg?, options?): Promise<TResult>",
          command: "playwright"
        },
        {
          name: "expectNavigation",
          kind: "method",
          signature: "expectNavigation(action, options?): Promise<T>",
          command: "playwright"
        },
        {
          name: "frameLocator",
          kind: "method",
          signature: "frameLocator(selector: string): PlaywrightFrameLocator"
        },
        {
          name: "getByLabel",
          kind: "method",
          signature: "getByLabel(text, options?): PlaywrightLocator"
        },
        {
          name: "getByPlaceholder",
          kind: "method",
          signature: "getByPlaceholder(text, options?): PlaywrightLocator"
        },
        {
          name: "getByRole",
          kind: "method",
          signature: "getByRole(role, options?): PlaywrightLocator"
        },
        {
          name: "getByTestId",
          kind: "method",
          signature: "getByTestId(testId: string): PlaywrightLocator"
        },
        {
          name: "getByText",
          kind: "method",
          signature: "getByText(text, options?): PlaywrightLocator"
        },
        {
          name: "locator",
          kind: "method",
          signature: "locator(selector: string): PlaywrightLocator"
        },
        {
          name: "waitForEvent",
          kind: "method",
          signature: "waitForEvent(event, options?): Promise<PlaywrightDownload>",
          command: "playwright",
          declarations: [
            {
              signature: 'waitForEvent(event: "download", options?): Promise<PlaywrightDownload>'
            },
            {
              signature: 'waitForEvent(event: "filechooser", options?): Promise<PlaywrightFileChooser>',
              unsupportedByDefaultIn: ["iab"]
            }
          ]
        },
        {
          name: "waitForLoadState",
          kind: "method",
          signature: "waitForLoadState(options?): Promise<void>",
          command: "playwright"
        },
        {
          name: "waitForTimeout",
          kind: "method",
          signature: "waitForTimeout(timeoutMs: number): Promise<void>",
          command: "playwrightWaitForTimeout"
        },
        {
          name: "waitForURL",
          kind: "method",
          signature: "waitForURL(url: string, options?): Promise<void>",
          command: "playwright"
        }
      ]
    },
    PlaywrightFrameLocator: {
      members: [
        "frameLocator",
        "getByLabel",
        "getByPlaceholder",
        "getByRole",
        "getByTestId",
        "getByText",
        "locator"
      ].map((name) => ({
        name,
        kind: "method",
        signature: `${name}(...): PlaywrightLocator`
      }))
    },
    PlaywrightLocator: {
      members: [
        "all",
        "allTextContents",
        "and",
        "check",
        "click",
        "count",
        "dblclick",
        "downloadMedia",
        "evaluate",
        "fill",
        "filter",
        "first",
        "getAttribute",
        "getByLabel",
        "getByPlaceholder",
        "getByRole",
        "getByTestId",
        "getByText",
        "innerText",
        "isEnabled",
        "isVisible",
        "last",
        "locator",
        "nth",
        "or",
        "press",
        "selectOption",
        "setChecked",
        "textContent",
        "type",
        "uncheck",
        "waitFor"
      ].map((name) => ({
        name,
        kind: "method",
        signature: `${name}(...)`,
        command: [
          "all",
          "and",
          "filter",
          "first",
          "getByLabel",
          "getByPlaceholder",
          "getByRole",
          "getByTestId",
          "getByText",
          "last",
          "locator",
          "nth",
          "or"
        ].includes(name) ? void 0 : "playwright"
      }))
    },
    PlaywrightDownload: {
      members: [
        {
          name: "path",
          kind: "method",
          signature: "path(options?): Promise<string | null>",
          command: "playwright",
          documented: false
        }
      ]
    },
    PlaywrightFileChooser: {
      members: [
        { name: "isMultiple", kind: "method", signature: "isMultiple(): boolean" },
        {
          name: "setFiles",
          kind: "method",
          signature: "setFiles(files, options?): Promise<void>",
          command: "playwright",
          unsupportedByDefaultIn: ["iab"]
        }
      ]
    },
    CUAAPI: {
      members: [
        ...["click", "double_click", "drag", "keypress", "move", "scroll", "type"].map((name) => ({
          name,
          kind: "method",
          signature: `${name}(...)`
        })),
        {
          name: "downloadMedia",
          kind: "method",
          signature: "downloadMedia(options): Promise<void>",
          unsupportedByDefaultIn: ["iab"],
          documented: false
        }
      ]
    },
    DomCUAAPI: {
      members: [
        ...["click", "double_click", "get_visible_dom", "keypress", "scroll", "type"].map((name) => ({ name, kind: "method", signature: `${name}(...)` })),
        {
          name: "downloadMedia",
          kind: "method",
          signature: "downloadMedia(options): Promise<void>",
          unsupportedByDefaultIn: ["iab"],
          documented: false
        }
      ]
    }
  }
};
function isManifest(value) {
  if (!value || typeof value !== "object")
    return false;
  const record = value;
  return typeof record.version === "number" && Boolean(record.objects && typeof record.objects === "object");
}
function loadBrowserApiManifest(documentationRoot) {
  if (!documentationRoot)
    return FALLBACK_MANIFEST;
  const path = join(documentationRoot, "api.json");
  if (!existsSync(path))
    return FALLBACK_MANIFEST;
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8"));
    return isManifest(parsed) ? parsed : FALLBACK_MANIFEST;
  } catch {
    return FALLBACK_MANIFEST;
  }
}
function capabilityIds(descriptor, scope) {
  return new Set((descriptor.capabilities[scope] ?? []).map((capability) => capability.id));
}
var BrowserApiPolicy = class {
  manifest;
  descriptor;
  members = /* @__PURE__ */ new Map();
  browserCapabilities;
  tabCapabilities;
  constructor(manifest, descriptor) {
    this.manifest = manifest;
    this.descriptor = descriptor;
    this.browserCapabilities = capabilityIds(descriptor, "browser");
    this.tabCapabilities = capabilityIds(descriptor, "tab");
    for (const [objectName, object] of Object.entries(manifest.objects)) {
      for (const member of object.members ?? []) {
        this.members.set(`${objectName}.${member.name}`, member);
      }
    }
  }
  isKnown(objectName, memberName) {
    return this.members.has(`${objectName}.${memberName}`);
  }
  supports(objectName, memberName) {
    const key = `${objectName}.${memberName}`;
    const member = this.members.get(key);
    if (!member)
      return true;
    let supported = this.supportsRequirement(member);
    if (supported && member.declarations?.length) {
      supported = member.declarations.some((declaration) => this.supportsRequirement(declaration));
    }
    return this.descriptor.apiSupportOverrides?.[key] ?? supported;
  }
  supportedMembers(objectName) {
    return (this.manifest.objects[objectName]?.members ?? []).filter((member) => this.supports(objectName, member.name)).map((member) => ({
      ...member,
      ...member.declarations ? {
        declarations: member.declarations.filter((declaration) => this.supportsRequirement(declaration))
      } : {}
    }));
  }
  updateDescriptor(descriptor) {
    this.descriptor = descriptor;
    this.browserCapabilities.clear();
    this.tabCapabilities.clear();
    for (const id of capabilityIds(descriptor, "browser"))
      this.browserCapabilities.add(id);
    for (const id of capabilityIds(descriptor, "tab"))
      this.tabCapabilities.add(id);
  }
  supportsRequirement(requirement) {
    if ((requirement.unsupportedByDefaultIn ?? []).includes(this.descriptor.type))
      return false;
    return !requirement.requiresCapabilities?.some((capability) => {
      const separator = capability.indexOf(":");
      if (separator > 0) {
        const scope = capability.slice(0, separator);
        const id = capability.slice(separator + 1);
        if (scope === "browser")
          return !this.browserCapabilities.has(id);
        if (scope === "tab")
          return !this.tabCapabilities.has(id);
      }
      return !this.browserCapabilities.has(capability) && !this.tabCapabilities.has(capability);
    });
  }
};
function createBrowserApiProxy(target, objectName, policy, options = {}) {
  const isHidden = (property) => {
    if (typeof property !== "string")
      return false;
    const known = policy.isKnown(objectName, property);
    return known ? !policy.supports(objectName, property) : options.hideUnknown === true;
  };
  const handler = {
    get(current, property, receiver) {
      if (isHidden(property))
        return void 0;
      const value = Reflect.get(current, property, receiver);
      return options.hideUnknown && typeof value === "function" ? value.bind(current) : value;
    },
    has(current, property) {
      if (isHidden(property))
        return false;
      return Reflect.has(current, property);
    },
    getOwnPropertyDescriptor(current, property) {
      if (isHidden(property))
        return void 0;
      return Reflect.getOwnPropertyDescriptor(current, property);
    },
    ownKeys(current) {
      return Reflect.ownKeys(current).filter((property) => !isHidden(property));
    }
  };
  return new Proxy(target, handler);
}

// ../core/dist/browser-client/documentation.js
function documentApplies(document, descriptor, api) {
  const when = document.when;
  if (!when)
    return true;
  if (!descriptor)
    return false;
  if (when.browserTypes && !when.browserTypes.includes(descriptor.type))
    return false;
  const browserCapabilities = new Set((descriptor.capabilities.browser ?? []).map((capability) => capability.id));
  const tabCapabilities = new Set((descriptor.capabilities.tab ?? []).map((capability) => capability.id));
  if (when.requiredBrowserCapabilities?.some((id) => !browserCapabilities.has(id)))
    return false;
  if (when.requiredTabCapabilities?.some((id) => !tabCapabilities.has(id)))
    return false;
  if (when.requiredApiMembers?.length) {
    const policy = new BrowserApiPolicy(api, descriptor);
    if (when.requiredApiMembers.some((member) => {
      const separator = member.indexOf(".");
      return separator <= 0 || !policy.supports(member.slice(0, separator), member.slice(separator + 1));
    })) {
      return false;
    }
  }
  return true;
}
var FALLBACK_DOCUMENTATION = [
  "# Built-in Browser Automation API",
  "",
  // 修复原因：Browser Use MCP 已是每次调用 fresh kernel；fallback 不能继续诱导模型复用 global binding。
  "The official browser-use plugin docs are unavailable, and every Browser Use call starts in a fresh kernel. Start with `await agent.browsers.list()`, then select a reported runtime browser with `const browser = await agent.browsers.getDefault()` or the matching `get()` / `getForUrl(url)` selection. Repeat the same verified selection in each call without switching backend.",
  "Backend types are `iab | extension | cdp`; Playwright is a tab API surface, not a backend. Never assume an unlisted backend is available.",
  "High-level methods return payloads directly and throw `BrowserCommandError` on failure.",
  "Before each logical tab-operation batch, return the complete `browser.tabs.list()` result in a dedicated observation cell. Each `TabInfo` includes `viewport: BrowserViewportSize`. After the model inspects the list, match by stable id or verified URL/title and call `tabs.get(id)` in the next cell; if no controlled tab matches, inspect and claim `browser.user.openTabs()` before creating a new tab.",
  // 修复原因：模型每次对同站 URL 都走 tabs.new()，任务结束后 IAB 堆满标签页（用户工单反馈）。
  // open() 现在内置同站复用（激活 + 原地跳转），文档必须把 open() 教成默认导航入口。
  "`agent.browsers.open(url)` is the default navigation entry: it reuses an existing same-site controlled tab (same hostname), activates it so the user sees it, and navigates in place instead of stacking new tabs. Only pass `{ reuseTab: false }` (or use `browser.tabs.new()`) when the task genuinely needs a parallel independent tab.",
  // 修复原因：Codex 的模型轨迹会在新 URL 的 goto 后显式确认 DOMContentLoaded；fallback 也必须保留同一顺序。
  'For a genuinely new URL with no intended existing page, use `const tab = await browser.tabs.new()`, run `await tab.goto(url)`, then run `await tab.playwright.waitForLoadState({ state: "domcontentloaded" })` before returning the first title, URL, or DOM observation.',
  'After every successful `tab.goto(url)`, explicitly call `await tab.playwright.waitForLoadState({ state: "domcontentloaded" })` before the first title, URL, or DOM observation. Keep this confirmation in the model-visible trajectory even when goto() has already settled the backend navigation. Do not replace it with networkidle or a fixed sleep; routine URL/load-state waits remain capped at 3000ms.',
  "If the latest domSnapshot already contains the target, use its facts directly. Do not use evaluate() to rediscover related elements, enumerate inputs, dump HTML, walk the DOM, or probe guessed selectors.",
  "Never guess locator labels, accessible names, placeholders, selectors, or URL patterns. If count() is 0, do not action-wait: take a fresh domSnapshot() and rebuild. After timeout/strict/parse failure, never retry the same locator.",
  // 修复原因：快照里的 heading/text 可能由祖先卡片处理点击，不能因缺少 link role 就放弃或猜测新角色。
  "A snapshot-proven heading or visible text does not need a `link` or `button` role to be clicked. Do not replace a snapshot-proven `heading` with a guessed `link` role. When user intent authorizes navigation and the actual target is unique, click it directly.",
  // 修复原因：两套 tab 查询分开返回时，模型会在第一套结果后重新决策并跳过 popup 来源。
  "Use at most one state-changing action per observation cycle. An unchanged source-tab URL does not prove the click failed. Judge an action by whether its expected effect appeared, not by whether `browser.tabs.list()` is non-empty. An existing source tab or unrelated controlled tab is not an action effect. When an action may open a popup/new tab and the source tab does not show the expected effect, read `browser.tabs.list()` and `browser.user.openTabs()` unconditionally in the same observation cell. Prefer `const [controlledTabs, userTabs] = await Promise.all([browser.tabs.list(), browser.user.openTabs()]);`. Return `{ controlledTabs, userTabs }` as that cell's final result so the model makes one decision from both lists. Do not return the controlled list first or decide whether to query user tabs from its contents.",
  "playwright.evaluate() and locator.evaluate() execute JavaScript in the page context and may change page state. Use them for page-side logic that cannot be expressed through the high-level locator API; use normal action methods when they communicate the intended interaction more clearly.",
  "Routine locator, URL/load-state wait, and evaluate operations default to and are capped at 3000ms; fixed tab.playwright.waitForTimeout(ms) is separate."
].join("\n");
function readJson(path) {
  if (!existsSync2(path))
    return void 0;
  try {
    return JSON.parse(readFileSync2(path, "utf8"));
  } catch {
    return void 0;
  }
}
function readMarkdown(root, relativePath) {
  const path = join2(root, relativePath);
  if (!existsSync2(path))
    return void 0;
  try {
    return readFileSync2(path, "utf8").trim();
  } catch {
    return void 0;
  }
}
function formatApi(api) {
  if (!api)
    return "";
  const lines = [];
  lines.push("## API manifest");
  if (api.entrypoints && api.entrypoints.length > 0) {
    lines.push("- entrypoints:");
    for (const entrypoint of api.entrypoints)
      lines.push(`  - \`${entrypoint}\``);
  }
  if (api.semantics) {
    lines.push("- semantics:");
    for (const [key, value] of Object.entries(api.semantics))
      lines.push(`  - ${key}: ${value}`);
  }
  if (api.types) {
    lines.push("- types:");
    for (const [name, declaration] of Object.entries(api.types)) {
      lines.push(`  - \`${name}\`: \`${declaration}\``);
    }
  }
  if (api.objects) {
    lines.push("- objects:");
    for (const [name, object] of Object.entries(api.objects)) {
      lines.push(`  - ${name}`);
      for (const member of object.members ?? []) {
        if (member.documented === false)
          continue;
        const signatures = member.declarations?.length ? member.declarations.filter((declaration) => declaration.documented !== false).map((declaration) => declaration.signature) : [member.signature];
        for (const signature of signatures)
          lines.push(`    - ${member.kind} \`${signature}\``);
      }
    }
  }
  return lines.join("\n");
}
function effectiveApi(api, descriptor) {
  if (!descriptor) {
    return {
      ...api,
      objects: Object.fromEntries(Object.entries(api.objects).map(([objectName, object]) => [
        objectName,
        {
          members: object.members.filter((member) => (member.unsupportedByDefaultIn?.length ?? 0) === 0 && (member.requiresCapabilities?.length ?? 0) === 0).map((member) => ({
            ...member,
            ...member.declarations ? {
              declarations: member.declarations.filter((declaration) => (declaration.unsupportedByDefaultIn?.length ?? 0) === 0 && (declaration.requiresCapabilities?.length ?? 0) === 0)
            } : {}
          }))
        }
      ]))
    };
  }
  const policy = new BrowserApiPolicy(api, descriptor);
  return {
    ...api,
    objects: Object.fromEntries(Object.keys(api.objects).map((objectName) => [
      objectName,
      { members: policy.supportedMembers(objectName) }
    ]))
  };
}
function loadBrowserDocumentation(documentationRoot, name, descriptor) {
  if (!documentationRoot) {
    if (name)
      throw new Error(`Browser documentation not found: ${name}`);
    return FALLBACK_DOCUMENTATION;
  }
  const apiPath = join2(documentationRoot, "api.json");
  const hasApi = existsSync2(apiPath);
  const api = loadBrowserApiManifest(documentationRoot);
  const manifest = readJson(join2(documentationRoot, "documents.json"));
  if (!hasApi && !manifest) {
    if (name)
      throw new Error(`Browser documentation not found: ${name}`);
    return FALLBACK_DOCUMENTATION;
  }
  if (name) {
    const document = manifest?.documents?.find((candidate) => {
      const pathName = candidate.path?.replace(/\.md$/u, "");
      return (candidate.name === name || pathName === name) && documentApplies(candidate, descriptor, api);
    });
    const markdown = document?.path ? readMarkdown(documentationRoot, document.path) : void 0;
    if (!markdown)
      throw new Error(`Browser documentation not found: ${name}`);
    return markdown;
  }
  const sections = [];
  if (descriptor) {
    sections.push([
      "# Selected Browser",
      `- Name: ${descriptor.name}`,
      `- Type: ${descriptor.type}`,
      `- ID: ${descriptor.id}`,
      // 修复原因：Browser descriptor 持久，但承载它的 JavaScript wrapper 每次调用都会销毁。
      "Recreate this browser wrapper in every fresh Browser Use call using the same verified selection. A new user turn, fresh kernel, or tab error does not invalidate the browser backend; select another browser only when the browser-selection policy requires it.",
      "If a tab is stale or missing later, recover or create a tab after inspecting current tab facts; never switch browser backend merely to recover a tab. Empty controlled-tab lists are normal after explicit close or session release and do not invalidate the selected browser backend."
    ].join("\n"));
  }
  const title = manifest?.title ?? "Built-in Browser Automation API";
  sections.push(`# ${title}`);
  const apiText = formatApi(effectiveApi(api, descriptor));
  if (apiText)
    sections.push(apiText);
  for (const doc of manifest?.documents ?? []) {
    if (!doc.path || doc.mode === "lookup" || !documentApplies(doc, descriptor, api))
      continue;
    const markdown = readMarkdown(documentationRoot, doc.path);
    if (markdown)
      sections.push(markdown);
  }
  return sections.filter(Boolean).join("\n\n").trim() || FALLBACK_DOCUMENTATION;
}

// ../core/dist/browser-client/selection.js
import { isIP } from "node:net";
function isPreferredExtension(info) {
  return info.type === "extension" && (info.metadata?.preferred === "true" || info.metadata?.preferredInstance === "true" || info.metadata?.profileIsLastUsed === "true" || info.metadata?.profileOrdering === "0");
}
function backendFallbackRank(info) {
  if (info.type === "iab")
    return 0;
  if (isPreferredExtension(info))
    return 1;
  if (info.type === "extension")
    return 2;
  return 3;
}
function selectDefaultBrowser(infos) {
  return [...infos].sort((left, right) => backendFallbackRank(left) - backendFallbackRank(right))[0];
}
function parseUrl(value) {
  try {
    return new URL(value);
  } catch {
    throw new Error(`Invalid browser target URL: ${value}`);
  }
}
function isLocalTarget(url) {
  if (url.protocol === "file:")
    return true;
  const host = url.hostname.toLowerCase();
  return host === "localhost" || host.endsWith(".localhost") || host === "127.0.0.1" || host === "::1" || host === "[::1]";
}
function withoutHash(url) {
  const copy = new URL(url.href);
  copy.hash = "";
  return copy.href;
}
function urlMatchRank(target, candidate) {
  if (withoutHash(target) === withoutHash(candidate))
    return 0;
  if (target.origin === candidate.origin && target.pathname === candidate.pathname)
    return 1;
  if (target.hostname === candidate.hostname)
    return 2;
  const targetHost = target.hostname.toLowerCase();
  const candidateHost = candidate.hostname.toLowerCase();
  const isParentHost = (parent, child) => parent.includes(".") && isIP(parent) === 0 && child.endsWith(`.${parent}`);
  if (isParentHost(candidateHost, targetHost) || isParentHost(targetHost, candidateHost))
    return 3;
  return void 0;
}
function selectTabForUrl(targetValue, tabs) {
  const target = parseUrl(targetValue);
  let best;
  for (const tab of tabs) {
    if (!tab.url)
      continue;
    let rank;
    try {
      rank = urlMatchRank(target, parseUrl(tab.url));
    } catch {
      continue;
    }
    if (rank === void 0 || rank > 2)
      continue;
    if (best === void 0 || rank < best.rank || // 同 rank：active 优先；都不 active 时列表靠后（更新）的胜出。
    rank === best.rank && (tab.active === true || best.tab.active !== true)) {
      best = { tab, rank };
    }
  }
  return best?.tab;
}
function selectBrowserForUrl(infos, targetValue, tabsByBrowserId) {
  if (infos.length === 0) {
    throw new Error("No browser backend is available");
  }
  if (infos.length === 1) {
    return infos[0];
  }
  const target = parseUrl(targetValue);
  if (isLocalTarget(target)) {
    const iab = infos.find((info) => info.type === "iab");
    if (iab)
      return iab;
  }
  const matches = infos.flatMap((info) => {
    let best;
    for (const value of tabsByBrowserId.get(info.id) ?? []) {
      try {
        const rank = urlMatchRank(target, parseUrl(value));
        if (rank !== void 0 && (best === void 0 || rank < best))
          best = rank;
      } catch {
      }
    }
    return best === void 0 ? [] : [{ info, matchRank: best }];
  });
  matches.sort((left, right) => left.matchRank - right.matchRank || backendFallbackRank(left.info) - backendFallbackRank(right.info));
  return matches[0]?.info ?? selectDefaultBrowser(infos) ?? infos[0];
}

// ../core/dist/browser-client/playwright.js
import { isRegExp } from "node:util/types";

// ../core/dist/browser-client/result.js
var BrowserCommandError = class extends Error {
  code;
  command;
  result;
  constructor(command, result, fallbackCode) {
    const code = result.error?.code ?? fallbackCode;
    super(result.error?.message ?? `Browser command failed: ${code}`);
    this.name = "BrowserCommandError";
    this.code = code;
    this.command = command;
    this.result = result;
  }
};
function base64ToBytes(base64) {
  return new Uint8Array(Buffer.from(base64, "base64"));
}
function expectOk(command, result) {
  if (!result.ok) {
    throw new BrowserCommandError(command, result, "browser_command_failed");
  }
  return result;
}
function expectPayload(command, result, value, payloadName) {
  expectOk(command, result);
  if (value === void 0) {
    throw new BrowserCommandError(command, {
      ...result,
      ok: false,
      error: { code: "execution_error", message: `Browser result missing ${payloadName}` }
    }, "execution_error");
  }
  return value;
}

// ../core/dist/browser-client/playwright.js
var locatorDetails = Symbol("zcode.playwright.locatorDetails");
var setObjectWrapper = Symbol("zcode.playwright.setObjectWrapper");
var publicMembers = {
  PlaywrightAPI: /* @__PURE__ */ new Set([
    "domSnapshot",
    "elementInfo",
    "elementScreenshot",
    "evaluate",
    "expectNavigation",
    "frameLocator",
    "getByLabel",
    "getByPlaceholder",
    "getByRole",
    "getByTestId",
    "getByText",
    "locator",
    "waitForEvent",
    "waitForLoadState",
    "waitForTimeout",
    "waitForURL"
  ]),
  PlaywrightFrameLocator: /* @__PURE__ */ new Set([
    "frameLocator",
    "getByLabel",
    "getByPlaceholder",
    "getByRole",
    "getByTestId",
    "getByText",
    "locator"
  ]),
  PlaywrightLocator: /* @__PURE__ */ new Set([
    "all",
    "allTextContents",
    "and",
    "check",
    "click",
    "count",
    "dblclick",
    "downloadMedia",
    "evaluate",
    "fill",
    "filter",
    "first",
    "getAttribute",
    "getByLabel",
    "getByPlaceholder",
    "getByRole",
    "getByTestId",
    "getByText",
    "innerText",
    "isEnabled",
    "isVisible",
    "last",
    "locator",
    "nth",
    "or",
    "press",
    "selectOption",
    "setChecked",
    "textContent",
    "type",
    "uncheck",
    "waitFor"
  ]),
  PlaywrightDownload: /* @__PURE__ */ new Set(["path"]),
  PlaywrightFileChooser: /* @__PURE__ */ new Set(["isMultiple", "setFiles"])
};
var defaultObjectWrapper = (value, objectName) => new Proxy(value, {
  get(target, property) {
    if (typeof property === "string" && !publicMembers[objectName]?.has(property)) {
      return void 0;
    }
    const member = Reflect.get(target, property, target);
    return typeof member === "function" ? member.bind(target) : member;
  },
  has(target, property) {
    if (typeof property === "string" && !publicMembers[objectName]?.has(property))
      return false;
    return Reflect.has(target, property);
  },
  ownKeys(target) {
    return Reflect.ownKeys(target).filter((property) => typeof property !== "string" || publicMembers[objectName]?.has(property));
  }
});
function playwrightCommand(action) {
  return { method: "playwright", action };
}
async function runAction(run, action) {
  const command = playwrightCommand(action);
  return expectOk(command, await run(command));
}
async function runValue(run, action) {
  return (await runAction(run, action)).value;
}
function withContext(error, context) {
  const message = error instanceof Error ? error.message : String(error);
  const wrapped = new Error(`${message}
${context}`, { cause: error });
  if (error instanceof Error && error.stack) {
    const frames = error.stack.split("\n").slice(1).join("\n");
    if (frames)
      wrapped.stack = `${wrapped.name}: ${wrapped.message}
${frames}`;
  }
  return wrapped;
}
function serializeMatcher(value, exact, method) {
  if (typeof value === "string") {
    const suffix = exact ? "s" : "i";
    return `${JSON.stringify(value)}${suffix}`;
  }
  if (isRegExp(value))
    return value.toString();
  throw new Error(`${method} requires a string or RegExp`);
}
function roleSelector(role, options) {
  if (!role)
    throw new Error("getByRole requires a role");
  const name = options.name === void 0 ? "" : `[name=${serializeMatcher(options.name, Boolean(options.exact), "getByRole")}]`;
  return `internal:role=${role}${name}`;
}
function textSelector(text, exact) {
  return `internal:text=${serializeMatcher(text, exact, "getByText")}`;
}
function labelSelector(text, exact) {
  return `internal:label=${serializeMatcher(text, exact, "getByLabel")}`;
}
function placeholderSelector(text, exact) {
  return `internal:attr=[placeholder=${serializeMatcher(text, exact, "getByPlaceholder")}]`;
}
function testIdSelector(testId) {
  if (!testId)
    throw new Error("getByTestId requires a testId");
  return `internal:testid=[data-testid=${JSON.stringify(testId)}s]`;
}
function evaluateSource(method, pageFunction) {
  if (typeof pageFunction === "string") {
    if (!pageFunction)
      throw new Error(`${method} requires a pageFunction`);
    return { expression: pageFunction, expressionKind: "string" };
  }
  if (typeof pageFunction === "function") {
    return { expression: pageFunction.toString(), expressionKind: "function" };
  }
  throw new Error(`${method} requires a string or function`);
}
function validateCoordinates(method, options) {
  if (!Number.isFinite(options.x) || !Number.isFinite(options.y)) {
    throw new Error(`${method} requires numeric x and y coordinates`);
  }
}
function normalizeSelections(input) {
  const values = Array.isArray(input) ? input : [input];
  if (values.length === 0)
    throw new Error("locator.selectOption requires at least one value");
  return values.map((selection) => {
    if (typeof selection === "string")
      return { value: selection };
    if (!selection || typeof selection !== "object") {
      throw new Error("locator.selectOption requires a string or { value?, label?, index? }");
    }
    if (selection.value !== void 0 && typeof selection.value !== "string") {
      throw new Error("locator.selectOption value must be a string");
    }
    if (selection.label !== void 0 && typeof selection.label !== "string") {
      throw new Error("locator.selectOption label must be a string");
    }
    if (selection.index !== void 0 && (!Number.isInteger(selection.index) || selection.index < 0)) {
      throw new Error("locator.selectOption index must be a non-negative integer");
    }
    if (selection.value === void 0 && selection.label === void 0 && selection.index === void 0) {
      throw new Error("locator.selectOption requires value, label, or index for each selection");
    }
    return { ...selection };
  });
}
var PlaywrightLocator = class _PlaywrightLocator {
  run;
  owner;
  selector;
  wrap;
  constructor(run, owner, selector, wrap = defaultObjectWrapper) {
    this.run = run;
    this.owner = owner;
    this.selector = selector;
    this.wrap = wrap;
  }
  create(selector) {
    return this.wrap(new _PlaywrightLocator(this.run, this.owner, selector, this.wrap), "PlaywrightLocator");
  }
  [locatorDetails]() {
    return { owner: this.owner, selector: this.selector };
  }
  action(operation, fields = {}) {
    return runAction(this.run, {
      name: "locator",
      selector: this.selector,
      operation,
      ...fields
    });
  }
  async value(operation, fields = {}) {
    return (await this.action(operation, fields)).value;
  }
  async click(options = {}) {
    try {
      await this.action("click", options);
    } catch (error) {
      throw withContext(error, `waiting on click for selector ${this.selector}`);
    }
  }
  async dblclick(options = {}) {
    try {
      await this.action("dblclick", options);
    } catch (error) {
      throw withContext(error, `waiting on dblclick for selector ${this.selector}`);
    }
  }
  async selectOption(input, { timeoutMs } = {}) {
    try {
      await this.action("selectOption", { selections: normalizeSelections(input), timeoutMs });
    } catch (error) {
      throw withContext(error, `locator.selectOption failed for selector ${this.selector}`);
    }
  }
  async fill(value, { timeoutMs } = {}) {
    if (value == null)
      throw new Error("locator.fill requires a value");
    try {
      await this.action("fill", { value, replace: true, timeoutMs });
    } catch (error) {
      throw withContext(error, `locator.fill failed for selector ${this.selector}`);
    }
  }
  async type(value, { timeoutMs } = {}) {
    if (value == null)
      throw new Error("locator.type requires a value");
    try {
      await this.action("fill", { value, replace: false, timeoutMs });
    } catch (error) {
      throw withContext(error, `locator.type failed for selector ${this.selector}`);
    }
  }
  async press(value, { timeoutMs } = {}) {
    if (value == null)
      throw new Error("locator.press requires a value");
    try {
      await this.action("press", { value, timeoutMs });
    } catch (error) {
      throw withContext(error, `locator.press failed for selector ${this.selector}`);
    }
  }
  async setChecked(checked, options = {}) {
    if (typeof checked !== "boolean")
      throw new Error("locator.setChecked requires a boolean");
    try {
      await this.action("setChecked", { checked, ...options });
    } catch (error) {
      throw withContext(error, `locator.setChecked(${checked}) failed for selector ${this.selector}`);
    }
  }
  check(options = {}) {
    return this.setChecked(true, options);
  }
  uncheck(options = {}) {
    return this.setChecked(false, options);
  }
  async waitFor(options) {
    if (!options?.state)
      throw new Error("locator.waitFor requires a state");
    try {
      await this.action("waitFor", options);
    } catch (error) {
      throw withContext(error, `locator.waitFor(${options.state}) timed out for selector ${this.selector}`);
    }
  }
  count() {
    return this.value("count");
  }
  async all() {
    const count = await this.count();
    return Array.from({ length: count }, (_unused, index) => this.nth(index));
  }
  textContent({ timeoutMs } = {}) {
    return this.value("textContent", { timeoutMs });
  }
  innerText({ timeoutMs } = {}) {
    return this.value("innerText", { timeoutMs });
  }
  getAttribute(name, { timeoutMs } = {}) {
    if (!name)
      throw new Error("locator.getAttribute requires a name");
    return this.value("getAttribute", { attribute: name, timeoutMs });
  }
  isVisible() {
    return this.value("isVisible");
  }
  isEnabled() {
    return this.value("isEnabled");
  }
  allTextContents({ timeoutMs } = {}) {
    return this.value("allTextContents", { timeoutMs });
  }
  evaluate(pageFunction, arg, options) {
    return this.value("evaluate", {
      ...evaluateSource("locator.evaluate", pageFunction),
      arg,
      timeoutMs: options?.timeoutMs
    });
  }
  async downloadMedia({ timeoutMs } = {}) {
    try {
      await this.action("downloadMedia", { timeoutMs });
    } catch (error) {
      throw withContext(error, `locator.downloadMedia failed for selector ${this.selector}`);
    }
  }
  locator(selector, options = {}) {
    if (!selector)
      throw new Error("locator.locator requires a selector");
    return this.create(`${this.selector} >> ${selector}`).filter(options);
  }
  first() {
    return this.create(`${this.selector} >> nth=0`);
  }
  last() {
    return this.create(`${this.selector} >> nth=-1`);
  }
  nth(index) {
    if (typeof index !== "number")
      throw new Error("locator.nth requires a numeric index");
    return this.create(`${this.selector} >> nth=${index}`);
  }
  and(locator) {
    const selector = this.compatibleSelector(locator, "locator.and");
    return this.create(`${this.selector} >> internal:and=${JSON.stringify(selector)}`);
  }
  or(locator) {
    const selector = this.compatibleSelector(locator, "locator.or");
    return this.create(`${this.selector} >> internal:or=${JSON.stringify(selector)}`);
  }
  filter(options = {}) {
    const selectors = [this.selector];
    if (options.hasText !== void 0) {
      selectors.push(`internal:has-text=${serializeMatcher(options.hasText, false, "locator.filter")}`);
    }
    if (options.hasNotText !== void 0) {
      selectors.push(`internal:has-not-text=${serializeMatcher(options.hasNotText, false, "locator.filter")}`);
    }
    if (options.has !== void 0) {
      const selector = this.compatibleSelector(options.has, "locator.filter has");
      selectors.push(`internal:has=${JSON.stringify(selector)}`);
    }
    if (options.hasNot !== void 0) {
      const selector = this.compatibleSelector(options.hasNot, "locator.filter hasNot");
      selectors.push(`internal:has-not=${JSON.stringify(selector)}`);
    }
    if (options.visible !== void 0) {
      if (typeof options.visible !== "boolean") {
        throw new Error("locator.filter visible must be a boolean");
      }
      selectors.push(`visible=${options.visible}`);
    }
    return this.create(selectors.join(" >> "));
  }
  getByRole(role, options = {}) {
    return this.child(roleSelector(role, options));
  }
  getByText(text, options = {}) {
    return this.child(textSelector(text, Boolean(options.exact)));
  }
  getByLabel(text, options = {}) {
    return this.child(labelSelector(text, Boolean(options.exact)));
  }
  getByPlaceholder(text, options = {}) {
    return this.child(placeholderSelector(text, Boolean(options.exact)));
  }
  getByTestId(testId) {
    return this.child(testIdSelector(testId));
  }
  child(selector) {
    return this.create(`${this.selector} >> ${selector}`);
  }
  compatibleSelector(locator, method) {
    if (!(locator instanceof _PlaywrightLocator)) {
      throw new Error(`${method} requires a PlaywrightLocator`);
    }
    const details = locator[locatorDetails]();
    if (details.owner !== this.owner)
      throw new Error("Locators must belong to the same tab");
    return details.selector;
  }
};
var PlaywrightFrameLocator = class _PlaywrightFrameLocator {
  run;
  owner;
  frameSelector;
  wrap;
  constructor(run, owner, frameSelector, wrap = defaultObjectWrapper) {
    this.run = run;
    this.owner = owner;
    this.frameSelector = frameSelector;
    this.wrap = wrap;
  }
  locator(selector) {
    if (!selector)
      throw new Error("frameLocator.locator requires a selector");
    return this.wrap(new PlaywrightLocator(this.run, this.owner, `${this.frameSelector} >> internal:control=enter-frame >> ${selector}`, this.wrap), "PlaywrightLocator");
  }
  frameLocator(selector) {
    if (!selector)
      throw new Error("frameLocator.frameLocator requires a selector");
    return this.wrap(new _PlaywrightFrameLocator(this.run, this.owner, `${this.frameSelector} >> internal:control=enter-frame >> ${selector}`, this.wrap), "PlaywrightFrameLocator");
  }
  getByRole(role, options = {}) {
    return this.locator(roleSelector(role, options));
  }
  getByText(text, options = {}) {
    return this.locator(textSelector(text, Boolean(options.exact)));
  }
  getByLabel(text, options = {}) {
    return this.locator(labelSelector(text, Boolean(options.exact)));
  }
  getByPlaceholder(text, options = {}) {
    return this.locator(placeholderSelector(text, Boolean(options.exact)));
  }
  getByTestId(testId) {
    return this.locator(testIdSelector(testId));
  }
};
var PlaywrightDownload = class {
  run;
  downloadId;
  constructor(run, downloadId) {
    this.run = run;
    this.downloadId = downloadId;
  }
  path({ timeoutMs } = {}) {
    return runValue(this.run, { name: "downloadPath", downloadId: this.downloadId, timeoutMs });
  }
};
var PlaywrightFileChooser = class {
  run;
  fileChooserId;
  multiple;
  constructor(run, fileChooserId, multiple) {
    this.run = run;
    this.fileChooserId = fileChooserId;
    this.multiple = multiple;
  }
  isMultiple() {
    return this.multiple;
  }
  async setFiles(files, { timeoutMs } = {}) {
    if (files == null)
      throw new Error("fileChooser.setFiles requires files");
    const normalized = Array.isArray(files) ? files : [files];
    if (normalized.length === 0)
      throw new Error("fileChooser.setFiles requires at least one file");
    try {
      await runAction(this.run, {
        name: "fileChooserSetFiles",
        fileChooserId: this.fileChooserId,
        files: normalized,
        timeoutMs
      });
    } catch (error) {
      throw withContext(error, "fileChooser.setFiles failed");
    }
  }
};
var PlaywrightAPI = class {
  run;
  owner = {};
  wrap = defaultObjectWrapper;
  constructor(run) {
    this.run = run;
  }
  [setObjectWrapper](wrap) {
    this.wrap = wrap;
  }
  evaluate(pageFunction, arg, options) {
    return runValue(this.run, {
      name: "evaluate",
      ...evaluateSource("playwright.evaluate", pageFunction),
      arg,
      timeoutMs: options?.timeoutMs
    });
  }
  locator(selector) {
    if (!selector)
      throw new Error("playwright.locator requires a selector");
    return this.wrap(new PlaywrightLocator(this.run, this.owner, selector, this.wrap), "PlaywrightLocator");
  }
  getByRole(role, options = {}) {
    return this.locator(roleSelector(role, options));
  }
  getByText(text, options = {}) {
    return this.locator(textSelector(text, Boolean(options.exact)));
  }
  getByLabel(text, options = {}) {
    return this.locator(labelSelector(text, Boolean(options.exact)));
  }
  getByPlaceholder(text, options = {}) {
    return this.locator(placeholderSelector(text, Boolean(options.exact)));
  }
  getByTestId(testId) {
    return this.locator(testIdSelector(testId));
  }
  frameLocator(selector) {
    if (!selector)
      throw new Error("playwright.frameLocator requires a selector");
    return this.wrap(new PlaywrightFrameLocator(this.run, this.owner, selector, this.wrap), "PlaywrightFrameLocator");
  }
  async waitForURL(url, options = {}) {
    if (!url)
      throw new Error("playwright.waitForURL requires a url");
    await runAction(this.run, { name: "waitForURL", url, ...options });
  }
  async waitForLoadState(options = {}) {
    await runAction(this.run, { name: "waitForLoadState", ...options });
  }
  async waitForTimeout(timeoutMs) {
    if (!Number.isInteger(timeoutMs) || timeoutMs < 0) {
      throw new Error("playwright.waitForTimeout requires a non-negative integer");
    }
    const command = { method: "playwrightWaitForTimeout", timeoutMs };
    expectOk(command, await this.run(command));
  }
  async waitForEvent(event, options = {}) {
    if (event !== "download" && event !== "filechooser") {
      throw new Error("playwright.waitForEvent only supports 'download' and 'filechooser'");
    }
    const value = await runValue(this.run, {
      name: "waitForEvent",
      event,
      timeoutMs: options.timeoutMs
    });
    return event === "download" ? this.wrap(new PlaywrightDownload(this.run, value.id), "PlaywrightDownload") : this.wrap(new PlaywrightFileChooser(this.run, value.id, Boolean(value.isMultiple)), "PlaywrightFileChooser");
  }
  async expectNavigation(action, options = {}) {
    const wait = options.url ? this.waitForURL(options.url, {
      ...options.timeoutMs === void 0 ? {} : { timeoutMs: options.timeoutMs },
      ...options.waitUntil === void 0 ? {} : { waitUntil: options.waitUntil }
    }) : this.waitForLoadState({
      ...options.timeoutMs === void 0 ? {} : { timeoutMs: options.timeoutMs },
      ...options.waitUntil === void 0 ? {} : { state: options.waitUntil }
    });
    const result = action();
    const [actionResult] = await Promise.all([result, wait]);
    return actionResult;
  }
  elementInfo(options) {
    validateCoordinates("playwright.elementInfo", options);
    return runValue(this.run, { name: "elementInfo", ...options });
  }
  async elementScreenshot(options) {
    validateCoordinates("playwright.elementScreenshot", options);
    const result = await runAction(this.run, { name: "elementScreenshot", ...options });
    if (!result.image)
      throw new Error("Browser result missing image");
    return base64ToBytes(result.image.base64);
  }
  domSnapshot() {
    return runValue(this.run, { name: "domSnapshot" });
  }
};
function createPlaywrightAPI(run) {
  return defaultObjectWrapper(new PlaywrightAPI(run), "PlaywrightAPI");
}
function configurePlaywrightObjectWrapper(api, wrap) {
  api[setObjectWrapper](wrap);
  return wrap(api, "PlaywrightAPI");
}

// ../core/dist/browser-client/facade.js
var BrowserCapability = class {
  id;
  description;
  #readDocumentation;
  constructor(id, description, readDocumentation) {
    this.id = id;
    this.description = description;
    this.#readDocumentation = readDocumentation;
  }
  async documentation() {
    return this.#readDocumentation(this.id);
  }
};
var VisibilityBrowserCapability = class extends BrowserCapability {
  execute;
  constructor(info, readDocumentation, execute) {
    super(info.id, info.description, readDocumentation);
    this.execute = execute;
  }
  async get() {
    const command = { method: "browserVisibilityGet" };
    const result = expectOk(command, await this.execute(command));
    if (typeof result.value !== "boolean") {
      throw new Error("Browser visibility result is missing a boolean value");
    }
    return result.value;
  }
  async set(visible) {
    if (typeof visible !== "boolean")
      throw new TypeError("visibility.set requires a boolean");
    const command = { method: "browserVisibilitySet", visible };
    expectOk(command, await this.execute(command));
  }
};
var BrowserCapabilityCollection = class {
  execute;
  assertAvailable;
  #read;
  #readDocumentation;
  constructor(read, readDocumentation, execute, assertAvailable = () => void 0) {
    this.execute = execute;
    this.assertAvailable = assertAvailable;
    this.#read = read;
    this.#readDocumentation = readDocumentation;
  }
  async list() {
    this.assertAvailable();
    return this.#read().map((capability) => ({ ...capability }));
  }
  async get(id) {
    this.assertAvailable();
    const capability = this.#read().find((candidate) => candidate.id === id);
    if (!capability)
      throw new Error(`Browser capability '${id}' is unavailable`);
    if (id === "visibility" && this.execute) {
      return new VisibilityBrowserCapability(capability, this.#readDocumentation, this.execute);
    }
    return new BrowserCapability(capability.id, capability.description, this.#readDocumentation);
  }
};
var BrowserRecordingAPI = class {
  run;
  constructor(run) {
    this.run = run;
  }
  async start(options = {}) {
    const command = { method: "recordingStart", options };
    const result = await this.run(command);
    return expectPayload(command, result, result.recording, "recording");
  }
  async status(recordingId, options = {}) {
    if (!recordingId)
      throw new TypeError("recording.status requires a recording id");
    const command = {
      method: "recordingStatus",
      recordingId,
      ...options
    };
    const result = await this.run(command);
    return expectPayload(command, result, result.recording, "recording");
  }
  async cancel(recordingId) {
    if (!recordingId)
      throw new TypeError("recording.cancel requires a recording id");
    const command = { method: "recordingCancel", recordingId };
    const result = await this.run(command);
    return expectPayload(command, result, result.recording, "recording");
  }
};
function validateViewportSize(viewportSize) {
  if (!Number.isInteger(viewportSize?.width) || viewportSize.width < BROWSER_VIEWPORT_LIMITS.minWidth || viewportSize.width > BROWSER_VIEWPORT_LIMITS.maxWidth || !Number.isInteger(viewportSize?.height) || viewportSize.height < BROWSER_VIEWPORT_LIMITS.minHeight || viewportSize.height > BROWSER_VIEWPORT_LIMITS.maxHeight) {
    throw new TypeError(`setViewportSize requires integer width ${BROWSER_VIEWPORT_LIMITS.minWidth}..${BROWSER_VIEWPORT_LIMITS.maxWidth} and height ${BROWSER_VIEWPORT_LIMITS.minHeight}..${BROWSER_VIEWPORT_LIMITS.maxHeight}`);
  }
}
function cuaButton(button = 1) {
  if (button === 2)
    return "middle";
  if (button === 3)
    return "right";
  if (button === 1)
    return "left";
  throw new Error(`Unsupported CUA mouse button: ${button}`);
}
function cuaModifiers(keys = []) {
  return keys.filter((key) => ["Alt", "Control", "ControlOrMeta", "Meta", "Shift"].includes(key));
}
var RawTab = class {
  run;
  constructor(run) {
    this.run = run;
  }
  navigate(url) {
    return this.run({ method: "navigate", url });
  }
  getState() {
    return this.run({ method: "getState" });
  }
  screenshot(opts) {
    return this.run({ method: "screenshot", ...opts });
  }
  back() {
    return this.run({ method: "back" });
  }
  forward() {
    return this.run({ method: "forward" });
  }
  reload() {
    return this.run({ method: "reload" });
  }
  snapshot(opts) {
    return this.run({ method: "snapshot", ...opts });
  }
  click(target, opts) {
    if (typeof target === "string") {
      return this.run({ method: "click", ref: target, ...opts });
    }
    const { x, y, ...rest } = target;
    return this.run({ method: "click", x, y, ...rest, ...opts });
  }
  type(text, opts) {
    return this.run({ method: "type", text, ref: opts?.ref });
  }
  press(key, opts) {
    return this.run({ method: "press", key, ref: opts?.ref, modifiers: opts?.modifiers });
  }
  scroll(opts) {
    return this.run({ method: "scroll", ...opts });
  }
  hover(target) {
    if (typeof target === "string") {
      return this.run({ method: "hover", ref: target });
    }
    return this.run({ method: "hover", x: target.x, y: target.y });
  }
  select(ref, values) {
    return this.run({ method: "select", ref, values });
  }
  check(ref, checked = true) {
    return this.run({ method: "check", ref, checked });
  }
  drag(from, to, opts) {
    const fromPart = typeof from === "string" ? { fromRef: from } : { from };
    const toPart = typeof to === "string" ? { toRef: to } : { to };
    return this.run({ method: "drag", ...fromPart, ...toPart, ...opts });
  }
  close() {
    return this.run({ method: "close" });
  }
  elementInfo(x, y) {
    return this.run({ method: "elementInfo", x, y });
  }
  evaluate(expressionOrFn) {
    const expression = typeof expressionOrFn === "function" ? `(${expressionOrFn.toString()})()` : expressionOrFn;
    return this.run({ method: "evaluate", expression });
  }
  getDialog() {
    return this.run({ method: "getDialog" });
  }
  handleDialog(accept, promptText) {
    return this.run({ method: "handleDialog", accept, promptText });
  }
};
var DialogBase = class {
  type;
  respond;
  constructor(type, respond) {
    this.type = type;
    this.respond = respond;
  }
  dismiss() {
    return this.respond(false);
  }
};
var AlertDialog = class extends DialogBase {
};
var BeforeUnloadDialog = class extends DialogBase {
};
var ConfirmDialog = class extends DialogBase {
  accept() {
    return this.respond(true);
  }
};
var PromptDialog = class extends DialogBase {
  accept(text) {
    return this.respond(true, text);
  }
};
function createJsDialog(dialog, respond) {
  switch (dialog.type) {
    case "confirm":
      return new ConfirmDialog("confirm", respond);
    case "prompt":
      return new PromptDialog("prompt", respond);
    case "beforeunload":
      return new BeforeUnloadDialog("beforeunload", respond);
    default:
      return new AlertDialog("alert", respond);
  }
}
var Tab = class {
  execute;
  tabId;
  id;
  raw;
  capabilities;
  playwright;
  recording;
  wrapObject = (value, _objectName) => value;
  viewportSizeValue;
  constructor(execute, tabId, summary, capabilityDescriptors = [], readCapabilityDocumentation = () => "") {
    this.execute = execute;
    this.tabId = tabId;
    this.id = tabId ?? "";
    this.raw = new RawTab((command) => this.run(command));
    this.capabilities = new BrowserCapabilityCollection(() => capabilityDescriptors, readCapabilityDocumentation);
    this.playwright = createPlaywrightAPI((command) => this.run(command));
    this.recording = new BrowserRecordingAPI((command) => this.run(command));
    this.viewportSizeValue = summary?.viewport ? { ...summary.viewport } : null;
  }
  run(command) {
    const withTab = this.tabId ? { ...command, tabId: this.tabId } : command;
    return this.execute(withTab);
  }
  /** Browser connection capability policy 在对象建好后注入，并保持 Playwright 对象 identity。 */
  applyPlaywrightPolicy(policy) {
    const wrap = (value, objectName) => createBrowserApiProxy(value, objectName, policy, { hideUnknown: true });
    this.wrapObject = wrap;
    this.playwright = configurePlaywrightObjectWrapper(this.playwright, wrap);
    this.recording = wrap(this.recording, "BrowserRecordingAPI");
    return this;
  }
  async action(command) {
    expectOk(command, await this.run(command));
  }
  async goto(url) {
    await this.action({ method: "navigate", url });
  }
  async url() {
    return (await this.getState()).url || void 0;
  }
  async title() {
    return (await this.getState()).title || void 0;
  }
  async navigate(url) {
    await this.goto(url);
  }
  async getState() {
    const command = { method: "getState" };
    const result = await this.run(command);
    return expectPayload(command, result, result.state, "state");
  }
  /** 对齐 Playwright Page.setViewportSize；ZCode 额外施加内置自由尺寸的安全边界。 */
  async setViewportSize(viewportSize) {
    validateViewportSize(viewportSize);
    await this.action({ method: "browserViewportSet", ...viewportSize });
    this.viewportSizeValue = { ...viewportSize };
  }
  /** 对齐 Playwright Page.viewportSize；返回本次 tab binding 最近一次观察到的实际值。 */
  viewportSize() {
    return this.viewportSizeValue ? { ...this.viewportSizeValue } : null;
  }
  async screenshot(opts) {
    const command = { method: "screenshot", ...opts };
    const result = await this.run(command);
    const image = expectPayload(command, result, result.image, "image");
    return base64ToBytes(image.base64);
  }
  async back() {
    await this.action({ method: "back" });
  }
  async forward() {
    await this.action({ method: "forward" });
  }
  async reload() {
    await this.action({ method: "reload" });
  }
  async snapshot(opts) {
    const command = { method: "snapshot", ...opts };
    const result = await this.run(command);
    return expectPayload(command, result, result.snapshot, "snapshot");
  }
  async click(target, opts) {
    await this.action(typeof target === "string" ? { method: "click", ref: target, ...opts } : (() => {
      const { x, y, ...rest } = target;
      return { method: "click", x, y, ...rest, ...opts };
    })());
  }
  async type(text, opts) {
    await this.action({ method: "type", text, ref: opts?.ref });
  }
  async press(key, opts) {
    await this.action({ method: "press", key, ref: opts?.ref, modifiers: opts?.modifiers });
  }
  async scroll(opts) {
    await this.action({ method: "scroll", ...opts });
  }
  async hover(target) {
    await this.action(typeof target === "string" ? { method: "hover", ref: target } : { method: "hover", x: target.x, y: target.y });
  }
  async select(ref, values) {
    await this.action({ method: "select", ref, values });
  }
  async check(ref, checked = true) {
    await this.action({ method: "check", ref, checked });
  }
  async drag(from, to, opts) {
    const fromPart = typeof from === "string" ? { fromRef: from } : { from };
    const toPart = typeof to === "string" ? { toRef: to } : { to };
    await this.action({ method: "drag", ...fromPart, ...toPart, ...opts });
  }
  async close() {
    await this.action({ method: "close" });
  }
  async finalize(opts) {
    await this.action({ method: "finalize", deliverable: opts?.deliverable });
  }
  async markDeliverable() {
    await this.action({ method: "markDeliverable", tabId: this.id });
  }
  async markHandoff() {
    await this.action({ method: "markHandoff", tabId: this.id });
  }
  async elementInfo(x, y) {
    const command = { method: "elementInfo", x, y };
    const result = expectOk(command, await this.run(command));
    return result.element;
  }
  async evaluate(expressionOrFn) {
    const expression = typeof expressionOrFn === "function" ? `(${expressionOrFn.toString()})()` : expressionOrFn;
    const command = { method: "evaluate", expression };
    const result = expectOk(command, await this.run(command));
    return result.value;
  }
  async getDialog() {
    const command = { method: "getDialog" };
    const result = expectOk(command, await this.run(command));
    return result.dialog ?? null;
  }
  async getJsDialog() {
    const dialog = await this.getDialog();
    if (!dialog)
      return void 0;
    const value = createJsDialog(dialog, (accept, promptText) => this.handleDialog(accept, promptText));
    const objectName = dialog.type === "confirm" ? "ConfirmDialog" : dialog.type === "prompt" ? "PromptDialog" : dialog.type === "beforeunload" ? "BeforeUnloadDialog" : "AlertDialog";
    return this.wrapObject(value, objectName);
  }
  async handleDialog(accept, promptText) {
    await this.action({ method: "handleDialog", accept, promptText });
  }
  get cua() {
    return this.wrapObject({
      click: ({ x, y, button, keypress }) => {
        const modifiers = cuaModifiers(keypress);
        return this.action({
          method: "click",
          x,
          y,
          button: cuaButton(button),
          ...modifiers.length > 0 ? { modifiers } : {}
        });
      },
      double_click: ({ x, y, keypress }) => {
        const modifiers = cuaModifiers(keypress);
        return this.action({
          method: "click",
          x,
          y,
          doubleClick: true,
          ...modifiers.length > 0 ? { modifiers } : {}
        });
      },
      downloadMedia: ({ x, y }) => this.action({ method: "click", x, y }),
      drag: ({ keys, path }) => {
        if (path.length === 0)
          throw new Error("cua.drag requires a non-empty path");
        const modifiers = cuaModifiers(keys);
        return this.action({
          method: "cuaDrag",
          path,
          ...modifiers.length > 0 ? { modifiers } : {}
        });
      },
      keypress: ({ keys }) => this.action({ method: "cuaKeypress", keys }),
      move: ({ x, y, keys }) => {
        const modifiers = cuaModifiers(keys);
        return this.action({
          method: "hover",
          x,
          y,
          ...modifiers.length > 0 ? { modifiers } : {}
        });
      },
      scroll: ({ x, y, keypress, scrollX, scrollY }) => {
        const modifiers = cuaModifiers(keypress);
        return this.action({
          method: "cuaScroll",
          x,
          y,
          scrollX,
          scrollY,
          ...modifiers.length > 0 ? { modifiers } : {}
        });
      },
      type: ({ text }) => this.action({ method: "type", text })
    }, "CUAAPI");
  }
  get dom_cua() {
    return this.wrapObject({
      get_visible_dom: () => this.snapshot(),
      click: ({ node_id }) => this.action({ method: "click", ref: node_id }),
      double_click: ({ node_id }) => this.action({ method: "click", ref: node_id, doubleClick: true }),
      downloadMedia: ({ node_id }) => this.action({ method: "click", ref: node_id }),
      type: ({ text }) => this.action({ method: "type", text }),
      scroll: ({ node_id, x, y }) => this.action({ method: "domCuaScroll", nodeId: node_id, scrollX: x, scrollY: y }),
      keypress: ({ keys }) => this.action({ method: "cuaKeypress", keys })
    }, "DomCUAAPI");
  }
};
var BrowserTabs = class {
  execute;
  wrapTab;
  capabilities;
  readCapabilityDocumentation;
  constructor(execute, wrapTab = (tab) => tab, capabilities = [], readCapabilityDocumentation = () => "") {
    this.execute = execute;
    this.wrapTab = wrapTab;
    this.capabilities = capabilities;
    this.readCapabilityDocumentation = readCapabilityDocumentation;
  }
  tab(summary) {
    return this.wrapTab(new Tab(this.execute, summary.tabId, summary, this.capabilities, this.readCapabilityDocumentation));
  }
  async summaries() {
    const command = { method: "list" };
    const result = await this.execute(command);
    expectOk(command, result);
    return result.tabs ?? [];
  }
  async list() {
    return (await this.summaries()).map((summary) => ({
      id: summary.tabId,
      viewport: { ...summary.viewport },
      ...summary.active ? { active: true } : {},
      ...summary.title ? { title: summary.title } : {},
      ...summary.url ? { url: summary.url } : {}
    }));
  }
  async selected() {
    const summaries = await this.summaries();
    const selected = summaries.find((summary) => summary.active === true) ?? summaries.at(-1);
    return selected ? this.tab(selected) : void 0;
  }
  async get(tabId) {
    const summary = (await this.summaries()).find((candidate) => candidate.tabId === tabId);
    if (!summary) {
      throw new BrowserCommandError({ method: "list" }, {
        ok: false,
        elapsedMs: 0,
        error: { code: "backend_unavailable", message: `Browser tab '${tabId}' is unavailable` }
      }, "backend_unavailable");
    }
    const command = { method: "activateTab", tabId };
    const result = await this.execute(command);
    const activated = expectPayload(command, result, result.tab, "tab");
    return this.tab(activated);
  }
  async new() {
    const command = { method: "newTab" };
    const result = await this.execute(command);
    const summary = expectPayload(command, result, result.tab, "tab");
    return this.tab(summary);
  }
  /**
   * open(url) 的复用入口：按 URL 匹配已有 agent-owned tab，命中则激活（activateTab，
   * 用户立即看到该 tab）并返回，未命中返回 undefined。list/activate 的失败向调用方抛出，
   * 由 open() 统一降级为 newTab，复用链路绝不阻断任务。
   */
  async reuse(url) {
    const summaries = await this.summaries();
    const matched = selectTabForUrl(url, summaries);
    if (!matched)
      return void 0;
    const command = { method: "activateTab", tabId: matched.tabId };
    const result = await this.execute(command);
    const activated = expectPayload(command, result, result.tab, "tab");
    return this.tab(activated);
  }
  async finalize(options = {}) {
    const keep = (options.keep ?? []).map(({ tab, status }) => ({
      tabId: typeof tab === "string" ? tab : tab.id,
      status
    }));
    const command = { method: "finalizeTabs", keep };
    expectOk(command, await this.execute(command));
  }
};
var BrowserUser = class {
  execute;
  wrapTab;
  capabilities;
  readCapabilityDocumentation;
  constructor(execute, wrapTab, capabilities = [], readCapabilityDocumentation = () => "") {
    this.execute = execute;
    this.wrapTab = wrapTab;
    this.capabilities = capabilities;
    this.readCapabilityDocumentation = readCapabilityDocumentation;
  }
  async openTabs() {
    const command = { method: "listUserTabs" };
    const result = expectOk(command, await this.execute(command));
    return result.userTabs ?? [];
  }
  async claimTab(tab) {
    const command = {
      method: "claimTab",
      tabId: typeof tab === "string" ? tab : tab.id
    };
    const result = await this.execute(command);
    const summary = expectPayload(command, result, result.tab, "tab");
    return this.wrapTab(new Tab(this.execute, summary.tabId, summary, this.capabilities, this.readCapabilityDocumentation));
  }
  async history(_options = {}) {
    throw new Error("Browser history is unavailable for the iab backend");
  }
};
var Browser = class {
  readDocumentation;
  tabs;
  user;
  capabilities;
  defaultTab;
  info;
  execute;
  policy;
  runtimeObject;
  constructor(info, execute, readDocumentation, manifest, assertAvailable = () => void 0) {
    this.readDocumentation = readDocumentation;
    this.info = info;
    this.policy = new BrowserApiPolicy(manifest, info);
    this.execute = (command) => execute(this.info.id, this.info.generation, command);
    const capabilityDocumentation = (name) => this.readDocumentation(name, this.info);
    const wrapTab = (tab) => createBrowserApiProxy(tab.applyPlaywrightPolicy(this.policy), "Tab", this.policy);
    this.capabilities = new BrowserCapabilityCollection(() => this.info.capabilities.browser ?? [], capabilityDocumentation, this.execute, assertAvailable);
    this.tabs = createBrowserApiProxy(new BrowserTabs(this.execute, wrapTab, this.info.capabilities.tab ?? [], capabilityDocumentation), "Tabs", this.policy);
    this.user = createBrowserApiProxy(new BrowserUser(this.execute, wrapTab, this.info.capabilities.tab ?? [], capabilityDocumentation), "BrowserUser", this.policy);
    this.defaultTab = wrapTab(new Tab(this.execute, void 0, void 0, this.info.capabilities.tab ?? [], capabilityDocumentation));
    this.runtimeObject = createBrowserApiProxy(this, "Browser", this.policy);
  }
  /** Codex-compatible runtime browser identity；不能用 backend type 代替。 */
  get browserId() {
    return this.info.id;
  }
  get type() {
    return this.info.type;
  }
  get generation() {
    return this.info.generation;
  }
  /** @deprecated 迁移兼容；新代码使用 browserId。 */
  get id() {
    return this.browserId;
  }
  /** @deprecated 迁移兼容；新代码使用 descriptor.type。 */
  get backend() {
    return this.type;
  }
  get default() {
    return this.defaultTab;
  }
  async documentation() {
    return this.readDocumentation(void 0, this.info);
  }
  async nameSession(name) {
    if (!name.trim())
      throw new Error("browser.nameSession requires a non-empty name");
    const command = { method: "nameSession", name };
    expectOk(command, await this.execute(command));
  }
  /** registry refresh 后保留对象 identity，同时更新 capability/metadata。 */
  updateInfo(info) {
    this.info = info;
    this.policy.updateDescriptor(info);
  }
  executeCommand(command) {
    return this.execute(command);
  }
  createTab(tabId) {
    return createBrowserApiProxy(new Tab(this.execute, tabId, void 0, this.info.capabilities.tab ?? [], (name) => this.readDocumentation(name, this.info)), "Tab", this.policy);
  }
  asRuntimeObject() {
    return this.runtimeObject;
  }
};
var BrowsersFacade = class {
  assertAvailable;
  transport;
  readDocumentation;
  manifest;
  browsers = /* @__PURE__ */ new Map();
  runtimeObject;
  constructor(transport, options = {}) {
    const assertAvailable = options.assertAvailable ?? (() => void 0);
    this.assertAvailable = assertAvailable;
    this.transport = {
      list: async () => {
        assertAvailable();
        return await transport.list();
      },
      execute: async (browserId, browserGeneration, command) => {
        assertAvailable();
        return await transport.execute(browserId, browserGeneration, command);
      }
    };
    this.manifest = loadBrowserApiManifest(options.documentationRoot);
    this.readDocumentation = (name, descriptor) => {
      assertAvailable();
      return loadBrowserDocumentation(options.documentationRoot, name, descriptor);
    };
    const topLevelPolicy = new BrowserApiPolicy(this.manifest, {
      id: "browser-client",
      generation: 0,
      type: "iab",
      name: "Browser client",
      capabilities: {}
    });
    this.runtimeObject = createBrowserApiProxy(this, "Browsers", topLevelPolicy, {
      hideUnknown: true
    });
  }
  asRuntimeObject() {
    return this.runtimeObject;
  }
  async listAvailable() {
    return await this.transport.list();
  }
  async list() {
    return (await this.listAvailable()).map(({ generation: _generation, ...descriptor }) => descriptor);
  }
  async get(idOrType) {
    const infos = await this.listAvailable();
    const info = infos.find((candidate) => candidate.id === idOrType) ?? infos.find((candidate) => candidate.type === idOrType);
    if (!info) {
      const available = infos.map((candidate) => `${candidate.type}:${candidate.id}`).join(", ");
      throw new BrowserCommandError({ method: "list" }, {
        ok: false,
        elapsedMs: 0,
        error: {
          code: "backend_unavailable",
          message: `Browser backend '${idOrType}' is unavailable${available ? `; available: ${available}` : ""}`
        }
      }, "backend_unavailable");
    }
    return this.browserFor(info);
  }
  async getDefault() {
    const info = selectDefaultBrowser(await this.listAvailable());
    if (!info) {
      return this.get("__no_browser_backend__");
    }
    return this.browserFor(info);
  }
  async getForUrl(url) {
    const infos = await this.listAvailable();
    if (infos.length === 0) {
      return this.get("__no_browser_backend__");
    }
    if (infos.length === 1) {
      return this.browserFor(infos[0]);
    }
    const tabsByBrowserId = /* @__PURE__ */ new Map();
    await Promise.all(infos.map(async (info) => {
      try {
        const tabs = await this.browserFor(info).tabs.list();
        tabsByBrowserId.set(info.id, tabs.flatMap((tab) => tab.url ? [tab.url] : []));
      } catch {
        tabsByBrowserId.set(info.id, []);
      }
    }));
    return this.browserFor(selectBrowserForUrl(infos, url, tabsByBrowserId));
  }
  async open(url, options = {}) {
    const browser = await this.getDefault();
    if (url && options.reuseTab !== false) {
      const reusable = await browser.tabs.reuse(url).catch(() => void 0);
      if (reusable) {
        await reusable.goto(url);
        return reusable;
      }
    }
    const tab = await browser.tabs.new();
    if (url) {
      await tab.goto(url);
    }
    return tab;
  }
  async current() {
    const browser = await this.getDefault();
    return await browser.tabs.selected() ?? browser.tabs.new();
  }
  async listTabs() {
    const browser = await this.getDefault();
    return (await browser.tabs.list()).map((tab) => browser.createTab(tab.id));
  }
  tab(tabId) {
    const browser = this.getDefault();
    return new Tab(async (command) => (await browser).executeCommand(command), tabId);
  }
  documentation(name) {
    return this.readDocumentation(name);
  }
  browserFor(info) {
    const existing = this.browsers.get(info.id);
    if (existing && existing.raw.generation === info.generation) {
      existing.raw.updateInfo(info);
      return existing.runtime;
    }
    const raw = new Browser(info, this.transport.execute, this.readDocumentation, this.manifest, this.assertAvailable);
    const runtime = raw.asRuntimeObject();
    this.browsers.set(info.id, { raw, runtime });
    return runtime;
  }
};

// ../core/dist/browser-client/index.js
function setupBrowserRuntime(opts) {
  opts.assertAvailable?.();
  const agent = opts.globals.agent ??= {};
  agent.browsers = new BrowsersFacade(opts.transport, {
    documentationRoot: opts.documentationRoot,
    assertAvailable: opts.assertAvailable
  }).asRuntimeObject();
  agent.documentation = Object.freeze({
    get: async (name) => {
      opts.assertAvailable?.();
      if (!name)
        throw new TypeError("agent.documentation.get requires a document name");
      return loadBrowserDocumentation(opts.documentationRoot, name);
    }
  });
}

// src/runtime-bridge.ts
var NODE_REPL_BROWSER_BRIDGE_SYMBOL = Symbol.for("zcode.node-repl.browser-control-bridge");
function readNodeReplBrowserRuntimeBridge(globals) {
  const bridge = globals[NODE_REPL_BROWSER_BRIDGE_SYMBOL];
  if (!bridge || typeof bridge !== "object") {
    throw new Error(
      "Browser runtime bridge is unavailable. Use Browser from a ZCode desktop or shared-host session."
    );
  }
  return bridge;
}

// src/browser-client.ts
async function setupBrowserRuntime2(input) {
  const bridge = readNodeReplBrowserRuntimeBridge(input.globals);
  bridge.assertAvailable();
  setupBrowserRuntime({
    globals: input.globals,
    transport: bridge,
    documentationRoot: bridge.documentationRoot,
    assertAvailable: bridge.assertAvailable
  });
}
export {
  setupBrowserRuntime2 as setupBrowserRuntime
};
