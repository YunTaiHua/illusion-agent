/**
 * @fileoverview 玻璃拟态下拉选择组件
 *
 * 替代原生 <select>，为下拉选项面板提供玻璃拟态质感（backdrop-filter 模糊）。
 * 原生 <select> 的 <option> 列表由浏览器渲染，无法应用 CSS 玻璃效果，且在
 * 深色模式下常显示为纯黑背景；本组件用自定义浮层解决该问题。
 *
 * 特性：
 * - 触发器为简洁输入风格（无玻璃），聚焦时外圈阴影散光
 * - 下拉面板使用实色卡片背景（bg-surface-card-alt），无玻璃模糊
 * - 通过 React Portal 渲染到 document.body，避免被父容器 overflow 裁剪
 * - 动态计算面板位置（基于触发器 getBoundingClientRect），支持滚动跟随
 * - 支持键盘导航（↑↓ 选择、Enter 确认、Esc 关闭）
 * - 点击外部自动关闭
 * - 面板动画与输入区底部 ToolBar 下拉一致（animate-fade），滚动条隐藏
 *
 * @module GlassDropdown
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

/** 下拉选项接口 */
export interface DropdownOption {
  /** 选项值 */
  value: string;
  /** 显示标签 */
  label: string;
}

/**
 * GlassDropdown 组件属性接口
 */
interface GlassDropdownProps {
  /** 当前选中值 */
  value: string;
  /** 选项列表 */
  options: DropdownOption[];
  /** 值变化回调 */
  onChange: (value: string) => void;
  /** 占位提示（可选） */
  placeholder?: string;
  /** 是否禁用 */
  disabled?: boolean;
  /** 附加容器类名（可选） */
  className?: string;
}

/** 面板浮动位置（基于视口坐标） */
interface PanelPosition {
  top: number;
  left: number;
  width: number;
}

/**
 * 玻璃拟态下拉选择组件
 *
 * @param props - 组件属性
 * @returns 返回下拉选择的 JSX 元素
 */
export function GlassDropdown({
  value, options, onChange, placeholder, disabled, className,
}: GlassDropdownProps) {
  const [open, setOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const [panelPos, setPanelPos] = useState<PanelPosition | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const selectedLabel = useMemo(() => {
    const opt = options.find((o) => o.value === value);
    return opt ? opt.label : (placeholder ?? value);
  }, [options, value, placeholder]);

  // 打开时定位到当前选中项
  useEffect(() => {
    if (!open) return;
    const idx = Math.max(0, options.findIndex((o) => o.value === value));
    setHighlightedIndex(idx);
  }, [open, options, value]);

  // 计算面板位置：基于触发器 rect，面板渲染到 body 后绝对定位
  const updatePanelPosition = useCallback(() => {
    if (!triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    setPanelPos({
      top: rect.bottom + 4,
      left: rect.left,
      width: rect.width,
    });
  }, []);

  // 打开时及窗口尺寸变化时重新计算位置
  useLayoutEffect(() => {
    if (!open) return;
    updatePanelPosition();
    const handleResize = () => updatePanelPosition();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [open, updatePanelPosition]);

  // 滚动时重新计算面板位置；触发器移出视口则关闭面板
  // 不直接关闭：点击触发器时浏览器为让 focused button 可见会自动滚动
  // overflow 容器，capture 监听会捕获该滚动并立即关闭面板，导致"无法打开"
  useEffect(() => {
    if (!open) return;
    const handleScroll = () => {
      if (!triggerRef.current) return;
      const rect = triggerRef.current.getBoundingClientRect();
      // 触发器完全移出视口（上边或下边）才关闭
      if (rect.bottom < 0 || rect.top > window.innerHeight) {
        setOpen(false);
        return;
      }
      updatePanelPosition();
    };
    window.addEventListener('scroll', handleScroll, true);
    return () => window.removeEventListener('scroll', handleScroll, true);
  }, [open, updatePanelPosition]);

  // 滚动高亮项到可视区
  useEffect(() => {
    if (!open || !listRef.current) return;
    const el = listRef.current.children[highlightedIndex] as HTMLElement;
    el?.scrollIntoView({ block: 'nearest' });
  }, [highlightedIndex, open]);

  // 点击外部关闭（容器和面板都视为内部）
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      if (containerRef.current?.contains(target)) return;
      if (listRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (!open) { setOpen(true); return; }
      setHighlightedIndex((i) => Math.min(i + 1, options.length - 1));
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (!open) { setOpen(true); return; }
      setHighlightedIndex((i) => Math.max(i - 1, 0));
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      if (!open) { setOpen(true); return; }
      const opt = options[highlightedIndex];
      if (opt) { onChange(opt.value); setOpen(false); }
      return;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
      return;
    }
  }, [open, options, highlightedIndex, onChange]);

  const handleSelect = useCallback((val: string) => {
    onChange(val);
    setOpen(false);
  }, [onChange]);

  const handleTriggerClick = useCallback(() => {
    if (disabled) return;
    setOpen((v) => !v);
  }, [disabled]);

  // 阻止 mousedown 默认行为（focus），避免浏览器为让 focused button 可见
  // 而自动滚动 overflow 容器，从而触发 scroll 监听导致面板立即关闭
  const handleTriggerMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
  }, []);

  return (
    <div
      ref={containerRef}
      className={`relative ${className ?? ''}`}
      onKeyDown={handleKeyDown}
    >
      {/* 触发器：简洁输入风格，聚焦时阴影散光 */}
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        onClick={handleTriggerClick}
        onMouseDown={handleTriggerMouseDown}
        title={selectedLabel}
        className={`w-full px-3 py-2 rounded-md bg-surface-card-alt border border-border-light text-content-primary text-sm text-left transition-all duration-200 cursor-pointer flex items-center justify-between gap-2 disabled:opacity-50 disabled:cursor-not-allowed ${
          open ? 'border-primary shadow-glow' : 'focus:border-primary focus:shadow-glow'
        }`}
      >
        <span className={`truncate ${selectedLabel === (placeholder ?? '') ? 'text-content-disabled' : ''}`}>
          {selectedLabel}
        </span>
        <svg
          className={`w-3.5 h-3.5 text-content-secondary shrink-0 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
          viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
        >
          <path d="M3 4.5l3 3 3-3" />
        </svg>
      </button>

      {/* 下拉面板：通过 Portal 渲染到 body，避免父容器 overflow 裁剪 */}
      {open && panelPos && createPortal(
        <div
          ref={listRef}
          className="fixed z-50 bg-surface-card-alt border border-border-medium rounded-lg max-h-56 overflow-y-auto p-1 animate-fade dropdown-scroll shadow-card dropdown-panel"
          style={{ top: `${panelPos.top}px`, left: `${panelPos.left}px`, width: `${panelPos.width}px` }}
        >
          {options.map((opt, idx) => {
            const isSelected = opt.value === value;
            const isHighlighted = idx === highlightedIndex;
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => handleSelect(opt.value)}
                title={opt.label}
                className={`w-full text-left px-3 py-2 border border-transparent hover:border-border-light text-sm transition-colors cursor-pointer flex items-center gap-2 ${
                  isSelected
                    ? 'text-primary font-medium glass-option-hover'
                    : isHighlighted
                      ? 'glass-option-active text-content-primary'
                      : 'text-content-secondary glass-option-hover'
                }`}
              >
                <span className="flex-1 truncate">{opt.label}</span>
                {isSelected && (
                  <svg className="w-3.5 h-3.5 shrink-0" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M2.5 6.5l2.5 2.5 4.5-5" />
                  </svg>
                )}
              </button>
            );
          })}
        </div>,
        document.body,
      )}
    </div>
  );
}
