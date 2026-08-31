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
import { isIconVisible, useDesktopUpdater } from '../hooks/useDesktopUpdater';

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
      className="w-11 h-9 group flex items-center justify-center text-content-secondary"
    >
      {/* 悬停态为居中圆角小圆片填充 */}
      <span className={`flex items-center justify-center w-7 h-7 rounded-full transition-colors ${
        variant === 'close'
          ? 'group-hover:bg-danger group-hover:text-white'
          : 'group-hover:bg-black/10 dark:group-hover:bg-white/15 dark:group-hover:text-white'
      }`}>
        {children}
      </span>
    </button>
  );
}

/** 进度环几何常量（r=6 的圆周长，供 stroke-dasharray 计算） */
const RING_RADIUS = 6;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

/**
 * 顶栏更新图标（最小化按钮附近）。
 * 发现新版本后自动下载：下载中显示环形进度，就绪后点击立即重启安装；
 * 不点击则应用退出时自动安装。检查中/无更新/失败时隐藏（失败等下次复查）。
 * 图标语义区分：下载态（available）为箭头向下的下载图标；安装就绪态
 * （downloaded）为圆圈对勾图标，避免用户误以为仍需再次下载。
 */
function UpdateButton({ lang }: { lang: UiLanguage }) {
  const { state, startDownload, installNow } = useDesktopUpdater();
  if (!isIconVisible(state)) return null;

  const readyTitle = state.version
    ? t(lang, 'updater_ready_desc').replace('{version}', state.version)
    : t(lang, 'updater_ready_desc_no_version');

  // 发现新版本：点击开始下载（箭头向下，语义"下载"）
  if (state.status === 'available') {
    const availableTitle = state.version
      ? t(lang, 'updater_available_tooltip').replace('{version}', state.version)
      : t(lang, 'updater_available_tooltip_no_version');
    return (
      <button
        onClick={startDownload}
        title={availableTitle}
        aria-label={availableTitle}
        className="w-8 h-9 group flex items-center justify-center text-primary cursor-pointer animate-updater-blink"
      >
        <span className="flex items-center justify-center w-7 h-7 rounded-full transition-colors group-hover:bg-primary-light group-hover:text-primary-hover dark:group-hover:bg-white/10">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M8 2v7.5" />
            <path d="M4.8 7l3.2 3.2L11.2 7" />
            <path d="M2.5 13.5h11" />
          </svg>
        </span>
      </button>
    );
  }

  // 安装就绪态：点击退出应用并显式安装（圆圈对勾，语义"安装就绪，点击安装"；
  // 不再随应用退出自动安装，避免用户无感知触发安装）
  if (state.status === 'downloaded') {
    return (
      <button
        onClick={installNow}
        title={readyTitle}
        aria-label={readyTitle}
        className="w-8 h-9 group flex items-center justify-center text-primary cursor-pointer"
      >
        <span className="flex items-center justify-center w-7 h-7 rounded-full transition-colors group-hover:bg-primary-light group-hover:text-primary-hover dark:group-hover:bg-white/10">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="8" cy="8" r="6.25" />
            <path d="M5.6 8.2l1.7 1.7 3.1-3.4" />
          </svg>
        </span>
      </button>
    );
  }

  const downloadingTitle = state.version
    ? t(lang, 'updater_downloading_desc').replace('{version}', state.version)
    : t(lang, 'updater_downloading_desc_no_version');

  // 下载中：环形进度（stroke-dasharray 按 percent 截取圆周）
  if (state.status === 'downloading' && state.progress) {
    const percent = Math.min(100, Math.max(0, state.progress.percent));
    return (
      <span
        title={`${downloadingTitle} ${percent.toFixed(0)}%`}
        className="w-8 h-9 flex items-center justify-center text-primary"
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" strokeWidth="1.5" strokeLinecap="round">
          <circle cx="8" cy="8" r={RING_RADIUS} stroke="currentColor" strokeOpacity="0.25" />
          <circle
            cx="8"
            cy="8"
            r={RING_RADIUS}
            stroke="currentColor"
            strokeDasharray={`${(percent / 100) * RING_CIRCUMFERENCE} ${RING_CIRCUMFERENCE}`}
            transform="rotate(-90 8 8)"
          />
        </svg>
      </span>
    );
  }

  // 下载中但进度事件未到：虚线环旋转占位
  return (
    <span
      title={downloadingTitle}
      className="w-8 h-9 flex items-center justify-center text-primary"
    >
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" className="animate-spin">
        <circle cx="8" cy="8" r={RING_RADIUS} strokeDasharray="8 6" />
      </svg>
    </span>
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
      className="app-region-drag app-titlebar flex items-center h-9 shrink-0 select-none relative z-50"
      style={{
        paddingLeft: isMac ? '80px' : '12px',
      }}
    >
      {/* 品牌展示（仅 Win/Linux）：图标 + 标语，纯展示无交互；mac 交由原生交通灯区 */}
      {!isMac && (
        <div className="flex items-center gap-2 mr-3">
          <img src="/icon.png" alt="" width={20} height={20} draggable={false} className="select-none" />
          <span className="font-body font-bold text-content-primary text-xs tracking-wider whitespace-nowrap">
            {t(lang, 'titlebar_slogan')}
          </span>
        </div>
      )}
      <div className="flex-1" />

      {/* macOS：原生交通灯在左侧，更新图标置于顶栏右侧 */}
      {isMac && (
        <div className="app-region-no-drag pr-3 flex items-center">
          <UpdateButton lang={lang} />
        </div>
      )}

      {/* Windows/Linux：更新图标紧邻最小化按钮，右侧自定义窗口控制按钮 */}
      {!isMac && api && (
        <div className="app-region-no-drag flex items-center">
          <UpdateButton lang={lang} />
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
