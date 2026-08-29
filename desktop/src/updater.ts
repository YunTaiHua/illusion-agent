/**
 * 自动更新模块
 * =============
 *
 * 基于 electron-updater + GitHub Releases（feed 由 electron-builder.yml 的
 * publish 配置生成，打包时自动写入 app-update.yml）。
 *
 * 交互策略（点击式，不打扰用户）：
 *   - 启动即检查一次新版本；用户不关程序的场景以 12 小时为周期复查兜底
 *   - 发现新版本仅在顶栏最小化按钮附近亮出更新图标，不自动下载；
 *     用户点击图标才开始下载，进度经事件广播渲染为进度环
 *   - 下载完成后点击图标立即重启安装（quitAndInstall）；
 *     不点击则应用正常退出时自动安装（autoInstallOnAppQuit）
 *
 * 未签名说明：
 *   更新包完整性由 latest.yml 内嵌的 SHA512 校验（electron-updater 内建）。
 *   Windows 静默安装不触发 SmartScreen（下载文件无 Mark-of-the-Web 标记）；
 *   Linux 走 AppImage 内建更新。
 *   macOS 的 Squirrel.Mac 要求应用已签名才能自动更新——当前三端均未签名，
 *   故 macOS 整体跳过自动更新（isUpdateSupported 平台门控），macOS 用户
 *   手动下载新 dmg 覆盖安装。zip target 已在构建配置中保留，签名后即可启用。
 *
 * 状态模型：
 *   模块内维护一份 UpdaterState，所有变更通过 webContents.send 广播给
 *   渲染进程（渠道 'updater:event'）；渲染进程加载晚于事件（如托盘启动
 *   后窗口隐藏中完成下载）时，通过 handle('updater:get-state') 拉取兜底。
 */
import { app, autoUpdater as electronQuitSignal, BrowserWindow, Notification } from 'electron';
import { autoUpdater } from 'electron-updater';
import { t } from './i18n';
import { getUiLanguage } from './settings';

/** 更新流程状态（渲染进程据此渲染顶栏更新图标） */
export type UpdaterStatus =
  | 'idle'            // 初始
  | 'checking'        // 检查中
  | 'available'       // 发现新版本（随即自动进入下载）
  | 'downloading'     // 下载中
  | 'downloaded'      // 下载完成（退出自动安装 / 点击图标立即重启安装）
  | 'error';          // 出错（离线/GitHub 不可达等；图标隐藏，等待下次复查）

/** 下载进度（与 electron-updater ProgressInfo 对齐的字段子集） */
export interface UpdaterProgress {
  percent: number;
  transferred: number;
  total: number;
  bytesPerSecond: number;
}

/** 模块级更新状态快照（广播与 get-state 拉取共用） */
export interface UpdaterState {
  status: UpdaterStatus;
  /** 新版本号（available/downloading/downloaded 阶段有值） */
  version?: string;
  /** 下载进度（downloading 阶段有值） */
  progress?: UpdaterProgress;
  /** 错误消息（error 阶段有值） */
  error?: string;
}

/** 检查节奏：启动延迟、周期复查兜底、出错后退避重试（毫秒） */
const AUTO_CHECK_DELAY_MS = 0;
const AUTO_CHECK_INTERVAL_MS = 12 * 60 * 60 * 1000;
const ERROR_RETRY_DELAY_MS = 2 * 60 * 1000;
const ERROR_RETRY_MAX = 3;

interface UpdaterDeps {
  /** 取主窗口（事件广播用；窗口可能已销毁，实现方须自行判空） */
  getMainWindow: () => BrowserWindow | null;
  /**
   * quitAndInstall 触发的主进程退出前回调。
   * 主进程据此置位"真正退出"标记，避免关闭事件被拦截成"最小化到托盘"
   * 导致更新安装被挂起。
   */
  onBeforeQuitForUpdate: () => void;
}

let state: UpdaterState = { status: 'idle' };
let deps: UpdaterDeps | null = null;
let initialized = false;
let intervalTimer: NodeJS.Timeout | null = null;
let errorRetries = 0;

/**
 * 打包产物内更新器是否可用。
 *   - 开发模式 electron 未打包，无 app-update.yml → 不可用
 *   - macOS：Squirrel.Mac 要求应用已签名才能自动更新，未签名（当前状态）
 *     会在安装阶段失败 → 平台整体跳过，macOS 用户手动下载新 dmg 更新
 */
export function isUpdateSupported(): boolean {
  return app.isPackaged && process.platform !== 'darwin';
}

/** 当前状态快照（渲染进程挂载时拉取，兜底同步错过的广播） */
export function currentState(): UpdaterState {
  return state;
}

function setState(next: UpdaterState): void {
  state = next;
  const win = deps?.getMainWindow();
  // hide 到托盘时 webContents 仍在，事件可送达；销毁中则跳过（渲染进程
  // 重新挂载时会经 get-state 拉取最新状态）
  if (win && !win.isDestroyed()) {
    win.webContents.send('updater:event', state);
  }
}

/** 检查更新（启动延迟检查与周期复查共用）。
 * checking/downloading/downloaded 为流程进行中，不重复检查；
 * available 为"等待用户点击下载"的稳态，允许复查以发现更新的版本。 */
function checkForUpdates(): void {
  if (!isUpdateSupported()) return;
  if (
    state.status === 'checking'
    || state.status === 'downloading'
    || state.status === 'downloaded'
  ) {
    return;
  }
  setState({ status: 'checking' });
  autoUpdater.checkForUpdates().catch((e: Error) => {
    // checkForUpdates 在无网络等场景 reject，同时也会触发 error 事件；
    // 此处兜底置错误态，避免卡在 checking
    if (state.status === 'checking') {
      setState({ status: 'error', error: e.message });
    }
  });
}

/** 开始下载已发现的新版本（仅 available 态有效，由顶栏更新图标点击触发） */
export function downloadUpdate(): void {
  if (!isUpdateSupported()) return;
  if (state.status !== 'available') return;
  setState({ status: 'downloading', version: state.version });
  // 下载失败由 error 事件统一置错误态并安排退避重试
  autoUpdater.downloadUpdate().catch(() => undefined);
}

/** 退出并安装已下载的更新：静默安装（沿用上次安装目录）后自动重启应用 */
export function quitAndInstall(): void {
  if (!isUpdateSupported()) return;
  if (state.status !== 'downloaded') return;
  // 走正常退出链路清理后端进程树（before-quit → quitApp），随后安装器接管
  autoUpdater.quitAndInstall(true, true);
}

/**
 * 初始化更新器：注册事件、启动检查节奏。
 * 开发模式（app.isPackaged=false）下整体跳过。
 */
export function initUpdater(deps_: UpdaterDeps): void {
  deps = deps_;
  if (!isUpdateSupported() || initialized) return;
  initialized = true;

  // electron-updater 层关闭自动下载，由用户点击顶栏更新图标后经
  // 'updater:download' IPC 显式触发 downloadUpdate()，状态机串起
  // checking → available → downloading → downloaded 全程广播
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = true; // 已下载的更新在应用退出时自动安装

  autoUpdater.on('checking-for-update', () => {
    if (state.status !== 'checking') setState({ status: 'checking' });
  });
  autoUpdater.on('update-available', (info) => {
    errorRetries = 0;
    // 仅亮出更新图标（available 态），不自动下载——用户点击顶栏图标
    // 后由 'updater:download' IPC 触发 downloadUpdate()
    setState({ status: 'available', version: info.version });
  });
  autoUpdater.on('update-not-available', () => {
    errorRetries = 0;
    setState({ status: 'idle' });
  });
  autoUpdater.on('download-progress', (progress) => {
    setState({
      status: 'downloading',
      version: state.version,
      progress: {
        percent: progress.percent,
        transferred: progress.transferred,
        total: progress.total,
        bytesPerSecond: progress.bytesPerSecond,
      },
    });
  });
  autoUpdater.on('update-downloaded', (info) => {
    setState({ status: 'downloaded', version: info.version });
    notifyDownloaded();
  });
  autoUpdater.on('error', (e) => {
    // 检查/下载任一环节失败（离线、GitHub 不可达等）→ 置错误态，
    // 顶栏图标隐藏；短退避重试数次后交由周期复查兜底
    setState({ status: 'error', error: e.message });
    if (errorRetries < ERROR_RETRY_MAX) {
      errorRetries += 1;
      const timer = setTimeout(() => checkForUpdates(), ERROR_RETRY_DELAY_MS);
      timer.unref();
    }
  });

  // 用户点更新图标"立即重启安装"：先放行窗口关闭（否则被拦截成最小化到托盘）。
  // 注意 electron-updater 6.x 在 quitAndInstall 时于 Electron 内置
  // autoUpdater 上发出该信号，而非 electron-updater 实例本身。
  electronQuitSignal.on('before-quit-for-update', () => {
    deps?.onBeforeQuitForUpdate();
  });

  // 启动即检查（与 Web 端后端 update_available toast 时机同步），之后周期复查兜底（不阻止应用退出）
  const startupTimer = setTimeout(() => checkForUpdates(), AUTO_CHECK_DELAY_MS);
  startupTimer.unref();
  intervalTimer = setInterval(() => checkForUpdates(), AUTO_CHECK_INTERVAL_MS);
  intervalTimer.unref();
}

/** 下载完成后窗口隐藏在托盘时，发系统通知提醒（窗口可见时无需打扰） */
function notifyDownloaded(): void {
  const win = deps?.getMainWindow();
  if (!win || win.isDestroyed() || win.isVisible()) return;
  if (!Notification.isSupported()) return;
  const lang = getUiLanguage();
  try {
    const notification = new Notification({
      title: t(lang, 'app_name'),
      body: state.version
        ? t(lang, 'update_downloaded_notify', { version: state.version })
        : t(lang, 'update_downloaded_notify_no_version'),
      silent: true,
    });
    notification.on('click', () => {
      const w = deps?.getMainWindow();
      if (!w || w.isDestroyed()) return;
      if (!w.isVisible()) w.show();
      w.focus();
    });
    notification.show();
  } catch {
    // 通知失败不影响更新流程
  }
}
