/**
 * 预加载脚本
 * ============
 *
 * 在渲染进程加载前执行，通过 contextBridge 暴露安全 API。
 *
 * 暴露内容（window.illusionDesktop）：
 *   - version：Electron 版本，渲染进程可据此判断是否在桌面壳内
 *   - platform：运行平台，用于顶部栏交通灯/自定义按钮的差异处理
 *   - minimize / toggleMaximize / close：窗口控制，通过 IPC 转发主进程
 *   - showNotification：系统级通知（toast 透传），主进程创建并处理点击聚焦
 *
 * 浏览器直接访问 Web 端时本脚本不执行，window.illusionDesktop 为 undefined。
 */
/// <reference lib="dom" />
import { app, contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('illusionDesktop', {
  /** Electron 版本 */
  version: process.versions.electron,
  /** 应用版本（package.json version，更新 UI 显示用） */
  appVersion: app.getVersion(),
  /** 运行平台：win32 / darwin / linux */
  platform: process.platform,
  /** 最小化窗口 */
  minimize: () => ipcRenderer.send('window-minimize'),
  /** 切换最大化/还原 */
  toggleMaximize: () => ipcRenderer.send('window-toggle-maximize'),
  /** 最大化窗口（仅最大化，不切换；用于连接成功后自动最大化） */
  maximize: () => ipcRenderer.send('window-maximize'),
  /** 关闭窗口（主进程 close 事件 → 最小化到托盘） */
  close: () => ipcRenderer.send('window-close'),
  /** 系统级通知（toast 透传）：主进程创建 Notification，点击聚焦应用窗口 */
  showNotification: (title: string, body: string) =>
    ipcRenderer.send('show-notification', {
      title: typeof title === 'string' ? title : '',
      body: typeof body === 'string' ? body : '',
    }),
  /**
   * 自动更新：主进程自动检查新版本并亮出顶栏更新图标，用户点击图标下载；
   *   - getState：拉取主进程当前更新状态（挂载兜底同步，避免错过广播）
   *   - download：用户点击图标开始下载（仅 available 态有效）
   *   - install：更新就绪后点击图标立即重启安装（不点击则退出时自动安装）
   *   - onEvent：订阅状态广播（'updater:event'，载荷为主进程 UpdaterState）
   */
  updater: {
    getState: () => ipcRenderer.invoke('updater:get-state'),
    download: () => ipcRenderer.send('updater:download'),
    install: () => ipcRenderer.send('updater:install'),
    onEvent: (cb: (state: unknown) => void) => {
      const handler = (_event: unknown, state: unknown) => cb(state);
      ipcRenderer.on('updater:event', handler);
      return () => ipcRenderer.removeListener('updater:event', handler);
    },
  },
});

/**
 * 外链点击拦截（渲染进程层）
 * ==========================
 *
 * 监听 document 的 click 事件（事件冒泡），当点击目标是一个指向外部 URL 的
 * <a> 标签时，阻止默认导航，通过 IPC 通知主进程在系统浏览器打开。
 *
 * 这与主进程的 will-navigate 拦截互为补充，共同确保外链不会在应用窗口内跳转。
 */
document.addEventListener('click', (e: MouseEvent) => {
  // 已被其他处理器阻止默认行为的点击不再处理
  if (e.defaultPrevented) return;
  const anchor = (e.target as HTMLElement | null)?.closest('a[href]') as HTMLAnchorElement | null;
  if (!anchor) return;
  const href = anchor.href;
  // 仅处理 http/https 协议（排除 mailto:、tel:、file:、#anchor 等）
  if (!/^https?:\/\//i.test(href)) return;
  // 应用内部链接不拦截
  if (href.startsWith(window.location.origin)) return;
  e.preventDefault();
  ipcRenderer.send('open-external', href);
});
