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
      },
    },
  },
  plugins: [],
};
