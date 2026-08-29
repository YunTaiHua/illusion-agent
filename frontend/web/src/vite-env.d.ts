/// <reference types="vite/client" />

/**
 * Electron 桌面壳通过 contextBridge 注入的 API。
 * 仅在桌面壳内存在；浏览器直接访问 Web 端时为 undefined。
 */
/**
 * 主进程自动更新状态快照（与 desktop/src/updater.ts 的 UpdaterState 对齐）
 */
interface DesktopUpdaterState {
  /** 更新流程状态：检查与下载由主进程自动驱动，错误时图标隐藏等下次复查 */
  status: 'idle' | 'checking' | 'available' | 'downloading' | 'downloaded' | 'error';
  /** 新版本号（available / downloading / downloaded 阶段有值） */
  version?: string;
  /** 下载进度（downloading 阶段有值） */
  progress?: {
    percent: number;
    transferred: number;
    total: number;
    bytesPerSecond: number;
  };
  /** 错误消息（error 阶段有值） */
  error?: string;
}

interface IllusionDesktopBridge {
  /** Electron 版本 */
  version: string;
  /** 应用版本（package.json version） */
  appVersion: string;
  /** 运行平台：win32 / darwin / linux */
  platform: string;
  /** 最小化窗口 */
  minimize: () => void;
  /** 切换最大化/还原 */
  toggleMaximize: () => void;
  /** 最大化窗口（仅最大化，不切换；用于连接成功后自动最大化） */
  maximize: () => void;
  /** 关闭窗口（触发主进程 close → 最小化到托盘） */
  close: () => void;
  /**
   * 发送系统级通知（toast 透传）
   * 由主进程创建 Electron Notification，点击通知聚焦应用窗口；
   * 渲染进程不可见时由 toast 分发逻辑调用
   */
  showNotification: (title: string, body: string) => void;
  /**
   * 自动更新：主进程自动检查并亮出顶栏更新图标，用户点击图标下载；
   * 就绪后点击立即重启安装，不点击则退出时自动安装
   *   - getState：拉取主进程当前状态（挂载兜底，避免错过广播）
   *   - download / install：点击图标开始下载 / 立即重启安装
   *   - onEvent：订阅主进程状态广播，返回取消订阅函数
   */
  updater: {
    getState: () => Promise<DesktopUpdaterState | null>;
    download: () => void;
    install: () => void;
    onEvent: (cb: (state: DesktopUpdaterState) => void) => () => void;
  };
}

interface Window {
  illusionDesktop?: IllusionDesktopBridge;
}
