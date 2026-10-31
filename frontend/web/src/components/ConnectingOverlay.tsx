/**
 * @fileoverview 连接遮罩层组件
 *
 * 首次启动连接后端时显示的全屏遮罩层，在连接失败时展示错误提示与解决方式。
 *
 * 视觉：白底 + 居中圆角应用图标。图标分两层——外层（呼吸层）持 drop-shadow
 * 滤镜并承载微呼吸循环 scale 1↔1.035；内层（旋转层）承载匀速旋转与就绪
 * 鼓点。旋转挂载即开始（linear 无限循环，连接期/收束期合并为一段持续的
 * 环境运动，图标从出现到揭幕无静止帧）：
 *   连接期：匀速旋转 + 呼吸；
 *   收束期（ascend）：就绪瞬间叠发一次 scale 1→1.06→1 鼓点（1100ms，
 *                      缩放与旋转角无关、无需对齐圈相位），播完经
 *                      onAnimationEnd 通知 App 才允许揭幕淡出；
 *   静止期（rest）  ：鼓点终帧保持（scale 1），旋转继续，等待淡出——
 *                      旋转随整层溶解，从不错切。
 * 收束动画播完经 onAnimationEnd 通知 App，揭幕淡出只允许在动画完整结束后
 * 启动（打断保护）；后台节流下的兜底由 App 侧定时器承担。整层运动仍只有
 * opacity 淡出，过渡全程由合成器驱动；reduced-motion 下两段动画被全局规则
 * 降级为 0.01ms 单次，animationend 照常触发，揭幕零延迟。
 *
 * 错误提示：分为认证失败（auth）与后端不可达（unreachable）两类，差异化
 * 展示解决方式。认证失败提供终端操作指引；不可达展示重连按钮。
 *
 * 衔接链：Desktop 白底+图标 → 图标淡出 → web 首帧静态遮罩（index.html
 * #boot-splash，与本组件像素级同画面）→ 本遮罩挂载即完全不透明接管（App
 * 同帧移除静态遮罩）→ breathe 呼吸 → ascend 收束 → 后端就绪揭示主界面 →
 * 两段式淡出（App.tsx：先遮后挂，双 rAF + 一拍空闲后启动过渡，
 * onTransitionEnd 精确卸载）。
 *
 * @module ConnectingOverlay
 */

import type { UiLanguage } from '../i18n';
import { t } from '../i18n';
import type { WebConnectionError } from '../hooks/useWebSocketSession';

/** 图标运动阶段（由 App 依遮罩状态机推导下发） */
export type IconPhase = 'breathe' | 'ascend' | 'rest';

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
  /** 图标运动阶段：breathe 连接期微呼吸（默认）/ ascend 揭幕收束 / rest 静止 */
  iconPhase?: IconPhase;
  /** 收束动画完成回调（icon-ascend 的 onAnimationEnd 触发） */
  onAscendComplete?: () => void;
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
 * 全屏纯白遮罩 + 居中应用图标（呼吸/收束动效见文件头）+ 连接错误时展示
 * 错误提示与解决方式。
 *
 * @param props - 组件属性
 * @returns 遮罩层 JSX 元素
 */
export default function ConnectingOverlay({
  lang,
  fading,
  onFaded,
  iconPhase = 'breathe',
  onAscendComplete,
  connectionError,
  onRetry,
  retrying,
}: ConnectingOverlayProps) {
  // 挂载即完全不透明：index.html 静态首帧遮罩与本组件像素级同画面（同
  // 图标/同尺寸/同阴影），接管零跳变，无需淡入过渡；App 在挂载后移除该
  // 静态层。transition-opacity 仅服务于揭幕淡出。
  const showError = connectionError && !fading;

  // 揭幕门控：只认就绪鼓点（icon-swell）的结束事件——它是就绪后唯一的
  // 有界动画，播完才允许淡出（"动画完整不打断"由它承担）。匀速旋转与
  // 呼吸均为 infinite 循环，正常情况永不发 animationend（reduced-motion
  // 降级单次瞬时会发，忽略，门控由 swell 事件承担）。
  const handleIconAnimationEnd = (e: React.AnimationEvent<HTMLImageElement>) => {
    if (e.animationName !== 'icon-swell') return;
    onAscendComplete?.();
  };

  // 旋转层：匀速旋转全程挂载（挂载即转、永不停摆，随整层溶解）；就绪
  // 鼓点仅在 ascend/rest 挂载并保持到淡出结束（both 填充定格 scale 1，
  // 无跳变；错误路径回到 breathe 时卸除，重新就绪后完整重播）。
  const iconAnimClass =
    iconPhase === 'breathe' ? 'animate-icon-spin' : 'animate-icon-spin animate-icon-swell';

  return (
    <div
      className={`fixed inset-0 z-[60] flex flex-col items-center justify-center bg-white transition-opacity ${
        fading ? 'opacity-0 pointer-events-none' : 'opacity-100'
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
      {/* 呼吸层（外）+ 旋转层（内）：外层持 drop-shadow 滤镜并承载微呼吸
          缩放；内层承载匀速旋转与就绪鼓点。滤镜置于外层使投影偏移方向不
          随内部旋转公转；两层缩放分属不同元素、乘法叠加。图标参数与
          index.html #boot-splash 像素级一致——接管与移除静态层时不得有
          任何跳变（静态层静止、旋转自接管帧起步，属切变非跳变）。 */}
      <div
        className="animate-icon-breathe"
        style={{
          filter:
            'drop-shadow(0 3px 10px rgba(0, 0, 0, 0.19)) drop-shadow(0 14px 40px rgba(0, 0, 0, 0.36))',
          willChange: 'transform',
        }}
      >
        <img
          src="/icon.png"
          alt=""
          width={512}
          height={512}
          draggable={false}
          onAnimationEnd={handleIconAnimationEnd}
          className={`select-none block ${iconAnimClass}`}
          style={{
            width: 'clamp(84px, 16vmin, 120px)',
            height: 'auto',
            willChange: 'transform',
          }}
        />
      </div>

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
