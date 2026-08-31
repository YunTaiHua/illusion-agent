/**
 * @fileoverview Web 启动令牌（launch token）读取工具
 *
 * 后端（illusion web）每次启动生成一个 launch token 并打印在完整访问
 * URL 中（http://host:port/?token=...）。本模块负责：
 *   - 页面首次加载时从 URL query 取出 token；
 *   - 存入 sessionStorage（token 交换后地址栏已 303 清理，刷新页面靠
 *     sessionStorage 恢复，避免丢失会话）；
 *   - 供 REST 客户端注入 Authorization: Bearer 头、WebSocket 拼接
 *     ?token= 查询参数。
 *
 * token 缺失（如纯 cookie 会话恢复）时各函数返回 null / 不加头，后端
 * 的签名 cookie 校验兜底。
 *
 * @module launchToken
 */

const URL_TOKEN_KEY = 'token';
const STORAGE_KEY = 'illusion_web_token';

/** 模块级缓存：避免每次请求都解析 URL / 访问 storage */
let cached: string | null | undefined;

function readUrlToken(): string | null {
  try {
    return new URLSearchParams(window.location.search).get(URL_TOKEN_KEY);
  } catch {
    return null;
  }
}

/**
 * 获取当前会话的 launch token。
 *
 * 优先级：内存缓存 → URL query → sessionStorage。取到后写入
 * sessionStorage（首次加载的 URL token 与后续刷新的 storage 兜底统一）。
 *
 * @returns token 字符串；无凭据时为 null
 */
export function getLaunchToken(): string | null {
  if (cached !== undefined) return cached;
  const fromUrl = readUrlToken();
  const token = fromUrl ?? sessionStorage.getItem(STORAGE_KEY);
  cached = token;
  if (token) {
    try {
      sessionStorage.setItem(STORAGE_KEY, token);
    } catch {
      // 隐私模式等 storage 不可用场景：仅内存缓存，不阻塞请求
    }
  }
  return token;
}

/**
 * 把认证头合并进既有请求头（Authorization: Bearer <token>）。
 *
 * @param headers - 既有请求头
 * @returns 合并后的请求头；无 token 时原样返回
 */
export function attachAuthHeaders(headers: Record<string, string>): Record<string, string> {
  const token = getLaunchToken();
  if (!token) return headers;
  return { ...headers, Authorization: `Bearer ${token}` };
}

/**
 * 生成带 token 的查询字符串（WebSocket URL 拼接用）。
 *
 * @returns 形如 "?token=xxx"；无 token 时为空串
 */
export function authQueryString(): string {
  const token = getLaunchToken();
  return token ? `?token=${encodeURIComponent(token)}` : '';
}