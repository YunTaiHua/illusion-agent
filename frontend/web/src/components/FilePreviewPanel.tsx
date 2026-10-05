/**
 * @fileoverview 文件预览停靠列组件（多标签页）
 *
 * 右栏右侧的文件内容停靠区（默认查看形态），多标签页交互：
 * 顶部为可横向滚动的标签页条——每点开一个文件累积一个 tab（文件类型
 * 图标 + 文件名 + Diff 徽标 + 悬浮关闭按钮），同一时刻显示激活 tab 的
 * 内容，互不顶掉；条右端为操作区：关闭其他 / 新建 / 收起 / 关闭标签页。
 *
 * tab 下方保留原头部（完整路径 + 元信息 + 内容/Diff 切换 + 复制 +
 * 弹窗 + 关闭），主体复用 FilePreviewBody（行号 + 语法高亮 / diff 着色）。
 * 与右栏之间由 App 渲染的分隔条支持鼠标拖拽调整宽度。
 *
 * @module FilePreviewPanel
 */

import { useEffect, useRef, useState } from 'react';
import { t, type UiLanguage } from '../i18n';
import { fileIconColor } from '../utils/fileIcon';
import AutoScrollText from './AutoScrollText';
import BrowserPreviewBody from './BrowserPreviewBody';
import { normalizeBrowserInput } from '../lib/browserUrl';
import { CopyButton, FilePreviewBody, splitFilePath } from './FileViewerModal';
import { CloseIcon, FileIcon, GlobeIcon, MinusIcon, PlusIcon, PopOutIcon, TrashIcon } from './icons';
import type { BrowserFrame, BrowserState, PreviewTab } from '../types/protocol';

/**
 * FilePreviewPanel 组件属性接口
 */
interface FilePreviewPanelProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 已打开的预览标签页列表 */
  tabs: PreviewTab[];
  /** 当前激活标签页（tabs 非空时必有） */
  activeTab: PreviewTab | null;
  /** 列宽（px，由 App 拖拽管理） */
  width: number;
  /** 当前文件是否有 Git 变更（true/null=可展示 diff；false=无变更，隐藏"Diff"切换） */
  hasDiff?: boolean | null;
  /** 工作区根目录标识（如 "illusion-agent/"）：路径无目录前缀（根目录文件、
   *  相对路径打开）时头部用它补全路径展示；合成 tab（智能体摘要）不补 */
  rootDirLabel?: string | null;
  /** 激活指定标签页 */
  onActivateTab: (key: string) => void;
  /** 关闭指定标签页 */
  onCloseTab: (key: string) => void;
  /** 关闭除指定 tab 外的全部标签页 */
  onCloseOtherTabs: (key: string) => void;
  /** 按路径打开新标签页（工作区内相对路径或任意绝对路径） */
  onOpenPath: (path: string) => void;
  /** 切换激活 tab 到内容视图（原地切换；内容 tab 已存在时直接激活它） */
  onOpenContent: (key: string) => void;
  /** 切换激活 tab 到 diff 视图（原地切换；diff tab 已存在时直接激活它） */
  onOpenDiff: (key: string) => void;
  /** 弹窗查看（放大为全屏弹窗） */
  onPopOut: () => void;
  /** 收起预览列（隐藏卡片但保留所有 tab 与浏览器 guest） */
  onHideColumn?: () => void;
  /** === 内置浏览器 Tab（activeTab.kind === 'browser' 时使用）=== */
  /** 浏览器状态（browser_state） */
  browserState?: BrowserState | null;
  /** 浏览器最新画面帧（browser_frame） */
  browserFrame?: BrowserFrame | null;
  /** agent 操作中呼吸指示 */
  browserOpActive?: boolean;
  /** 浏览器启动中（冷启空窗期显示启动指示） */
  browserStarting?: boolean;
  /** 是否运行在 Electron 桌面壳内 */
  isDesktop?: boolean;
  /** WS 请求发送器（浏览器面板操作） */
  sendRequest?: (payload: Record<string, unknown>) => void;
  /** 浏览器画面区拾取结果（元素选择模式） */
  onPickResult?: (info: Record<string, unknown> | null) => void;
  /** 浏览器网址提交（预览卡片 + 面板输入框；启动浏览器并直达） */
  onOpenBrowser?: (url: string) => void;
  /** 元素选择模式（浏览器 Tab 工具栏开关；受控） */
  pickMode?: boolean;
  /** 切换元素选择模式 */
  onTogglePickMode?: () => void;
  /** 用户点击（target=_blank）产生的新浏览器 tab 已创建：激活它 */
  onGuestTabOpened?: () => void;
}

/**
 * 文件预览停靠列组件（多标签页）
 *
 * @param props - 组件属性
 * @returns 返回停靠列的 JSX 元素
 */
export default function FilePreviewPanel({
  lang, tabs, activeTab, width, hasDiff, rootDirLabel,
  onActivateTab, onCloseTab, onCloseOtherTabs, onOpenPath,
  onOpenContent, onOpenDiff, onPopOut, onHideColumn,
  browserState, browserFrame, browserOpActive, browserStarting, isDesktop, sendRequest, onPickResult,
  onOpenBrowser, pickMode, onTogglePickMode, onGuestTabOpened,
}: FilePreviewPanelProps) {
  // 浏览器 Tab：主体由 BrowserPreviewBody 全权渲染（工具栏 + 画面区），
  // 文件专属头部（路径/复制/Diff 切换）不适用
  // 占位兜底：forceMount 门下 activePreviewTab 可能为 null（浏览器 open 但未激活）
  const activePreview = activeTab ?? tabs[0] ?? null;
  if (!activePreview) {
    return <aside className="panel-card panel-card-right-stack preview-card flex flex-col h-full shrink-0 overflow-hidden select-none" style={{ width: `${width}px` }} />;
  }
  const isBrowserTab = activePreview.kind === 'browser';
  // 浏览器层展示的 tab：激活时为当前浏览器 tab（内容与顶栏一致），
  // 文件 tab 激活时保持第一个浏览器 tab 常驻（保活，display 隐藏）
  const browserTabInStrip = isBrowserTab
    ? activeTab
    : (tabs.find((tb) => tb.kind === 'browser') ?? null);
  const [dir, filename] = splitFilePath(activePreview.path);
  // 根目录文件（无目录前缀）用工作区目录名补全路径展示，与带目录的文件统一风格
  const dirLabel = dir || (!activePreview.synthetic && rootDirLabel ? rootDirLabel : '');
  const payload = activePreview.payload;
  const isDiff = activePreview.kind === 'diff';
  // 视图切换按钮：diff 视图始终保留"内容"入口以退出；内容视图仅在存在 Git
  // 变更（或未知，宽限处理）时展示"Diff"，无变更文件不显示以免展示空的 diff
  const showToggle = !payload?.error && !payload?.binary && (isDiff || hasDiff !== false);

  // 激活 tab 变化时滚动到可视区（横向溢出场景）
  const stripRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const strip = stripRef.current;
    if (!strip) return;
    strip.querySelector('[data-active-tab="true"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }, [activePreview.key, tabs.length]);

  return (
    <aside
      className="panel-card panel-card-right-stack preview-card flex flex-col h-full shrink-0 overflow-hidden select-none"
      style={{ width: `${width}px` }}
    >
      {/* 标签页条：等宽 tab（横向滚动承接溢出）+ 右端「按路径打开」「关闭菜单」 */}
      <div className="flex items-center gap-1 px-2 pt-2 shrink-0">
        <div ref={stripRef} className="flex flex-1 min-w-0 items-center gap-1 overflow-x-auto scrollbar-hidden">
          {tabs.map((tab) => {
            const active = tab.key === activePreview.key;
            const isBrowser = tab.kind === 'browser';
            const [, tabName] = splitFilePath(tab.path);
            // 浏览器标签页展示页面标题/URL（占位页无 tab 时用「浏览器」）；
            const displayName = isBrowser
              ? (tab.browserTabId ? tab.path : t(lang, 'browser_title'))
              : tabName;
            return (
              <div
                key={tab.key}
                data-active-tab={active ? 'true' : undefined}
                onClick={() => onActivateTab(tab.key)}
                /* 中键：mousedown 阶段即取消默认行为，避免触发浏览器自动滚动光标 */
                onMouseDown={(e) => { if (e.button === 1) e.preventDefault(); }}
                onAuxClick={(e) => { if (e.button === 1) { e.preventDefault(); onCloseTab(tab.key); } }}
                title={isBrowser ? displayName : tab.path}
                className={`group as-host relative flex h-7 flex-[1_1_7rem] min-w-[4.5rem] max-w-[10rem] items-center gap-1 rounded-lg border px-1.5 text-xs whitespace-nowrap transition-colors cursor-default ${
                  active
                    ? 'border-border-medium bg-[var(--bg-hover)] font-semibold text-content-primary shadow-soft'
                    : 'border-transparent font-medium text-content-secondary hover:bg-[var(--badge-bg-subtle)] hover:text-content-primary'
                }`}
              >
                {isBrowser ? (
                  <GlobeIcon className="w-3.5 h-3.5 shrink-0 text-primary" />
                ) : (
                  <FileIcon className="w-3.5 h-3.5 shrink-0" color={fileIconColor(tab.path)} />
                )}
                {/* 悬浮自动滚动：标题被截断时悬浮平滑滚动到尾部 */}
                <AutoScrollText trigger="parent" className="min-w-0 flex-1 text-xs" title={displayName}>
                  {displayName}
                </AutoScrollText>
                {tab.kind === 'diff' && (
                  <span className={`shrink-0 rounded-full border px-1 text-[10px] leading-3 ${
                    active ? 'border-border-medium bg-surface-card text-content-secondary' : 'border-border-light text-content-secondary'
                  }`}>
                    {t(lang, 'diff_view')}
                  </span>
                )}
                <button
                  onClick={(e) => { e.stopPropagation(); onCloseTab(tab.key); }}
                  title={`${t(lang, 'close_tab')} · ${displayName}`}
                  aria-label={`${t(lang, 'close_tab')} · ${displayName}`}
                  className={`shrink-0 w-4 h-4 flex items-center justify-center rounded-md text-content-secondary hover:text-content-primary hover:bg-[var(--badge-bg-subtle)] transition-opacity cursor-pointer ${
                    active ? 'opacity-80 hover:opacity-100' : 'opacity-0 group-hover:opacity-70'
                  }`}
                >
                  <CloseIcon className="w-2.5 h-2.5" />
                </button>
              </div>
            );
          })}
        </div>
        {/* 标签条操作区：关闭其他（垃圾桶）· 新建（+）· 收起（减号）· 关闭标签页（叉） */}
        <button
          onClick={() => onCloseOtherTabs(activePreview.key)}
          disabled={tabs.length <= 1}
          title={t(lang, 'close_other_tabs')}
          aria-label={t(lang, 'close_other_tabs')}
          className="w-6 h-6 flex items-center justify-center rounded-md text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
        >
          <TrashIcon className="w-3.5 h-3.5" />
        </button>
        <TabStripActions
          lang={lang}
          onOpenPath={onOpenPath}
          onOpenBrowserUrl={(url) => onOpenBrowser?.(url)}
        />
        <button
          onClick={() => onHideColumn?.()}
          title={t(lang, 'preview_minimize')}
          aria-label={t(lang, 'preview_minimize')}
          className="w-6 h-6 flex items-center justify-center rounded-md text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer shrink-0"
        >
          <MinusIcon className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={() => onCloseTab(activePreview.key)}
          title={t(lang, 'close_tab')}
          aria-label={t(lang, 'close_tab')}
          className="w-6 h-6 flex items-center justify-center rounded-md text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer shrink-0"
        >
          <CloseIcon className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* 头部：激活 tab 的完整路径 + 视图切换/复制/弹窗/关闭（浏览器 Tab 无文件头部，
          由 BrowserPreviewBody 自带工具栏） */}
      {!isBrowserTab && (
      <div className="px-4 pt-2.5 pb-3 border-b border-border-light shrink-0">
        <div className="flex items-center gap-1.5">
          <div className="flex-1 min-w-0">
            {/* 路径行：目录弱化 + 文件名；截断时悬浮自动滚动展示完整路径 */}
            <AutoScrollText className="text-sm font-semibold text-content-primary" title={activePreview.path}>
              {dirLabel && <span className="text-content-disabled font-normal">{dirLabel}</span>}
              {filename}
            </AutoScrollText>
          </div>
          {/* 左侧文字按钮：内容 / Diff 视图切换 + 复制（字体样式统一） */}
          {showToggle && (
            <button
              onClick={() => (isDiff ? onOpenContent(activePreview.key) : onOpenDiff(activePreview.key))}
              title={isDiff ? t(lang, 'content_view') : t(lang, 'diff_view')}
              className="px-2 py-1 text-[11px] font-semibold rounded-md text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer shrink-0"
            >
              {isDiff ? t(lang, 'content_view') : t(lang, 'diff_view')}
            </button>
          )}
          <CopyButton lang={lang} payload={payload ?? { path: activePreview.path }} />
          {/* 右侧图标按钮：弹窗查看 + 收起（隐藏卡片但保留所有 tab 与浏览器，
              不卸载 guest） */}
          <button
            onClick={onPopOut}
            title={t(lang, 'popout_preview')}
            aria-label={t(lang, 'popout_preview')}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer shrink-0"
          >
            <PopOutIcon className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => onCloseTab(activePreview.key)}
            title={t(lang, 'close_tab')}
            aria-label={t(lang, 'close_tab')}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer shrink-0"
          >
            <CloseIcon className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
      )}

      {/* 主体区域（relative：浏览器层常驻，切文件 tab 时 display:none 保活
          —— Electron guest 一旦卸载，切回即整页重载，保活同 display 隐藏） */}
      <div className="relative flex-1 min-h-0">
        {browserTabInStrip && (
          <div
            className="absolute inset-0 overflow-hidden"
            style={isBrowserTab
              ? undefined
              // 非激活保活：0.001 透明度保留 compositor surface（display:none
              // 会让 capturePage 截空图）；pointer-events 放行底层文件内容
              : { opacity: 0.001, pointerEvents: 'none', zIndex: 0 }}
          >
            <BrowserPreviewBody
              lang={lang}
              browserState={browserState ?? null}
              browserFrame={browserFrame ?? null}
              browserOpActive={browserOpActive ?? false}
              starting={browserStarting ?? false}
              isDesktop={isDesktop ?? false}
              sendRequest={sendRequest ?? (() => undefined)}
              onPickResult={onPickResult}
              autoFocusUrl
              pickMode={pickMode}
              onTogglePickMode={onTogglePickMode}
              onGuestTabOpened={onGuestTabOpened}
              browserTabId={browserTabInStrip.browserTabId}
            />
          </div>
        )}
        {!isBrowserTab && (
          <div className="absolute inset-0 overflow-hidden select-text">
            <FilePreviewBody lang={lang} payload={payload ?? { path: activePreview.path }} loading={activePreview.loading} />
          </div>
        )}
      </div>
    </aside>
  );
}

/**
 * 标签页条右端操作区：「+」新建标签页
 *
 * 一个按钮、一个浮层，两种目标各占一行：工作区路径（后端 web_read_file
 * 已放开相对路径与绝对路径）与浏览器网址（走多开逻辑，每次新建浏览器
 * 标签页）。回车即提交、Esc 或点击外部关闭。
 */
function TabStripActions({ lang, onOpenPath, onOpenBrowserUrl }: {
  lang: UiLanguage;
  onOpenPath: (path: string) => void;
  /** 浏览器网址提交（新建浏览器标签页并直达） */
  onOpenBrowserUrl?: (url: string) => void;
}) {
  const [addOpen, setAddOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [browserDraft, setBrowserDraft] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  // 点击外部关闭浮层；Esc 关闭
  useEffect(() => {
    if (!addOpen) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setAddOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setAddOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [addOpen]);

  // 展开浮层时聚焦路径输入（等待面板挂载）
  useEffect(() => {
    if (addOpen) inputRef.current?.focus();
  }, [addOpen]);

  const submitPath = () => {
    const value = draft.trim().replace(/^"(.*)"$/, '$1');
    if (!value) return;
    onOpenPath(value.replace(/\\/g, '/'));
    setDraft('');
    setAddOpen(false);
  };

  /** 浏览器网址提交：新建标签页并直达（非网址输入回退搜索） */
  const submitBrowser = () => {
    const target = normalizeBrowserInput(browserDraft);
    if (!target) return;
    setBrowserDraft('');
    setAddOpen(false);
    onOpenBrowserUrl?.(target);
  };

  return (
    <div ref={rootRef} className="relative shrink-0 flex items-center gap-0.5 pr-0.5">
      <button
        onClick={() => setAddOpen((v) => !v)}
        title={t(lang, 'add_tab_menu')}
        aria-label={t(lang, 'add_tab_menu')}
        aria-expanded={addOpen}
        className="w-6 h-6 flex items-center justify-center rounded-md text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer"
      >
        <PlusIcon className="w-3.5 h-3.5" />
      </button>
      {addOpen && (
        <div className="absolute right-0 top-8 z-30 flex flex-col gap-1.5 bg-surface-card-alt border border-border-medium rounded-lg shadow-card p-2 w-[300px] max-w-[340px] animate-fade">
          <div className="flex items-center gap-1">
            <FileIcon className="w-3.5 h-3.5 shrink-0" color="var(--primary)" />
            <input
              ref={inputRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') submitPath(); }}
              placeholder={t(lang, 'open_file_path_placeholder')}
              className="flex-1 min-w-0 bg-transparent text-xs text-content-primary placeholder:text-content-disabled px-2 py-1.5 rounded-md border border-border-light focus:border-primary outline-none"
            />
          </div>
          {onOpenBrowserUrl && (
            <div className="flex items-center gap-1">
              <GlobeIcon className="w-3.5 h-3.5 text-primary shrink-0" />
              <input
                value={browserDraft}
                onChange={(e) => setBrowserDraft(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') submitBrowser(); }}
                placeholder={t(lang, 'browser_open_url_placeholder')}
                spellCheck={false}
                className="flex-1 min-w-0 bg-transparent text-xs text-content-primary placeholder:text-content-disabled px-2 py-1.5 rounded-md border border-border-light focus:border-primary outline-none"
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
