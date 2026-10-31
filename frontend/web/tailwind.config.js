/** @type {import('tailwindcss').Config} */
export default {
  // 主题以 <html> 的 .dark 类切换，dark: 需按 class 匹配（否则跟随系统 media 不生效）
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: '#2a9d99',    /* 青绿色 - 清新主色 */
          hover: '#238b87',     /* 深青绿 */
          light: 'rgba(42, 157, 153, 0.1)',
        },
        secondary: {
          DEFAULT: '#7c6fb0',   /* 淡紫色 */
          hover: '#6b5ea0',
        },
        accent: {
          DEFAULT: '#e8856c',   /* 珊瑚橙 - 强调色 */
          hover: '#d4745b',
        },
        success: {
          DEFAULT: '#4caf7d',   /* 薄荷绿 */
          hover: '#3d9e6e',
        },
        warning: {
          DEFAULT: '#e8a84c',   /* 琥珀黄 */
          hover: '#d4973b',
        },
        danger: {
          DEFAULT: '#d45b5b',   /* 柔和红 */
          hover: '#c34a4a',
        },
        info: {
          DEFAULT: 'var(--info-color)',    /* 蓝色 - 信息提示 */
        },
        surface: {
          main: 'var(--bg-main)',      /* 纯白背景（深色模式由 :root.dark 覆盖） */
          card: 'var(--bg-card)',
          'card-alt': 'var(--bg-card-alt)',
          hover: 'var(--bg-hover)',
          input: 'var(--bg-input)',
        },
        content: {
          primary: 'var(--text-primary)',   /* 深灰黑 */
          secondary: 'var(--text-secondary)', /* 蓝灰 */
          disabled: 'var(--text-disabled)',  /* 浅蓝灰 */
        },
        border: {
          light: 'var(--border-light)',     /* 淡蓝灰边框 */
          medium: 'var(--border-medium)',    /* 中蓝灰边框 */
        },
        /* 粉彩色块 - 用于装饰和高亮 */
        pastel: {
          mint: 'var(--pastel-mint)',      /* 薄荷绿 */
          lilac: 'var(--pastel-lilac)',     /* 淡紫 */
          cream: 'var(--pastel-cream)',     /* 奶油黄 */
          pink: 'var(--pastel-pink)',      /* 粉红 */
          sky: 'var(--pastel-sky)',        /* 天蓝 */
          coral: 'var(--pastel-coral)',     /* 珊瑚 */
        },
      },
      fontFamily: {
        display: ['Playfair Display', 'Georgia', 'serif'],
        body: ['Inter', 'Inter Fallback', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      boxShadow: {
        'soft': '0 1px 2px rgba(0, 0, 0, 0.04), 0 1px 3px rgba(0, 0, 0, 0.06)',
        'card': '0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -2px rgba(0, 0, 0, 0.05)',
        'glow': '0 0 20px rgba(42, 157, 153, 0.15)',
      },
      borderRadius: {
        'xs': '4px',
        'sm': '6px',
        'md': '8px',
        'lg': '12px',
        'xl': '16px',
        'pill': '9999px',
      },
      keyframes: {
        'fade': {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        'fade-in': {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'fade-in-up': {
          '0%': { opacity: '0', transform: 'translateY(12px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'scale-in': {
          '0%': { opacity: '0', transform: 'scale(0.95)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        'scale-out': {
          '0%': { opacity: '1', transform: 'scale(1)' },
          '100%': { opacity: '0', transform: 'scale(0.95)' },
        },
        'fade-out': {
          '0%': { opacity: '1' },
          '100%': { opacity: '0' },
        },
        'toast-in': {
          '0%': { opacity: '0', transform: 'translateX(24px) scale(0.96)' },
          '100%': { opacity: '1', transform: 'translateX(0) scale(1)' },
        },
        'toast-out': {
          '0%': { opacity: '1', transform: 'translateX(0) scale(1)' },
          '100%': { opacity: '0', transform: 'translateX(24px) scale(0.96)' },
        },
        'progress-shrink': {
          '0%': { width: '100%' },
          '100%': { width: '0%' },
        },
        /* 连接遮罩图标 · 微呼吸循环（作用于外层呼吸层，贯穿遮罩整个生命周期；
           与内层匀速旋转叠加为环境运动——图标从出现到揭幕无静止帧；幅度
           ±3.5%，与收束缩放分属两个元素、乘法叠加） */
        'icon-breathe': {
          '0%': { scale: '1' },
          '100%': { scale: '1.035' },
        },
        /* 连接遮罩图标 · 匀速旋转（挂载即转，linear 无限循环——每圈边界
           速度连续，无起停停顿；贯穿连接期/收束期/揭幕淡出，随整层溶解） */
        'icon-spin': {
          to: { rotate: '360deg' },
        },
        /* 连接遮罩图标 · 就绪鼓点（收束签名）：就绪瞬间在持续旋转之上叠发
           一次 scale 1→1.06→1（缩放与旋转角无关，无需与圈相位对齐，无
           跳变），播完（animationend 门控）才允许揭幕淡出——"动画完整
           不打断"由这条有界动画承担。时长 1100ms 与 App.tsx 的
           ICON_SWELL_ANIMATION_MS 契约一致（兜底定时器依据）。
           reduced-motion 下由全局规则降级为 0.01ms 单次，animationend 照常
           触发 → 揭幕零延迟。滤镜在外层：投影偏移方向不随内部旋转公转。 */
        'icon-swell': {
          '0%': { scale: '1' },
          '50%': { scale: '1.06' },
          '100%': { scale: '1' },
        },
      },
      animation: {
        'fade': 'fade 0.15s ease-out both',
        'fade-in': 'fade-in 0.2s ease-out',
        'fade-in-up': 'fade-in-up 0.3s ease-out both',
        'scale-in': 'scale-in 0.2s ease-out both',
        'scale-out': 'scale-out 0.15s ease-in both',
        'fade-out': 'fade-out 0.15s ease-in both',
        'toast-in': 'toast-in 0.35s cubic-bezier(0.16, 1, 0.3, 1) both',
        'toast-out': 'toast-out 0.2s ease-in both',
        'progress-shrink': 'progress-shrink linear forwards',
        'icon-breathe': 'icon-breathe 2.4s cubic-bezier(0.45, 0, 0.55, 1) infinite alternate',
        'icon-spin': 'icon-spin 1400ms linear infinite',
        'icon-swell': 'icon-swell 1100ms cubic-bezier(0.3, 0.3, 0.5, 1) both',
      },
    },
  },
  plugins: [],
};
