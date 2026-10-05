/**
 * @fileoverview 浏览器预览主体（工具栏 + 自由尺寸 + 画面区）
 *
 * 结构：
 * - BrowserToolbar（h-12 px-3 gap-2）：后退/前进/刷新（28px icon、lucide
 *   Chevron/RefreshCw 同形）+ h-7 地址栏（rounded-lg，placeholder「输入网址
 *   后回车」）+ 自由尺寸切换（MonitorSmartphone）+ 元素拾取
 *   （MousePointerClick，激活态高亮）+「⋯」更多菜单（在默认浏览器中打开 /
 *   打开调试工具）。后退/前进按真实导航边界置灰；刷新加载中旋转。
 * - BrowserViewportToolbar（自由尺寸模式，h-8 居中）：宽×高输入（w-14 居中
 *   tabular-nums，越界提示「请输入 {min} 到 {max} 之间的整数」）+ 缩放下拉
 *   （适应窗口 / 50–200%）。
 * - 画面区（BrowserViewportSurface + ResponsiveBrowserViewport）：普通模式
 *   画面填满；自由尺寸 p-4 画布内 Fit/缩放居中（白底 + ring 描边）+ 四边/
 *   四角拖拽（Grip 图标 hover 显现、指针捕获、Shift 步进 10px、失焦终止）。
 * - 空态/加载失败态照 浏览器空态 / BrowserLoadErrorState
 *
 * 双模式承载：桌面=真实 <webview> guest（BrowserGuestSurface 命令式管理，
 * 把手 setPointerCapture 拖拽——指针划过 guest 事件不断流）；Web=截图流
 * 画布 + 交互转发。
 *
 * @module BrowserPreviewBody
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { t, type UiLanguage } from '../i18n';
import { normalizeBrowserInput } from '../lib/browserUrl';
import { BugIcon, ChevronDownIcon, ChevronLeftThinIcon, ChevronRightThinIcon, EllipsisIcon, ExternalLinkIcon, GlobeIcon, GripHorizontalIcon, GripVerticalIcon, MonitorSmartphoneIcon, MousePointerIcon, RefreshCwIcon, TriangleAlertIcon } from './icons';
import type { BrowserFrame, BrowserState } from '../types/protocol';
import { BrowserGuestSurface, type BrowserGuestHandle } from './BrowserGuestSurface';
import {
  resolveVisualScale,
  useFitCanvasMeasure,
  useResponsiveDrag,
  type ResizeDir,
  type ResponsiveSize,
} from '../hooks/useResponsiveDrag';

/** 自由尺寸视口限制 */
const VIEWPORT_LIMITS = { minWidth: 320, maxWidth: 3840, minHeight: 320, maxHeight: 2160 };
/** 缩放档位 */
const ZOOM_OPTIONS = ['fit', '50', '75', '100', '125', '150', '200'] as const;
type Zoom = (typeof ZOOM_OPTIONS)[number];
/** Fit 画布内边距（与 p-4 一致） */
const CANVAS_PADDING_PX = 16;
/** 自由尺寸初始视口：取后端默认逻辑视口（1280×720）——进画布即当前
 *  大小，而非手机的 393×852（进入时尺寸应符合直觉） */
const DEFAULT_RESPONSIVE_SIZE = { width: 1280, height: 720 };

/** 浏览器预览主体属性 */
interface BrowserPreviewBodyProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 浏览器状态（browser_state） */
  browserState: BrowserState | null;
  /** 最新画面帧（browser_frame） */
  browserFrame: BrowserFrame | null;
  /** agent 操作中呼吸指示 */
  browserOpActive: boolean;
  /** 是否运行在 Electron 桌面壳内 */
  isDesktop: boolean;
  /** WS 请求发送器 */
  sendRequest: (payload: Record<string, unknown>) => void;
  /** 拾取结果回传（桌面内嵌 webview 拾取） */
  onPickResult?: (info: Record<string, unknown> | null) => void;
  /** 挂载后自动聚焦 URL 栏 */
  autoFocusUrl?: boolean;
  /** 元素选择模式开关（受控：App 持有，拾取结果到达后关闭） */
  pickMode?: boolean;
  /** 切换元素选择模式 */
  onTogglePickMode?: () => void;
  /** 用户点击（target=_blank）产生的新浏览器 tab 已创建：父级激活它 */
  onGuestTabOpened?: () => void;
  /** 本卡片对应的后端浏览器 tab id（undefined = 零 tab 占位页） */
  browserTabId?: string;
  /** 浏览器启动中（冷启空窗：空态层透出） */
  starting?: boolean;
}

/** 键盘转发的特殊键（Playwright 键名映射）；可打印字符走 input 通道 */
const SPECIAL_KEYS: Record<string, string> = {
  Enter: 'Enter', Backspace: 'Backspace', Delete: 'Delete', Escape: 'Escape',
  Tab: 'Tab', ArrowUp: 'ArrowUp', ArrowDown: 'ArrowDown', ArrowLeft: 'ArrowLeft',
  ArrowRight: 'ArrowRight', Home: 'Home', End: 'End', PageUp: 'PageUp',
  PageDown: 'PageDown',
};

export default function BrowserPreviewBody({
  lang, browserState, browserFrame, isDesktop, sendRequest, browserOpActive,
  onPickResult, autoFocusUrl, pickMode, onTogglePickMode, onGuestTabOpened, browserTabId, starting,
}: BrowserPreviewBodyProps) {
  const open = browserState?.open === true;
  const mode = browserState?.mode ?? (isDesktop ? 'desktop' : 'managed');
  const tabs = browserState?.tabs ?? [];
  const activeTab = (browserTabId ? tabs.find((tb) => tb.id === browserTabId) : null)
    ?? tabs.find((tb) => tb.active) ?? null;
  // 是否已有真实页面（about:blank/空 = 未导航；桌面叠加 webview 事件回传）
  const [desktopNavigated, setDesktopNavigated] = useState(false);
  const hasPage = (!!activeTab?.url && activeTab.url !== 'about:blank') || desktopNavigated;
  // chrome 状态：地址值 + canGoBack/Forward + isLoading + errorMessage
  const [addressValue, setAddressValue] = useState('');
  const [canGoBack, setCanGoBack] = useState(false);
  const [canGoForward, setCanGoForward] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const [isReady, setIsReady] = useState(false);
  const urlInputRef = useRef<HTMLInputElement | null>(null);
  const busyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startBusy = () => {
    setIsLoading(true);
    if (busyTimerRef.current) clearTimeout(busyTimerRef.current);
    busyTimerRef.current = setTimeout(() => setIsLoading(false), 20000);
  };
  // 桌面内嵌 guest 直驱句柄（human 动作直调 webview 方法）
  const guestRef = useRef<BrowserGuestHandle | null>(null);
  // 自由尺寸模式
  // 默认自由尺寸（用户要求：浏览器打开即自由尺寸画布，而非全屏铺满）
  const [responsiveMode, setResponsiveMode] = useState(true);
  const [responsiveSize, setResponsiveSize] = useState(DEFAULT_RESPONSIVE_SIZE);
  const [zoom, setZoom] = useState<Zoom>('fit');

  // 同步激活 tab 的 chrome 状态（web 模式自后端；桌面由 onChromeState 覆盖）
  useEffect(() => {
    setAddressValue(activeTab?.url ?? '');
    setCanGoBack(activeTab?.can_go_back ?? false);
    setCanGoForward(activeTab?.can_go_forward ?? false);
    if (activeTab?.url) {
      setIsLoading(false);
      setIsReady(true);
    }
  }, [activeTab?.url, activeTab?.can_go_back, activeTab?.can_go_forward]);

  // 后端推送的导航失败（browser_state.error）
  useEffect(() => {
    setErrorMessage(browserState?.error ?? '');
  }, [browserState?.error]);

  // 新画面帧到达 = 页面已加载完成（reload 同 URL 时 URL 不变，靠帧清 spinner，
  // 否则刷新按钮旋转至 20s 超时才停）
  useEffect(() => {
    if (browserFrame?.jpeg_base64) setIsLoading(false);
  }, [browserFrame?.jpeg_base64]);

  useEffect(() => {
    if (autoFocusUrl) urlInputRef.current?.focus();
  }, [autoFocusUrl, open]);

  // + 新建空白 tab 后聚焦地址栏（仅空白跃迁时触发）
  const prevUrlRef = useRef(activeTab?.url ?? '');
  useEffect(() => {
    const url = activeTab?.url ?? '';
    if (url !== prevUrlRef.current) {
      const becameBlank = !url || url === 'about:blank';
      prevUrlRef.current = url;
      if (becameBlank) urlInputRef.current?.focus();
    }
  }, [activeTab?.url]);

  /** 地址栏归一化（含搜索回退）+ 导航。
   *  桌面直驱 webview.loadURL；web 走后端请求。 */
  const openUrl = (raw: string) => {
    const target = normalizeBrowserInput(raw) || raw.trim();
    if (!target || !raw.trim()) return;
    startBusy();
    setErrorMessage('');
    if (mode === 'desktop') {
      if (activeTab) {
        guestRef.current?.openUrl(target);
        return;
      }
      // 零 guest：回落后端（自开浏览器 + 新建 tab + 后台导航），
      // webview 就绪后 dom-ready 消费 pendingUrl / 状态推送同步地址栏
      sendRequest({ type: 'web_browser_navigate', url: target });
      return;
    }
    sendRequest({ type: 'web_browser_navigate', url: target, tab_id: activeTab?.id });
  };

  const goBack = () => {
    if (!canGoBack) return;
    startBusy();
    if (mode === 'desktop') { guestRef.current?.back(); return; }
    sendRequest({ type: 'web_browser_back', tab_id: activeTab?.id });
  };
  const goForward = () => {
    if (!canGoForward) return;
    startBusy();
    if (mode === 'desktop') { guestRef.current?.forward(); return; }
    sendRequest({ type: 'web_browser_forward', tab_id: activeTab?.id });
  };
  const reload = () => {
    if (!isReady) return;
    startBusy();
    if (mode === 'desktop') { guestRef.current?.reload(); return; }
    sendRequest({ type: 'web_browser_reload', tab_id: activeTab?.id });
  };
  const openExternal = () => {
    if (!isReady || !activeTab?.url) return;
    if (!/^https?:/i.test(activeTab.url)) return;
    window.open(activeTab.url, '_blank');
  };
  const openDevTools = () => {
    if (!isDesktop || !isReady) return;
    if (mode === 'desktop') { guestRef.current?.openDevTools(); return; }
    sendRequest({ type: 'web_browser_devtools', tab_id: activeTab?.id });
  };
  /** 自由尺寸 → 逻辑视口。
   *  sync=false（拖拽逐帧路径）只改本地尺寸——每帧回发后端会让状态推送
   *  以 60Hz 反复打回整个 App（无谓重渲染风暴）；手势结束由 onCommit 同步一次 */
  const applyResponsiveSize = (w: number, h: number, sync = true) => {
    const cw = Math.max(VIEWPORT_LIMITS.minWidth, Math.min(VIEWPORT_LIMITS.maxWidth, Math.round(w)));
    const ch = Math.max(VIEWPORT_LIMITS.minHeight, Math.min(VIEWPORT_LIMITS.maxHeight, Math.round(h)));
    setResponsiveSize({ width: cw, height: ch });
    if (sync) sendRequest({ type: 'web_browser_resize', width: cw, height: ch });
  };

  // 自由尺寸画布测量 + 拖拽（同一
  // webview 节点只换外层 frame 尺寸，禁止重建 guest）
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const canvasSize = useFitCanvasMeasure(canvasRef, responsiveMode);
  const visualScale = resolveVisualScale(canvasSize, zoom, responsiveSize);
  const rendererScaleRef = useRef(visualScale > 0 ? visualScale : 1);
  rendererScaleRef.current = visualScale > 0 ? visualScale : 1;
  // 拖拽逐帧只改本地；手势结束向后端同步最终视口一次（引用稳定，
  // 避免回调身份抖动殃及 hook 内部 effect）
  const applyDragSize = useCallback((w: number, h: number) => applyResponsiveSize(w, h, false), []);
  const commitDragSize = useCallback((w: number, h: number) => {
    sendRequest({ type: 'web_browser_resize', width: w, height: h });
  }, [sendRequest]);
  const drag = useResponsiveDrag(responsiveSize, applyDragSize, commitDragSize, rendererScaleRef);

  /** 拖拽把手按下：fit 冻结为当前百分比。
   *  fit 的缩放比 = 画布/当前尺寸——拖拽改变尺寸的同一瞬间缩放比反向变化，
   *  拖大的量被缩放比吃掉（frame 钉死在画布宽），拖拽高度时另一维还会反向
   *  收缩，加上滚动条出现/消失的反馈回路 = 剧烈抖动 + "拖了没反应"。
   *  冻结后 frame 与指针 1:1；松手后可点「适应窗口」重新适配。 */
  const beginResize = (directions: ResizeDir, e: React.PointerEvent<HTMLDivElement>) => {
    if (zoom === 'fit') {
      const pct = Math.min(200, Math.max(10, Math.round(visualScale * 100)));
      setZoom(String(pct) as Zoom);
    }
    drag.beginResize(directions, e);
  };

  /** 键盘步进与拖拽同款冻结（fit 下逐键同样会被缩放比反算抵消） */
  const resizeKeyDown = (directions: ResizeDir, e: React.KeyboardEvent<HTMLDivElement>) => {
    if (zoom === 'fit') {
      const pct = Math.min(200, Math.max(10, Math.round(visualScale * 100)));
      setZoom(String(pct) as Zoom);
    }
    drag.onKeyDown(directions, e);
  };

  // 桌面 guest chrome 状态（guest 侧只回报"正在显示"的 tab，无需按 tab 过滤）。
  // 地址栏只在导航完成时回写：加载中的事件携带的是旧 URL，途中回写会让
  // 地址栏/工具栏反复跳变（刷新按钮悬停抖动的来源）
  const handleChromeState = (state: {
    tabId: string; url: string; canGoBack: boolean; canGoForward: boolean;
    loading: boolean; error: string | null; navigated: boolean;
  }) => {
    setCanGoBack(state.canGoBack);
    setCanGoForward(state.canGoForward);
    setIsLoading(state.loading);
    if (state.error) setErrorMessage(state.error);
    if (state.navigated) {
      setDesktopNavigated(true);
      if (state.url) setAddressValue(state.url);
    }
    if (state.loading || state.navigated) {
      setIsReady(true);
      if (!state.loading) setErrorMessage('');
    }
  };

  // guest 侧 tab 指令处理完成（create/close）：拉帧 + 推状态——手动点击
  // target=_blank 新开的 tab 由此即时进入顶栏标签条（capture 同时推送状态）。
  // 用户点击产生的新 tab（guest=true）还要抢前台——与浏览器行为一致；
  // agent 建的 tab 绝不抢占用户正在看的页面
  const handleTabsChanged = useCallback((info: { kind: 'create' | 'close'; guest: boolean }) => {
    if (info.guest && info.kind === 'create') onGuestTabOpened?.();
    sendRequest({ type: 'web_browser_capture' });
  }, [sendRequest, onGuestTabOpened]);

  // 画面帧过滤：帧属于本卡片的 tab 才渲染（多 tab 并存时避免串帧）
  const frame = browserFrame && (!browserFrame.tab_id || !activeTab?.id || browserFrame.tab_id === activeTab.id)
    ? browserFrame
    : null;
  const vw = browserState?.viewport_width ?? 1280;
  const vh = browserState?.viewport_height ?? 720;

  // agent 侧视口变更跟随：browser_resize 的推送应用到画布（自然模式切到
  // 自由尺寸）。人类拖拽回发的同一值在此被跳过，不会形成回环
  const lastViewportRef = useRef(`${vw}x${vh}`);
  useEffect(() => {
    const key = `${vw}x${vh}`;
    if (key === lastViewportRef.current) return;
    lastViewportRef.current = key;
    if (!open) return;
    if (responsiveSize.width === vw && responsiveSize.height === vh) return;
    setResponsiveSize({ width: vw, height: vh });
    setResponsiveMode(true);
  }, [vw, vh, open, responsiveSize.width, responsiveSize.height]);

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* ============ 浏览器工具栏（h-12 px-3 gap-2）============ */}
      <form
        onSubmit={(e) => { e.preventDefault(); openUrl(addressValue); }}
        className="flex items-center h-12 px-3 gap-2 shrink-0"
      >
        <BrowserIconButton
          label={t(lang, 'browser_btn_back')}
          disabled={!canGoBack}
          onClick={goBack}
        >
          <ChevronLeftThinIcon className="w-4 h-4" />
        </BrowserIconButton>
        <BrowserIconButton
          label={t(lang, 'browser_btn_forward')}
          disabled={!canGoForward}
          onClick={goForward}
        >
          <ChevronRightThinIcon className="w-4 h-4" />
        </BrowserIconButton>
        <BrowserIconButton
          label={t(lang, 'browser_btn_reload')}
          disabled={!isReady}
          onClick={reload}
        >
          <RefreshCwIcon spinning={isLoading} />
        </BrowserIconButton>
        <input
          ref={urlInputRef}
          type="text"
          value={addressValue}
          onChange={(e) => setAddressValue(e.target.value)}
          placeholder={t(lang, 'browser_address_placeholder')}
          spellCheck={false}
          className="h-7 flex-1 min-w-0 rounded-lg border border-[var(--border-light)] bg-transparent px-2 py-0.5 text-sm text-content-primary placeholder:text-content-disabled focus:outline-none focus:border-primary transition-colors"
        />
        <BrowserIconButton
          label={t(lang, responsiveMode ? 'browser_responsive_exit' : 'browser_responsive_enter')}
          disabled={false}
          active={responsiveMode}
          pressed={responsiveMode}
          onClick={() => {
            const next = !responsiveMode;
            setResponsiveMode(next);
            if (!next) {
              // 退出自由尺寸时终止进行中的拖拽（把手即将卸载，结束事件无人接收）
              drag.finishResize();
            } else {
              // 进入自由尺寸：以后端当前视口作为画布逻辑尺寸。后端视口本就是
              // vw×vh，无需回发 resize——此刻发只会得到一次无效果的状态回推
              setResponsiveSize({ width: vw, height: vh });
            }
          }}
        >
          <MonitorSmartphoneIcon className="w-4 h-4" />
        </BrowserIconButton>
        <BrowserIconButton
          label={t(lang, pickMode ? 'browser_picker_cancel' : 'browser_picker_start')}
          disabled={!isReady || !hasPage}
          active={pickMode === true}
          onClick={() => onTogglePickMode?.()}
        >
          <MousePointerIcon className="w-4 h-4" />
        </BrowserIconButton>
        {browserOpActive && (
          <span
            className="shrink-0 w-2 h-2 rounded-full bg-primary animate-pulse"
            title={t(lang, 'browser_agent_operating')}
            aria-label={t(lang, 'browser_agent_operating')}
          />
        )}
        <ToolbarMoreMenu
          lang={lang}
          canOpenExternal={isReady && !!activeTab?.url && /^https?:/i.test(activeTab.url)}
          canOpenDevTools={isDesktop && isReady}
          onOpenExternal={openExternal}
          onOpenDevTools={openDevTools}
        />
      </form>

      {/* ============ 自由尺寸工具栏 ============ */}
      {open && responsiveMode && (
        <ResponsiveViewportToolbar
          lang={lang}
          size={responsiveSize}
          zoom={zoom}
          onSizeChange={applyResponsiveSize}
          onZoomChange={setZoom}
        />
      )}

      {/* ============ 画面区 ============
          桌面模式：guest 层恒定挂载——条件分支里卸载它 = 销毁真实
          Chromium guest = 整个浏览器消失 + blink.mojom.Widget 拒绝。
          空态/错误态是 z-10 兄弟覆盖层（覆盖层与 webview 同级，
          webview 只是 beneath，永不卸载） */}
      <div className="relative flex-1 min-h-0 min-w-0 overflow-hidden">
        {!open ? (
          <EmptyState lang={lang} starting={starting === true} />
        ) : mode === 'desktop' ? (
          <>
      <div
        ref={canvasRef}
        className={responsiveMode
          ? 'h-full min-h-0 w-full min-w-0 overflow-auto'
          : 'absolute inset-0 overflow-hidden'}
        style={responsiveMode ? { background: 'var(--bg-card-alt, #f3f4f6)' } : undefined}
      >
        <div className={responsiveMode
          ? 'flex min-h-full w-max min-w-full items-center justify-center p-4'
          : 'h-full w-full'}
        >
          <div
            className={responsiveMode ? 'relative shrink-0 bg-white' : 'relative h-full w-full'}
            style={responsiveMode
              ? {
                  width: `${responsiveSize.width * visualScale}px`,
                  height: `${responsiveSize.height * visualScale}px`,
                  boxShadow: '0 1px 2px rgba(0,0,0,0.06), 0 0 0 1px var(--border-light, #e8ebf0)',
                }
              : undefined}
          >
            {/* 稳定层包装：开关自由尺寸只改 CSS 尺寸/transform，绝不重建 guest */}
            <div
              className={responsiveMode ? 'relative shrink-0' : 'h-full w-full'}
              style={responsiveMode
                ? {
                    width: `${responsiveSize.width}px`,
                    height: `${responsiveSize.height}px`,
                    transform: `scale(${visualScale})`,
                    transformOrigin: 'top left',
                  }
                : undefined}
            >
              <BrowserGuestSurface
                handleRef={guestRef}
                activeTabId={activeTab?.id ?? null}
                tabs={tabs}
                responsive={responsiveMode}
                pickMode={pickMode === true}
                onPickResult={onPickResult}
                onChromeState={handleChromeState}
                onNavigated={() => sendRequest({ type: 'web_browser_capture' })}
                onTabsChanged={handleTabsChanged}
              />
              {responsiveMode && (
                <ResizeHandles
                  lang={lang}
                  size={responsiveSize}
                  beginResize={beginResize}
                  onPointerMove={drag.onPointerMove}
                  finishResize={drag.finishResize}
                  onKeyDown={resizeKeyDown}
                />
              )}
            </div>
          </div>
        </div>
      </div>
      {/* 覆盖层（兄弟节点；webview 保持挂载，仅被遮住） */}
            {errorMessage ? (
              <LoadErrorState
                lang={lang}
                message={errorMessage}
                onRetry={() => openUrl(activeTab?.url || addressValue)}
              />
            ) : !hasPage ? (
              <EmptyState lang={lang} starting={starting === true} />
            ) : null}
          </>
        ) : responsiveMode ? (
          <ResponsiveCanvas
            lang={lang}
            size={responsiveSize}
            zoom={zoom}
            visualScale={visualScale}
            drag={drag}
            handles={(
              <ResizeHandles
                lang={lang}
                size={responsiveSize}
                beginResize={beginResize}
                onPointerMove={drag.onPointerMove}
                finishResize={drag.finishResize}
                onKeyDown={resizeKeyDown}
              />
            )}
          >
            <FrameCanvas
              lang={lang}
              frame={frame}
              viewportWidth={vw}
              viewportHeight={vh}
              pageUrl={activeTab?.url ?? ''}
              sendRequest={sendRequest}
              onRequestFocusUrl={() => urlInputRef.current?.focus()}
              pickMode={pickMode === true}
              letterbox={false}
            />
          </ResponsiveCanvas>
        ) : (
          <FrameCanvas
            lang={lang}
            frame={frame}
            viewportWidth={vw}
            viewportHeight={vh}
            pageUrl={activeTab?.url ?? ''}
            sendRequest={sendRequest}
            onRequestFocusUrl={() => urlInputRef.current?.focus()}
            pickMode={pickMode === true}
            letterbox
          />
        )}
      </div>
    </div>
  );
}

  /* ==================== BrowserIconButton（28px 方形、图标 16px）==================== */

function BrowserIconButton({ label, disabled, active, pressed, onClick, children }: {
  label: string;
  disabled: boolean;
  active?: boolean;
  pressed?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      aria-pressed={pressed}
      disabled={disabled}
      onClick={onClick}
      className={`shrink-0 w-7 h-7 rounded-lg flex items-center justify-center transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed ${
        active
          ? 'bg-[var(--badge-bg-subtle)] text-content-primary'
          : 'text-content-secondary glass-option-hover hover:text-content-primary'
      }`}
    >
      {children}
    </button>
  );
}

/* ==================== 浏览器工具栏MoreMenu（w-52 右对齐）==================== */

function ToolbarMoreMenu({ lang, canOpenExternal, canOpenDevTools, onOpenExternal, onOpenDevTools }: {
  lang: UiLanguage;
  canOpenExternal: boolean;
  canOpenDevTools: boolean;
  onOpenExternal: () => void;
  onOpenDevTools: () => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const btnRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState({ left: 0, top: 0 });
  const moreLabel = t(lang, 'browser_more');

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (rootRef.current?.contains(target)) return;
      if (menuRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        ref={btnRef}
        type="button"
        title={moreLabel}
        aria-label={moreLabel}
        onClick={() => {
          if (open) { setOpen(false); return; }
          const rect = btnRef.current?.getBoundingClientRect();
          if (rect) setPos({ left: rect.right - 208, top: rect.bottom + 4 });
          setOpen(true);
        }}
        className="w-7 h-7 rounded-lg flex items-center justify-center text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer"
      >
        <EllipsisIcon className="w-4 h-4" />
      </button>
      {open && createPortal(
        <div
          ref={menuRef}
          className="fixed z-[60] w-52 bg-surface-card-alt border border-[var(--border-medium)] rounded-lg shadow-card p-1 animate-fade"
          style={{ left: pos.left, top: pos.top }}
        >
          <button
            type="button"
            disabled={!canOpenExternal}
            onClick={() => { setOpen(false); onOpenExternal(); }}
            className="w-full flex items-center gap-2 px-2 py-1.5 text-sm text-content-secondary hover:text-content-primary glass-option-hover rounded-md transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed text-left"
          >
            <ExternalLinkIcon className="w-4 h-4 shrink-0" />
            <span>{t(lang, 'browser_open_external')}</span>
          </button>
          <button
            type="button"
            disabled={!canOpenDevTools}
            onClick={() => { setOpen(false); onOpenDevTools(); }}
            className="w-full flex items-center gap-2 px-2 py-1.5 text-sm text-content-secondary hover:text-content-primary glass-option-hover rounded-md transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed text-left"
          >
            <BugIcon className="w-4 h-4 shrink-0" />
            <span>{t(lang, 'browser_devtools')}</span>
          </button>
        </div>,
        document.body,
      )}
    </div>
  );
}

/* ==================== BrowserViewportToolbar（h-8 居中：宽×高 + 缩放）==================== */

function ResponsiveViewportToolbar({ lang, size, zoom, onSizeChange, onZoomChange }: {
  lang: UiLanguage;
  size: { width: number; height: number };
  zoom: Zoom;
  onSizeChange: (w: number, h: number) => void;
  onZoomChange: (z: Zoom) => void;
}) {
  const [widthDraft, setWidthDraft] = useState(String(size.width));
  const [heightDraft, setHeightDraft] = useState(String(size.height));
  const [invalid, setInvalid] = useState({ width: false, height: false });
  const rootRef = useRef<HTMLDivElement | null>(null);
  const zoomBtnRef = useRef<HTMLButtonElement | null>(null);
  const zoomMenuRef = useRef<HTMLDivElement | null>(null);
  const [zoomOpen, setZoomOpen] = useState(false);
  const [zoomPos, setZoomPos] = useState({ left: 0, top: 0 });

  useEffect(() => setWidthDraft(String(size.width)), [size.width]);
  useEffect(() => setHeightDraft(String(size.height)), [size.height]);

  useEffect(() => {
    if (!zoomOpen) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (rootRef.current?.contains(target)) return;
      if (zoomMenuRef.current?.contains(target)) return;
      setZoomOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [zoomOpen]);

  const limitsFor = (dimension: 'width' | 'height') => dimension === 'width'
    ? { min: VIEWPORT_LIMITS.minWidth, max: VIEWPORT_LIMITS.maxWidth }
    : { min: VIEWPORT_LIMITS.minHeight, max: VIEWPORT_LIMITS.maxHeight };

  const rangeError = (dimension: 'width' | 'height') => {
    const limits = limitsFor(dimension);
    return t(lang, 'browser_responsive_range_error')
      .replace('{min}', String(limits.min)).replace('{max}', String(limits.max));
  };

  const resetDraft = () => {
    setWidthDraft(String(size.width));
    setHeightDraft(String(size.height));
  };

  const commitDraft = (dimension: 'width' | 'height') => {
    const draft = dimension === 'width' ? widthDraft : heightDraft;
    const value = Number(draft.trim());
    const limits = limitsFor(dimension);
    if (!Number.isInteger(value) || value < limits.min || value > limits.max) {
      setInvalid((prev) => ({ ...prev, [dimension]: true }));
      return;
    }
    setInvalid((prev) => ({ ...prev, [dimension]: false }));
    if (size[dimension] === value) {
      resetDraft();
      return;
    }
    onSizeChange(
      dimension === 'width' ? value : size.width,
      dimension === 'height' ? value : size.height,
    );
  };

  const dimensionKeyDown = (dimension: 'width' | 'height', e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      commitDraft(dimension);
      return;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      setInvalid((prev) => ({ ...prev, [dimension]: false }));
      resetDraft();
      e.currentTarget.blur();
    }
  };

  return (
    <div
      ref={rootRef}
      className="flex h-8 shrink-0 items-center justify-center gap-1 overflow-x-auto border-y border-[var(--border-light)] px-2"
      style={{ background: 'var(--bg-card, #ffffff)' }}
    >
      <Tooltip open={invalid.width} content={rangeError('width')}>
        <input
          aria-invalid={invalid.width || undefined}
          aria-label={t(lang, 'browser_responsive_width')}
          className="h-7 w-14 shrink-0 rounded-md border border-transparent bg-transparent px-1 text-center text-sm font-medium tabular-nums text-content-primary glass-option-hover focus:outline-none focus:border-primary aria-invalid:border-danger aria-invalid:text-danger transition-colors"
          inputMode="numeric"
          max={VIEWPORT_LIMITS.maxWidth}
          min={VIEWPORT_LIMITS.minWidth}
          onBlur={() => commitDraft('width')}
          onChange={(e) => {
            setWidthDraft(e.target.value);
            if (invalid.width) {
              const v = Number(e.target.value.trim());
              const ok = Number.isInteger(v) && v >= VIEWPORT_LIMITS.minWidth && v <= VIEWPORT_LIMITS.maxWidth;
              setInvalid((prev) => ({ ...prev, width: !ok }));
            }
          }}
          onKeyDown={(e) => dimensionKeyDown('width', e)}
          spellCheck={false}
          value={widthDraft}
        />
      </Tooltip>
      <span aria-hidden="true" className="shrink-0 text-sm text-content-secondary">×</span>
      <Tooltip open={invalid.height} content={rangeError('height')}>
        <input
          aria-invalid={invalid.height || undefined}
          aria-label={t(lang, 'browser_responsive_height')}
          className="h-7 w-14 shrink-0 rounded-md border border-transparent bg-transparent px-1 text-center text-sm font-medium tabular-nums text-content-primary glass-option-hover focus:outline-none focus:border-primary aria-invalid:border-danger aria-invalid:text-danger transition-colors"
          inputMode="numeric"
          max={VIEWPORT_LIMITS.maxHeight}
          min={VIEWPORT_LIMITS.minHeight}
          onBlur={() => commitDraft('height')}
          onChange={(e) => {
            setHeightDraft(e.target.value);
            if (invalid.height) {
              const v = Number(e.target.value.trim());
              const ok = Number.isInteger(v) && v >= VIEWPORT_LIMITS.minHeight && v <= VIEWPORT_LIMITS.maxHeight;
              setInvalid((prev) => ({ ...prev, height: !ok }));
            }
          }}
          onKeyDown={(e) => dimensionKeyDown('height', e)}
          spellCheck={false}
          value={heightDraft}
        />
      </Tooltip>
      <button
        ref={zoomBtnRef}
        type="button"
        aria-label={t(lang, 'browser_responsive_zoom')}
        onClick={() => {
          if (zoomOpen) { setZoomOpen(false); return; }
          const rect = zoomBtnRef.current?.getBoundingClientRect();
          if (rect) setZoomPos({ left: rect.right - 150, top: rect.bottom + 4 });
          setZoomOpen(true);
        }}
        className="h-7 min-w-24 shrink-0 rounded-md px-2 flex items-center justify-center gap-1 text-sm font-medium tabular-nums text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer"
      >
        {zoom === 'fit' ? t(lang, 'browser_responsive_fit') : `${zoom}%`}
        <ChevronDownIcon className="w-3.5 h-3.5" />
      </button>
      {zoomOpen && createPortal(
        <div
          ref={zoomMenuRef}
          className="fixed z-[60] w-36 bg-surface-card-alt border border-[var(--border-medium)] rounded-lg shadow-card p-1 animate-fade"
          style={{ left: zoomPos.left, top: zoomPos.top }}
        >
          {ZOOM_OPTIONS.map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => { onZoomChange(option); setZoomOpen(false); }}
              className={`w-full px-2 py-1.5 text-sm text-left rounded-md transition-colors cursor-pointer ${
                zoom === option
                  ? 'bg-[var(--badge-bg-subtle)] text-content-primary font-medium'
                  : 'text-content-secondary hover:text-content-primary glass-option-hover'
              }`}
            >
              {option === 'fit' ? t(lang, 'browser_responsive_fit') : `${option}%`}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </div>
  );
}

/* ==================== ResponsiveBrowserViewport（自由尺寸画布 + 四边拖拽）==================== */


function ResponsiveCanvas({ lang, size, zoom, handles, children }: {
  lang: UiLanguage;
  size: { width: number; height: number };
  zoom: Zoom;
  /** Fit/缩放比例（父级 useResponsiveDrag 链路统一提供） */
  visualScale: number;
  /** 拖拽处理器（父级统一提供） */
  drag: {
    beginResize: (d: { widthDirection: -1 | 0 | 1; heightDirection: -1 | 0 | 1 }, e: React.PointerEvent<HTMLDivElement>) => void;
    onPointerMove: (e: React.PointerEvent<HTMLDivElement>) => void;
    finishResize: (pointerId?: number) => void;
    onKeyDown: (d: { widthDirection: -1 | 0 | 1; heightDirection: -1 | 0 | 1 }, e: React.KeyboardEvent<HTMLDivElement>) => void;
    active: boolean;
  };

  /** 未缩放 frame 层上的把手（命中区不受 visualScale 影响） */
  handles?: React.ReactNode;
  children: React.ReactNode;
}) {
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const [canvasSize, setCanvasSize] = useState<{ w: number; h: number } | null>(null);

  // zoom=fit 时按画布减 p4×2 内边距解析缩放（上限 1）；固定档位直取
  const fitScale = canvasSize && canvasSize.w > 0 && canvasSize.h > 0
    ? Math.min(
        1,
        Math.max(0, canvasSize.w - CANVAS_PADDING_PX * 2) / size.width,
        Math.max(0, canvasSize.h - CANVAS_PADDING_PX * 2) / size.height,
      )
    : 1;
  const resolvedScale = zoom !== 'fit' ? Number(zoom) / 100 : fitScale;

  // Fit 画布尺寸测量（挂载时量一次 + ResizeObserver 跟随；0 尺寸不采纳）
  useEffect(() => {
    if (zoom !== 'fit') return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const update = (w: number, h: number) => {
      if (w <= 0 || h <= 0) return;
      setCanvasSize((prev) => (prev && prev.w === w && prev.h === h ? prev : { w, h }));
    };
    const rect = canvas.getBoundingClientRect();
    update(rect.width, rect.height);
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) update(entry.contentRect.width, entry.contentRect.height);
    });
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [zoom]);








  return (
    <div
      ref={canvasRef}
      className="h-full min-h-0 w-full min-w-0 overflow-auto"
      style={{ background: 'var(--bg-card-alt, #f3f4f6)' }}
    >
      <div className="flex min-h-full w-max min-w-full items-center justify-center p-4">
        {/* frame：显示尺寸 = 逻辑视口 × resolvedScale；bg-card + ring-1 */}
        <div
          className="relative shrink-0 bg-white"
          style={{
            width: `${size.width * resolvedScale}px`,
            height: `${size.height * resolvedScale}px`,
            boxShadow: '0 1px 2px rgba(0,0,0,0.06), 0 0 0 1px var(--border-light, #e8ebf0)',
          }}
        >
          <div
            aria-label={t(lang, 'browser_responsive_viewport_label')}
            className="relative shrink-0"
            style={{
              width: `${size.width}px`,
              height: `${size.height}px`,
              transform: `scale(${resolvedScale})`,
              transformOrigin: 'top left',
              visibility: canvasSize ? undefined : 'hidden',
            }}
          >
            {children}
          </div>
          {/* 把手挂在未缩放 frame 层：命中区不受缩放影响；拖拽中隐藏 */}
          {handles}
        </div>
      </div>
    </div>
  );
}

/* ==================== ResponsiveBrowserResizeHandles（四边四角把手）==================== */

// 把手内嵌 frame 边缘（-2 = 8px 越出，落在画布 p-4 内边距里不被裁剪）：
// 命中区 w-4/h-4（16px），grip 图标仅 hover 显现
const EDGE_HANDLES = [
  { key: 'left', cls: 'top-0 -left-2 h-full w-4 cursor-ew-resize', w: -1 as const, h: 0 as const, orientation: 'vertical' },
  { key: 'right', cls: 'top-0 -right-2 h-full w-4 cursor-ew-resize', w: 1 as const, h: 0 as const, orientation: 'vertical' },
  { key: 'top', cls: '-top-2 left-0 h-4 w-full cursor-ns-resize', w: 0 as const, h: -1 as const, orientation: 'horizontal' },
  { key: 'bottom', cls: '-bottom-2 left-0 h-4 w-full cursor-ns-resize', w: 0 as const, h: 1 as const, orientation: 'horizontal' },
] as const;
const CORNER_HANDLES = [
  { key: 'top-left', cls: '-top-2 -left-2 size-5 cursor-nwse-resize', w: -1 as const, h: -1 as const },
  { key: 'top-right', cls: '-top-2 -right-2 size-5 cursor-nesw-resize', w: 1 as const, h: -1 as const },
  { key: 'bottom-left', cls: '-bottom-2 -left-2 size-5 cursor-nesw-resize', w: -1 as const, h: 1 as const },
  { key: 'bottom-right', cls: '-bottom-2 -right-2 size-5 cursor-nwse-resize', w: 1 as const, h: 1 as const },
] as const;

// grip 图标不参与命中（pointer-events-none），hover/聚焦时显现
const GRIP_ICON_CLASS = 'pointer-events-none absolute top-1/2 left-1/2 size-4 -translate-x-1/2 -translate-y-1/2 text-content-disabled opacity-0 transition-[color,opacity] group-hover/resize-edge:opacity-100 group-focus-visible/resize-edge:opacity-100';

function ResizeHandles({ lang, size, beginResize, onPointerMove, finishResize, onKeyDown }: {
  lang: UiLanguage;
  size: ResponsiveSize;
  beginResize: (d: { widthDirection: -1 | 0 | 1; heightDirection: -1 | 0 | 1 }, e: React.PointerEvent<HTMLDivElement>) => void;
  onPointerMove: (e: React.PointerEvent<HTMLDivElement>) => void;
  finishResize: (pointerId?: number) => void;
  onKeyDown: (d: { widthDirection: -1 | 0 | 1; heightDirection: -1 | 0 | 1 }, e: React.KeyboardEvent<HTMLDivElement>) => void;
}) {
  return (
    <>
      {EDGE_HANDLES.map((handle) => (
        <div
          key={handle.key}
          role="separator"
          tabIndex={0}
          aria-label={handle.orientation === 'vertical'
            ? t(lang, 'browser_responsive_resize_width')
            : t(lang, 'browser_responsive_resize_height')}
          aria-orientation={handle.orientation}
          aria-valuenow={handle.orientation === 'vertical' ? size.width : size.height}
          aria-valuemin={handle.orientation === 'vertical' ? VIEWPORT_LIMITS.minWidth : VIEWPORT_LIMITS.minHeight}
          aria-valuemax={handle.orientation === 'vertical' ? VIEWPORT_LIMITS.maxWidth : VIEWPORT_LIMITS.maxHeight}
          data-resize-edge={handle.key}
          className={`group/resize-edge absolute z-30 touch-none outline-none ${handle.cls}`}
          onKeyDown={(e) => onKeyDown({ widthDirection: handle.w, heightDirection: handle.h }, e)}
          onLostPointerCapture={() => finishResize()}
          onPointerCancel={() => finishResize()}
          onPointerDown={(e) => beginResize({ widthDirection: handle.w, heightDirection: handle.h }, e)}
          onPointerMove={onPointerMove}
          onPointerUp={() => finishResize()}
        >
          {handle.orientation === 'vertical' ? <GripVerticalIcon className={GRIP_ICON_CLASS} /> : <GripHorizontalIcon className={GRIP_ICON_CLASS} />}
        </div>
      ))}
      {CORNER_HANDLES.map((handle) => (
        <div
          key={handle.key}
          aria-hidden="true"
          data-resize-corner={handle.key}
          className={`absolute z-40 touch-none ${handle.cls}`}
          onLostPointerCapture={() => finishResize()}
          onPointerCancel={() => finishResize()}
          onPointerDown={(e) => beginResize({ widthDirection: handle.w, heightDirection: handle.h }, e)}
          onPointerMove={onPointerMove}
          onPointerUp={() => finishResize()}
        />
      ))}
    </>
  );
}

/* ==================== 浏览器空态 ==================== */

function EmptyState({ lang, starting }: { lang: UiLanguage; starting: boolean }) {
  return (
    <div className="absolute inset-0 z-10 flex items-center justify-center px-6" style={{ background: 'var(--bg-card, #ffffff)' }}>
      <div className="flex max-w-sm flex-col pb-10 items-center text-center">
        {starting ? (
          <svg className="mb-6 w-16 h-16 animate-spin text-content-secondary opacity-30" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.2" strokeOpacity="0.35" />
            <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
          </svg>
        ) : (
          <GlobeIcon className="mb-6 w-16 h-16 text-content-secondary opacity-30" />
        )}
        <h3 className="text-sm font-medium text-content-primary">{t(lang, 'browser_empty_title')}</h3>
        <p className="mt-2 text-sm text-content-secondary">{t(lang, 'browser_empty_hint')}</p>
      </div>
    </div>
  );
}

/* ==================== 加载失败态 ==================== */

function LoadErrorState({ lang, message, onRetry }: { lang: UiLanguage; message: string; onRetry: () => void }) {
  return (
    <div className="absolute inset-0 z-10 flex items-center justify-center px-6" style={{ background: 'var(--bg-card, #ffffff)' }}>
      <div className="flex max-w-sm flex-col items-center pb-10 text-center">
        <TriangleAlertIcon className="mb-6 w-16 h-16 text-warning opacity-60" />
        <h3 className="text-sm font-medium text-content-primary">{t(lang, 'browser_load_error_title')}</h3>
        <p className="mt-2 font-mono text-xs break-all text-content-disabled">{message}</p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-6 flex items-center gap-2 rounded-lg border border-[var(--border-medium)] px-3 py-1.5 text-sm text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer"
        >
          <RefreshCwIcon className="w-4 h-4" />
          {t(lang, 'browser_load_error_retry')}
        </button>
      </div>
    </div>
  );
}

/* ==================== Web 模式截图流画布 ==================== */

function FrameCanvas({ lang, frame, viewportWidth, viewportHeight, pageUrl, sendRequest, onRequestFocusUrl, pickMode, letterbox }: {
  lang: UiLanguage;
  frame: BrowserFrame | null;
  viewportWidth: number;
  viewportHeight: number;
  pageUrl: string;
  sendRequest: (payload: Record<string, unknown>) => void;
  onRequestFocusUrl: () => void;
  pickMode: boolean;
  /** true = 常规模式：等比缩放居中；false = 嵌在自由尺寸画布内（100% 填充 frame） */
  letterbox: boolean;
}) {
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [canvasSize, setCanvasSize] = useState<{ w: number; h: number } | null>(null);
  const src = useMemo(
    () => (frame?.jpeg_base64 ? `data:image/jpeg;base64,${frame.jpeg_base64}` : null),
    [frame?.jpeg_base64],
  );
  const blank = !src || /^(about:blank)?$/i.test(pageUrl || '');

  useEffect(() => {
    if (!letterbox) return;
    const el = containerRef.current;
    if (!el) return;
    const measure = () => {
      const w = el.clientWidth;
      const h = el.clientHeight;
      if (w <= 0 || h <= 0 || viewportWidth <= 0 || viewportHeight <= 0) {
        setCanvasSize(null);
        return;
      }
      const scale = Math.min(w / viewportWidth, h / viewportHeight);
      setCanvasSize({ w: Math.floor(viewportWidth * scale), h: Math.floor(viewportHeight * scale) });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [viewportWidth, viewportHeight, letterbox]);

  const pageCoords = (e: { clientX: number; clientY: number }) => {
    const el = canvasRef.current;
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    const x = ((e.clientX - rect.left) / rect.width) * viewportWidth;
    const y = ((e.clientY - rect.top) / rect.height) * viewportHeight;
    if (x < 0 || y < 0 || x > viewportWidth || y > viewportHeight) return null;
    return { x, y };
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (blank) return;
    if (e.ctrlKey || e.metaKey || e.altKey) {
      if (e.key.length === 1 || SPECIAL_KEYS[e.key]) {
        const mod = e.ctrlKey ? 'Control' : e.metaKey ? 'Meta' : 'Alt';
        e.preventDefault();
        sendRequest({
          type: 'web_browser_interact', browser_action: 'key',
          value: `${mod}+${e.key.length === 1 ? e.key.toLowerCase() : e.key}`,
        });
      }
      return;
    }
    if (SPECIAL_KEYS[e.key]) {
      e.preventDefault();
      sendRequest({ type: 'web_browser_interact', browser_action: 'key', value: SPECIAL_KEYS[e.key] });
      return;
    }
    if (e.key.length === 1) {
      e.preventDefault();
      sendRequest({ type: 'web_browser_interact', browser_action: 'input', query: e.key });
    }
  };

  if (blank && letterbox) {
    return (
      <div
        role="button"
        onClick={onRequestFocusUrl}
        className="h-full w-full flex flex-col items-center justify-center gap-2 text-center px-6 cursor-pointer select-none"
      >
        <GlobeIcon className="w-16 h-16 text-content-secondary opacity-30" />
        <div className="text-sm font-medium text-content-primary">{t(lang, 'browser_empty_title')}</div>
        <div className="text-sm text-content-secondary">{t(lang, 'browser_empty_hint')}</div>
      </div>
    );
  }

  const canvas = (
    <div
      ref={canvasRef}
      role="button"
      tabIndex={0}
      aria-label={t(lang, 'browser_canvas_aria')}
      style={letterbox && canvasSize ? { width: `${canvasSize.w}px`, height: `${canvasSize.h}px` } : { width: '100%', height: '100%' }}
      className={`relative overflow-hidden bg-white outline-none select-none ${
        pickMode ? 'cursor-crosshair' : 'cursor-pointer'
      }`}
      onClick={(e) => {
        const pos = pageCoords(e);
        if (!pos) return;
        // 拾取模式先转发悬停再点击：页面内选择器脚本按 mousemove 取 hovered
        if (pickMode) {
          sendRequest({ type: 'web_browser_interact', browser_action: 'move', x: pos.x, y: pos.y });
        }
        sendRequest({ type: 'web_browser_interact', browser_action: 'click', x: pos.x, y: pos.y });
      }}
      onDoubleClick={(e) => {
        if (pickMode) return;
        const pos = pageCoords(e);
        if (pos) sendRequest({ type: 'web_browser_interact', browser_action: 'dblclick', x: pos.x, y: pos.y });
      }}
      onWheel={(e) => {
        if (pickMode) return;
        const pos = pageCoords(e);
        if (!pos) return;
        e.preventDefault();
        sendRequest({
          type: 'web_browser_interact', browser_action: 'scroll',
          x: pos.x, y: pos.y, dx: e.deltaX, dy: e.deltaY,
        });
      }}
      onKeyDown={onKeyDown}
    >
      <img src={src ?? undefined} alt={frame?.url || 'browser'} className="w-full h-full object-fill select-none" draggable={false} />
      {pickMode && (
        <div className="absolute top-2 left-1/2 -translate-x-1/2 rounded-full bg-primary/90 text-white px-3 py-1 text-[10px] font-medium select-none pointer-events-none">
          {t(lang, 'browser_pick_hint')}
        </div>
      )}
    </div>
  );

  if (!letterbox) return canvas;
  return (
    <div
      ref={containerRef}
      className="flex-1 min-h-0 flex items-center justify-center overflow-hidden"
      style={{ background: 'var(--bg-card-alt, #f3f4f6)' }}
    >
      {canvas}
    </div>
  );
}

/* ==================== 轻量 Tooltip（越界草稿提示，TooltipContent bottom）==================== */

function Tooltip({ open, content, children }: {
  open: boolean;
  content: string;
  children: React.ReactNode;
}) {
  if (!open) return <>{children}</>;
  return (
    <span className="relative inline-flex">
      {children}
      <span className="pointer-events-none absolute top-full left-1/2 z-50 mt-1 -translate-x-1/2 whitespace-nowrap rounded-md border border-danger/30 bg-surface-card-alt px-2 py-1 text-[10px] text-danger shadow-card">
        {content}
      </span>
    </span>
  );
}


