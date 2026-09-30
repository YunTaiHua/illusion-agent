/**
 * @fileoverview 文件预览停靠列组件（多标签页）
 *
 * 右栏右侧的文件内容停靠区（默认查看形态），多标签页交互：
 * 顶部为可横向滚动的标签页条——每点开一个文件累积一个 tab（文件类型
 * 图标 + 文件名 + Diff 徽标 + 悬浮关闭按钮），同一时刻显示激活 tab 的
 * 内容，互不顶掉；右端提供「按路径打开」（+，可输入工作区内相对路径
 * 或任意绝对路径）与「⋯」菜单（关闭其他 / 关闭全部标签页）。
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
import { CopyButton, FilePreviewBody, splitFilePath } from './FileViewerModal';
import { CloseIcon, FileIcon, PlusIcon } from './icons';
import type { PreviewTab } from '../types/protocol';

/**
 * FilePreviewPanel 组件属性接口
 */
interface FilePreviewPanelProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 已打开的预览标签页列表 */
  tabs: PreviewTab[];
  /** 当前激活标签页（tabs 非空时必有） */
  activeTab: PreviewTab;
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
  /** 关闭全部标签页 */
  onCloseAllTabs: () => void;
  /** 按路径打开新标签页（工作区内相对路径或任意绝对路径） */
  onOpenPath: (path: string) => void;
  /** 切换激活 tab 到内容视图（原地切换；内容 tab 已存在时直接激活它） */
  onOpenContent: (key: string) => void;
  /** 切换激活 tab 到 diff 视图（原地切换；diff tab 已存在时直接激活它） */
  onOpenDiff: (key: string) => void;
  /** 弹窗查看（放大为全屏弹窗） */
  onPopOut: () => void;
  /** 关闭当前激活标签页 */
  onClose: () => void;
}

/**
 * 文件预览停靠列组件（多标签页）
 *
 * @param props - 组件属性
 * @returns 返回停靠列的 JSX 元素
 */
export default function FilePreviewPanel({
  lang, tabs, activeTab, width, hasDiff, rootDirLabel,
  onActivateTab, onCloseTab, onCloseOtherTabs, onCloseAllTabs, onOpenPath,
  onOpenContent, onOpenDiff, onPopOut, onClose,
}: FilePreviewPanelProps) {
  const [dir, filename] = splitFilePath(activeTab.path);
  // 根目录文件（无目录前缀）用工作区目录名补全路径展示，与带目录的文件统一风格
  const dirLabel = dir || (!activeTab.synthetic && rootDirLabel ? rootDirLabel : '');
  const payload = activeTab.payload;
  const isDiff = activeTab.kind === 'diff';
  // 视图切换按钮：diff 视图始终保留"内容"入口以退出；内容视图仅在存在 Git
  // 变更（或未知，宽限处理）时展示"Diff"，无变更文件不显示以免展示空的 diff
  const showToggle = !payload?.error && !payload?.binary && (isDiff || hasDiff !== false);

  // 激活 tab 变化时滚动到可视区（横向溢出场景）
  const stripRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const strip = stripRef.current;
    if (!strip) return;
    strip.querySelector('[data-active-tab="true"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }, [activeTab.key, tabs.length]);

  return (
    <aside
      className="panel-card panel-card-right-stack preview-card flex flex-col h-full shrink-0 overflow-hidden select-none"
      style={{ width: `${width}px` }}
    >
      {/* 标签页条：等宽 tab（横向滚动承接溢出）+ 右端「按路径打开」「关闭菜单」 */}
      <div className="flex items-center gap-1 px-2 pt-2 shrink-0">
        <div ref={stripRef} className="flex flex-1 min-w-0 items-center gap-1 overflow-x-auto scrollbar-hidden">
          {tabs.map((tab) => {
            const active = tab.key === activeTab.key;
            const [, tabName] = splitFilePath(tab.path);
            return (
              <div
                key={tab.key}
                data-active-tab={active ? 'true' : undefined}
                onClick={() => onActivateTab(tab.key)}
                /* 中键：mousedown 阶段即取消默认行为，避免触发浏览器自动滚动光标 */
                onMouseDown={(e) => { if (e.button === 1) e.preventDefault(); }}
                onAuxClick={(e) => { if (e.button === 1) { e.preventDefault(); onCloseTab(tab.key); } }}
                title={tab.path}
                className={`group as-host relative flex h-7 flex-[1_1_7rem] min-w-[4.5rem] max-w-[10rem] items-center gap-1 rounded-lg border px-1.5 text-xs whitespace-nowrap transition-colors cursor-default ${
                  active
                    ? 'border-border-medium bg-[var(--bg-hover)] font-semibold text-content-primary shadow-soft'
                    : 'border-transparent font-medium text-content-secondary hover:bg-[var(--badge-bg-subtle)] hover:text-content-primary'
                }`}
              >
                <FileIcon className="w-3.5 h-3.5 shrink-0" color={fileIconColor(tab.path)} />
                {/* 悬浮自动滚动：标题被截断时悬浮平滑滚动到尾部 */}
                <AutoScrollText trigger="parent" className="min-w-0 flex-1 text-xs" title={tabName}>
                  {tabName}
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
                  title={`${t(lang, 'close_tab')} · ${tabName}`}
                  aria-label={`${t(lang, 'close_tab')} · ${tabName}`}
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
        <TabStripActions
          lang={lang}
          tabCount={tabs.length}
          onOpenPath={onOpenPath}
          onCloseOtherTabs={onCloseOtherTabs}
          onCloseAllTabs={onCloseAllTabs}
          activeKey={activeTab.key}
        />
      </div>

      {/* 头部：激活 tab 的完整路径 + 视图切换/复制/弹窗/关闭 */}
      <div className="px-4 pt-2.5 pb-3 border-b border-border-light shrink-0">
        <div className="flex items-center gap-1.5">
          <div className="flex-1 min-w-0">
            {/* 路径行：目录弱化 + 文件名；截断时悬浮自动滚动展示完整路径 */}
            <AutoScrollText className="text-sm font-semibold text-content-primary" title={activeTab.path}>
              {dirLabel && <span className="text-content-disabled font-normal">{dirLabel}</span>}
              {filename}
            </AutoScrollText>
          </div>
          {/* 左侧文字按钮：内容 / Diff 视图切换 + 复制（字体样式统一） */}
          {showToggle && (
            <button
              onClick={() => (isDiff ? onOpenContent(activeTab.key) : onOpenDiff(activeTab.key))}
              title={isDiff ? t(lang, 'content_view') : t(lang, 'diff_view')}
              className="px-2 py-1 text-[11px] font-semibold rounded-md text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer shrink-0"
            >
              {isDiff ? t(lang, 'content_view') : t(lang, 'diff_view')}
            </button>
          )}
          <CopyButton lang={lang} payload={payload ?? { path: activeTab.path }} />
          {/* 右侧图标按钮：弹窗查看 + 关闭（风格一致） */}
          <button
            onClick={onPopOut}
            title={t(lang, 'popout_preview')}
            aria-label={t(lang, 'popout_preview')}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer shrink-0"
          >
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9.5 1.5h5v5" />
              <path d="M14.5 1.5L8 8" />
              <path d="M13 9.5v3A1.5 1.5 0 0 1 11.5 14h-7A1.5 1.5 0 0 1 3 12.5v-7A1.5 1.5 0 0 1 4.5 4h3" />
            </svg>
          </button>
          <button
            onClick={onClose}
            title={t(lang, 'close_tab')}
            aria-label={t(lang, 'close_tab')}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer shrink-0"
          >
            <CloseIcon className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* 主体：共享渲染体（滚动条贴住面板边缘，不再留间隙；允许自由选中文本） */}
      <div className="flex-1 min-h-0 overflow-hidden select-text">
        <FilePreviewBody lang={lang} payload={payload ?? { path: activeTab.path }} loading={activeTab.loading} />
      </div>
    </aside>
  );
}

/**
 * 标签页条右端操作区：「按路径打开」（+）与「⋯」菜单（关闭其他/全部）
 *
 * 「按路径打开」展开一个小输入面板（Enter 提交 / Esc 或点击外部关闭），
 * 支持工作区内相对路径与任意绝对路径（后端 web_read_file 已放开）。
 */
function TabStripActions({ lang, tabCount, activeKey, onOpenPath, onCloseOtherTabs, onCloseAllTabs }: {
  lang: UiLanguage;
  tabCount: number;
  activeKey: string;
  onOpenPath: (path: string) => void;
  onCloseOtherTabs: (key: string) => void;
  onCloseAllTabs: () => void;
}) {
  const [pathOpen, setPathOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  // 点击外部关闭两个浮层；Esc 关闭路径输入
  useEffect(() => {
    if (!pathOpen && !menuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setPathOpen(false);
        setMenuOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setPathOpen(false); setMenuOpen(false); }
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [pathOpen, menuOpen]);

  // 展开路径输入时聚焦（等待面板挂载）
  useEffect(() => {
    if (pathOpen) inputRef.current?.focus();
  }, [pathOpen]);

  const submitPath = () => {
    const value = draft.trim().replace(/^"(.*)"$/, '$1');
    if (!value) return;
    onOpenPath(value.replace(/\\/g, '/'));
    setDraft('');
    setPathOpen(false);
  };

  return (
    <div ref={rootRef} className="relative shrink-0 flex items-center gap-0.5 pr-0.5">
      <button
        onClick={() => { setPathOpen((v) => !v); setMenuOpen(false); }}
        title={t(lang, 'open_file_path')}
        aria-label={t(lang, 'open_file_path')}
        className="w-6 h-6 flex items-center justify-center rounded-md text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer"
      >
        <PlusIcon className="w-3.5 h-3.5" />
      </button>
      <button
        onClick={() => { setMenuOpen((v) => !v); setPathOpen(false); }}
        title={t(lang, 'tab_actions')}
        aria-label={t(lang, 'tab_actions')}
        className="w-6 h-6 flex items-center justify-center rounded-md text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer"
      >
        {/* 横向三点（省略号菜单） */}
        <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="currentColor">
          <circle cx="3.5" cy="8" r="1.2" />
          <circle cx="8" cy="8" r="1.2" />
          <circle cx="12.5" cy="8" r="1.2" />
        </svg>
      </button>

      {/* 按路径打开：浮动输入面板 */}
      {pathOpen && (
        <div className="absolute right-0 top-8 z-30 flex items-center gap-1 bg-surface-card-alt border border-border-medium rounded-lg shadow-card p-1.5 w-[300px] max-w-[340px] animate-fade">
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') submitPath(); }}
            placeholder={t(lang, 'open_file_path_placeholder')}
            className="flex-1 min-w-0 bg-transparent text-xs text-content-primary placeholder:text-content-disabled px-2 py-1.5 rounded-md border border-border-light focus:border-primary outline-none"
          />
          <button
            onClick={submitPath}
            disabled={!draft.trim()}
            className="px-2.5 py-1.5 text-xs font-semibold text-white bg-primary hover:bg-primary-hover rounded-md transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
          >
            {t(lang, 'open_action')}
          </button>
        </div>
      )}

      {/* 标签页菜单：关闭其他 / 关闭全部 */}
      {menuOpen && (
        <div className="absolute right-0 top-8 z-30 flex flex-col bg-surface-card-alt border border-border-medium rounded-lg shadow-card p-1 min-w-[9rem] animate-fade">
          <button
            onClick={() => { onCloseOtherTabs(activeKey); setMenuOpen(false); }}
            disabled={tabCount <= 1}
            className="px-2.5 py-1.5 text-xs text-left text-content-primary rounded-md glass-option-hover transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {t(lang, 'close_other_tabs')}
          </button>
          <button
            onClick={() => { onCloseAllTabs(); setMenuOpen(false); }}
            className="px-2.5 py-1.5 text-xs text-left text-content-primary rounded-md glass-option-hover transition-colors cursor-pointer"
          >
            {t(lang, 'close_all_tabs')}
          </button>
        </div>
      )}
    </div>
  );
}
