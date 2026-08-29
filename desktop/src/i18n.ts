/**
 * 桌面壳 i18n 模块
 * ==================
 *
 * 提供托盘菜单、对话框等用户可见文本的国际化。
 * 语言由 ~/.illusion/settings.json 的 ui_language 字段决定，
 * 与后端 illusion.config.i18n 保持一致。
 *
 * 注意：本模块仅覆盖桌面壳层文本；应用内 Web UI 的 i18n
 * 由前端 (frontend/web) 自行处理，此处不重复。
 */
import type { UiLanguage } from './settings';

/** 桌面壳用户可见文本表 */
const MESSAGES = {
  'zh-CN': {
    tray_show: '显示主窗口',
    tray_hide: '隐藏主窗口',
    tray_open_terminal: '打开终端',
    tray_quit: '退出',
    app_name: 'Illusion Agent',
    err_no_runtime: '未找到可用的 Python 运行时，无法启动后端。',
    err_backend_failed: '后端启动失败：{message}',
    err_backend_crashed: '后端意外退出（退出码 {code}），应用将关闭。',
    update_downloaded_notify: '新版本 {version} 已就绪，重启应用后安装。',
    update_downloaded_notify_no_version: '新版本已就绪，重启应用后安装。',
  },
  'en-US': {
    tray_show: 'Show Main Window',
    tray_hide: 'Hide Main Window',
    tray_open_terminal: 'Open Terminal',
    tray_quit: 'Quit',
    app_name: 'Illusion Agent',
    err_no_runtime: 'No usable Python runtime found, cannot start backend.',
    err_backend_failed: 'Backend failed to start: {message}',
    err_backend_crashed: 'Backend exited unexpectedly (exit code {code}), the app will close.',
    update_downloaded_notify: 'Version {version} is ready and will be installed on next restart.',
    update_downloaded_notify_no_version: 'The update is ready and will be installed on next restart.',
  },
} as const;

export type MessageKey = keyof (typeof MESSAGES)['zh-CN'];

/** 简单模板替换：{name} → value */
function format(template: string, vars: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? `{${k}}`));
}

/** 翻译函数 */
export function t(
  lang: UiLanguage,
  key: MessageKey,
  vars?: Record<string, string | number>,
): string {
  const template = MESSAGES[lang][key];
  return vars ? format(template, vars) : template;
}
