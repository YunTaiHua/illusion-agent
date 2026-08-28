/**
 * 快捷方式创建模块（仅 Windows）
 * ==============================
 *
 * 两类 .lnk，共同锚定应用身份（AUMID = com.illusionagent.desktop）：
 *   1. 桌面快捷方式：打包后首次启动创建，指向当前 exe
 *   2. 开始菜单快捷方式（关键）：携带 appUserModelId 属性——Windows 的
 *      任务栏归属与系统通知的"来源名称/图标"都按 AUMID 反查已安装
 *      快捷方式；查无此 ID 时回退到宿主 exe（electron.exe）的默认占位
 *      图标与名称。仅首次创建（标记文件记录），用户删除即尊重其意愿
 *      不再重建——它与桌面快捷方式的"误删自愈"语义刻意不同：桌面项
 *      是用户可见的功能入口，开始菜单项只是 OS 品牌化的管道设施。
 *      开发模式（electron.exe 宿主）尤其依赖它。
 *
 * 其它设计要点：
 *   - Electron ShortcutDetails 原生支持 appUserModelId（无需 PowerShell COM）
 *   - 静默失败：任何写失败不阻塞应用启动
 *
 * @module shortcut
 */
import { app, shell } from 'electron';
import * as path from 'node:path';
import * as fs from 'node:fs';

/** 桌面快捷方式文件名（不含扩展名，shell.writeShortcutLink 会自动补 .lnk） */
const SHORTCUT_NAME = 'Illusion Agent';
/** 应用身份标识：与 main.ts setAppUserModelId 及 electron-builder appId 一致 */
export const APP_USER_MODEL_ID = 'com.illusionagent.desktop';

/**
 * 若桌面不存在快捷方式则创建（仅 Windows 打包后生效）。
 *
 * 调用时机：主窗口显示后调用，避免阻塞首屏。操作本身极快（写一个 lnk 文件）。
 * 任何异常均静默吞掉，不影响应用正常启动。
 */
export function createDesktopShortcutIfAbsent(): void {
  // 非 Windows 或开发环境直接跳过
  if (process.platform !== 'win32') return;
  if (!app.isPackaged) return;

  try {
    const desktopDir = app.getPath('desktop');
    const lnkPath = path.join(desktopDir, `${SHORTCUT_NAME}.lnk`);

    // 已存在则跳过（用户可能已手动创建或上次已生成）
    if (fs.existsSync(lnkPath)) return;

    // 创建快捷方式：指向当前 exe，工作目录设为 exe 所在目录
    // 不传 icon → 继承 exe 内嵌图标；不传 args → 正常启动（不走单实例二次启动分支）
    shell.writeShortcutLink(lnkPath, 'create', {
      target: process.execPath,
      cwd: path.dirname(process.execPath),
      description: 'Illusion Agent 桌面版',
      appUserModelId: APP_USER_MODEL_ID,
    });
  } catch {
    // 静默失败：桌面目录不可写、权限不足等情况下不干扰应用启动
  }
}

/**
 * 确保「开始菜单」存在携带 AUMID 的应用快捷方式（仅 Windows）。
 *
 * Windows 按当前进程的 AppUserModelID 在开始菜单反查快捷方式，以决定
 * 任务栏图标归属与系统通知的来源名称/图标；开发模式宿主是 electron.exe
 * 且安装包快捷方式与此 AUMID 无关，必须自行注册。
 *
 * 仅首次创建：标记文件（与 lnk 同目录）存在即跳过——用户删除 lnk 后
 * 标记仍在，尊重"不想要快捷方式"的意愿，不再重建；连标记一起清除才
 * 恢复首次语义。异常静默，不影响启动。
 */
export function ensureWindowsAumidShortcut(): void {
  if (process.platform !== 'win32') return;
  try {
    const startMenuDir = path.join(
      app.getPath('appData'),
      'Microsoft', 'Windows', 'Start Menu', 'Programs',
    );
    fs.mkdirSync(startMenuDir, { recursive: true });
    const lnkPath = path.join(startMenuDir, `${SHORTCUT_NAME}.lnk`);
    const markerPath = path.join(startMenuDir, `.${SHORTCUT_NAME}.aumid.created`);
    if (fs.existsSync(markerPath)) return;

    // 图标：开发模式用工程内 ico（随图标管线刷新）；打包后 exe 已内嵌，省略
    const devIco = path.resolve(__dirname, '..', 'build', 'icon.ico');

    shell.writeShortcutLink(lnkPath, 'create', {
      target: process.execPath,
      cwd: path.dirname(process.execPath),
      description: 'Illusion Agent',
      appUserModelId: APP_USER_MODEL_ID,
      icon: fs.existsSync(devIco) ? devIco : undefined,
      iconIndex: 0,
    });
    fs.writeFileSync(markerPath, new Date().toISOString());
  } catch {
    // 静默失败：不影响启动；仅影响开发模式的任务栏/通知品牌化
  }
}