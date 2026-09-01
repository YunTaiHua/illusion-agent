/**
 * @fileoverview 待办事项面板组件
 *
 * Web 前端的待办事项面板组件：
 * - 状态用「圆环」字形表达（完成=勾选圆环、进行中=实心圆环、待办=虚线圆环）
 * - 头部带清单图标 + 活跃任务预览 + 进度计数
 * - 列表项长文本自动换行
 *
 * 保留的功能：
 * - 任务状态显示（进行中、待处理、已完成）
 * - 自动排序（进行中 > 待处理 > 已完成）
 * - 折叠/展开功能、活跃任务预览
 * - 面板始终显示，空列表时显示「暂无待办」占位
 *
 * @module TodoPanel
 */

import { useMemo, useState } from 'react';
import { t, type UiLanguage } from '../i18n';
import type { TodoItemSnapshot } from '../types/protocol';

/**
 * TodoPanel 组件属性接口
 */
interface TodoPanelProps {
  /** 待办事项列表 */
  items: TodoItemSnapshot[];
  /** 当前 UI 语言 */
  lang?: UiLanguage;
}

/**
 * 待办事项面板组件
 *
 * Web 前端的待办事项面板组件。
 *
 * @param props - 组件属性
 * @returns 返回待办事项面板的 JSX 元素
 */
export default function TodoPanel({ items, lang = 'zh-CN' }: TodoPanelProps) {
  // 默认折叠；面板始终显示，不随任务完成自动隐藏
  const [collapsed, setCollapsed] = useState(true);

  // 排序：in_progress > pending > completed
  const sorted = useMemo(() => {
    const order: Record<string, number> = { in_progress: 0, pending: 1, completed: 2 };
    return [...items].sort((a, b) => (order[a.status] ?? 3) - (order[b.status] ?? 3));
  }, [items]);

  const done = sorted.filter((i) => i.status === 'completed').length;
  const total = sorted.length;

  // 找到当前活跃任务用于折叠态预览
  const activeItem = useMemo(() => {
    return sorted.find((i) => i.status === 'in_progress')
      ?? sorted.find((i) => i.status === 'pending')
      ?? [...sorted].reverse().find((i) => i.status === 'completed')
      ?? sorted[0]
      ?? null;
  }, [sorted]);

  const isEmpty = sorted.length === 0;

  return (
    // pill-badge-static 禁用 hover 反馈；px-3 缩小字体两侧边距
    <div
      className="pill-badge pill-badge-static rounded-lg overflow-hidden flex flex-col"
      style={{ borderColor: 'var(--border-medium)' }}
    >
      {/* 头部 */}
      <button
        onClick={() => setCollapsed((c) => !c)}
        className="w-full px-3 py-2.5 flex items-center gap-2.5 cursor-pointer"
      >
        {/* 清单图标 */}
        <svg
          className="w-4 h-4 text-content-disabled shrink-0"
          viewBox="0 0 16 16" fill="currentColor"
        >
          <circle cx="3.5" cy="3.5" r="1.4" />
          <rect x="6.5" y="2.75" width="8" height="1.5" rx="0.75" />
          <circle cx="3.5" cy="8" r="1.4" />
          <rect x="6.5" y="7.25" width="8" height="1.5" rx="0.75" />
          <circle cx="3.5" cy="12.5" r="1.4" />
          <rect x="6.5" y="11.75" width="8" height="1.5" rx="0.75" />
        </svg>

        {/* 折叠态：显示活跃任务预览，或空列表提示（leading-4 与 16px 图标垂直居中对齐；
            title 悬浮显示完整文本，避免 truncate 截断看不到全貌） */}
        {collapsed && (isEmpty ? (
          <span className="flex-1 text-[13px] leading-4 text-content-disabled truncate text-left min-w-0">
            {t(lang, 'no_todos')}
          </span>
        ) : activeItem ? (
          <span
            title={activeItem.activeForm && activeItem.status === 'in_progress' ? activeItem.activeForm : activeItem.content}
            className="flex-1 text-[13px] leading-4 text-content-secondary truncate text-left min-w-0"
          >
            {activeItem.activeForm && activeItem.status === 'in_progress'
              ? activeItem.activeForm
              : activeItem.content}
          </span>
        ) : null)}

        {/* 展开态：显示面板标题，填充折叠标题位置的空区 */}
        {!collapsed && (
          <span className="flex-1 text-[13px] leading-4 text-content-secondary font-medium truncate text-left min-w-0">
            {t(lang, 'todo_title')}
          </span>
        )}

        {/* 进度计数（统一颜色） */}
        <span className="text-xs text-content-disabled tabular-nums shrink-0">
          <span>{done}</span>
          <span className="mx-0.5">/</span>
          <span>{total}</span>
        </span>

        {/* 折叠箭头 */}
        <svg
          className={`w-3.5 h-3.5 text-content-disabled shrink-0 transition-transform duration-200 ${collapsed ? '' : 'rotate-180'}`}
          viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
        >
          <path d="M4 6l4 4 4-4" />
        </svg>
      </button>

      {/* 任务列表（行内 px-3 与头部 px-3 对齐，列表项图标/文字缩进与折叠态预览一致） */}
      {!collapsed && (
        <div className="pb-3 flex flex-col gap-0.5 max-h-40 overflow-y-auto scrollbar-hidden">
          {isEmpty ? (
            <div className="flex items-center gap-2.5 px-3 py-2 min-w-0">
              {/* 图标位占位空格，保持与有 todo 列表项的文字列缩进对齐 */}
              <div className="shrink-0 w-4 h-4" />
              <span className="text-[13px] text-content-disabled">{t(lang, 'no_todos')}</span>
            </div>
          ) : sorted.map((item, idx) => (
            <TodoRow key={`${item.content}-${idx}`} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * 状态字形：进行中为实心圆环、待办为虚线圆环、完成为勾选圆环
 *
 * @param status - 任务状态
 * @returns 返回对应状态的圆环字形 SVG
 */
function StatusGlyph({ status }: { status: TodoItemSnapshot['status'] }) {
  // 三种字形统一 16px（w-4 h-4）填充图标槽位，与头部清单图标同宽同起点，
  // 保证展开列表各行图标列、文字列严格对齐
  // 进行中：主色实线圆环（无动画，降低渲染要求）
  if (status === 'in_progress') {
    return (
      <svg
        className="w-4 h-4 text-primary"
        viewBox="0 0 14 14" fill="none"
      >
        <circle cx="7" cy="7" r="5.8" stroke="currentColor" strokeWidth="1.4" />
      </svg>
    );
  }

  // 待办：虚线圆环
  if (status === 'pending') {
    return (
      <svg
        className="w-4 h-4 text-content-disabled"
        viewBox="0 0 14 14" fill="none"
      >
        <circle cx="7" cy="7" r="5.8" stroke="currentColor" strokeWidth="1.2" strokeDasharray="2.4 2.4" />
      </svg>
    );
  }

  // 已完成：勾选圆环
  return (
    <svg
      className="w-4 h-4 text-success"
      viewBox="0 0 14 14" fill="none"
    >
      <circle cx="7" cy="7" r="5.8" stroke="currentColor" strokeWidth="1.2" fill="currentColor" fillOpacity="0.12" />
      <path
        d="M4.5 7l1.8 1.8 3.2-3.6"
        stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"
      />
    </svg>
  );
}

function TodoRow({ item }: { item: TodoItemSnapshot }) {
  const status = item.status;

  // 行内 px-3 与头部 px-3 对齐：16px 字形容器与头部清单图标同起点，文字与头部预览同起点
  return (
    <div className="flex items-start gap-2.5 px-3 py-1.5 rounded-md min-w-0">
      {/* 状态字形 */}
      <div className="shrink-0 w-4 h-4 mt-0.5 flex items-center justify-center">
        <StatusGlyph status={status} />
      </div>

      {/* 文本：长文本自动换行 */}
      <span
        className={`text-[13px] leading-5 flex-1 min-w-0 break-words transition-colors duration-200 ${
          status === 'completed'
            ? 'text-content-disabled line-through'
            : status === 'in_progress'
            ? 'text-content-primary font-medium'
            : 'text-content-secondary'
        }`}
      >
        {item.activeForm && status === 'in_progress' ? item.activeForm : item.content}
      </span>
    </div>
  );
}