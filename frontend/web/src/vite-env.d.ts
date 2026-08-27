/// <reference types="vite/client" />

/**
 * Electron 桌面壳通过 contextBridge 注入的 API。
 * 仅在桌面壳内存在；浏览器直接访问 Web 端时为 undefined。
 */
interface IllusionDesktopBridge {
  /** Electron 版本 */
  version: string;
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
}

interface Window {
  illusionDesktop?: IllusionDesktopBridge;
}
