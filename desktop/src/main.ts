/**
 * Electron 主进程入口
 * =====================
 *
 * 职责：
 *   1. 单实例锁：重复启动聚焦到现有窗口
 *   2. 检测运行时（Python/Node）
 *   3. 启动后端（动态端口）
 *   4. 创建主窗口并加载后端 URL
 *   5. 创建系统托盘（关闭最小化到托盘，菜单退出）
 *   6. 真正退出时清理后端进程树
 *
 * 生命周期对应 docs/zh-CN/desktop.md "托盘行为" 与 "守护进程生命周期"。
 */
import { app, BrowserWindow, shell, dialog, Menu, ipcMain, Notification, session } from 'electron';
import { spawn } from 'node:child_process';
import * as path from 'node:path';
import * as fs from 'node:fs';
import { getUiLanguage } from './settings';
import type { UiLanguage } from './settings';
import { resolveRuntime } from './runtime';
import { Backend } from './backend';
import { createTray } from './tray';
import { t } from './i18n';
import {
  initUpdater,
  downloadUpdate,
  quitAndInstall,
  currentState,
} from './updater';

// 全局引用，防止被 GC 回收导致窗口/托盘消失
let mainWindow: BrowserWindow | null = null;
let tray: ReturnType<typeof createTray> | null = null;
let backend: Backend | null = null;
// 当前应用后端 URL（用于区分内部导航与外链跳转）
let appUrl = '';
// 是否处于"真正退出"流程（区分关闭到托盘与退出）
let isQuitting = false;

/** 窗口图标路径解析：打包资源 → 工程内 resources → 源 assets（开发时） */
function resolveWindowIcon(): string | undefined {
  const candidates = [
    path.join(process.resourcesPath ?? '', 'icon.png'),
    path.resolve(__dirname, '..', 'resources', 'icon.png'),
    path.resolve(__dirname, '..', 'build', 'assets', 'icon_256x256.png'),
  ];
  return candidates.find((c) => fs.existsSync(c));
}

/** 创建主窗口 */
function createWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    show: false,               // 由 app.whenReady 在加载本地加载页后 show
    // 遮罩层为浅色（白），窗口底色保持白色与其一致，避免遮罩阶段露出色差
    backgroundColor: '#ffffff',
    title: 'Illusion Agent',
    icon: resolveWindowIcon(),
    autoHideMenuBar: true, // Windows/Linux 菜单栏自动隐藏（按 Alt 可唤出）
    // 自定义顶部栏：macOS 隐藏标题栏保留交通灯按钮，Win/Linux 完全无边框
    ...(process.platform === 'darwin'
      ? { titleBarStyle: 'hidden' as const }
      : { frame: false }),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: false,
    },
  });

  // 彻底隐藏窗口内菜单栏（File/Edit/View/Window）
  win.setMenuBarVisibility(false);

  // 关闭按钮 → 最小化到托盘（除非正在真正退出）
  win.on('close', (e) => {
    if (!isQuitting) {
      e.preventDefault();
      win.hide();
    }
  });

  // 外链点击在系统浏览器打开，不在应用内跳转（仅允许 http/https 协议）
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url).catch(() => {});
    return { action: 'deny' };
  });

  // 拦截当前窗口内的导航（如点击普通 <a href> 链接），外部链接重定向到系统浏览器
  win.webContents.on('will-navigate', (event, url) => {
    if (appUrl && url.startsWith(appUrl)) return; // 应用内部导航
    event.preventDefault();
    if (/^https?:\/\//i.test(url)) shell.openExternal(url).catch(() => {});
  });

  return win;
}

/** 真正退出：杀后端、退出应用 */
function quitApp(): void {
  if (isQuitting) return;
  isQuitting = true;
  if (backend) {
    backend.kill();
    backend = null;
  }
  app.quit();
}

/**
 * 打开内置终端。
 * 当前阶段先用系统默认终端兜底；后续集成应用内 xterm，
 * 暴露内置 python/node 给无环境用户。
 */
function openTerminal(): void {
  // TODO: 集成应用内 xterm 终端，注入内置 python/node 到 PATH
  if (process.platform === 'win32') {
    spawn('cmd', ['/k', 'title Illusion Agent Terminal'], { detached: true, shell: true });
  } else if (process.platform === 'darwin') {
    spawn('open', ['-a', 'Terminal'], { detached: true });
  } else {
    spawn('x-terminal-emulator', [], { detached: true });
  }
}

app.whenReady().then(async () => {
  // 移除默认应用菜单（File/Edit/View/Window）。
  // macOS 保留系统菜单栏（屏幕顶部，不占窗口空间，且保留 Cmd+Q 等系统快捷键）。
  if (process.platform !== 'darwin') {
    Menu.setApplicationMenu(null);
  }

  // Windows：Toast 透传的系统级通知依赖 AppUserModelID，须与
  // electron-builder.yml 的 appId 一致，打包后通知才能正常归属到应用。
  if (process.platform === 'win32') {
    app.setAppUserModelId('com.illusionagent.desktop');
  }

  // --- 单实例锁：重复启动聚焦现有窗口 ---
  if (!app.requestSingleInstanceLock()) {
    app.quit();
    return;
  }
  app.on('second-instance', () => {
    if (mainWindow) {
      if (!mainWindow.isVisible()) mainWindow.show();
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  // 启动即清空会话 cookie（须在单实例锁之后：仅持有锁的实例清理，
  // 否则重复启动触发的第二个实例会清掉运行中实例的登录 cookie）。
  // 桌面版每次启动都在随机端口上拉起后端，登录流程签发的认证 cookie
  // 曾按端口哈希命名，动态端口下旧 cookie 永不被覆盖、无限累积，请求
  // 头膨胀到服务器上限后 WS 握手被 400 拒绝（界面卡在遮罩层）。启动
  // 时清一次：本会话的登录 cookie 由随后 token 交换重新签发，恒为空起步。
  try {
    await session.defaultSession.clearStorageData({ storages: ['cookies'] });
  } catch {
    // 清除失败不阻塞启动（后续 token 交换会签发新 cookie）
  }

  // --- 自动更新 ---
  // 仅打包版生效（开发模式无 app-update.yml，updater 内部跳过）。
  // 启动即初始化并立即检查（与 Web 端后端 update_available toast 时机同步），
  // 事件经 webContents 广播给渲染进程顶栏更新图标；before-quit-for-update
  // 置位 isQuitting，放行窗口关闭，否则更新安装会被"关闭最小化到托盘"
  // 拦截挂起。
  initUpdater({
    getMainWindow: () => mainWindow,
    onBeforeQuitForUpdate: () => {
      isQuitting = true;
    },
  });

  const lang: UiLanguage = getUiLanguage();

  // --- 先创建窗口并显示加载页 ---
  // 后台启动可能耗时（运行时检测 + Python 初始化），若等后端就绪再建窗，
  // 用户会长时间看到"空白/无响应"，造成"启动很慢"的体感。故先把窗口
  // 亮出来并展示本地加载页，后端就绪后再切换到应用 URL。
  // 加载页为纯白底：与 web 端 ConnectingOverlay 的 bg-white 同框一致，
  // 消除粒子球旋转 + 切白底之间的间隙卡顿感。web 端遮罩层淡入接管，
  // 700ms ease-out 过渡柔化（两段式启动）切换。
  mainWindow = createWindow();
  // 启动页：白底延续（与 web 端 ConnectingOverlay 的 bg-white 无缝同框），
  // 中央嵌入圆角应用图标作品牌锚点。图标文件经 base64 内嵌——data-url 页
  // 无法再发起相对资源请求。收到后端就绪信号（backend.start() resolve）
  // 后由 executeJavaScript 触发 __splashFadeOut() 淡出图标，短暂停留于
  // 纯白再整页交接 web——衔接链：Desktop 白底+图标 → 淡出 → web 白底
  // → ConnectingOverlay 淡入（700ms ease-out，App.tsx 事件驱动卸载）。
  const splashIconPath = resolveWindowIcon();
  let splashLogo = '';
  if (splashIconPath) {
    try {
      const b64 = fs.readFileSync(splashIconPath).toString('base64');
      splashLogo = `<img id="splash-logo" alt="" src="data:image/png;base64,${b64}" />`;
    } catch {
      splashLogo = ''; // 图标读取失败降级为纯白启动页，不阻塞启动
    }
  }
  mainWindow.loadURL(
    'data:text/html;charset=utf-8,' +
      encodeURIComponent(
        `<!DOCTYPE html><html><head><style>
          html,body{margin:0;height:100%;background:#ffffff}
          body{display:flex;align-items:center;justify-content:center}
          #splash-logo{
            width:clamp(84px,16vmin,120px);height:auto;display:block;
            filter:drop-shadow(0 3px 10px rgba(0,0,0,0.19)) drop-shadow(0 14px 40px rgba(0,0,0,0.36));
            transition:opacity .5s ease;
          }
        </style></head><body>${splashLogo}<script>
          window.__splashFadeOut = function(){
            var el = document.getElementById('splash-logo');
            if (el) el.style.opacity = '0';
          };
        </script></body></html>`,
      ),
  );
  mainWindow.show();
  // 显示后再最大化：在 show:false 时调用 maximize 可能被随后的 show() 重置，
  // 导致第二次启动不全屏（窗口状态被系统恢复为还原态）。先 show 再 maximize
  // 保证窗口每次都以全屏呈现。
  mainWindow.maximize();

  // --- 检测运行时 ---
  const runtime = resolveRuntime();
  if (!runtime.python) {
    dialog.showErrorBox(t(lang, 'app_name'), t(lang, 'err_no_runtime'));
    app.quit();
    return;
  }

  // --- 启动后端 ---
  // 构造 env：当用内置运行时（用户无环境）时，把内置 python/node 目录加到 PATH 前面，
  // 让后端进程及其子进程（agent 的 bash 工具执行 `python xxx.py` / `node xxx.js`）都能找到
  const env: NodeJS.ProcessEnv = { ...process.env };
  const extraPaths: string[] = [];
  if (!runtime.pythonFromUser && runtime.python) {
    const pyDir = path.dirname(runtime.python);
    extraPaths.push(pyDir);
    // Windows: pip 等脚本在 Scripts/ 子目录
    const scriptsDir = path.join(pyDir, 'Scripts');
    if (fs.existsSync(scriptsDir)) extraPaths.push(scriptsDir);
  }
  if (!runtime.nodeFromUser && runtime.node) {
    extraPaths.push(path.dirname(runtime.node));
  }
  if (extraPaths.length > 0) {
    // Windows 环境变量键名大小写不敏感（Path vs PATH），先提取原值再删除所有变体，避免冲突
    const origPath = env.PATH ?? env.Path ?? (env as Record<string, string>).path ?? '';
    for (const k of Object.keys(env)) {
      if (k.toUpperCase() === 'PATH') delete env[k];
    }
    env.PATH = extraPaths.join(path.delimiter) + path.delimiter + origPath;
  }
  backend = new Backend({ pythonPath: runtime.python, env });
  let url: string;
  try {
    url = await backend.start();
    appUrl = url;
  } catch (e) {
    dialog.showErrorBox(
      t(lang, 'app_name'),
      t(lang, 'err_backend_failed', { message: (e as Error).message }),
    );
    app.quit();
    return;
  }

  // 后端意外退出时提示用户并退出（主动退出时 isQuitting=true，不触发）
  backend.on('exit', (code) => {
    if (!isQuitting && code !== 0) {
      dialog.showErrorBox(t(lang, 'app_name'), t(lang, 'err_backend_crashed', { code: String(code) }));
      quitApp();
    }
  });

  // --- 后端就绪后加载应用并显示（窗口已在启动时创建并展示加载页） ---
  // 先淡出启动页图标（0.5s 过渡）并停留半拍，让画面交接时是"纯白 →
  // web 白底"的同色衔接，再由 web 端 ConnectingOverlay 接管视觉焦点
  await mainWindow.webContents
    .executeJavaScript('window.__splashFadeOut && window.__splashFadeOut()')
    .catch(() => undefined);
  await new Promise((resolve) => setTimeout(resolve, 600));
  try {
    await mainWindow.loadURL(url);
  } catch (e) {
    // 后端在 start() 与导航之间意外退出：给出对话框而非永久白窗
    dialog.showErrorBox(
      t(lang, 'app_name'),
      t(lang, 'err_backend_failed', { message: (e as Error).message }),
    );
    quitApp();
    return;
  }
  mainWindow.show();

  // --- 托盘 ---
  tray = createTray(mainWindow, lang, {
    onQuit: quitApp,
    onOpenTerminal: openTerminal,
  });

  // macOS: 关闭所有窗口时不退出（托盘常驻）；win/linux 同样靠托盘菜单退出
  app.on('window-all-closed', () => {
    // 不调用 app.quit，保持托盘常驻
  });
  app.on('activate', () => {
    if (mainWindow && !mainWindow.isVisible()) mainWindow.show();
  });

  // 应用被要求退出（Cmd+Q / 系统关机）→ 走真正退出流程
  app.on('before-quit', (e) => {
    if (!isQuitting) {
      e.preventDefault();
      quitApp();
    }
  });
});

// 兜底：退出时确保后端被杀
app.on('will-quit', () => {
  if (backend) backend.kill();
});

// ========== 窗口控制 IPC（自定义顶部栏按钮） ==========
ipcMain.on('window-minimize', () => mainWindow?.minimize());
ipcMain.on('window-maximize', () => mainWindow?.maximize());
ipcMain.on('window-toggle-maximize', () => {
  if (!mainWindow) return;
  if (mainWindow.isMaximized()) mainWindow.unmaximize();
  else mainWindow.maximize();
});
ipcMain.on('window-close', () => mainWindow?.close());

// ========== 外链拦截 IPC（preload 渲染进程点击拦截） ==========
ipcMain.on('open-external', (_event, url: string) => {
  if (typeof url === 'string' && /^https?:\/\//i.test(url)) {
    shell.openExternal(url).catch(() => {});
  }
});

// ========== 自动更新 IPC（preload 暴露 updater 桥，渲染进程顶栏图标调用） ==========
// 检查由主进程自动驱动（启动检查 + 12h 复查）；下载由用户点击顶栏图标
// 触发；安装可在就绪后点击图标立即执行，或应用退出时自动完成。
// 开发模式更新器整体跳过，状态恒为 idle，渲染进程图标自然隐藏。
ipcMain.handle('updater:get-state', () => currentState());
ipcMain.on('updater:download', () => downloadUpdate());
ipcMain.on('updater:install', () => quitAndInstall());

// ========== Toast 透传 IPC：系统级通知（渲染进程不可见时转发任务结果/询问/权限提醒） ==========
// 音效由渲染进程统一播放（Web Audio，受 settings.json notifications.sound 控制），
// 此处 silent 关闭系统默认提示音，避免双重声音。
ipcMain.on('show-notification', (_event, payload: { title?: unknown; body?: unknown }) => {
  if (!Notification.isSupported()) return;
  const title = typeof payload?.title === 'string' ? payload.title : '';
  const body = typeof payload?.body === 'string' ? payload.body : '';
  if (!title && !body) return;
  try {
    const notification = new Notification({
      title: title || 'Illusion Agent',
      body,
      icon: resolveWindowIcon(),
      silent: true,
    });
    // 点击通知回到应用（窗口隐藏在托盘时先恢复显示）
    notification.on('click', () => {
      if (!mainWindow) return;
      if (mainWindow.isMinimized()) mainWindow.restore();
      if (!mainWindow.isVisible()) mainWindow.show();
      mainWindow.focus();
    });
    notification.show();
  } catch {
    // 通知创建失败（如系统禁用通知）不影响主流程
  }
});
