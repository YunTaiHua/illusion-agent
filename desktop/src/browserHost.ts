/**
 * 浏览器控制服务器（桌面版内置浏览器）
 * =====================================
 *
 * 桌面版内置浏览器控制服务器：真实浏览器由渲染进程的 <webview> guest 承载
 * （App 级 BrowserHostLayer），本模块在主进程启动一个本地 HTTP 控制服务器，
 * Python 后端（illusion.browser.desktop_bridge，经 ILLUSION_DESKTOP_BROWSER_URL
 * / TOKEN env 发现）把浏览器命令 POST 进来，由本模块路由到对应 guest 的
 * webContents 执行：
 *
 *   Python 工具 → HTTP /command → 本模块 → webContents API / sendInputEvent /
 *   executeJavaScript → 结果原路返回
 *
 * Tab 生命周期由主进程驱动：create/close/select 经 'browser-tab-command' IPC
 * 推给渲染进程；渲染进程 webview did-attach 后经 'browser-guest-attached'
 * 回报 webContentsId，本模块建立 tabId → webContents 映射（registerGuest）。
 *
 * 安全：仅监听 127.0.0.1，每会话随机 token（32 字节 hex），请求头校验。
 */
import * as http from 'node:http';
import * as crypto from 'node:crypto';
import { webContents } from 'electron';
import type { BrowserWindow, WebContents } from 'electron';

/** guest 注册表条目 */
interface TabEntry {
  id: string;
  webContentsId: number | null;
  url: string;
  title: string;
  active: boolean;
  /** 创建时刻：pending（未 attach）条目超时由 getState 清理 */
  createdAt: number;
}

export interface BrowserHostOptions {
  /** 主窗口获取器（tab 指令 IPC 目标） */
  getWindow: () => BrowserWindow | null;
}

export interface BrowserHost {
  /** 端口就绪信号（listen 异步；注入 env 前必须 await） */
  ready: Promise<void>;
  /** 控制服务器地址（http://127.0.0.1:<port>） */
  getUrl(): string;
  /** 请求校验 token */
  getToken(): string;
  /** 渲染进程 webview did-attach 回报注册（tabId → webContents 映射） */
  registerGuest(tabId: string, webContentsId: number): void;
  /** 现存受控 tab 列表（渲染进程晚挂载时补齐 webview） */
  getTabs(): Array<{ id: string; url: string }>
  /** 渲染进程 tab 关闭回调（清理映射） */
  removeGuest(tabId: string): void;
  /** guest 内 window.open 转化的新 tab（不开 OS 窗口，走内置多开） */
  openTabFromGuest(url: string): void;
  /** 关闭控制服务器 */
  close(): void;
}

/** 视口默认值与边界（与 Python 侧 base.py 对齐） */
const DEFAULT_VIEWPORT = { width: 1280, height: 720 };

/**
 * 把 evaluate 表达式包成可执行形式（对齐托管端 _wrap_expression）
 *
 * - `() => ...` / `(async) => ...` / `async () => ...`：IIFE 调用
 * - `return/const/let/var/if/for/while/try` 等语句开头：包 IIFE 块
 * - 其余裸表达式：包 lambda 取地址值
 */
function wrapEvalExpression(expression: string): string {
  const expr = expression.trim();
  if (!expr) return 'undefined';
  // 已是 IIFE（(() => …)() / (async () => …)()）：直通，避免二次包裹成坏
  // 代码。必须早于函数形态判断——IIFE 同样以 (async 开头
  if (/^\(\s*(?:async\s+)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>[\s\S]*\)\s*\(\s*\)\s*$/.test(expr)) {
    return expr;
  }
  if (expr.startsWith('()') || expr.startsWith('(async') || expr.startsWith('async')) {
    return `(${expr})()`;
  }
  if (/^(return|const|let|var|if|for|while|try)\b/.test(expr)) {
    return `(() => { ${expr} })()`;
  }
  return `(() => (${expr}))()`;
}
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, Math.round(v)));

/** Attach 等待超时（渲染进程创建 webview 的往返） */
const ATTACH_TIMEOUT_MS = 8000;
/** 导航 settle 超时：
 *  超时返回结构化错误而非悬挂——Python 侧 HTTP 超时（35s）必须永远等不到，
 *  否则报出来的是 "bridge unreachable" 而不是真实原因 */
const NAVIGATE_TIMEOUT_MS = 10000;
/** ERR_ABORTED 后确认新 document 已提交的轮询窗口 */
const ABORTED_CONFIRM_TIMEOUT_MS = 500;
/** 单帧捕获上限（新 guest 冷合成 + 一次重试的预算） */
const CAPTURE_TIMEOUT_MS = 15000;

export function startBrowserHost(options: BrowserHostOptions): BrowserHost {
  const token = crypto.randomBytes(32).toString('hex');
  const tabs = new Map<string, TabEntry>();
  let tabCounter = 0;
  let activeTabId: string | null = null;
  const viewport = { ...DEFAULT_VIEWPORT };

  // ---- tab 指令 → 渲染进程 ----

  const sendTabCommand = (cmd: { kind: 'create' | 'close' | 'select'; tabId: string; url?: string; source?: 'guest' }): void => {
    options.getWindow()?.webContents.send('browser-tab-command', cmd);
  };

  /** 等待指定 tab 的 guest attach（渲染进程 webview did-attach 回报）。
   *  竞态加固：create 指令可能早于渲染层 BrowserGuestSurface 订阅到达
   *  （订阅依赖 browser_state 推送触发重挂载）——每 1.5s 重发 create
   *  （渲染层对重复 create 幂等），attach 成功即停，超时才判失败 */
  const waitForAttach = (tabId: string, timeoutMs = ATTACH_TIMEOUT_MS): Promise<TabEntry> =>
    new Promise((resolve, reject) => {
      const start = Date.now();
      let lastResend = Date.now();
      const check = () => {
        const entry = tabs.get(tabId);
        if (entry && entry.webContentsId != null) {
          resolve(entry);
          return;
        }
        if (Date.now() - start > timeoutMs) {
          reject(new Error(`Tab ${tabId} guest attach timeout`));
          return;
        }
        if (Date.now() - lastResend > 1500) {
          lastResend = Date.now();
          const pending = tabs.get(tabId);
          if (pending) {
            options.getWindow()?.webContents.send('browser-tab-command', {
              kind: 'create', tabId, url: pending.url,
            });
          }
        }
        setTimeout(check, 60);
      };
      check();
    });

  // ---- guest 注册 ----

  const registerGuest = (tabId: string, webContentsId: number): void => {
    const entry = tabs.get(tabId);
    if (!entry) return;
    entry.webContentsId = webContentsId;
    const wc = getWebContents(entry);
    if (wc) {
      wc.on('did-navigate', (_e, url) => { entry.url = url; });
      wc.on('did-navigate-in-page', (_e, url) => { entry.url = url; });
      wc.on('page-title-updated', (_e, title) => { entry.title = title; });
    }
  };

  const removeGuest = (tabId: string): void => {
    tabs.delete(tabId);
  };

  /** guest 内 window.open/target=_blank 的落点：不开 OS 窗口，新建内置 tab
   *  （与顶栏「+」同一条多开链路）。渲染层 BrowserGuestSurface 收到 create
   *  指令后建 guest 并经 onTabsChanged 触发状态推送，标签条即时可见。 */
  const openTabFromGuest = (url: string): void => {
    tabCounter += 1;
    const tabId = `t${tabCounter}`;
    const entry: TabEntry = {
      id: tabId, webContentsId: null, url, title: '', active: true, createdAt: Date.now(),
    };
    tabs.set(tabId, entry);
    for (const other of tabs.values()) other.active = other.id === tabId;
    activeTabId = tabId;
    // source='guest'：用户点击产生的新 tab，渲染层据此让它抢前台
    //（agent 建的 tab 不带 source，绝不抢占用户正在看的页面）
    sendTabCommand({ kind: 'create', tabId, url, source: 'guest' });
  };

  // ref 契约校验：与 Python 托管端 _ref_locator 同一口径（^e\d+$）。
  // ref 由模型提供、可能来自不可信页面内容（提示注入）——原样拼进
  // querySelector 选择器字符串会逃逸出引号执行任意 JS，必须先校验。
  const VALID_REF = /^e\d+$/;
  const requireRef = (ref: unknown): string => {
    const value = typeof ref === 'string' ? ref : '';
    if (!VALID_REF.test(value)) {
      throw new Error(`Invalid ref: ${String(ref)} (expected e<number>; run browser_snapshot again)`);
    }
    return value;
  };

  const getWebContents = (entry: TabEntry): WebContents | null => {
    if (entry.webContentsId == null) return null;
    try {
      const wc = webContents.fromId(entry.webContentsId);
      if (wc && !wc.isDestroyed()) return wc;
    } catch {
      // guest 已销毁
    }
    entry.webContentsId = null;
    return null;
  };

  const tabInfo = (entry: TabEntry) => {
    let canGoBack = false;
    let canGoForward = false;
    if (entry.webContentsId != null) {
      try {
        const wc = webContents.fromId(entry.webContentsId);
        if (wc && !wc.isDestroyed()) {
          canGoBack = wc.navigationHistory
            ? wc.navigationHistory.canGoBack()
            : wc.canGoBack();
          canGoForward = wc.navigationHistory
            ? wc.navigationHistory.canGoForward()
            : wc.canGoForward();
        }
      } catch {
        // guest 已销毁：保持 false
      }
    }
    return {
      id: entry.id,
      url: entry.url,
      title: entry.title,
      active: entry.id === activeTabId,
      can_go_back: canGoBack,
      can_go_forward: canGoForward,
    };
  };

  /** guest 销毁竞态下读取 URL（destroyed 的 webContents 会抛错） */
  const safeGetUrl = (wc: WebContents): string => {
    try {
      return wc.getURL();
    } catch {
      return '';
    }
  };

  /** 置为激活 tab 并通知渲染层显示（与 selectTab 同一套） */
  const activateTab = (tabId: string): void => {
    if (activeTabId !== tabId) {
      activeTabId = tabId;
      for (const other of tabs.values()) other.active = other.id === tabId;
      sendTabCommand({ kind: 'select', tabId });
    }
  };

  /** 导航后刷新标题缓存（about:blank 等无 title 事件的页面不发
   *  page-title-updated，注册表会残留上一页的标题） */
  const refreshTitle = async (wc: WebContents, tabId: string): Promise<void> => {
    const entry = tabs.get(tabId);
    if (!entry) return;
    try {
      const title = await withTimeout(
        wc.executeJavaScript('document.title', false) as Promise<string>,
        2000, 'title refresh',
      );
      entry.title = typeof title === 'string' ? title : '';
    } catch { /* 读取失败保留旧值 */ }
  };

  const requireWc = (tabId: string | undefined): WebContents => {
    const entry = tabId ? tabs.get(tabId) : null;
    if (!entry) throw new Error(`Unknown tab: ${tabId ?? '(none)'}`);
    const wc = getWebContents(entry);
    if (!wc) {
      throw new Error(`Tab ${tabId} guest is not attached — close this tab `
        + '(browser_tabs close) and create it again if the problem persists');
    }
    return wc;
  };

  /** 观察/交互 op 用的等待版 requireWc：guest 尚未 attach（建 tab 与渲染层
   *  挂载之间的窗口）时等待而非立即失败——agent 工具调用不再因时序全瘫。
   *  超时仍抛错（面板确实关闭时快失败）。 */
  const awaitWc = async (tabId: string | undefined): Promise<WebContents> => {
    const id: string = tabId ? String(tabId) : "";
    const entry = id ? tabs.get(id) : null;
    if (!entry) throw new Error(`Unknown tab: ${id || "(none)"}`);
    if (entry.webContentsId == null) {
      try {
        await waitForAttach(id, ATTACH_TIMEOUT_MS);
      } catch {
        throw new Error(`Tab ${id} guest is not attached (browser panel may be closed) `
          + '— close this tab (browser_tabs close) and create it again if the problem persists');
      }
    }
    return requireWc(id);
  };

  // ---- 输入事件辅助 ----

  function dispatchMouseClick(wc: WebContents, x: number, y: number, clickCount: number): void {
    wc.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount });
    wc.sendInputEvent({ type: 'mouseUp', x, y, button: 'left', clickCount });
  }

  function dispatchKey(wc: WebContents, key: string): void {
    // Playwright 键名 → Electron sendInputEvent keyCode；支持 Control+a 组合
    const parts = key.split('+').map((p) => p.trim()).filter(Boolean);
    const modifiers: Array<'control' | 'alt' | 'meta' | 'shift'> = [];
    let main = '';
    for (const part of parts) {
      const low = part.toLowerCase();
      if (low === 'control' || low === 'ctrl') modifiers.push('control');
      else if (low === 'alt' || low === 'option') modifiers.push('alt');
      else if (low === 'meta' || low === 'command' || low === 'cmd') modifiers.push('meta');
      else if (low === 'shift') modifiers.push('shift');
      else main = part;
    }
    if (!main) main = 'Unidentified';
    const keyCode = normalizeKeyCode(main);
    for (const mod of modifiers) {
      wc.sendInputEvent({ type: 'keyDown', keyCode: modName(mod), modifiers });
    }
    wc.sendInputEvent({ type: 'keyDown', keyCode, modifiers });
    wc.sendInputEvent({ type: 'keyUp', keyCode, modifiers });
    for (const mod of [...modifiers].reverse()) {
      wc.sendInputEvent({ type: 'keyUp', keyCode: modName(mod), modifiers: [] });
    }
  }

  function modName(mod: 'control' | 'alt' | 'meta' | 'shift'): string {
    return mod === 'control' ? 'Control' : mod === 'alt' ? 'Alt' : mod === 'meta' ? 'Meta' : 'Shift';
  }

  function normalizeKeyCode(key: string): string {
    const named: Record<string, string> = {
      enter: 'Enter', return: 'Enter', tab: 'Tab', escape: 'Escape', esc: 'Escape',
      space: 'Space', backspace: 'Backspace', delete: 'Delete', home: 'Home',
      end: 'End', pageup: 'PageUp', pagedown: 'PageDown', up: 'ArrowUp',
      arrowup: 'ArrowUp', down: 'ArrowDown', arrowdown: 'ArrowDown',
      left: 'ArrowLeft', arrowleft: 'ArrowLeft', right: 'ArrowRight',
      arrowright: 'ArrowRight',
    };
    return named[key.toLowerCase()] ?? key;
  }

  function settledDomReady(wc: WebContents, timeoutMs?: number): Promise<void> {
    return new Promise((resolve) => {
      let timer: NodeJS.Timeout | null = null;
      const done = () => {
        if (timer) clearTimeout(timer);
        wc.removeListener('dom-ready', done);
        resolve();
      };
      if (timeoutMs != null) timer = setTimeout(done, timeoutMs);
      if (wc.isLoading()) {
        wc.on('dom-ready', done);
      } else {
        done();
      }
    });
  }

  /** ERR_ABORTED 导航的"已提交"确认：重定向链/SPA 接管会让 loadURL 以 ERR_ABORTED reject，但新
   *  document 已在加载。短轮询确认 URL 已离开原址（或加载已结束）即认定
   *  成功——不能把已成功导航的页面回报成硬失败。 */
  function confirmNavigationCommitted(wc: WebContents, previousUrl: string): Promise<boolean> {
    return new Promise((resolve) => {
      const start = Date.now();
      const check = () => {
        let committed = false;
        try {
          committed = wc.getURL() !== previousUrl || !wc.isLoading();
        } catch {
          resolve(false);
          return;
        }
        if (committed || Date.now() - start > ABORTED_CONFIRM_TIMEOUT_MS) {
          resolve(committed);
          return;
        }
        setTimeout(check, 25);
      };
      check();
    });
  }

  function withTimeout<T>(promise: Promise<T>, ms: number, what: string): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`${what} timed out after ${ms}ms`)), ms);
      promise.then(
        (v) => { clearTimeout(timer); resolve(v); },
        (e) => { clearTimeout(timer); reject(e instanceof Error ? e : new Error(String(e))); },
      );
    });
  }

  // ---- 命令实现 ----

  const commands: Record<string, (payload: Record<string, unknown>) => Promise<unknown>> = {
    async getState() {
      // GC：pending（未 attach）超过 60s 的条目是历史残留（正常 attach 在
      // 数秒内完成）——不清理会一直出现在 tabs list 并报"已断开"
      const now = Date.now();
      for (const [tabId, entry] of [...tabs]) {
        if (entry.webContentsId == null && now - entry.createdAt > 60_000) {
          tabs.delete(tabId);
          if (activeTabId === tabId) {
            activeTabId = [...tabs.keys()][0] ?? null;
          }
          sendTabCommand({ kind: 'close', tabId });
        }
      }
      // pending tab（guest 未 attach）同样可见：前端 state 立即可见新 tab，
      // 渲染层随后建 guest 并 attach（attach 只影响截图/输入类 op 的可用性）
      return {
        tabs: [...tabs.values()].map(tabInfo),
        viewport_width: viewport.width,
        viewport_height: viewport.height,
      };
    },
    async listTabs() {
      const state = await commands.getState({});
      return { tabs: (state as { tabs: unknown[] }).tabs };
    },
    async newTab(payload) {
      tabCounter += 1;
      const tabId = `t${tabCounter}`;
      // guest 统一以 about:blank 创建、由 navigate 单次导航：
      // 此前 create 直接带 url（渲染层 src 立即加载）+ navigate 再 loadURL，
      // 第二次加载打断第一次 → ERR_ABORTED(-3) 风暴（每次 + 输入网址必现）
      const url = typeof payload.url === 'string' && payload.url ? payload.url : '';
      // guest 一律以 about:blank 创建、由 navigate 单次加载（src + loadURL 双加载
      // 会互相 ERR_ABORTED）。**不等待 attach**：attach 依赖渲染层挂载 guest，
      // 而新建 tab 在 attach 前必须对所有查询可见（getState/listTabs 含 pending），
      // 否则前端 state 恒为空、navigate 的 requireWc 直接失败（"输入网址毫无
      // 反应"根因）。渲染层通过 create 指令/挂载重放/1.5s 重发任一通路建 guest。
      const hasUrl = Boolean(url) && url !== 'about:blank';
      const waitLoad = payload.waitLoad === true;
      // 带 URL 的 tab：entry.url 即目标（pending 也可见），create 指令携带 url，
      // 渲染层以 src 首挂直接加载——一次加载，无二次 navigate 的 ERR_ABORTED，
      // 也不再依赖"attach 完成才能开始加载"的竞速。
      const entry: TabEntry = {
        id: tabId, webContentsId: null, url: hasUrl ? url : 'about:blank',
        title: '', active: true, createdAt: Date.now(),
      };
      tabs.set(tabId, entry);
      for (const other of tabs.values()) other.active = other.id === tabId;
      activeTabId = tabId;
      sendTabCommand(hasUrl ? { kind: 'create', tabId, url } : { kind: 'create', tabId });
      // waitLoad（工具路径）：等 guest attach + dom-ready 后带真实 url/title
      // 返回——调用方（browser_navigate）拿到的是已加载完成的状态。
      if (hasUrl && waitLoad) {
        await waitForAttach(tabId).catch(() => undefined);
        const wc = entry.webContentsId != null ? getWebContents(entry) : null;
        if (wc) await settledDomReady(wc, 10000).catch(() => undefined);
        const settled = tabs.get(tabId);
        if (settled) return { tab: tabInfo({ ...settled, url: wc ? safeGetUrl(wc) || settled.url : settled.url }) };
      }
      // 无条件返回：缺省返回会让 Python _tab({}) 造出 id="" 的空 tab，
      // 模型据此 close/select 空 id → "Unknown tab:"
      return { tab: tabInfo(tabs.get(tabId)!) };
    },
    async closeAll() {
      // 关闭整个浏览器：清空 tab 注册表并通知渲染层卸载全部 guest。
      // 只断 Python 侧桥接不够——控制服务器 tabs 表会留下僵尸 tab，
      // 下次 getState 仍回报它们（"关闭后仍显示 tabs"根因）。
      for (const entry of [...tabs.values()]) {
        options.getWindow()?.webContents.send('browser-tab-command', { kind: 'close', tabId: entry.id });
      }
      tabs.clear();
      activeTabId = null;
      return {};
    },
    async closeTab(payload) {
      const tabId = String(payload.tabId ?? '');
      const entry = tabs.get(tabId);
      if (!entry) throw new Error(`Unknown tab: ${tabId}`);
      sendTabCommand({ kind: 'close', tabId });
      tabs.delete(tabId);
      if (activeTabId === tabId) {
        activeTabId = [...tabs.keys()][0] ?? null;
        if (activeTabId) sendTabCommand({ kind: 'select', tabId: activeTabId });
      }
      return {};
    },
    async selectTab(payload) {
      const tabId = String(payload.tabId ?? '');
      const entry = tabs.get(tabId);
      if (!entry) throw new Error(`Unknown tab: ${tabId}`);
      activeTabId = tabId;
      for (const other of tabs.values()) other.active = other.id === tabId;
      sendTabCommand({ kind: 'select', tabId });
      return { tab: tabInfo(entry) };
    },
    async getActiveTab() {
      // pending tab（guest 未 attach）也是合法激活 tab：建 tab 与 attach 之间
      // 的窗口内，tool 的 get_active_tab 不该抛 "No open tabs"。截屏/输入类
      // op 仍由 requireWc 给出真实的 not-ready 错误。
      const entry = activeTabId ? tabs.get(activeTabId) : null;
      if (!entry) {
        throw new Error('No open tabs. Navigate from the panel address bar first.');
      }
      return { tab: tabInfo(entry) };
    },
    async navigate(payload) {
      const tabId = String(payload.tabId ?? '');
      const url = String(payload.url ?? 'about:blank');
      // 等待 guest attach（渲染层建 guest + did-attach 回报）；pending tab
      // 在此等待而非报错。等不到再抛，快失败不长时间挂起。
      const entry0 = tabs.get(tabId);
      if (!entry0) throw new Error(`Unknown tab: ${tabId}`);
      if (entry0.webContentsId == null) {
        try {
          await waitForAttach(tabId, ATTACH_TIMEOUT_MS);
        } catch {
          throw new Error(`Tab ${tabId} guest is not ready (browser panel may be closed)`);
        }
      }
      const wc = requireWc(tabId);
      // settleNavigation 语义：loadURL 带 10s 上限，超时返回结构化
      // 错误（Python 侧永远不该等到自己的 HTTP 超时——那会变成假象的
      // "bridge unreachable"）。等待完整 load 事件（≥ domcontentloaded，
      // 与工具契约一致）；ERR_ABORTED 按已提交确认兜底。
      const previousUrl = safeGetUrl(wc);
      try {
        await withTimeout(wc.loadURL(url), NAVIGATE_TIMEOUT_MS, 'Navigation');
      } catch (exc) {
        const message = exc instanceof Error ? exc.message : String(exc);
        if (message.includes('ERR_ABORTED') || message.includes('(-3)')) {
          if (await confirmNavigationCommitted(wc, previousUrl)) {
            activateTab(tabId);
            await refreshTitle(wc, tabId);
            const entryOk = tabs.get(tabId);
            return { tab: entryOk ? tabInfo({ ...entryOk, url: safeGetUrl(wc) }) : null };
          }
        }
        throw exc;
      }
      // 导航到哪个 tab，agent 的工作面就在哪个 tab：置为 active 并通知
      // 渲染层显示它——否则坐标点击/截图的目标页与显示页错位（隐藏 guest
      // 收不到输入事件，画面层也看不到新页面）
      activateTab(tabId);
      await refreshTitle(wc, tabId);
      const entry = tabs.get(tabId);
      return { tab: entry ? tabInfo({ ...entry, url: safeGetUrl(wc) }) : null };
    },
    async reload(payload) {
      const wc = requireWc(payload.tabId ? String(payload.tabId) : undefined);
      await withTimeout(
        new Promise<void>((resolve) => { wc.once('did-finish-load', resolve); wc.reload(); }),
        NAVIGATE_TIMEOUT_MS, 'reload',
      );
      return {};
    },
    async goBack(payload) {
      const wc = requireWc(payload.tabId ? String(payload.tabId) : undefined);
      if (wc.navigationHistory?.canGoBack()) wc.navigationHistory.goBack();
      else if (wc.canGoBack()) wc.goBack();
      return {};
    },
    async goForward(payload) {
      const wc = requireWc(payload.tabId ? String(payload.tabId) : undefined);
      if (wc.navigationHistory?.canGoForward()) wc.navigationHistory.goForward();
      else if (wc.canGoForward()) wc.goForward();
      return {};
    },
    async click(payload) {
      const wc = await awaitWc(payload.tabId ? String(payload.tabId) : undefined);
      const ref = typeof payload.ref === 'string' ? payload.ref : null;
      let x = typeof payload.x === 'number' ? payload.x : null;
      let y = typeof payload.y === 'number' ? payload.y : null;
      if (ref) {
        // ref → 元素中心坐标（快照 ref 契约：data-illusion-ref 属性）
        const validRef = requireRef(ref);
        const rect = await wc.executeJavaScript(`(() => {
          const el = document.querySelector('[data-illusion-ref="${validRef}"]');
          if (!el) return null;
          el.scrollIntoView({ block: 'center', inline: 'center' });
          const r = el.getBoundingClientRect();
          return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
        })()`, false);
        if (!rect) throw new Error(`Ref ${ref} not found (stale snapshot). Run browser_snapshot again.`);
        x = rect.x;
        y = rect.y;
      }
      if (x == null || y == null) throw new Error('click requires either ref or x/y coordinates');
      dispatchMouseClick(wc, x, y, payload.double === true ? 2 : 1);
      return {};
    },
    async hover(payload) {
      const wc = await awaitWc(payload.tabId ? String(payload.tabId) : undefined);
      // 悬停（拾取模式先移动再点击：页面选择器脚本依赖 mousemove 记录 hovered，
      // 缺少悬停时首击会被判为取消）
      wc.sendInputEvent({
        type: 'mouseMove',
        x: Number(payload.x ?? 0),
        y: Number(payload.y ?? 0),
      });
      return {};
    },
    async type(payload) {
      const wc = await awaitWc(payload.tabId ? String(payload.tabId) : undefined);
      const text = String(payload.text ?? '');
      const ref = typeof payload.ref === 'string' ? payload.ref : null;
      if (ref) {
        // 聚焦 + 可输入校验（防止"Typed N chars"假成功：link/button 等非
        // 可输入元素聚焦点击可能触发导航，文本静默丢失）
        const validRef = requireRef(ref);
        const check = await wc.executeJavaScript(`(() => {
          const el = document.querySelector('[data-illusion-ref="${validRef}"]');
          if (!el) return { exists: false };
          el.scrollIntoView({ block: 'center' });
          const tag = el.tagName.toLowerCase();
          const editable = el.isContentEditable || tag === 'textarea' || tag === 'select'
            || (tag === 'input' && (el.getAttribute('type') || 'text').toLowerCase() !== 'hidden');
          const disabled = el.disabled === true || el.getAttribute('aria-disabled') === 'true';
          if (editable && !disabled) el.focus();
          return { exists: true, tag, editable, disabled,
                   focused: document.activeElement === el };
        })()`, false);
        if (!check || !check.exists) {
          throw new Error(`Ref ${ref} not found (stale snapshot). Run browser_snapshot again.`);
        }
        if (check.disabled) {
          throw new Error(`Ref ${ref} points to a disabled <${check.tag}> element — typing is not possible. Check the page state with browser_snapshot.`);
        }
        if (!check.editable) {
          throw new Error(`Ref ${ref} points to a non-typeable <${check.tag}> element. browser_type only accepts input/textarea/select/contenteditable targets; use browser_click for links and buttons.`);
        }
        if (!check.focused) {
          throw new Error(`Ref ${ref} found but could not be focused. Run browser_snapshot again.`);
        }
      } else {
        // 无 ref：校验当前聚焦元素可输入（追加语义）
        const focused = await wc.executeJavaScript(`(() => {
          const el = document.activeElement;
          if (!el) return { tag: 'none', editable: false };
          const tag = el.tagName.toLowerCase();
          const editable = el.isContentEditable || tag === 'textarea' || tag === 'select'
            || (tag === 'input' && (el.getAttribute('type') || 'text').toLowerCase() !== 'hidden');
          return { tag, editable };
        })()`, false);
        if (!focused || !focused.editable) {
          throw new Error(`No focused editable element (active element is <${focused?.tag ?? 'none'}>). Pass a ref from browser_snapshot to focus a textbox first (note: typing without ref APPENDS to the current value).`);
        }
      }
      // 键入前状态（选区/既有值长度/标签）：与托管端同一 note 语义。
      // execCommand('insertText') 与受控输入兼容且原生消费选区（全选后输入=替换）；
      // 失败时回退 setRangeText（选区替换 / 末尾插入，与真实键盘一致）
      const typeResult = await wc.executeJavaScript(`(() => {
        const el = document.activeElement;
        if (!el) return { ok: false, sel: false, valueLen: -1, tag: 'none' };
        const tag = el.tagName.toLowerCase();
        const selStart = typeof el.selectionStart === 'number' ? el.selectionStart : -1;
        const selEnd = typeof el.selectionEnd === 'number' ? el.selectionEnd : -1;
        // 注入脚本禁止 TS 非空断言（!. 会原样进入页面 JS 即语法错误）
        const selObj = window.getSelection ? window.getSelection() : null;
        const hadSelection = selStart >= 0 ? selStart !== selEnd
          : !!(selObj && !selObj.isCollapsed);
        const valueLen = typeof el.value === 'string' ? el.value.length : -1;
        if (document.execCommand && document.execCommand('insertText', false, ${JSON.stringify(text)})) {
          return { ok: true, sel: hadSelection, valueLen, tag };
        }
        const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
          : (el instanceof HTMLInputElement ? HTMLInputElement.prototype : null);
        if (proto && 'value' in el && typeof el.setRangeText === 'function') {
          if (hadSelection) el.setRangeText(${JSON.stringify(text)}, selStart, selEnd, 'end');
          else el.setRangeText(${JSON.stringify(text)}, el.value.length, el.value.length, 'end');
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          return { ok: true, sel: hadSelection, valueLen, tag };
        }
        return { ok: false, sel: hadSelection, valueLen, tag };
      })()`, false);
      if (!typeResult || !typeResult.ok) throw new Error('No focused editable element to type into');
      if (payload.submit === true) {
        // 留出一拍让页面的 input 事件处理完，再发 Enter（紧贴的 Enter 会被
        // 受控输入的重渲染吞掉——submit 不触发的根因）
        await new Promise((resolve) => setTimeout(resolve, 50));
        dispatchKey(wc, 'Enter');
      }
      const chars = text.length;
      let note: string;
      if (typeResult.sel) note = `replaced selection with ${chars} chars`;
      else if (!ref && typeof typeResult.valueLen === 'number' && typeResult.valueLen > 0) {
        note = `appended ${chars} chars to existing value (${typeResult.valueLen} chars) — `
          + 'to replace the whole value: focus the field (browser_type with ref), '
          + 'press Control+a (browser_press_key), then type again';
      } else if (ref) note = `typed ${chars} chars into <${typeResult.tag}>`;
      else note = `typed ${chars} chars into focused <${typeResult.tag}>`;
      return { note };
    },
    async pressKey(payload) {
      const wc = await awaitWc(payload.tabId ? String(payload.tabId) : undefined);
      const key = String(payload.key ?? '');
      // 未知键名拒绝（与托管端 Keyboard.press 同语义），杜绝假成功
      const main = key.split('+').map((p) => p.trim()).filter(Boolean)
        .filter((p) => !/^(control|ctrl|meta|command|cmd|alt|option|shift)$/i.test(p)).pop() ?? '';
      const KNOWN_KEYS = new Set([
        'Enter', 'Return', 'Tab', 'Escape', 'Esc', 'Backspace', 'Delete', 'Space',
        'Home', 'End', 'PageUp', 'PageDown', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
        'Up', 'Down', 'Left', 'Right', 'Insert', 'Clear', 'Cancel', 'Help',
        'CapsLock', 'NumLock', 'ScrollLock', 'Pause', 'PrintScreen', 'Unidentified',
      ]);
      if (!main
        || (main.length !== 1 && !KNOWN_KEYS.has(main) && !/^F([1-9]|1[0-2])$/.test(main))) {
        throw new Error(
          `Unknown key: "${main || key}" — use a single character (e.g. "a", "1") `
          + 'or a named key (Enter, Tab, Escape, Backspace, ArrowUp/Down/Left/Right, '
          + 'Home, End, PageUp, PageDown, Space, F1-F12), optionally with Control/Alt/Shift/Meta prefixes',
        );
      }
      // select-all 语义键：焦点不在可编辑元素上时原生全选会静默无效——
      // 与托管端同语义，先给出可行动的错误而非假成功
      if (/^(control|meta|ctrl)\+a$/i.test(key.trim())) {
        const editable = await wc.executeJavaScript(`(() => {
          const el = document.activeElement;
          if (!el) return false;
          const tag = el.tagName.toLowerCase();
          return el.isContentEditable || tag === 'textarea'
            || (tag === 'input'
                && (el.getAttribute('type') || 'text').toLowerCase() !== 'hidden');
        })()`, false);
        if (!editable) {
          throw new Error(
            'select-all (Control+a) requires a focused editable element '
            + '(textbox/textarea/contenteditable); the active element is not editable, '
            + 'so the selection would be silently lost. Focus an element first '
            + '(browser_type with ref, or browser_click on the field).',
          );
        }
      }
      dispatchKey(wc, key);
      // 全选确定性兜底：修饰键事件可能被页面吞掉（选区未建立），聚焦元素
      // 可编辑时直接 execCommand selectAll，保证"全选后输入=替换"可用
      if (/^(control|meta|ctrl)\+a$/i.test(key.trim())) {
        await wc.executeJavaScript(`(() => {
          const el = document.activeElement;
          if (!el) return false;
          if (typeof el.selectionStart === 'number') {
            if (el.selectionStart !== el.selectionEnd) return true;
            el.setSelectionRange(0, el.value.length);
            return true;
          }
          const sel = window.getSelection();
          if (sel && !sel.isCollapsed) return true;
          if (el.isContentEditable && document.execCommand) return document.execCommand('selectAll');
          return false;
        })()`, false);
      }
      return {};
    },
    async scroll(payload) {
      const wc = await awaitWc(payload.tabId ? String(payload.tabId) : undefined);
      const dx = Number(payload.dx ?? 0);
      const dy = Number(payload.dy ?? 0);
      const readPos = () => wc.executeJavaScript('(() => ({ x: window.scrollX, y: window.scrollY }))()', false);
      const moved = (a: { x: number; y: number }, b: { x: number; y: number }) =>
        Math.abs(a.x - b.x) > 0.5 || Math.abs(a.y - b.y) > 0.5;
      const before = await readPos();
      // 原生滚轮优先：可作用于光标下的内嵌滚动容器。但 Electron 滚轮 delta
      // 的符号与幅度遵循平台原生约定（Windows 正值=向上），与 Web 约定相反
      // 且随页面缩放失真——滚完后按"请求位移 − 实际位移"用 scrollBy 精确
      // 补差，方向与落点由确定性路径锁定，不依赖滚轮符号约定
      wc.sendInputEvent({ type: 'mouseWheel', x: Number(payload.x ?? 0), y: Number(payload.y ?? 0), deltaX: dx, deltaY: dy });
      await new Promise((r) => setTimeout(r, 150));
      let after = await readPos();
      const remX = dx - (after.x - before.x);
      const remY = dy - (after.y - before.y);
      if (Math.abs(remX) > 0.5 || Math.abs(remY) > 0.5) {
        await wc.executeJavaScript(`window.scrollBy(${remX}, ${remY})`, false);
        await new Promise((r) => setTimeout(r, 100));
        after = await readPos();
      }
      if (!moved(before, after)) {
        const direction = Math.abs(dx) >= Math.abs(dy) ? 'horizontal' : 'vertical';
        throw new Error(`Scroll had no effect: the page did not move in the ${direction} direction (dx=${dx}, dy=${dy}); there is likely nothing to scroll here. Verify the page state with browser_snapshot instead of retrying.`);
      }
      return { note: `scrolled to (${Math.round(after.x)}, ${Math.round(after.y)})` };
    },
    async screenshot(payload) {
      const wc = await awaitWc(payload.tabId ? String(payload.tabId) : undefined);
      // 新 attach 的 guest 可能还没有首帧合成面，capturePage 会长时间悬空——
      // 先 invalidate 触发重绘，再限时捕获；超时给结构化错误而非 35s 悬挂
      try {
        wc.invalidate();
      } catch { /* 销毁竞态忽略 */ }
      const image = await withTimeout(
        wc.capturePage(),
        CAPTURE_TIMEOUT_MS,
        'capture',
      ).catch(async (exc) => {
        // 一次重试：再失效一帧后等待一小拍（合成器冷启动）
        try { wc.invalidate(); } catch { /* ignore */ }
        await new Promise((resolve) => setTimeout(resolve, 150));
        return wc.capturePage();
      });
      const jpeg = image.toJPEG(80);
      return { jpegBase64: jpeg.toString('base64') };
    },
    async snapshot(payload) {
      const wc = await awaitWc(payload.tabId ? String(payload.tabId) : undefined);
      const result = await wc.executeJavaScript(SNAPSHOT_SCRIPT, false);
      return result;
    },
    async evaluate(payload) {
      const wc = requireWc(payload.tabId ? String(payload.tabId) : undefined);
      // 表达式必须"调用起来"：原样执行 `() => 'ping'` 求值结果是函数对象，
      // CDP returnByValue 克隆不了函数 → "An object could not be cloned"
      // （与页面无关，任何站点都中招）。包装规则与托管端 _wrap_expression
      // 对齐：函数形态 IIFE 调用；语句形态包块；裸表达式取地址值。
      const expr = String(payload.expression ?? 'null');
      // undefined 语义保留（与托管端同款 ser）：CDP/JSON 序列化会把
      // undefined 顶层变 null、对象内属性静默丢弃——JS 侧递归包装为哨兵，
      // 返回后还原为字面量 `undefined` 文本。Promise.resolve 兼容同步/异步。
      // 用户代码以内联源码执行并包页内 try/catch：运行时异常以值形式带回
      // （消息 + 堆栈）。executeJavaScript 走调试通道不受页面 CSP 约束，
      // 页内的 eval/new Function 才受——严格 CSP 站点（bing/Google）必须走直呼。
      const code =
        "(async () => {" +
        " const U = '__illusion_undef_7f3a__';" +
        " const ser = (v) => {" +
        "  if (v === undefined) { const o = {}; o[U] = 1; return o; }" +
        "  if (v === null || typeof v !== 'object') return v;" +
        "  if (typeof v.toJSON === 'function') return ser(v.toJSON());" +
        "  if (Array.isArray(v)) return v.map(ser);" +
        "  const o = {};" +
        "  for (const k of Object.keys(v)) o[k] = ser(v[k]);" +
        "  return o;" +
        " };" +
        " try {" +
        "  const __v = await Promise.resolve(" + wrapEvalExpression(expr) + ");" +
        "  return ser(__v);" +
        " } catch (e) {" +
        "  return { __illusion_eval_error: String((e && e.stack) || e) };" +
        " }" +
        " })()";
      let result: unknown;
      try {
        result = await wc.executeJavaScript(code, false);
      } catch (exc) {
        // 整段脚本解析失败 = 语法错误：单独探测取具体 SyntaxError（探测用
        // new Function，严格 CSP 页面会拒绝——此时保留宿主通用消息）
        let syntax: string | null = null;
        try {
          syntax = await wc.executeJavaScript(
            `(() => { try { new Function(${JSON.stringify(wrapEvalExpression(expr))}); return null; }`
            + ` catch (e) { return String((e && e.message) || e); } })()`,
            false,
          );
        } catch { syntax = null; }
        if (typeof syntax === 'string' && syntax
          && !/Content Security Policy|unsafe-eval|violates/i.test(syntax)) {
          throw new Error(`SyntaxError: ${syntax}`);
        }
        const message = exc instanceof Error ? exc.message : String(exc);
        // 克隆失败兜底：表达式可能是语句串，包成块再跑一次
        if (!message.includes('could not be cloned')) throw exc;
        result = await wc.executeJavaScript(`(() => { ${expr} })()`, false);
      }
      if (result && typeof result === 'object' && !Array.isArray(result)
        && typeof (result as Record<string, unknown>)['__illusion_eval_error'] === 'string') {
        throw new Error(String((result as Record<string, unknown>)['__illusion_eval_error']));
      }
      let text: string;
      if (result === undefined) text = 'undefined';
      else if (result === null) text = 'null';
      else if (typeof result === 'string') text = result;
      else if (typeof result === 'object' && result !== null
        && (result as Record<string, unknown>)['__illusion_undef_7f3a__'] === 1) {
        text = 'undefined';
      } else {
        // 哨兵还原为字面量 undefined 文本（与托管端 _render_eval_result 一致）
        const seen = JSON.stringify(result, (k, v) =>
          v && typeof v === 'object' && v['__illusion_undef_7f3a__'] === 1 ? '__illusion_undef__' : v);
        text = seen.replace(/"__illusion_undef__"/g, 'undefined');
      }
      return { text };
    },
    async openDevtools(payload) {
      const wc = await awaitWc(payload.tabId ? String(payload.tabId) : undefined);
      // guest DevTools 独立窗口
      wc.openDevTools();
      return {};
    },
    async pickScript(payload) {
      const wc = await awaitWc(payload.tabId ? String(payload.tabId) : undefined);
      // 执行前端下发的拾取脚本（Promise 式：用户点击/Esc 时 resolve）
      const script = String(payload.script ?? '');
      const result = await withTimeout(
        wc.executeJavaScript(script, false),
        180000,
        'element picking',
      );
      return { element: result };
    },
    async pickCancel(payload) {
      const wc = requireWc(payload.tabId ? String(payload.tabId) : undefined);
      // 取消进行中的拾取（幂等：选择器未注入时为 no-op）
      await wc.executeJavaScript(
        '(() => { const p = window.__illusionWebElementPicker; if (p && typeof p.cancel === "function") p.cancel(); })()',
        false,
      );
      return {};
    },
    async resize(payload) {
      viewport.width = clamp(Number(payload.width ?? DEFAULT_VIEWPORT.width), 320, 3840);
      viewport.height = clamp(Number(payload.height ?? DEFAULT_VIEWPORT.height), 320, 2160);
      // 渲染进程经 browserState（getState）跟随调整 webview 元素尺寸与缩放
      return { width: viewport.width, height: viewport.height };
    },
    async wait(payload) {
      const seconds = Math.max(0, Math.min(Number(payload.seconds ?? 0), 30));
      if (payload.load === true) {
        const wc = requireWc(payload.tabId ? String(payload.tabId) : undefined);
        await withTimeout(settledDomReady(wc), seconds * 1000 + 5000, 'wait load');
      } else {
        await new Promise((r) => setTimeout(r, seconds * 1000));
      }
      return {};
    },
  };

  // ---- HTTP 控制服务器 ----

  const server = http.createServer((req, res) => {
    if (req.method !== 'POST' || req.url !== '/command') {
      res.writeHead(404).end();
      return;
    }
    if (req.headers['x-illusion-browser-token'] !== token) {
      res.writeHead(403, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'invalid token' }));
      return;
    }
    const chunks: Buffer[] = [];
    req.on('data', (c: Buffer) => chunks.push(c));
    req.on('end', async () => {
      let body: { op?: string } = {};
      try {
        body = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
      } catch {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'invalid json' }));
        return;
      }
      const op = String(body.op ?? '');
      const handler = commands[op];
      try {
        if (!handler) throw new Error(`Unknown op: ${op}`);
        const result = await handler(body as Record<string, unknown>);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, result }));
      } catch (exc) {
        const message = exc instanceof Error ? exc.message : String(exc);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: message }));
      }
    });
  });

  // listen 是异步的：端口就绪前 getUrl 会拿到 0 端口，必须在注入 env 前等待
  const listening = new Promise<void>((resolve, reject) => {
    server.once('listening', () => resolve());
    server.once('error', (err) => reject(err));
  });
  // Node 默认 keepAliveTimeout 仅 5s：Python httpx 复用池内连接时，空闲超过
  // 5s 的连接被服务端掐断，下一个命令直接 ConnectionReset（"bridge
  // unreachable"的另一来源）。拉长保活窗口；headersTimeout 必须大于它。
  server.keepAliveTimeout = 65_000;
  server.headersTimeout = 70_000;
  server.listen(0, '127.0.0.1');

  return {
    /** 等待控制服务器端口就绪（main 在读取 getUrl/env 前必须 await） */
    ready: listening,
    getUrl() {
      const addr = server.address();
      const port = typeof addr === 'object' && addr ? addr.port : 0;
      return `http://127.0.0.1:${port}`;
    },
    getToken: () => token,
    registerGuest,
    removeGuest,
    openTabFromGuest,
    getTabs(): Array<{ id: string; url: string }> {
      // 不过滤 attach 状态：窗口期内（create 已发、attach 未回）的 tab
      // 也要下发，渲染进程创建 webview 后会完成注册闭环
      return [...tabs.values()].map((e) => ({ id: e.id, url: e.url }));
    },
    close: () => server.close(),
  };
}

/** ARIA 快照注入脚本（与 Python 侧 illusion/browser/snapshot.py 的

 *  SNAPSHOT_SCRIPT 保持同步；漂移由 tests/test_browser/test_snapshot_script_sync.py 守门） */

// String.raw 保持脚本内反斜杠序列的字面值：普通模板字符串会把 \n 求值为真实换行，

// 运行时脚本即成语法错误，表现为 Script failed to execute

const SNAPSHOT_SCRIPT = String.raw`


(() => {
  // 防御性外壳：真实站点上脚本任何一处抛错（此前 bing/github 均触发
  // "Script failed to execute"）都不能让快照整体失败——捕获并随载荷返回，
  // 工具层据此向模型报告真实异常。
  try {
    return __illusionSnapshotCore();
  } catch (err) {
    return {
      url: location.href,
      title: document.title || "",
      yaml: "",
      viewport_width: window.innerWidth,
      viewport_height: window.innerHeight,
      error: String((err && err.stack) || err),
    };
  }
})();

function __illusionSnapshotCore() {
  const MAX_NODES = 1200;
  const INTERACTIVE = ['button','link','textbox','searchbox','checkbox','radio','combobox',
    'slider','spinbutton','switch','tab','option','menuitem','treeitem','listbox'];
  let nodeCount = 0;
  let truncated = false;

  // === ref 分配（防撞号）===
  // data-illusion-ref 持久化在 DOM 元素上（跨快照稳定 id），但本脚本每次
  // 执行都重新运行——若计数器从 0 起步，页面新出现的元素会拿到 e1，
  // 与仍留在 DOM 中的旧 e1 撞号（快照输出中出现同 ref 多元素，触发
  // 工具层的歧义拒绝）。修复：
  //   1. 预扫描全文档：refCounts 记录每个现存 ref 的出现次数（撞号检测），
  //      maxRefNum 取最大编号；
  //   2. 新 ref 从 maxRefNum+1 起步线性探测，绝不与现存/已分配冲突；
  //   3. 元素仅保留"全文档唯一"的现存 ref，撞号 ref 双方都重新分配。
  const usedRefs = new Set();
  const refCounts = Object.create(null);
  let maxRefNum = 0;
  try {
    for (const el of document.querySelectorAll('[data-illusion-ref]')) {
      const ref = el.getAttribute('data-illusion-ref') || '';
      const m = /^e\d+$/.test(ref);
      if (m) {
        usedRefs.add(ref);
        refCounts[ref] = (refCounts[ref] || 0) + 1;
        const n = parseInt(ref.slice(1), 10);
        if (n > maxRefNum) maxRefNum = n;
      }
    }
  } catch (e) {}
  let refCounter = maxRefNum;

  function allocRef() {
    // 线性探测找一个未占用的编号（防御手工构造的 data-illusion-ref）
    let n = refCounter + 1;
    while (usedRefs.has('e' + n)) n += 1;
    refCounter = n;
    const ref = 'e' + n;
    usedRefs.add(ref);
    refCounts[ref] = 1;
    return ref;
  }

  function isVisible(el, style) {
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = el.getBoundingClientRect();
    return !(rect.width <= 0 && rect.height <= 0);
  }

  function roleOf(el) {
    const explicit = (el.getAttribute('role') || '').trim();
    if (explicit) return explicit === 'none' || explicit === 'presentation' ? null : explicit;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'input') {
      if (type === 'hidden') return null;
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'range') return 'slider';
      if (type === 'number') return 'spinbutton';
      if (type === 'search') return 'searchbox';
      if (type === 'button' || type === 'submit' || type === 'reset' || type === 'file') return 'button';
      if (type === 'image') return 'img';
      return 'textbox';
    }
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return 'combobox';
    if (tag === 'option') return 'option';
    if (tag === 'img') return 'img';
    if (/^h([1-6])$/.test(tag)) return 'heading';
    if (tag === 'ul' || tag === 'ol' || tag === 'dl') return 'list';
    if (tag === 'li' || tag === 'dt' || tag === 'dd') return 'listitem';
    if (tag === 'nav') return 'navigation';
    if (tag === 'main') return 'main';
    if (tag === 'aside') return 'complementary';
    if (tag === 'header') return 'banner';
    if (tag === 'footer') return 'contentinfo';
    if (tag === 'dialog') return 'dialog';
    if (tag === 'table') return 'table';
    if (tag === 'tr') return 'row';
    if (tag === 'th' || tag === 'td') return 'cell';
    if (tag === 'hr') return 'separator';
    if (tag === 'progress') return 'progressbar';
    if (tag === 'output') return 'status';
    if (tag === 'fieldset') return 'group';
    if (el.isContentEditable) return 'textbox';
    return null;
  }

  function accessibleName(el, doc) {
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim().slice(0, 120);
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const parts = [];
      for (const id of labelledby.split(/\s+/)) {
        const node = doc.getElementById(id);
        if (node) parts.push((node.textContent || '').replace(/\s+/g, ' ').trim());
      }
      const joined = parts.filter(Boolean).join(' ');
      if (joined) return joined.slice(0, 120);
    }
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'img') {
      const alt = el.getAttribute('alt');
      if (alt && alt.trim()) return alt.trim().slice(0, 120);
    }
    if (tag === 'input' || tag === 'textarea') {
      const ph = el.getAttribute('placeholder');
      if (ph && ph.trim()) return ph.trim().slice(0, 120);
      if (tag === 'input' && (type === 'button' || type === 'submit' || type === 'reset')) {
        const v = el.getAttribute('value');
        if (v && v.trim()) return v.trim().slice(0, 120);
      }
    }
    if (el.id) {
      try {
        const esc = (window.CSS && CSS.escape) ? CSS.escape(el.id) : el.id;
        const lbl = doc.querySelector('label[for="' + esc + '"]');
        if (lbl) {
          const t = (lbl.textContent || '').replace(/\s+/g, ' ').trim();
          if (t) return t.slice(0, 120);
        }
      } catch (e) {}
    }
    const wrap = el.closest('label');
    if (wrap && wrap !== el) {
      const t = (wrap.textContent || '').replace(/\s+/g, ' ').trim();
      if (t) return t.slice(0, 120);
    }
    const title = el.getAttribute('title');
    if (title && title.trim()) return title.trim().slice(0, 120);
    let text = '';
    if (tag === 'select') {
      const opt = el.selectedOptions && el.selectedOptions[0];
      if (opt) text = opt.textContent || '';
    } else if (tag === 'input' || tag === 'textarea') {
      text = String(el.value || '');
    } else {
      text = el.textContent || '';
    }
    text = text.replace(/\s+/g, ' ').trim();
    return text ? text.slice(0, 120) : '';
  }

  function quote(name) { return JSON.stringify(name); }

  function ensureRef(el) {
    const existing = el.getAttribute('data-illusion-ref');
    // 仅当元素持有"全文档唯一"的现存 ref 时保留（跨快照稳定）；
    // 无 ref 或撞号 ref（refCounts > 1）→ 重新分配
    if (existing && refCounts[existing] === 1) {
      return existing;
    }
    const ref = allocRef();
    try { el.setAttribute('data-illusion-ref', ref); } catch (e) {}
    return ref;
  }

  function elementLine(el, role, name, depth) {
    const attrs = [];
    const tag = el.tagName.toLowerCase();
    if (role === 'heading') {
      const level = /^h([1-6])$/.test(tag) ? tag[1] : (el.getAttribute('aria-level') || '1');
      attrs.push('[level=' + level + ']');
    }
    if (['checkbox','radio','switch','option','menuitemcheckbox','menuitemradio'].includes(role)) {
      const checked = el.checked !== undefined ? !!el.checked : el.getAttribute('aria-checked') === 'true';
      if (checked) attrs.push('[checked]');
    }
    const expanded = el.getAttribute('aria-expanded');
    if (expanded === 'true') attrs.push('[expanded]');
    if (expanded === 'false') attrs.push('[collapsed]');
    if (role === 'option' && el.selected) attrs.push('[selected]');
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') attrs.push('[disabled]');
    if (el.getAttribute('aria-invalid') === 'true') attrs.push('[invalid]');
    attrs.push('[ref=' + ensureRef(el) + ']');
    const namePart = name ? ' ' + quote(name) : '';
    return '  '.repeat(depth) + '- ' + role + namePart + ' ' + attrs.join(' ');
  }

  function emitText(text, depth, out) {
    const t = text.replace(/\s+/g, ' ').trim();
    if (t) out.push('  '.repeat(depth) + '- text: ' + quote(t.slice(0, 200)));
  }

  function walk(el, depth, out, doc) {
    if (nodeCount >= MAX_NODES) { truncated = true; return; }
    if (el.nodeType !== 1) return;
    const tag = el.tagName.toLowerCase();
    if (['script','style','noscript','template','head','meta','link','title','base'].includes(tag)) return;
    if (el.getAttribute('aria-hidden') === 'true') return;
    if (tag === 'svg' || tag === 'canvas') {
      // 画布/矢量图产出占位行，让模型感知其存在（快照看不到其内部内容）
      nodeCount += 1;
      const name = el.getAttribute('aria-label') || el.getAttribute('title') || '';
      out.push('  '.repeat(depth) + '- ' + (tag === 'svg' ? 'image' : 'canvas') +
        (name ? ' ' + quote(name) : '') + ' [ref=' + ensureRef(el) + ']');
      return;
    }
    let style;
    try { style = getComputedStyle(el); } catch (e) { return; }
    if (!isVisible(el, style)) return;

    if (tag === 'iframe') {
      nodeCount += 1;
      let child = null;
      try { child = el.contentDocument; } catch (e) { child = null; }
      if (child && child.body) {
        out.push('  '.repeat(depth) + '- iframe:');
        for (const c of child.body.children) walk(c, depth + 1, out, child);
      } else {
        out.push('  '.repeat(depth) + '- iframe [cross-origin]');
      }
      return;
    }

    const role = roleOf(el);
    if (role) {
      nodeCount += 1;
      out.push(elementLine(el, role, accessibleName(el, doc), depth));
      // 原生 select 额外展开 option 列表（可逐项点选）
      if (role === 'combobox' && tag === 'select') {
        for (const opt of el.options || []) {
          if (nodeCount >= MAX_NODES) { truncated = true; break; }
          nodeCount += 1;
          const oname = (opt.textContent || '').replace(/\s+/g, ' ').trim();
          out.push('  '.repeat(depth + 1) + '- option ' + quote(oname) +
            (opt.selected ? ' [selected]' : '') + ' [ref=' + ensureRef(opt) + ']');
        }
      }
      // 可交互叶子元素不再下钻（其内容已由可访问名概括）
      if (INTERACTIVE.includes(role) && role !== 'listbox' && role !== 'option') return;
      for (const c of el.children) walk(c, depth + 1, out, doc);
      return;
    }
    // 通用容器：不产出节点行，直接下钻；仅当其携带直接文本时输出 text 行
    let directText = '';
    for (const n of el.childNodes) {
      if (n.nodeType === 3) directText += n.textContent || '';
    }
    if (el.children.length === 0) {
      nodeCount += 1;
      emitText(directText, depth, out);
      return;
    }
    if (directText.trim()) emitText(directText, depth, out);
    for (const c of el.children) walk(c, depth + 1, out, doc);
  }

  const out = [];
  for (const c of (document.body ? document.body.children : [])) walk(c, 0, out, document);
  let yaml = out.join('\n');
  if (truncated) yaml += '\n- text: "[snapshot truncated: page too large - narrow scope or scroll]"';
  return {
    url: location.href,
    title: document.title || '',
    yaml,
    viewport_width: window.innerWidth,
    viewport_height: window.innerHeight,
  };
}

`;


