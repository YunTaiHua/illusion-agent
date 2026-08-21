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
import { app, BrowserWindow, shell, dialog, Menu, ipcMain } from 'electron';
import { spawn } from 'node:child_process';
import * as path from 'node:path';
import * as fs from 'node:fs';
import { getUiLanguage } from './settings';
import type { UiLanguage } from './settings';
import { resolveRuntime } from './runtime';
import { Backend } from './backend';
import { createTray } from './tray';
import { createDesktopShortcutIfAbsent } from './shortcut';
import { t } from './i18n';

// 全局引用，防止被 GC 回收导致窗口/托盘消失
let mainWindow: BrowserWindow | null = null;
let tray: ReturnType<typeof createTray> | null = null;
let backend: Backend | null = null;
// 当前应用后端 URL（用于区分内部导航与外链跳转）
let appUrl = '';
// 是否处于"真正退出"流程（区分关闭到托盘与退出）
let isQuitting = false;

/**
 * 加载页莫比乌斯环路径（复刻 web 端 ConnectedOverlay 的 MOBIUS_PATH，
 * 80x80 viewBox，中心 40,40）。仅用于本地加载页的内联 SVG。
 */
const MOBIUS_PATH = [
  'M 40 20',
  'C 55 20, 65 32, 55 40',
  'C 48 46, 44 36, 40 36',
  'C 36 36, 32 46, 25 40',
  'C 15 32, 25 20, 40 20',
].join(' ');

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

  const lang: UiLanguage = getUiLanguage();

  // --- 先创建窗口并显示加载页 ---
  // 后台启动可能耗时（运行时检测 + Python 初始化），若等后端就绪再建窗，
  // 用户会长时间看到"空白/无响应"，造成"启动很慢"的体感。故先把窗口
  // 亮出来并展示本地加载页，后端就绪后再切换到应用 URL。
  // 加载页视觉复刻 web 端 ConnectedOverlay 的莫比乌斯环遮罩（青绿→淡紫→
  // 珊瑚渐变光带 + 呼吸缩放），保证与启动后的应用 UI 谐和统一。
  mainWindow = createWindow();
  mainWindow.loadURL(
    'data:text/html;charset=utf-8,' +
      encodeURIComponent(
        `<!DOCTYPE html><html><head><style>
          html,body{margin:0;height:100%}
          body{display:flex;align-items:center;justify-content:center;background:#ffffff}
          #mobi{animation:pulseSoft 2.5s ease-in-out infinite;filter:drop-shadow(0 0 6px rgba(42,157,153,.35))}
          #ring{fill:none;stroke-width:3.5;stroke-linecap:round;stroke-dasharray:100 28;animation:mobiFlow 1.8s linear infinite}
          @keyframes pulseSoft{0%,100%{transform:scale(1);opacity:.9}50%{transform:scale(1.08);opacity:1}}
          @keyframes mobiFlow{from{stroke-dashoffset:0}to{stroke-dashoffset:-128}}
        </style></head><body>
          <svg id="mobi" width="112" height="112" viewBox="0 0 80 80">
            <defs>
              <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stop-color="#2a9d99"><animate attributeName="stop-color" values="#2a9d99;#7c6fb0;#e8856c;#2a9d99" dur="4s" repeatCount="indefinite"/></stop>
                <stop offset="50%" stop-color="#7c6fb0"><animate attributeName="stop-color" values="#7c6fb0;#e8856c;#2a9d99;#7c6fb0" dur="4s" repeatCount="indefinite"/></stop>
                <stop offset="100%" stop-color="#e8856c"><animate attributeName="stop-color" values="#e8856c;#2a9d99;#7c6fb0;#e8856c" dur="4s" repeatCount="indefinite"/></stop>
              </linearGradient>
            </defs>
            <path id="ring" stroke="url(#g)" d="${MOBIUS_PATH}"/>
          </svg>
        </body></html>`,
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
  await mainWindow.loadURL(url);
  mainWindow.show();

  // --- 首次启动自动创建桌面快捷方式（仅 Windows 打包后，已存在则跳过） ---
  createDesktopShortcutIfAbsent();

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
