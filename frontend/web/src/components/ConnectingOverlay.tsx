/**
 * @fileoverview 连接遮罩层组件
 *
 * 首次启动连接后端时显示的全屏遮罩层，替代原顶部"正在连接..."横条。
 * 零外部依赖，Canvas 2D + CSS 过渡。
 *
 * 视觉：粒子球体 — 斐波那契球面点阵绕竖直轴旋转，受光面粒子更大更实，
 * 背光粒子缩小变淡（磷光青 #2a9d99，与浅色主题标题「群青海岸」磷光带同族）。
 * 背景为纯白 bg-white：不透出下层 body 渐变，避免右下角浅灰光带，同时与
 * Desktop 加载页纯白同框一致。出现/消失用 1s opacity 过渡柔化。
 *
 * @module ConnectingOverlay
 */

import { useEffect, useState } from 'react';
import type { UiLanguage } from '../i18n';
import DotSphere from './DotSphere';

/**
 * ConnectingOverlay 组件属性接口
 */
interface ConnectingOverlayProps {
  /** 当前 UI 语言（用于无障碍 aria-label 国际化） */
  lang: UiLanguage;
  /** 淡出中：播放退出过渡并放行下层交互（父组件在动画结束后卸载本组件） */
  fading?: boolean;
}

/**
 * 连接遮罩层组件
 *
 * 全屏纯白遮罩 + 居中粒子球体旋转 + 呼吸缩放。
 * 淡入：挂载后下一帧切到 opacity-100；淡出：fading 时切到 opacity-0，
 * 均由 transition-opacity duration-1000 平滑过渡（快连接时淡出从当前透明度
 * 起过渡，不会跳回起点）。
 *
 * @param props - 组件属性
 * @returns 遮罩层 JSX 元素
 */
export default function ConnectingOverlay({ lang, fading }: ConnectingOverlayProps) {
  const [visible, setVisible] = useState(false);

  // 挂载后下一帧再置可见：让 opacity 过渡接管淡入（而非初始即全显）
  useEffect(() => {
    const id = requestAnimationFrame(() => setVisible(true));
    return () => cancelAnimationFrame(id);
  }, []);

  return (
    <div
      className={`fixed inset-0 z-[60] flex items-center justify-center bg-white transition-opacity duration-1000 ${fading || !visible ? 'opacity-0 pointer-events-none' : 'opacity-100'}`}
      aria-label={lang === 'zh-CN' ? '正在连接...' : 'Connecting...'}
      role="status"
    >
      {/* 粒子球体 — 整体呼吸 pulse：scale 1↔1.08，opacity 0.9↔1，2.5s */}
      <div className="animate-pulse-soft">
        <DotSphere />
      </div>
    </div>
  );
}
