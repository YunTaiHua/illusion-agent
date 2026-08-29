/**
 * @fileoverview 桌面壳自动更新 Hook
 *
 * 仅在 Electron 桌面壳内生效（window.illusionDesktop 存在）；浏览器访问
 * Web 端时 isSupported 恒为 false。更新流程由主进程自动检查（启动检查 +
 * 12 小时复查），发现新版本仅在顶栏亮出更新图标，用户点击图标才下载；
 * 下载完成后需用户再次点击安装图标才会退出并显式安装（正常退出应用
 * 不会触发安装）。macOS 未签名不支持自动更新，主进程平台门控整体跳过。
 * 渲染进程职责：
 *   - 顶栏（TitleBar）依据状态显示更新图标：available 闪烁下载图标 /
 *     downloading 进度环 / downloaded 常亮安装图标（不闪烁）
 *   - 用户点击就绪图标显式安装（显示安装进度，退出应用进入安装器）
 *
 * 状态来源与同步：
 *   - 主进程 updater 模块广播 'updater:event'（模块级单例订阅一次）
 *   - 渲染进程可能晚于事件挂载（托盘隐藏期间完成下载等），挂载时经
 *     getState() 拉取主进程当前状态兜底
 *   - 模块级 store + useSyncExternalStore：所有消费方共享同一份状态
 *
 * @module useDesktopUpdater
 */

import { useCallback, useSyncExternalStore } from 'react';

/** 更新流程状态（与主进程 desktop/src/updater.ts 的 UpdaterState 对齐） */
export type UpdaterStatus =
  | 'idle'
  | 'checking'
  | 'available'
  | 'downloading'
  | 'downloaded'
  | 'error';

/** 下载进度（主进程透传 electron-updater ProgressInfo 字段子集） */
export interface UpdaterProgress {
  percent: number;
  transferred: number;
  total: number;
  bytesPerSecond: number;
}

/** 更新状态快照 */
export interface UpdaterState {
  status: UpdaterStatus;
  /** 新版本号（available / downloading / downloaded 阶段有值） */
  version?: string;
  /** 下载进度（downloading 阶段有值） */
  progress?: UpdaterProgress;
  /** 错误消息（error 阶段有值；错误时图标隐藏，等待下次复查） */
  error?: string;
}

/** 桥接 API 类型（与 desktop/src/preload.ts 暴露的 updater 对象、
 * vite-env.d.ts 的 DesktopUpdaterState 全局类型对齐） */
interface DesktopUpdaterBridge {
  getState: () => Promise<DesktopUpdaterState | null>;
  download: () => void;
  install: () => void;
  onEvent: (cb: (state: DesktopUpdaterState) => void) => () => void;
}

function getBridge(): DesktopUpdaterBridge | null {
  return window.illusionDesktop?.updater ?? null;
}

// ---- 模块级 store：所有消费方共享同一份状态 ----

let store: UpdaterState = { status: 'idle' };
const listeners = new Set<() => void>();
let bound = false;

function emit(): void {
  for (const listener of [...listeners]) listener();
}

/** 订阅主进程广播 + 拉取兜底状态（幂等，首个订阅者触发） */
function ensureBound(): void {
  if (bound) return;
  const bridge = getBridge();
  if (!bridge) return;
  bound = true;
  bridge.onEvent((remote) => {
    store = remote;
    emit();
  });
  void bridge
    .getState()
    .then((remote) => {
      if (remote) {
        store = remote;
        emit();
      }
    })
    .catch(() => undefined);
}

function subscribe(listener: () => void): () => void {
  ensureBound();
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): UpdaterState {
  return store;
}

/**
 * useDesktopUpdater：更新状态 + 图标点击动作。
 *
 * isSupported=false 时（浏览器 Web 端）动作为 no-op，
 * 渲染侧据此隐藏更新图标。
 */
export function useDesktopUpdater() {
  const state = useSyncExternalStore(subscribe, getSnapshot);

  /** 用户点击更新图标：开始下载新版本（仅 available 态有效，主进程有状态守卫） */
  const startDownload = useCallback(() => {
    getBridge()?.download();
  }, []);

  /** 立即重启安装已下载的更新（仅 downloaded 态生效，主进程有状态守卫） */
  const installNow = useCallback(() => {
    getBridge()?.install();
  }, []);

  return {
    /** 是否在桌面壳内（浏览器 Web 端为 false） */
    isSupported: typeof window !== 'undefined' && !!window.illusionDesktop,
    state,
    startDownload,
    installNow,
  };
}

/**
 * 顶栏更新图标可见性：仅发现新版本后的下载/就绪阶段展示，
 * 检查中、无更新、错误（等待下次复查重试）均隐藏，避免常驻打扰。
 */
export function isIconVisible(state: UpdaterState): boolean {
  return state.status === 'available' || state.status === 'downloading' || state.status === 'downloaded';
}
