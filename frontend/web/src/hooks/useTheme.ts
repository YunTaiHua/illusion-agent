/**
 * @fileoverview 主题（浅色/深色/跟随系统）管理 Hook
 *
 * 主题权威源与持久化基于后端 settings.json 的 theme 字段：
 * - 初始加载：通过 GET /api/settings 读取 theme 字段（light / dark / system）
 * - 切换主题：通过 PATCH /api/settings/theme 同步写入 settings.json
 * - 跟随系统：当 theme === 'system' 时，根据 prefers-color-scheme 决定实际深浅
 * - 通过在 `<html>` 上添加/移除 `dark` 类切换主题（配合 :root.dark CSS 变量）
 *
 * localStorage 的 `illusion-theme` 仅作为"上次实际生效深浅"的快速缓存：
 * index.html 内联脚本在 React 渲染前同步读取并应用，避免首帧主题闪烁（FOUC）；
 * API 返回后以 settings.json 为准校正。
 *
 * 该字段仅用于 web 前端，不传递到 terminal 端（terminal 无主题系统）。
 *
 * @module useTheme
 */

import { useCallback, useEffect, useState } from 'react';
import { settingsApi } from '../api';

/** 主题模式：浅色 / 深色 / 跟随系统 */
export type Theme = 'light' | 'dark' | 'system';
/** 实际生效的深浅主题（system 已解析为 light/dark） */
type ResolvedTheme = 'light' | 'dark';

/** localStorage 缓存键（与 index.html 内联脚本共用） */
const THEME_CACHE_KEY = 'illusion-theme';

/** 读取系统深色偏好 */
function readSystemTheme(): ResolvedTheme {
  if (typeof window !== 'undefined' && window.matchMedia) {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  return 'light';
}

/** 将主题模式解析为实际深浅主题 */
function resolveTheme(theme: Theme): ResolvedTheme {
  return theme === 'system' ? readSystemTheme() : theme;
}

/**
 * 读取本地缓存的深浅主题（同步，供首帧使用）
 *
 * 与 index.html 内联脚本逻辑保持一致：有缓存用缓存；
 * 无缓存（首次打开）跟随系统偏好，避免首帧与内联脚本不一致再次闪烁。
 */
function readCachedResolved(): ResolvedTheme {
  try {
    const cached = localStorage.getItem(THEME_CACHE_KEY);
    if (cached === 'dark' || cached === 'light') return cached;
  } catch {
    // localStorage 不可用时忽略
  }
  return readSystemTheme();
}

/** 应用主题到 <html> 根节点 */
function applyTheme(resolved: ResolvedTheme) {
  const root = document.documentElement;
  if (resolved === 'dark') {
    root.classList.add('dark');
  } else {
    root.classList.remove('dark');
  }
}

/**
 * 跨实例主题同步事件名
 *
 * 页面可能同时挂载多个 useTheme 实例（右栏 / 顶部按钮组 / 设置弹窗各自调用）。
 * 为避免某实例 setTheme 后其他实例的 theme state 显示漂移，setTheme 统一派发
 * 该自定义事件（detail 携带新主题模式），各实例监听后同步本地 state。
 */
const THEME_SYNC_EVENT = 'illusion:theme-change';

/** 派发主题同步事件 */
function broadcastTheme(next: Theme) {
  try {
    window.dispatchEvent(new CustomEvent(THEME_SYNC_EVENT, { detail: next }));
  } catch {
    // 非浏览器环境/事件派发失败时忽略，仅当前实例生效
  }
}

/**
 * 主题管理 Hook
 *
 * @returns { theme, resolved, toggleTheme, setTheme }
 *  - theme: 当前主题模式（light / dark / system）
 *  - resolved: 实际生效的深浅主题（system 已解析）
 *  - toggleTheme: 三态循环切换（light → dark → system → light）
 *  - setTheme: 直接设置主题模式
 */
export function useTheme() {
  // 初始模式默认 light（与 settings.json 默认值一致），待 API 返回后校正
  const [theme, setThemeState] = useState<Theme>('light');
  // 实际应用的主题：先从本地缓存读取（与 index.html 内联脚本一致，避免首帧闪烁），
  // 待 API 返回 settings.json 的真实 theme 后校正
  const [resolved, setResolved] = useState<ResolvedTheme>(readCachedResolved);

  // 初始加载：从 settings.json 读取 theme 字段
  useEffect(() => {
    let cancelled = false;
    settingsApi
      .get()
      .then((resp) => {
        if (cancelled) return;
        setThemeState(resp.theme);
        setResolved(resolveTheme(resp.theme));
      })
      .catch(() => {
        // 读取失败时保留默认值 light
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 应用到 DOM，并写回本地缓存（供下次启动首帧使用）
  useEffect(() => {
    applyTheme(resolved);
    try {
      localStorage.setItem(THEME_CACHE_KEY, resolved);
    } catch {
      // localStorage 不可用时忽略
    }
    // 桌面壳：nativeTheme 同步，内置浏览器 guest 的深浅随之切换
    window.illusionDesktop?.setAppTheme?.(resolved)?.catch(() => undefined);
  }, [resolved]);

  // 监听系统主题变化：仅当 theme === 'system' 时跟随
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const mql = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = (e: MediaQueryListEvent) => {
      if (theme === 'system') {
        setResolved(e.matches ? 'dark' : 'light');
      }
    };
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, [theme]);

  /** 设置主题模式并同步写入 settings.json */
  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    setResolved(resolveTheme(next));
    // 通知其他 useTheme 实例同步显示值（避免折叠态按钮/设置弹窗漂移）
    broadcastTheme(next);
    // 同步到 settings.json（失败忽略，不阻塞 UI）
    settingsApi.updateTheme(next).catch(() => {
      // 写入失败时不回滚 UI 状态，下次加载会从 settings.json 重新读取
    });
  }, []);

  // 监听其他实例的主题变更事件：同步本地 state（resolved 由 applyTheme effect 统一落 DOM）
  useEffect(() => {
    const handler = (e: Event) => {
      const next = (e as CustomEvent<Theme>).detail;
      if (next === 'light' || next === 'dark' || next === 'system') {
        setThemeState(next);
        setResolved(resolveTheme(next));
      }
    };
    window.addEventListener(THEME_SYNC_EVENT, handler);
    return () => window.removeEventListener(THEME_SYNC_EVENT, handler);
  }, []);

  /** 三态循环切换：light → dark → system → light */
  const toggleTheme = useCallback(() => {
    const order: Theme[] = ['light', 'dark', 'system'];
    const idx = order.indexOf(theme);
    // theme 必在 order 中，idx 不为 -1，取模结果必存在
    const next = order[(idx + 1) % order.length];
    if (next) setTheme(next);
  }, [theme, setTheme]);

  return { theme, resolved, toggleTheme, setTheme };
}
