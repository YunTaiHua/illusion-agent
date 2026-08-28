/**
 * 系统托盘模块
 * ==============
 *
 * 创建托盘图标与上下文菜单，处理"关闭最小化到托盘"行为。
 *
 * 行为约定（对应 docs/zh-CN/desktop.md "托盘行为" 一节）：
 *   - 窗口关闭按钮 → 隐藏到托盘（不退出）
 *   - 托盘单击 → 显示/隐藏主窗口
 *   - 菜单"显示/隐藏主窗口" → 切换可见性
 *   - 菜单"打开终端" → 暴露内置 python/node（无环境用户）
 *   - 菜单"退出" → 真正退出（触发 onQuit 回调）
 */
import { Tray, Menu, BrowserWindow, nativeImage } from 'electron';
import * as path from 'node:path';
import * as fs from 'node:fs';
import type { UiLanguage } from './settings';
import { t } from './i18n';

/**
 * 托盘图标路径解析。
 * 优先级：打包资源（process.resourcesPath/icon.png）→ 工程内 resources → 源 assets。
 * 图标源由 scripts/build_icons.py 准备（见 desktop/build/assets/）。
 */
function resolveTrayIcon(): string {
  const candidates = [
    // 打包后：extraResources 把 resources/icon.png 放到 Resources/icon.png
    path.join(process.resourcesPath ?? '', 'icon.png'),
    // 开发时：build_icons.py 已复制到 desktop/resources/icon.png
    path.resolve(__dirname, '..', 'resources', 'icon.png'),
    // 兜底：直接用源 assets 中的小尺寸图标
    path.resolve(__dirname, '..', 'build', 'assets', 'icon_32x32.png'),
  ];
  return candidates.find((c) => fs.existsSync(c)) ?? candidates[0];
}

/**
 * macOS 菜单栏模板图标路径（由 build_icons.py 生成）：
 * 透明底 + 纯黑形状，设置 template image 后由系统适配深浅色菜单栏。
 */
function resolveMenuBarTemplateIcon(): string {
  const candidates = [
    path.join(process.resourcesPath ?? '', 'iconTemplate.png'),
    path.resolve(__dirname, '..', 'resources', 'iconTemplate.png'),
  ];
  return candidates.find((c) => fs.existsSync(c)) ?? '';
}

export interface TrayCallbacks {
  /** 用户选择"退出"时调用（应杀后端、退出应用） */
  onQuit: () => void;
  /** 用户选择"打开终端"时调用 */
  onOpenTerminal: () => void;
}

/**
 * 创建并返回托盘实例。
 *
 * @param win  主窗口
 * @param lang 界面语言
 * @param cb   菜单回调
 */
export function createTray(win: BrowserWindow, lang: UiLanguage, cb: TrayCallbacks): Tray {
  // macOS：优先用模板图标（透明底黑形，系统适配深浅色）；存在时才设为
  // template image，缺失则回退彩色图标（与 Windows/Linux 一致）。
  const trayIconPath = process.platform === 'darwin' ? resolveMenuBarTemplateIcon() : '';
  const icon = nativeImage.createFromPath(trayIconPath || resolveTrayIcon());
  if (process.platform === 'darwin') {
    if (trayIconPath) icon.setTemplateImage(true);
    // macOS 菜单栏图标建议小尺寸，resize 防止过大
    else if (!icon.isEmpty()) icon.resize({ width: 22, height: 22 });
  }
  const tray = new Tray(icon);
  tray.setToolTip(t(lang, 'app_name'));

  /** 根据窗口可见性刷新菜单文本 */
  const updateMenu = () => {
    const menu = Menu.buildFromTemplate([
      {
        label: win.isVisible() ? t(lang, 'tray_hide') : t(lang, 'tray_show'),
        click: () => {
          if (win.isVisible()) win.hide();
          else win.show();
        },
      },
      {
        label: t(lang, 'tray_open_terminal'),
        click: () => cb.onOpenTerminal(),
      },
      { type: 'separator' },
      {
        label: t(lang, 'tray_quit'),
        click: () => cb.onQuit(),
      },
    ]);
    tray.setContextMenu(menu);
  };
  updateMenu();

  // 托盘单击：切换显示
  tray.on('click', () => {
    if (win.isVisible()) win.hide();
    else win.show();
  });

  // 窗口可见性变化时刷新菜单文本
  win.on('show', updateMenu);
  win.on('hide', updateMenu);

  return tray;
}
