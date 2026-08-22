/**
 * @fileoverview 自定义顶部栏组件（Electron 桌面壳专用）
 *
 * 仅在 Electron 桌面壳内渲染（检测 window.illusionDesktop）。
 * 提供窗口拖拽区与最小化/最大化/关闭按钮，简约现代风。
 *
 * 平台差异：
 *   - macOS：保留原生交通灯按钮，左侧留 80px 空间，仅渲染拖拽区与应用名。
 *   - Windows/Linux：渲染右侧自定义 min/max/close 按钮。
 *
 * 拖拽区通过 CSS 类 .app-region-drag / .app-region-no-drag 设置
 * -webkit-app-region（见 index.css），避免扩展 React CSSProperties 类型。
 *
 * @module TitleBar
 */

import { t, type UiLanguage } from '../i18n';

/** 是否运行在桌面壳内（模块加载时求值一次） */
const isDesktop = typeof window !== 'undefined' && !!window.illusionDesktop;

/** 窗口控制按钮（min/max/close） */
function WindowButton({
  onClick,
  variant,
  children,
  title,
}: {
  onClick: () => void;
  variant: 'normal' | 'close';
  children: React.ReactNode;
  title: string;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      aria-label={title}
      className={`w-11 h-9 flex items-center justify-center transition-colors ${
        variant === 'close'
          ? 'hover:bg-danger hover:text-white text-content-secondary'
          : 'hover:bg-black/10 dark:hover:bg-white/10 text-content-secondary'
      }`}
    >
      {children}
    </button>
  );
}

/**
 * 自定义顶部栏。浏览器端返回 null，仅桌面壳内渲染。
 *
 * @param props - 组件属性
 * @returns 顶部栏 JSX 元素（浏览器端返回 null）
 */
export default function TitleBar({ lang }: { lang: UiLanguage }) {
  if (!isDesktop) return null;

  const platform = window.illusionDesktop?.platform ?? '';
  const isMac = platform === 'darwin';
  const api = window.illusionDesktop;

  return (
    <div
      className="app-region-drag flex items-center h-9 shrink-0 select-none border-b border-border-medium relative z-50"
      style={{
        background: 'var(--glass-bg-panel)',
        backdropFilter: 'blur(var(--glass-blur-panel)) saturate(var(--glass-saturate))',
        paddingLeft: isMac ? '80px' : '12px',
      }}
    >
      {/* 应用名（居中偏左，简约标识） */}
      <span className="text-xs font-medium text-content-secondary tracking-wide">
        Illusion Agent
      </span>

      <div className="flex-1" />

      {/* Windows/Linux：右侧自定义窗口控制按钮；macOS 使用原生交通灯 */}
      {!isMac && api && (
        <div className="app-region-no-drag flex items-center">
          <WindowButton onClick={api.minimize} variant="normal" title={t(lang, 'window_minimize')}>
            <svg width="11" height="11" viewBox="0 0 11 11" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round">
              <path d="M1.5 5.5h8" />
            </svg>
          </WindowButton>
          <WindowButton onClick={api.toggleMaximize} variant="normal" title={t(lang, 'window_maximize')}>
            <svg width="11" height="11" viewBox="0 0 11 11" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round">
              <rect x="1.5" y="1.5" width="8" height="8" rx="1" />
            </svg>
          </WindowButton>
          <WindowButton onClick={api.close} variant="close" title={t(lang, 'window_close')}>
            <svg width="11" height="11" viewBox="0 0 11 11" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round">
              <path d="M2 2l7 7M9 2l-7 7" />
            </svg>
          </WindowButton>
        </div>
      )}
    </div>
  );
}
