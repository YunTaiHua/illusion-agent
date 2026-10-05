/**
 * @fileoverview 自由尺寸拖拽 hook + 四边四角把手（桌面内嵌分支与 Web 画布共用）
 *
 * - setPointerCapture 元素级捕获：指针划过 Electron <webview> 事件仍送达宿主
 *   （window 级监听收不到 guest 区域的 pointermove）
 * - pointermove 每帧只提交最后一组尺寸；buttons 是物理按键事实源
 * - 失焦终止手势；拖拽距离按缩放倒数换算；Shift 步进 10px
 *
 * @module useResponsiveDrag
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type React from 'react';

export type ResizeDir = { widthDirection: -1 | 0 | 1; heightDirection: -1 | 0 | 1 };

export interface ResponsiveSize { width: number; height: number }

export const VIEWPORT_LIMITS = { minWidth: 320, maxWidth: 3840, minHeight: 320, maxHeight: 2160 } as const;

export function clampSize(size: ResponsiveSize): ResponsiveSize {
  const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, Math.round(v)));
  return {
    width: clamp(size.width, VIEWPORT_LIMITS.minWidth, VIEWPORT_LIMITS.maxWidth),
    height: clamp(size.height, VIEWPORT_LIMITS.minHeight, VIEWPORT_LIMITS.maxHeight),
  };
}

/** 拖拽会话（捕获目标 + 起点 + 方向） */
interface ResizeDragState {
  captureTarget: HTMLDivElement;
  heightDirection: -1 | 0 | 1;
  pointerId: number;
  startClientX: number;
  startClientY: number;
  startSize: ResponsiveSize;
  widthDirection: -1 | 0 | 1;
}

/**
 * 自由尺寸拖拽状态机
 *
 * @param size - 当前逻辑视口尺寸
 * @param onSizeChange - 尺寸提交（已收敛到限制内；拖拽中逐帧调用）
 * @param onCommit - 手势结束/键盘步进后的最终尺寸（用于向后端同步一次）
 * @param rendererScaleRef - 当前视觉缩放（拖拽像素 → 逻辑像素换算）
 * @returns 拖拽事件处理器（绑定在把手元素上）
 */
export function useResponsiveDrag(
  size: ResponsiveSize,
  onSizeChange: (w: number, h: number) => void,
  onCommit: (w: number, h: number) => void,
  rendererScaleRef: React.RefObject<number>,
) {
  const [active, setActive] = useState(false);
  const dragRef = useRef<ResizeDragState | null>(null);
  const pendingRef = useRef<ResponsiveSize | null>(null);
  const rafRef = useRef(0);
  const sizeRef = useRef(size);
  sizeRef.current = size;

  const applySize = useCallback((w: number, h: number) => {
    const next = clampSize({ width: w, height: h });
    onSizeChange(next.width, next.height);
    return next;
  }, [onSizeChange]);

  // pointermove 频率可能高于刷新率：每帧只提交最后一组尺寸，避免无效渲染
  const scheduleSize = useCallback((w: number, h: number) => {
    pendingRef.current = clampSize({ width: w, height: h });
    if (rafRef.current !== 0) return;
    rafRef.current = window.requestAnimationFrame(() => {
      rafRef.current = 0;
      const pending = pendingRef.current;
      pendingRef.current = null;
      if (pending) applySize(pending.width, pending.height);
    });
  }, [applySize]);

  // finishResize 经 ref 暴露稳定身份：blur/unmount effect 只挂一次。若把
  // onCommit/onSizeChange 放进依赖，上游回调每次渲染换身份都会让 effect
  // 先 cleanup 再重挂——cleanup 里的 finishResize() 会在 pointerdown 的同一次
  // flush 里把刚建立的拖拽立刻杀掉（"拖拽毫无反应"）
  const finishResizeRef = useRef<(pointerId?: number, commitPendingSize?: boolean) => void>(() => undefined);
  finishResizeRef.current = (pointerId?: number, commitPendingSize = true) => {
    const drag = dragRef.current;
    if (!drag || (pointerId !== undefined && drag.pointerId !== pointerId)) return;
    dragRef.current = null;
    setActive(false);
    if (rafRef.current !== 0) {
      window.cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    }
    const pending = pendingRef.current;
    pendingRef.current = null;
    if (pending && commitPendingSize) {
      const final = applySize(pending.width, pending.height);
      onCommit(final.width, final.height);
    }
    try {
      if (drag.captureTarget.hasPointerCapture(drag.pointerId)) {
        drag.captureTarget.releasePointerCapture(drag.pointerId);
      }
    } catch {
      // capture 可能已被系统或浏览器撤销；状态已先清理，lostpointercapture 重入不会续拖
    }
  };
  /** 稳定身份的拖拽终止（effect/把手事件用） */
  const finishResize = useCallback((pointerId?: number, commitPendingSize = true) => {
    finishResizeRef.current(pointerId, commitPendingSize);
  }, []);

  const beginResize = useCallback((directions: ResizeDir, e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    finishResize();
    try {
      // 元素级指针捕获：此后该指针的所有事件都投递到把手节点，
      // 划过 webview（guest 渲染进程）也不例外
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // 自动化合成事件可能没有活跃 pointer；同一节点上的 move 仍可驱动尺寸
    }
    dragRef.current = {
      captureTarget: e.currentTarget,
      heightDirection: directions.heightDirection,
      pointerId: e.pointerId,
      startClientX: e.clientX,
      startClientY: e.clientY,
      startSize: sizeRef.current,
      widthDirection: directions.widthDirection,
    };
    setActive(true);
  }, [finishResize]);

  const onPointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== e.pointerId) return;
    // mousemove 的 buttons 是浏览器对当前物理按键的事实来源；即使窗口 blur
    // 被系统截图层吞掉，松开主键后的第一帧也必须终止旧手势
    if (e.pointerType === 'mouse' && (e.buttons & 1) === 0) {
      finishResize(e.pointerId);
      return;
    }
    e.preventDefault();
    // frame 使用 CSS transform 缩放后，pointer delta 是视觉像素；除以缩放
    // 换算回逻辑视口像素，50%/200% 下同样拖距产生正确变化
    const scale = rendererScaleRef.current;
    const safeScale = scale != null && scale > 0 ? scale : 1;
    scheduleSize(
      drag.startSize.width + ((e.clientX - drag.startClientX) / safeScale) * drag.widthDirection,
      drag.startSize.height + ((e.clientY - drag.startClientY) / safeScale) * drag.heightDirection,
    );
  }, [finishResize, scheduleSize]);

  const onKeyDown = useCallback((directions: ResizeDir, e: React.KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 10 : 1;
    const current = sizeRef.current;
    if (directions.widthDirection !== 0 && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
      e.preventDefault();
      const next = applySize(
        current.width + (e.key === 'ArrowRight' ? step : -step) * directions.widthDirection,
        current.height,
      );
      onCommit(next.width, next.height);
    }
    if (directions.heightDirection !== 0 && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
      e.preventDefault();
      const next = applySize(
        current.width,
        current.height + (e.key === 'ArrowDown' ? step : -step) * directions.heightDirection,
      );
      onCommit(next.width, next.height);
    }
  }, [applySize, onCommit]);

  // 失焦终止手势（系统截图/应用切换吞掉 pointerup 时，原 handle 收不到结束事件，
  // pointer capture 与 dragRef 残留会让重新聚焦后的普通移动继续 resize）
  useEffect(() => {
    const onBlur = () => finishResizeRef.current();
    window.addEventListener('blur', onBlur);
    return () => {
      window.removeEventListener('blur', onBlur);
      // 卸载时提交在途尺寸，但不再补发 onCommit 之外的语义（commit=true）
      finishResizeRef.current();
    };
  }, []);

  return { beginResize, onPointerMove, finishResize, onKeyDown, active };
}

/**
 * Fit 画布测量（useLayoutEffect：首绘前完成测量，frame 不会以未缩放尺寸闪现）
 *
 * inactive tab 的 display:none 会让 ResizeObserver 回报 0×0：非正尺寸不采纳、
 * 保留上一次有效画布，切回时 Fit 不会先回退为 1 再缩小。
 */
export function useFitCanvasMeasure(
  canvasRef: React.RefObject<HTMLDivElement | null>,
  enabled: boolean,
): ResponsiveSize | null {
  const [canvasSize, setCanvasSize] = useState<ResponsiveSize | null>(null);
  useLayoutEffect(() => {
    if (!enabled) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const update = (w: number, h: number) => {
      if (w <= 0 || h <= 0) return;
      setCanvasSize((prev) => (prev && prev.width === w && prev.height === h ? prev : { width: w, height: h }));
    };
    const rect = canvas.getBoundingClientRect();
    update(rect.width, rect.height);
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) update(entry.contentRect.width, entry.contentRect.height);
    });
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [canvasRef, enabled]);
  return canvasSize;
}


/** Fit/缩放解析：画布 - p4×2 内边距，上限 1） */
export function resolveVisualScale(
  canvasSize: ResponsiveSize | null,
  zoom: string,
  size: ResponsiveSize,
): number {
  const CANVAS_PADDING_PX = 16;
  // Fit 安全边距：frame 恰好等于可用空间时，亚像素溢出会反复触发滚动条
  // 出现/消失 → 画布 contentRect 交替变化 → scale 交替重算 → 无交互自激
  // 振荡（剧烈抖动 + 把手圆点漂浮）。横版 1280×720 恰好骑在这个边界上
  const FIT_SAFETY_PX = 2;
  if (zoom !== 'fit') return Number(zoom) / 100;
  if (!canvasSize || canvasSize.width <= 0 || canvasSize.height <= 0) return 1;
  return Math.min(
    1,
    Math.max(0, canvasSize.width - CANVAS_PADDING_PX * 2 - FIT_SAFETY_PX) / size.width,
    Math.max(0, canvasSize.height - CANVAS_PADDING_PX * 2 - FIT_SAFETY_PX) / size.height,
  );
}
