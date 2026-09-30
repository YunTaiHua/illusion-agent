/**
 * @fileoverview 悬浮自动滚动文本组件
 *
 * 文本被截断（内容宽于容器）时，鼠标悬浮触发单向循环滚动：内容持续
 * 向左移动，尾部走完后从起点重新进入（跑马灯式，不回弹）。带 200ms
 * 触发延时，悬浮离开立即复位；不溢出时静止显示。滚动速度恒定，
 * 时长随溢出量自适应。用于预览 tab 标题与文件头路径行等窄空间场景。
 *
 * @module AutoScrollText
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';

/** 两份内容之间的间隔（px），决定尾部走完后到重新进入的停顿感 */
const COPY_GAP = 48;

/**
 * AutoScrollText 组件属性接口
 */
interface AutoScrollTextProps {
  /** 文本内容（可含着色 span 等内联节点） */
  children: ReactNode;
  /** 外层容器附加类名（宽度约束由父级 flex/min-w-0 提供） */
  className?: string;
  /** 悬浮提示 */
  title?: string;
  /** 触发方式：self = 悬浮文本自身即触发；parent = 由带 as-host 类的最近父级触发（如整个 tab） */
  trigger?: 'self' | 'parent';
}

/**
 * 悬浮自动滚动文本组件
 *
 * @param props - 组件属性
 * @returns 返回自动滚动文本的 JSX 元素
 */
export default function AutoScrollText({ children, className = '', title, trigger = 'self' }: AutoScrollTextProps) {
  const outerRef = useRef<HTMLSpanElement>(null);
  const copyRef = useRef<HTMLSpanElement>(null);
  // 滚动距离（px）：首份内容宽度 + 间隔；为 0 表示无溢出、不滚动
  const [dist, setDist] = useState(0);

  const measure = useCallback(() => {
    const outer = outerRef.current;
    const copy = copyRef.current;
    if (!outer || !copy) return;
    setDist(copy.scrollWidth > outer.clientWidth + 1 ? copy.scrollWidth + COPY_GAP : 0);
  }, []);

  // 内容/布局变化时重测
  useLayoutEffect(measure, [measure, children]);

  // 容器宽度变化（tab 弹性伸缩/面板拖拽）时重测
  useEffect(() => {
    const outer = outerRef.current;
    if (!outer) return;
    const ro = new ResizeObserver(measure);
    ro.observe(outer);
    return () => ro.disconnect();
  }, [measure]);

  // 速度恒定 ~30px/s，钳制在 3–16s
  const dur = dist > 0 ? Math.min(16, Math.max(3, dist / 30)).toFixed(2) : '0';

  return (
    <span
      ref={outerRef}
      title={title}
      className={`block overflow-hidden whitespace-nowrap ${trigger === 'self' ? 'as-host' : ''} ${dist > 0 ? H_FADE_MASK : ''} ${className}`}
    >
      <span
        className="as-ticker-track inline-flex"
        style={{
          '--as-dist': `-${dist}px`,
          '--as-dur': `${dur}s`,
          animation: dist > 0 ? undefined : 'none',
        } as CSSProperties}
      >
        <span ref={copyRef} className="inline-block shrink-0 whitespace-nowrap">{children}</span>
        {dist > 0 && (
          <span className="inline-block shrink-0 whitespace-nowrap" style={{ paddingLeft: COPY_GAP }} aria-hidden="true">
            {children}
          </span>
        )}
      </span>
    </span>
  );
}

/** 右缘渐隐蒙版：仅在内容溢出（截断）时显示，提示"还有更多文本" */
const H_FADE_MASK =
  '[mask-image:linear-gradient(to_right,black_calc(100%-12px),transparent_100%)] [-webkit-mask-image:linear-gradient(to_right,black_calc(100%-12px),transparent_100%)]';
