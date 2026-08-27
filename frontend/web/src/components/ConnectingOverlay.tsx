/**
 * @fileoverview 连接遮罩层组件
 *
 * 首次启动连接后端时显示的全屏遮罩层。
 * 视觉：白底 + 居中圆角应用图标（public/icon.png，与 Desktop 启动页同源
 * 资产）。整层只有一种运动——opacity 淡入/淡出；无 canvas、无 CSS 关键帧，
 * 过渡全程由合成器驱动，淡出丝滑性不依赖主线程空闲度。
 *
 * 衔接链：Desktop 白底+图标 → 图标淡出 → web 纯白 → 本遮罩（图标淡入）
 * → 后端就绪揭示主界面 → 两段式淡出（App.tsx：先遮后挂，双 rAF + 一拍
 * 空闲后启动过渡，onTransitionEnd 精确卸载）。
 *
 * @module ConnectingOverlay
 */

import { useEffect, useState } from 'react';
import type { UiLanguage } from '../i18n';

/**
 * ConnectingOverlay 组件属性接口
 */
interface ConnectingOverlayProps {
  /** 当前 UI 语言（用于无障碍 aria-label 国际化） */
  lang: UiLanguage;
  /** 淡出中：播放退出过渡并放行下层交互（父组件在过渡结束后卸载本组件） */
  fading?: boolean;
  /** 淡出过渡完成回调：父组件据此精确卸载（不依赖与时长硬编码的定时器） */
  onFaded?: () => void;
}

/**
 * 连接遮罩层组件
 *
 * 全屏纯白遮罩 + 居中静态应用图标。
 * 淡入：挂载后下一帧切到 opacity-100；淡出：fading 时切到 opacity-0，
 * 由 transition-opacity 700ms ease-out 平滑过渡（快出缓收，退场利落）。
 *
 * @param props - 组件属性
 * @returns 遮罩层 JSX 元素
 */
export default function ConnectingOverlay({ lang, fading, onFaded }: ConnectingOverlayProps) {
  const [visible, setVisible] = useState(false);

  // 挂载后下一帧再置可见：让 opacity 过渡接管淡入（而非初始即全显）
  useEffect(() => {
    const id = requestAnimationFrame(() => setVisible(true));
    return () => cancelAnimationFrame(id);
  }, []);

  return (
    <div
      className={`fixed inset-0 z-[60] flex items-center justify-center bg-white transition-opacity ${fading || !visible ? 'opacity-0 pointer-events-none' : 'opacity-100'}`}
      style={{
        // 显式提升合成层：淡出全程由合成器线程驱动，
        // 主线程即便偶发长任务也无法卡住过渡
        willChange: 'opacity',
        transform: 'translateZ(0)',
        transitionDuration: '700ms',
        transitionTimingFunction: 'cubic-bezier(0.22, 0.61, 0.36, 1)',
      }}
      aria-label={lang === 'zh-CN' ? '正在连接...' : 'Connecting...'}
      role="status"
      // 淡出过渡自然结束时通知父组件卸载；只认本元素的 opacity 过渡，
      // 避免子元素过渡冒泡误触发
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
          // 双层投影：近距接触影 + 远距环境影，比单层更接近系统图标的悬浮质感
          filter:
            'drop-shadow(0 3px 10px rgba(0, 0, 0, 0.19)) drop-shadow(0 14px 40px rgba(0, 0, 0, 0.36))',
        }}
      />
    </div>
  );
}
