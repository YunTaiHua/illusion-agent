/**
 * @fileoverview 文件类型展示工具
 *
 * 按扩展名返回文件图标的着色（图标形状
 * 统一为线性文件剪影，扩展名决定颜色），供预览 tab 条、右栏文件卡片
 * 与单轮变更条共用，保证三处文件卡片的图标语言一致。
 *
 * @module fileIcon
 */

/**
 * 扩展名 → 图标着色（固定 hex，浅色/深色主题下均可辨识）
 *
 * 取各语言社区通用的品牌/语法高亮色相，饱和度略压低以贴合玻璃拟态
 * 的低对比界面；未收录的扩展名回退主题禁用灰。
 */
const EXT_ICON_COLORS: Record<string, string> = {
  ts: '#3178c6', tsx: '#3178c6',
  js: '#d8a940', jsx: '#d8a940', mjs: '#d8a940', cjs: '#d8a940',
  json: '#cb8e3c', jsonc: '#cb8e3c',
  md: '#7d8ca3', mdx: '#7d8ca3', txt: '#7d8ca3',
  css: '#8b5cf6', scss: '#c6538c', less: '#3b6db4',
  html: '#e0683c', xml: '#8ba03c', svg: '#c671d8',
  py: '#4b8bbe', pyi: '#4b8bbe',
  rs: '#c88a6b', go: '#3aa8c1', java: '#b0723c',
  c: '#5f8fcb', h: '#5f8fcb', cpp: '#d8638c', hpp: '#d8638c', cc: '#d8638c',
  sh: '#6bab5b', bash: '#6bab5b', zsh: '#6bab5b',
  yml: '#8aa83c', yaml: '#8aa83c', toml: '#8aa83c', ini: '#8aa83c',
  sql: '#c9825f', rb: '#c94f4a', php: '#8a8dc0', swift: '#d9704b',
  kt: '#a05acb', lua: '#4a6bc9', r: '#3c8facc',
};

/**
 * 按路径扩展名取文件图标着色
 *
 * @param path - 文件路径（任意分隔符）
 * @returns hex 颜色；未收录扩展名返回 undefined（回退主题色）
 */
export function fileIconColor(path: string): string | undefined {
  const name = path.replace(/\\/g, '/').split('/').pop() ?? '';
  const dot = name.lastIndexOf('.');
  if (dot <= 0) return undefined;
  return EXT_ICON_COLORS[name.slice(dot + 1).toLowerCase()];
}
