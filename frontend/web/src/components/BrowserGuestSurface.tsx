/**
 * @fileoverview 桌面内嵌浏览器 guest 视图（webview 内联渲染的命令式实现）
 *
 * guest `<webview>` 渲染在本组件树内，human 动作
 * 直驱 webview 方法（低延迟、无握手依赖）；agent 控制面不变（did-attach
 * 回报 webContentsId 给主进程挂 CDP）。
 *
 * 关键约束——webview 元素命令式管理，React 永不触碰节点：
 * guest 对 reconciliation 极度敏感（ref 换身份/src 重设都会重建 guest），
 * React 只拥有容器 div；webview 创建一次、事件绑定一次。
 *
 * @module BrowserGuestSurface
 */

import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react';
import { PICK_CANCEL_SCRIPT, PICK_ELEMENT_SCRIPT } from '../lib/webElementPickerScript';

/** TSX 不识别 webview 标签，命令式创建 */
const WEBVIEW_TAG = 'webview';
const PARTITION = 'persist:illusion-browser';

/** 对外暴露的 human 直驱动作（webview 方法直调） */
export interface BrowserGuestHandle {
  openUrl: (url: string) => void;
  back: () => void;
  forward: () => void;
  reload: () => void;
  openDevTools: () => void;
}

/** chrome 状态回传 */
interface ChromeState {
  tabId: string;
  url: string;
  canGoBack: boolean;
  canGoForward: boolean;
  loading: boolean;
  error: string | null;
  navigated: boolean;
}

interface BrowserGuestSurfaceProps {
  /** 激活 guest id（后端状态同源；未匹配时回落第一个） */
  activeTabId: string | null;
  /** 后端 tab 元数据（初始同步 + URL 兜底源） */
  tabs: Array<{ id: string; url: string }>;
  /** 自由尺寸：由外层 frame 控制尺寸，容器填满逻辑视口 */
  responsive: boolean;
  /** 元素选择模式（激活 guest 注入拾取脚本） */
  pickMode: boolean;
  /** 拾取结果（element；null = 取消） */
  onPickResult?: (info: Record<string, unknown> | null) => void;
  /** 本地导航完成（通知父级刷新后端状态） */
  onNavigated?: (tabId: string, url: string) => void;
  /** tab 指令处理完成（create/close 已生效）——父级借此刷新标签条状态；
   *  guest=true 表示由用户点击（target=_blank）产生的新 tab */
  onTabsChanged?: (info: { kind: 'create' | 'close'; guest: boolean }) => void;
  /** chrome 状态回传 */
  onChromeState?: (state: ChromeState) => void;
  /** human 直驱句柄（普通 prop 传 ref 对象，effect 内赋值） */
  handleRef?: MutableRefObject<BrowserGuestHandle | null>;
}

/** 命令式创建的 webview 记录 */
interface LiveGuest {
  wrapper: HTMLDivElement;
  /** did-attach 时刻（null = 尚未 attach，供看门狗判定） */
  attachedAt: number | null;
  /** 节点创建时刻 */
  mountedAt: number;
  /** 已强制重建次数（封顶防循环） */
  remounts: number;
  el: HTMLElement & {
    loadURL?: (u: string) => Promise<void>;
    goBack?: () => void;
    goForward?: () => void;
    reload?: () => void;
    openDevTools?: () => void;
    executeJavaScript?: (s: string, u?: boolean) => Promise<unknown>;
    getURL?: () => string;
    canGoBack?: () => boolean;
    canGoForward?: () => boolean;
    getWebContentsId?: () => number;
  };
}

/** tab 是否已有真实导航（about:blank/空 = 未导航） */
function hasRealNavigation(url: string): boolean {
  return !!url && url !== 'about:blank';
}

/** 安全调用未就绪 webview（detach 竞态吞掉不冒泡） */
function safeCall<T>(fn: () => T, fallback: T): T {
  try {
    return fn();
  } catch {
    return fallback;
  }
}

/**
 * 桌面内嵌 guest 表面（命令式 webview 生命周期）
 *
 * @param props - 组件属性
 * @returns 仅一个容器 div；webview 子节点由本组件命令式管理
 */
export function BrowserGuestSurface({
  activeTabId, tabs, responsive, pickMode, onPickResult, onNavigated, onTabsChanged, onChromeState, handleRef,
}: BrowserGuestSurfaceProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const liveRef = useRef<Map<string, LiveGuest>>(new Map());
  const readyRef = useRef<Set<string>>(new Set());
  const pendingUrlRef = useRef<Map<string, string>>(new Map());
  const shownRef = useRef<string | null>(null);
  // guest 列表：本组件自持订阅（面板锁存常驻 = 订阅覆盖全部 create/close）
  const [guestOrder, setGuestOrder] = useState<string[]>([]);
  // create 指令携带的首挂 URL（带 URL 建 tab：src 一次加载，无二次 navigate）
  const createUrlsRef = useRef<Map<string, string>>(new Map());

  // 主进程 tab 指令订阅 + 现存 tab 同步。面板已锁存常驻（forceMount），
  // 订阅生命周期覆盖全部 create/close；create 携带的首挂 URL 记入
  // createUrlsRef，guest 挂载时以 src 一次加载（缺此路径退化为 about:blank）。
  useEffect(() => {
    const bridge = window.illusionDesktop;
    if (!bridge?.browser) return;
    let disposed = false;
    bridge.browser.getTabs?.().then((existing) => {
      if (disposed || !existing?.length) return;
      setGuestOrder((prev) => {
        const next = [...prev];
        for (const tb of existing) {
          if (!next.includes(tb.id)) next.push(tb.id);
        }
        return next;
      });
    }).catch(() => undefined);
    const off = bridge.browser.onTabCommand((cmd: { kind: string; tabId: string; url?: string; source?: string }) => {
      const known = cmd.kind === 'create' || cmd.kind === 'close';
      if (cmd.kind === 'create') {
        if (cmd.url && cmd.url !== 'about:blank') createUrlsRef.current.set(cmd.tabId, cmd.url);
        else createUrlsRef.current.delete(cmd.tabId);
        setGuestOrder((prev) => (prev.includes(cmd.tabId) ? prev : [...prev, cmd.tabId]));
      } else if (cmd.kind === 'close') {
        createUrlsRef.current.delete(cmd.tabId);
        setGuestOrder((prev) => prev.filter((id) => id !== cmd.tabId));
      } else {
        return;
      }
      // create/close 已受理：通知父级拉帧 + 推状态（手动 target=_blank 新 tab
      // 由此即时进入顶栏标签条，不必等下一次工具调用）
      tabsChangedCbRef.current?.({
        kind: cmd.kind as 'create' | 'close',
        guest: known && cmd.source === 'guest',
      });
    });
    return () => { disposed = true; off(); };
  }, []);

  // 稳定回调引用：父组件每次渲染的新函数绝不触发重渲染/重绑定
  const chromeCbRef = useRef(onChromeState);
  chromeCbRef.current = onChromeState;
  const navCbRef = useRef(onNavigated);
  navCbRef.current = onNavigated;
  const pickCbRef = useRef(onPickResult);
  pickCbRef.current = onPickResult;
  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;
  const tabsChangedCbRef = useRef(onTabsChanged);
  tabsChangedCbRef.current = onTabsChanged;

  /** chrome 状态回报（仅"正在显示"的 guest）。
   *  非显示 guest 的加载事件是工具栏噪声：多 tab 并存时后台加载会把旧地址/
   *  加载态写进当前工具栏。url 仅在导航事件显式携带时传递——did-start-loading
   *  的 getURL() 是旧地址，途中回写会让地址栏跳变（悬停抖动根因）。 */
  const emitChrome = useCallback((tabId: string, patch: Partial<ChromeState> = {}) => {
    if (tabId !== shownRef.current) return;
    const entry = liveRef.current.get(tabId);
    const explicitUrl = 'url' in patch && typeof patch.url === 'string' ? patch.url : '';
    chromeCbRef.current?.({
      tabId,
      url: explicitUrl,
      canGoBack: patch.canGoBack ?? safeCall(() => entry?.el.canGoBack?.() ?? false, false),
      canGoForward: patch.canGoForward ?? safeCall(() => entry?.el.canGoForward?.() ?? false, false),
      loading: patch.loading ?? false,
      error: patch.error ?? null,
      navigated: patch.navigated ?? (explicitUrl ? hasRealNavigation(explicitUrl) : false),
    });
  }, []);


  // 显示的 guest：激活 id 优先，否则第一个
  const shownTabId = activeTabId && guestOrder.includes(activeTabId)
    ? activeTabId
    : (guestOrder[0] ?? null);
  shownRef.current = shownTabId;

  /** 显示切换（命令式：React 从不重设 webview 的任何属性）。
   *  非激活 guest 必须保持"可见 + opacity 0.001"（与卡片折叠保活同一配方）：
   *  visibility:hidden 会让 webview 不 attach、capturePage 无合成面——
   *  agent 新开的后台 tab 将永远连不上（"guest is not ready"根因） */
  const applyActive = useCallback(() => {
    for (const [tabId, live] of liveRef.current) {
      const active = tabId === shownRef.current;
      live.wrapper.style.visibility = 'visible';
      live.wrapper.style.pointerEvents = active ? 'auto' : 'none';
      live.el.style.opacity = active ? '1' : '0.001';
    }
  }, []);

  /** 为单个 guest 建节点并一次性绑定事件（此后 React 不再触碰该节点） */
  const mountGuest = useCallback((tabId: string, url: string): void => {
    const container = containerRef.current;
    if (!container) return;

    const wrapper = document.createElement('div');
    Object.assign(wrapper.style, {
      position: 'absolute', inset: '0', visibility: 'hidden', pointerEvents: 'none',
    });

    const el = document.createElement(WEBVIEW_TAG) as LiveGuest['el'];
    el.setAttribute('partition', PARTITION);
    // allowpopups 必须以"存在即真"的属性、且在 attach 前声明（布尔属性设
    // "false" 字符串同样为真——此前写 'false' 实际是开启弹窗，target=_blank
    // 直开 OS 窗口）。开启后 window.open 到达主进程的 window-open 处理器，
    // 由其转换为内置新 tab（弹层→标签页，绝不弹独立窗口）
    el.setAttribute('allowpopups', '');
    el.setAttribute('src', url || 'about:blank');
    Object.assign(el.style, {
      width: '100%', height: '100%', display: 'flex',
      // 导航完成前保持透明：新 guest 的白底首帧不得在页面绘制前露出
      //（导航瞬间全屏闪一下的根因）；首个 dom-ready 后恢复白底画布
      background: 'transparent',
    });
    el.addEventListener('dom-ready', () => { el.style.background = '#ffffff'; }, { once: true });

    const onNavigate = () => {
      const url = safeCall(() => el.getURL?.() ?? '', '');
      emitChrome(tabId, { url, loading: false, navigated: hasRealNavigation(url) });
      navCbRef.current?.(tabId, url);
    };
    const onFailLoad = (e: Event) => {
      const detail = ((e as CustomEvent).detail ?? {}) as {
        isMainFrame?: boolean; errorCode?: number; errorDescription?: string; validatedURL?: string;
      };
      if (detail.isMainFrame === false || detail.errorCode === -3) return; // 忽略子帧与 -3
      emitChrome(tabId, {
        url: detail.validatedURL ?? '', loading: false,
        error: detail.errorDescription || `Load failed (${detail.errorCode})`,
        navigated: false,
      });
    };

    // 事件（每个节点只绑一次）
    el.addEventListener('did-attach', () => {
      const rec = liveRef.current.get(tabId);
      if (rec) rec.attachedAt = Date.now();
      const wcId = safeCall(() => el.getWebContentsId?.() ?? 0, 0);
      if (wcId) window.illusionDesktop?.browser?.guestAttached(tabId, wcId);
    });
    el.addEventListener('dom-ready', () => {
      readyRef.current.add(tabId);
      const pending = pendingUrlRef.current.get(tabId);
      if (pending) {
        pendingUrlRef.current.delete(tabId);
        // 排队 URL 在 dom-ready 消费（<webview> src 只在首挂生效）
        el.loadURL?.(pending)?.catch(() => undefined);
        return;
      }
      emitChrome(tabId, { loading: false });
    });
    el.addEventListener('did-navigate', onNavigate);
    el.addEventListener('did-navigate-in-page', onNavigate);
    el.addEventListener('did-start-loading', () => emitChrome(tabId, { loading: true }));
    // did-stop-loading 是 Electron 的权威"加载结束"信号（主帧任一加载完成），
    // 与 did-navigate/dom-ready 互为兜底——缺了它刷新按钮会转个不停
    el.addEventListener('did-stop-loading', () => emitChrome(tabId, { loading: false }));
    el.addEventListener('did-fail-load', onFailLoad);

    wrapper.appendChild(el);
    container.appendChild(wrapper);
    liveRef.current.set(tabId, { attachedAt: null, el, mountedAt: Date.now(), remounts: 0, wrapper });
  }, [emitChrome]);

  // ---- guest 生命周期同步：只创建/移除，绝不重建 ----
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    for (const tabId of guestOrder) {
      if (liveRef.current.has(tabId)) continue;
      const backend = tabsRef.current.find((tb) => tb.id === tabId);
      const initial = createUrlsRef.current.get(tabId)
        ?? backend?.url ?? 'about:blank';
      mountGuest(tabId, initial);
    }
    for (const [tabId, live] of [...liveRef.current]) {
      if (guestOrder.includes(tabId)) continue;
      live.el.remove?.();
      live.wrapper.remove();
      liveRef.current.delete(tabId);
      readyRef.current.delete(tabId);
      pendingUrlRef.current.delete(tabId);
      // 回报主进程清理注册表（否则注册表残留不可 attached 的僵尸 tab，
      // tabs list 计数与画面不一致、"tab N 已断开"）
      window.illusionDesktop?.browser?.guestClosed?.(tabId);
    }
    applyActive();
  }, [guestOrder, mountGuest, applyActive]);

  // 组件卸载 = guest 全部销毁（弹窗查看/会话切换）——逐个回报，注册表清零
  useEffect(() => () => {
    for (const tabId of [...liveRef.current.keys()]) {
      window.illusionDesktop?.browser?.guestClosed?.(tabId);
    }
  }, []);

  // 激活切换 → 仅切 wrapper 可见性
  useEffect(() => { applyActive(); }, [shownTabId, applyActive]);

  // attach 看门狗：节点插入后超过宽限期仍未 did-attach 的 guest（弹层转换、
  // guest 生命周期偶发卡死）强制重建一次节点自愈，而不是留下永远
  // "未连接"的僵尸标签页；重建次数封顶，防住真无法 attach 的循环
  useEffect(() => {
    const timer = setInterval(() => {
      const now = Date.now();
      let remounted = false;
      for (const [tabId, live] of [...liveRef.current]) {
        if (live.attachedAt != null || live.remounts >= 2) continue;
        if (now - live.mountedAt < 5000) continue;
        live.el.remove?.();
        live.wrapper.remove();
        liveRef.current.delete(tabId);
        const url = createUrlsRef.current.get(tabId)
          ?? tabsRef.current.find((tb) => tb.id === tabId)?.url
          ?? 'about:blank';
        mountGuest(tabId, url);
        const fresh = liveRef.current.get(tabId);
        if (fresh) fresh.remounts = live.remounts + 1;
        remounted = true;
      }
      if (remounted) applyActive();
    }, 2000);
    return () => clearInterval(timer);
  }, [mountGuest, applyActive]);

  // ---- human 直驱动作 ----
  const withActive = useCallback((action: (el: LiveGuest['el']) => void) => {
    const tabId = shownRef.current;
    if (!tabId || !readyRef.current.has(tabId)) return;
    const live = liveRef.current.get(tabId);
    if (!live) return;
    try { action(live.el); } catch { /* detach 竞态：忽略 */ }
  }, []);

  const openUrl = useCallback((url: string) => {
    const tabId = shownRef.current;
    if (!tabId) return;
    withActive((el) => {
      emitChrome(tabId, { loading: true });
      el.loadURL?.(url)?.catch((error: unknown) => {
        const message = error instanceof Error ? error.message : String(error);
        if (message.includes('ERR_ABORTED')) return; // 被后续导航打断
        emitChrome(tabId, { url, loading: false, error: message, navigated: false });
      });
    });
    // guest 未就绪：排队，dom-ready 消费
    if (!readyRef.current.has(tabId)) pendingUrlRef.current.set(tabId, url);
  }, [emitChrome, withActive]);

  const back = useCallback(() => withActive((el) => el.goBack?.()), [withActive]);
  const forward = useCallback(() => withActive((el) => el.goForward?.()), [withActive]);
  const reload = useCallback(() => withActive((el) => el.reload?.()), [withActive]);
  const openDevTools = useCallback(() => withActive((el) => el.openDevTools?.()), [withActive]);

  // 暴露 human 直驱句柄（父组件经 handleRef prop 持有）
  useEffect(() => {
    if (!handleRef) return;
    handleRef.current = { openUrl, back, forward, reload, openDevTools };
    return () => { handleRef.current = null; };
  }, [handleRef, openUrl, back, forward, reload, openDevTools]);

  // ---- 元素拾取（注入一次 Promise 回结果）----
  const pickActiveRef = useRef(false);
  // 切换 tab 时：旧 guest 的选择态必须取消（注入取消脚本 + 复位在途标记），
  // 否则旧页面的 picker 永久驻留、新 tab 因 pickActiveRef 挂起无法注入
  //（拾取在 tab 切换后"死锁"根因）
  const prevShownRef = useRef<string | null>(null);
  useEffect(() => {
    const prev = prevShownRef.current;
    prevShownRef.current = shownTabId;
    if (!pickActiveRef.current || prev === shownTabId || !prev) return;
    const old = liveRef.current.get(prev);
    if (old && typeof old.el.executeJavaScript === 'function') {
      old.el.executeJavaScript(PICK_CANCEL_SCRIPT, false).catch(() => undefined);
    }
    pickActiveRef.current = false;
  }, [shownTabId]);

  useEffect(() => {
    const tabId = shownRef.current;
    if (!tabId || !pickMode || !readyRef.current.has(tabId)) return;
    const live = liveRef.current.get(tabId);
    if (!live || typeof live.el.executeJavaScript !== 'function') return;
    if (pickActiveRef.current) return;
    pickActiveRef.current = true;
    let cancelled = false;
    live.el.executeJavaScript(PICK_ELEMENT_SCRIPT, false)
      .then((result) => {
        if (cancelled) return;
        if (result && typeof result === 'object' && (result as { status?: string }).status === 'selected') {
          pickCbRef.current?.((result as { element: Record<string, unknown> }).element);
        }
      })
      .catch(() => undefined)
      .finally(() => { pickActiveRef.current = false; });
    return () => { cancelled = true; };
  }, [pickMode, shownTabId, guestOrder]);

  // 退出选择态：注入取消脚本（幂等）；复位在途标记
  const prevPickRef = useRef(false);
  useEffect(() => {
    const was = prevPickRef.current;
    prevPickRef.current = pickMode;
    pickActiveRef.current = false;
    if (!was || pickMode) return;
    const live = shownRef.current ? liveRef.current.get(shownRef.current) : null;
    if (live && typeof live.el.executeJavaScript === 'function') {
      live.el.executeJavaScript(PICK_CANCEL_SCRIPT, false).catch(() => undefined);
    }
  }, [pickMode, shownTabId]);

  // 只挂命令式创建的 webview 子节点：声明无 children，React 不参与其内部
  return (
    <div
      ref={containerRef}
      className={responsive ? 'w-full h-full' : 'absolute inset-0 overflow-hidden'}
    />
  );
}
