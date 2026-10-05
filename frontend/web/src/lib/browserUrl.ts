/**
 * @fileoverview 浏览器地址栏输入归一化（三处入口共用：URL 栏 / + 面板 / 右栏区块）
 *
 * 浏览器地址栏标准语义：像网址的按网址打开，不像的当搜索词。
 * 主机识别：本机/私网走 HTTP、公网域名走 HTTPS，并对"明显不是主机"的输入（含空白/中日韩等非
 * 主机字符）回退为 Bing 搜索——此前中文输入被 punycode 成 xn-- 域名
 * 报 ERR_NAME_NOT_RESOLVED，用户侧表现为"打开失败且莫名其妙"。
 *
 * @module browserUrl
 */

/** 显式协议白名单（与 ALLOWED_BROWSER_PROTOCOLS 对齐的子集） */
const ALLOWED_PROTOCOLS = new Set(['http:', 'https:']);

/** 协议前缀（http:/https:/file: 等） */
const PROTOCOL_RE = /^[a-zA-Z][a-zA-Z0-9+.-]*:/;

/** 主机形态：字母数字点连字符 + 可选端口 + 可选路径（不含空白/非 ASCII） */
const HOST_LIKE_RE = /^[a-zA-Z0-9]([a-zA-Z0-9.-]*)(:\d+)?([/?#].*)?$/;

/** 本机/私网主机（HTTP 优先，与shouldPreferHttpForSchemeLessUrl 口径一致） */
const LOCAL_HOST_RE = /^(localhost|127\.\d{1,3}\.\d{1,3}\.\d{1,3}|\[::1\]|::1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(:\d+)?([/?#].*)?$/i;

/** 搜索引擎（Bing：国内网络可达性优于 Google） */
const SEARCH_URL = 'https://www.bing.com/search?q=';

/**
 * 把地址栏输入归一化为可导航 URL
 *
 * @param input - 用户原始输入
 * @returns 可导航 URL；空串表示输入为空（调用方忽略）
 */
export function normalizeBrowserInput(input: string): string {
  const trimmed = input.trim();
  if (!trimmed) return '';

  // 显式协议：白名单内原样使用；其他协议（file:/javascript: 等）不导航
  const protocol = trimmed.match(PROTOCOL_RE)?.[0].toLowerCase();
  if (protocol) {
    if (!ALLOWED_PROTOCOLS.has(protocol)) return '';
    return trimmed;
  }

  // 无协议：本机/私网/带端口地址走 HTTP，域名形态走 HTTPS
  if (LOCAL_HOST_RE.test(trimmed)) return `http://${trimmed}`;
  if (HOST_LIKE_RE.test(trimmed) && trimmed.includes('.')) return `https://${trimmed}`;

  // 其余（含空白/中日韩等）：搜索词
  return SEARCH_URL + encodeURIComponent(trimmed);
}

/**
 * 输入是否会被当作搜索词（供输入框占位文案/提示判断）
 *
 * @param input - 用户原始输入
 * @returns true 表示将按搜索处理
 */
export function isSearchInput(input: string): boolean {
  const url = normalizeBrowserInput(input);
  return url.startsWith(SEARCH_URL);
}
