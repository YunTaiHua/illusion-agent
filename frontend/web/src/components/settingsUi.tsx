/**
 * @fileoverview 设置表单共享 UI 规范（样式常量 + 通用小组件）
 *
 * SetupForm 及其各 Tab（AgentsTab / CronTab / BrowserTab 等）共同引用，
 * 保证设置表单内标题层级、字段标签、输入框、注释提示风格统一：
 * - Tab 内容标题：text-base 加粗（每个 Tab 顶部唯一一个大标题）
 * - 区块/卡片标题：text-sm 中等字重
 * - 字段标签：text-xs 次要色（FieldLabel 渲染）
 * - 输入框：聚焦散光统一由 inputClass 提供
 * - 字段说明：一律收敛为 FieldLabel 的悬浮提示（title），不再铺小字注释
 *
 * @module settingsUi
 */

import type { ReactNode } from 'react';

/** Tab 内容区大标题（每个 Tab 顶部唯一标题，层级最高） */
export const tabTitleClass = 'text-base font-semibold text-content-primary';

/** 区块 / 卡片标题（扩展能力卡片、折叠面板标题行） */
export const cardTitleClass = 'text-sm font-medium text-content-primary';

/** 字段标签样式 */
export const labelClass = 'text-xs font-medium text-content-secondary mb-1.5';

/** 输入框通用样式（聚焦散光） */
export const inputClass = 'w-full px-3 py-2 rounded-md bg-surface-card-alt border border-border-light text-content-primary text-sm focus:outline-none focus:border-primary focus:shadow-glow transition-all duration-200';

/** FieldLabel 组件属性 */
interface FieldLabelProps {
  /** 标签文本 */
  label: string;
  /** 悬浮说明（可选）：鼠标悬浮在标签上时以原生 title 提示展示 */
  hint?: string;
}

/**
 * 字段标签：设置表单内所有字段标签统一由本组件渲染。
 *
 * 携带 hint 时显示问号光标（cursor-help），说明文字通过原生 title
 * 在悬浮时出现——替代原先平铺在字段下方的小字注释，减少视觉噪音。
 *
 * @param props - 组件属性
 * @returns 标签 JSX
 */
export function FieldLabel({ label, hint }: FieldLabelProps) {
  return (
    <div
      className={`${labelClass} ${hint ? 'cursor-help' : ''}`}
      title={hint}
    >
      {label}
    </div>
  );
}

/**
 * Tab 内容区统一标题行（标题 + 可选悬浮说明 + 可选右侧操作）。
 *
 * 每个设置 Tab 顶部渲染一次，替代此前各 Tab 自行拼装的标题 + 小字副标题；
 * action（如"创建 Agent"按钮）与标题同行右对齐。
 *
 * @param props.title - 标题文本
 * @param props.hint - 可选悬浮说明（原生 title 提示）
 * @param props.action - 可选右侧操作区（与标题同行）
 */
export function TabHeader({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="mb-4 flex items-center justify-between gap-3">
      <div className={hint ? 'cursor-help w-fit min-w-0' : 'min-w-0'} title={hint}>
        <h4 className={tabTitleClass}>{title}</h4>
      </div>
      {action}
    </div>
  );
}
