/**
 * @fileoverview 右栏浏览器区块（父级折叠区块；样式与技能/MCP 等区块一致）
 *
 * 结构与其他折叠区块一致，默认折叠 + 悬浮特效（图标淡出→chevron 淡入
 * + 计数徽标），点击展开查看。展开后——
 * - 每个受控 tab 一个子区块行（与side pane「每个浏览器 tab 一个标签
 *   页」同构，缩进对齐其他区块子行）：放大镜图标 + 标题/URL；无计数徽标、
 *   无关闭按钮（多 tab 管理在预览卡片顶栏）；
 * - 底部常显网址输入框：放大镜图标点击即提交（不再单独保留右箭头按钮），
 *   回车同样提交；浏览器未运行时提交=启动并直达，运行中=新开 tab。
 *
 * 本区块自身不触发"打开浏览器"——打开动作只来自 URL 提交或显式按钮，
 * 避免展开查看时闪现空白页。
 *
 * @module BrowserSection
 */

import { useState } from 'react';
import { t, type UiLanguage } from '../i18n';
import { normalizeBrowserInput } from '../lib/browserUrl';
import { ChevronRightIcon, CloseIcon, GlobeIcon, SearchIcon } from './icons';
import type { BrowserState } from '../types/protocol';

/** 浏览器区块属性 */
interface BrowserSectionProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 浏览器状态（browser_state；null = 后端尚未推送） */
  browserState: BrowserState | null;
  /** agent 操作中呼吸指示 */
  browserOpActive: boolean;
  /** 在预览卡片中打开该行对应的浏览器 Tab（不启动新浏览器实例） */
  onOpenPreview: (tabId: string) => void;
  /** 关闭浏览器 */
  onCloseBrowser: () => void;
  /** 启动浏览器并直达 URL（右栏输入框提交；newTab=true 时新开 tab） */
  onOpenAndNavigate: (url: string, newTab?: boolean) => void;
}

export default function BrowserSection({
  lang, browserState, browserOpActive, onOpenPreview, onCloseBrowser, onOpenAndNavigate,
}: BrowserSectionProps) {
  // 默认折叠（与其他父级区块一致）
  const [collapsed, setCollapsed] = useState(true);
  const [urlDraft, setUrlDraft] = useState('');
  const open = browserState?.open === true;
  const tabs = browserState?.tabs ?? [];

  const submitUrl = () => {
    const target = normalizeBrowserInput(urlDraft);
    if (!target) return;
    setUrlDraft('');
    // 运行中：新开 tab（多开）；未运行：启动并直达
    onOpenAndNavigate(target, open);
  };

  return (
    <div>
      {/* 父级行：与 CollapsibleSection 同款（图标槽 hover 切换 chevron + 标题 + 计数徽标；
          悬浮特效 glass-option-hover，默认折叠） */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => setCollapsed((v) => !v)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setCollapsed((v) => !v); } }}
        className="group/head mx-3 px-2 py-2 flex items-center gap-2 glass-option-hover transition-colors rounded-lg cursor-pointer select-none"
      >
        <div className="flex-1 min-w-0 flex items-center gap-2 py-0.5">
          <span className="relative w-4 h-4 shrink-0 flex items-center justify-center">
            <GlobeIcon className="absolute inset-0 m-auto w-3.5 h-3.5 text-content-secondary transition-opacity duration-100 group-hover/head:opacity-0" />
            <ChevronRightIcon
              className={`absolute inset-0 m-auto w-3 h-3 text-content-secondary opacity-0 group-hover/head:opacity-100 transition-[opacity,transform] duration-100 group-hover/head:duration-150 ${collapsed ? '' : 'rotate-90'}`}
            />
          </span>
          <span className="text-xs font-semibold text-content-primary tracking-wide">{t(lang, 'browser_title')}</span>
        </div>
        <span className="relative min-w-6 h-6 shrink-0 flex items-center justify-center">
          {browserOpActive ? (
            <span className="absolute inset-0 flex items-center justify-center animate-pulse" title={t(lang, 'browser_agent_operating')}>
              <SearchIcon className="w-3 h-3 text-primary" />
            </span>
          ) : (
            <span className="text-[10px] text-content-secondary bg-[var(--badge-bg-subtle)] px-1.5 py-0.5 rounded-full tabular-nums">
              {open ? tabs.length : 0}
            </span>
          )}
        </span>
      </div>

      {/* 展开内容：每个 tab 一个子区块行（缩进对齐 CollapsibleSection 子区）
          + 底部常显网址输入框 */}
      {!collapsed && (
        <div className="animate-fade">
          {open && tabs.length > 0 && (
            <div className="pt-0.5 pb-1 flex flex-col gap-0.5 max-h-56 overflow-y-auto dropdown-scroll">
              {tabs.map((tb) => (
                <div
                  key={tb.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => onOpenPreview(tb.id)}
                  onKeyDown={(e) => { if (e.key === 'Enter') onOpenPreview(tb.id); }}
                  title={t(lang, 'browser_open_in_preview')}
                  className="group/row flex items-center gap-2 mx-3 px-2 py-1 rounded-lg text-xs glass-option-hover transition-colors cursor-pointer text-left"
                >
                  <span className="relative w-6 h-6 shrink-0 flex items-center justify-center">
                    <SearchIcon className="w-3.5 h-3.5 text-primary" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-content-primary font-medium truncate">
                      {tb.title || tb.url || 'about:blank'}
                    </span>
                    {tb.url && tb.url !== 'about:blank' && (
                      <span className="block text-[10px] text-content-disabled truncate">{tb.url}</span>
                    )}
                  </span>
                  {/* 关闭浏览器（hover X；右侧 24px 槽位与父行徽标 min-w-6 同宽对齐） */}
                  <span className="relative w-6 h-6 shrink-0 flex items-center justify-center">
                    <button
                      onClick={(e) => { e.stopPropagation(); onCloseBrowser(); }}
                      title={t(lang, 'browser_close')}
                      aria-label={t(lang, 'browser_close')}
                      className="w-4 h-4 flex items-center justify-center rounded-md text-content-secondary opacity-0 group-hover/row:opacity-70 hover:!opacity-100 hover:text-danger transition-opacity cursor-pointer"
                    >
                      <CloseIcon className="w-2.5 h-2.5" />
                    </button>
                  </span>
                </div>
              ))}
            </div>
          )}
          {/* 网址输入框（常显）：放大镜图标点击即提交；与子行同一网格
              （mx-3 px-2 + 24px 图标槽），右缘 pr-6 与关闭 X 槽位等宽对齐 */}
          <div className="mx-3 px-2 pr-8 pb-2.5 flex items-center gap-2">
            <span className="relative w-6 h-6 shrink-0 flex items-center justify-center">
              <button
                onClick={submitUrl}
                disabled={!urlDraft.trim()}
                title={t(lang, 'browser_section_url_hint')}
                aria-label={t(lang, 'browser_open')}
                className="w-4 h-4 flex items-center justify-center rounded-md text-content-secondary hover:text-primary transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <SearchIcon className="w-3.5 h-3.5" />
              </button>
            </span>
            <input
              value={urlDraft}
              onChange={(e) => setUrlDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') submitUrl(); }}
              placeholder={t(lang, 'browser_section_url_placeholder')}
              title={t(lang, 'browser_section_url_hint')}
              spellCheck={false}
              className="flex-1 min-w-0 rounded-md border border-[var(--border-medium)] bg-transparent px-2 py-1 text-[11px] text-content-primary placeholder:text-content-disabled focus:outline-none focus:border-primary transition-colors"
            />
          </div>
        </div>
      )}
    </div>
  );
}
