/**
 * @fileoverview Toast 卡片内的紧凑 Markdown 渲染
 *
 * 任务结果预览可能包含标题/列表/表格/行内代码等 Markdown 结构，
 * 直接以 <pre> 纯文本展示可读性差。这里复用 react-markdown +
 * remark-gfm（表格/任务列表）+ rehype-sanitize 消毒（防 XSS）。
 *
 * 有意**不**引入 rehype-highlight / rehype-raw：
 *   - toast 正文以阅读摘要为主，代码高亮收益低、解析开销大；
 *   - 原始 HTML 不放行，LMM 输出中的内联标签一律按纯文本呈现，更安全。
 * 样式经全局 .prose 类与 .toast-md 紧凑化覆盖实现（见 index.css）。
 */

import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';

/** 允许 className 透传（对齐应用的消毒策略），其余沿用默认白名单 */
const sanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    '*': [...(defaultSchema.attributes?.['*'] ?? []), 'className'],
  },
};

/**
 * 渲染 toast 正文（紧凑 Markdown）
 *
 * @param props.text - Markdown 文本（后端本地化的任务结果预览）
 */
function ToastMarkdownImpl({ text }: { text: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[[rehypeSanitize, sanitizeSchema]]}>
      {text}
    </ReactMarkdown>
  );
}

/** 内容不变时不重复解析（同一载荷触发的重渲染直接复用节点） */
export const ToastMarkdown = memo(ToastMarkdownImpl);
