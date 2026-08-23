/**
 * @fileoverview 统一 on/off 两向开关组件
 *
 * 40×22 胶囊轨道 + 18px 圆形滑块，四边各留 2px 内边距，滑块任意状态下
 * 都不触边（避免旧版 ON 态滑块右缘顶满轨道、阴影溢出框外）；滑块用
 * transform 平移实现平滑滑动（GPU 加速），轨道变色用 transition-colors。
 * 全项目唯一的开关形态：设置弹窗（记忆/标题/通道/沙箱）、cron 任务等。
 *
 * 注意：几何尺寸（宽高/内边距/位移）全部用行内 style 表达——
 * 行内 style 使几何与 Tailwind JIT/purge 状态解耦，任何构建环境下
 * 都确定生效（曾观察到任意值类在个别构建中未生成，成因不明）。
 *
 * @module ToggleSwitch
 */

/** 开关组件属性 */
interface ToggleSwitchProps {
  /** 当前是否开启 */
  checked: boolean;
  /** 切换回调（参数为切换后的值） */
  onChange: (v: boolean) => void;
  /** 无障碍标签（行内已有可见文字时可省略） */
  label?: string;
  /** 悬浮提示 */
  title?: string;
  /** 是否禁用（运行时不可操作，如 cron 任务运行中 / 通道初始化中） */
  disabled?: boolean;
}

/** 轨道尺寸（40×22）与滑块尺寸（18），四边内边距 2px */
const TRACK_W = 40;
const TRACK_H = 22;
const KNOB = 18;
const INSET = 2;

/**
 * 统一 on/off 开关
 *
 * @param props - 组件属性
 * @returns 开关按钮 JSX
 */
export default function ToggleSwitch({ checked, onChange, label, title, disabled }: ToggleSwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      title={title}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative shrink-0 rounded-full transition-colors duration-200 outline-none focus-visible:ring-2 focus-visible:ring-primary/30 ${
        disabled
          ? 'opacity-40 cursor-not-allowed'
          : 'cursor-pointer'
      } ${
        checked ? 'bg-primary' : 'bg-border-light'
      }`}
      style={{ width: TRACK_W, height: TRACK_H }}
    >
      <span
        className="absolute rounded-full bg-white shadow-sm transition-transform duration-200"
        style={{
          width: KNOB,
          height: KNOB,
          top: INSET,
          left: INSET,
          transform: checked ? `translateX(${TRACK_W - KNOB - INSET * 2}px)` : 'translateX(0)',
        }}
      />
    </button>
  );
}
