/**
 * @fileoverview 连接遮罩层组件
 *
 * 首次启动连接后端时显示的全屏遮罩层，在连接失败时展示错误提示与解决方式。
 *
 * 视觉：白底 + 居中圆角应用图标。整层只有一种运动——opacity 淡入/淡出；
 * 无 canvas、无 CSS 关键帧，过渡全程由合成器驱动，淡出丝滑性不依赖主线程
 * 空闲度。
 *
 * 错误提示：分为认证失败（auth）与后端不可达（unreachable）两类，差异化
 * 展示解决方式。认证失败提供终端操作指引；不可达展示重连按钮。
 *
 * 衔接链：Desktop 白底+图标 → 图标淡出 → web 纯白 → 本遮罩（图标淡入）
 * → 后端就绪揭示主界面 → 两段式淡出（App.tsx：先遮后挂，双 rAF + 一拍
 * 空闲后启动过渡，onTransitionEnd 精确卸载）。
 *
 * @module ConnectingOverlay
 */

import { useEffect, useState } from 'react';
import type { UiLanguage } from '../i18n';
import { t } from '../i18n';
import type { WebConnectionError } from '../hooks/useWebSocketSession';

/**
 * ConnectingOverlay 组件属性接口
 */
interface ConnectingOverlayProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 淡出中：播放退出过渡并放行下层交互 */
  fading?: boolean;
  /** 淡出过渡完成回调 */
  onFaded?: () => void;
  /** 连接错误（认证失败 / 后端不可达），非 null 时显示错误提示 */
  connectionError?: WebConnectionError | null;
  /** 重新连接回调（不可达时显示按钮） */
  onRetry?: () => void;
  /** 重连中（按钮加载态，避免连点） */
  retrying?: boolean;
}

/**
 * 连接遮罩层组件
 *
 * 全屏纯白遮罩 + 居中静态应用图标 + 连接错误时展示错误提示与解决方式。
 *
 * @param props - 组件属性
 * @returns 遮罩层 JSX 元素
 */
export default function ConnectingOverlay({
  lang,
  fading,
  onFaded,
  connectionError,
  onRetry,
  retrying,
}: ConnectingOverlayProps) {
  const [visible, setVisible] = useState(false);

  // 挂载后下一帧再置可见：让 opacity 过渡接管淡入（而非初始即全显）
  useEffect(() => {
    const id = requestAnimationFrame(() => setVisible(true));
    return () => cancelAnimationFrame(id);
  }, []);

  const showError = connectionError && !fading;

  return (
    <div
      className={`fixed inset-0 z-[60] flex flex-col items-center justify-center bg-white transition-opacity ${
        fading || !visible ? 'opacity-0 pointer-events-none' : 'opacity-100'
      }`}
      style={{
        willChange: 'opacity',
        transform: 'translateZ(0)',
        transitionDuration: '700ms',
        transitionTimingFunction: 'cubic-bezier(0.22, 0.61, 0.36, 1)',
      }}
      aria-label={
        showError
          ? t(lang, 'overlay_error_title')
          : lang === 'zh-CN'
            ? '正在连接...'
            : 'Connecting...'
      }
      role="status"
      onTransitionEnd={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.propertyName !== 'opacity') return;
        if (fading && onFaded) onFaded();
      }}
    >
      <img
        src="/icon.png"
        alt=""
        width={512}
        height={512}
        draggable={false}
        className="select-none"
        style={{
          width: 'clamp(84px, 16vmin, 120px)',
          height: 'auto',
          filter:
            'drop-shadow(0 3px 10px rgba(0, 0, 0, 0.19)) drop-shadow(0 14px 40px rgba(0, 0, 0, 0.36))',
        }}
      />

      {/* 错误提示 */}
      {showError && (
        <div className="mt-8 max-w-md text-center px-4">
          <p className="text-base font-semibold text-gray-800 mb-2">
            {t(lang, 'overlay_error_title')}
          </p>
          <p className="text-sm text-gray-500 leading-relaxed">
            {connectionError.kind === 'auth'
              ? t(lang, 'overlay_error_auth_desc')
              : t(lang, 'overlay_error_unreachable_desc')}
          </p>
          {connectionError.kind === 'unreachable' && onRetry && (
            <button
              onClick={onRetry}
              disabled={retrying}
              className="mt-6 px-6 py-2.5 rounded-lg bg-gray-900 text-white text-sm font-medium
                         hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed
                         transition-colors select-none"
            >
              {retrying ? '...' : t(lang, 'overlay_retry')}
            </button>
          )}
        </div>
      )}
    </div>
  );
}