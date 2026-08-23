/**
 * @fileoverview 连接遮罩层组件
 *
 * 首次启动连接后端时显示的全屏遮罩层，替代原顶部"正在连接..."横条。
 * 零外部依赖，纯 SVG + CSS 动画。
 *
 * 视觉：莫比乌斯环 — 一条连续的光带沿路径流动，
 * 渐变色彩为浅色主题的青紫两色循环，无光晕，遮罩底色保持白色。
 * 整体缓慢呼吸缩放，象征"持续连接、无限循环"。
 *
 * @module ConnectingOverlay
 */

import type { UiLanguage } from '../i18n';

/**
 * ConnectingOverlay 组件属性接口
 */
interface ConnectingOverlayProps {
  /** 当前 UI 语言（用于无障碍 aria-label 国际化） */
  lang: UiLanguage;
  /** 淡出中：播放退出动画并放行下层交互（父组件在动画结束后卸载本组件） */
  fading?: boolean;
}

/**
 * 莫比乌斯环路径（80x80 viewBox 内，中心 40,40）
 *
 * ∞ 造型：由两段三次贝塞尔曲线组成的闭合环带，
 * 线宽 3.5，stroke-dasharray 动画模拟光带流动。
 */
const MOBIUS_PATH = [
  'M 40 20',
  'C 55 20, 65 32, 55 40',
  'C 48 46, 44 36, 40 36',
  'C 36 36, 32 46, 25 40',
  'C 15 32, 25 20, 40 20',
].join(' ');

/**
 * 连接遮罩层组件
 *
 * 全屏半透明模糊遮罩 + 居中莫比乌斯环光带流动 + 呼吸缩放。
 *
 * @param props - 组件属性
 * @returns 遮罩层 JSX 元素
 */
export default function ConnectingOverlay({ lang, fading }: ConnectingOverlayProps) {
  return (
    <div
      className={`fixed inset-0 z-[60] flex items-center justify-center bg-[#ffffff]/95 backdrop-blur-2xl ${fading ? 'animate-fade-out pointer-events-none' : 'animate-fade-in'}`}
      aria-label={lang === 'zh-CN' ? '正在连接...' : 'Connecting...'}
      role="status"
    >
      {/* 莫比乌斯环 — 整体呼吸 pulse：scale 1↔1.08，opacity 0.9↔1，2.5s（纯色 #dc4a78，无渐变无光晕） */}
      <svg width="112" height="112" viewBox="0 0 80 80" className="animate-pulse-soft">
        {/* 莫比乌斯环主线：纯色光带流动 */}
        <path
          d={MOBIUS_PATH}
          stroke="#dc4a78"
          strokeWidth="3.5"
          fill="none"
          strokeLinecap="round"
          strokeDasharray="100 28"
          strokeDashoffset="0"
          style={{
            animation: 'mobi-flow 1.8s linear infinite',
          }}
        />
      </svg>
    </div>
  );
}
