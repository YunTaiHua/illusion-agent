/**
 * @fileoverview 浏览器画面帧外部 store
 *
 * browser_frame 是高频截图流。它不进入 useWebSocketSession 的 React state，
 * 由 BrowserPreviewBody 这个叶子组件按 useSyncExternalStore 订阅，避免每帧
 * 重建 App、聊天区、右栏和预览列的整棵 React 树。
 *
 * @module browserFrameStore
 */

import { useSyncExternalStore } from 'react';
import type { BrowserFrame } from '../types/protocol';

export interface BrowserFrameStore {
  getSnapshot: () => BrowserFrame | null;
  subscribe: (listener: () => void) => () => void;
  set: (frame: BrowserFrame | null) => void;
}

/**
 * 创建一个浏览器画面帧 store。
 *
 * 快照引用只有在收到新帧或显式清空时才变化，满足
 * useSyncExternalStore 的稳定快照契约。
 */
export function createBrowserFrameStore(): BrowserFrameStore {
  let frame: BrowserFrame | null = null;
  const listeners = new Set<() => void>();

  return {
    getSnapshot: () => frame,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    set: (next) => {
      if (next === frame) return;
      frame = next;
      for (const listener of listeners) listener();
    },
  };
}

/** 订阅浏览器帧。store 为空时使用模块级稳定回退（快照恒 null、订阅为空操作），
 *  避免 useSyncExternalStore 因每渲染重建的回退闭包而反复解绑/重绑。 */
const EMPTY_FRAME: BrowserFrame | null = null;
const noopSubscribe = (): (() => void) => () => undefined;
const noopSnapshot = (): BrowserFrame | null => EMPTY_FRAME;

export function useBrowserFrame(store: BrowserFrameStore | null | undefined): BrowserFrame | null {
  return useSyncExternalStore(
    store ? store.subscribe : noopSubscribe,
    store ? store.getSnapshot : noopSnapshot,
    store ? store.getSnapshot : noopSnapshot,
  );
}
