/**
 * @fileoverview 消息气泡组件
 *
 * Web 前端的消息显示组件，支持：
 * - 用户消息（右对齐）
 * - 助手回复（左对齐，支持 Markdown 渲染）
 * - 工具调用结果（可展开/折叠）
 * - 待处理工具调用（带脉冲动画）
 * - 流式回复缓冲区
 *
 * @module MessageBubble
 */

import React, { memo, useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkSuperscript from '../remarkSuperscript';
import { highlightMentions } from '../utils/mention';
import rehypeHighlight from 'rehype-highlight';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema, type Options as SanitizeOptions } from 'rehype-sanitize';
import type { PluggableList } from 'unified';
import 'highlight.js/styles/github.css';
import { t, type UiLanguage } from '../i18n';
import { toolDisplayName } from '../utils/toolDisplayName';
import { renderAnsi } from '../utils/ansi';
import { openImagePreview } from '../utils/imagePreview';
import type { TranscriptItem, PendingToolCall } from '../types/protocol';
import { parseToolResultStat } from '../utils/turnGrouping';
import { CheckIcon, CopyIcon, GitForkIcon, RegenerateIcon, RewindIcon } from './icons';

/**
 * HTML 消毒 schema（防 XSS，对齐 opencode 的 DOMPurify 处理）
 *
 * 基于 rehype-sanitize 默认白名单：
 * - 过滤 script/style/iframe 及全部 on* 事件属性（如 <img onerror=...>）
 * - 额外允许 className（rehype-highlight 注入的语言类与 hljs 高亮 span 需要）
 * - img src 额外允许 data: 协议（兼容 base64 内联图片）
 */
const sanitizeSchema: SanitizeOptions = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    '*': [...(defaultSchema.attributes?.['*'] ?? []), 'className'],
  },
  protocols: {
    ...defaultSchema.protocols,
    src: [...(defaultSchema.protocols?.src ?? []), 'data'],
  },
};

/** 所有 markdown 渲染共用的 rehype 插件链：代码高亮 → 原始 HTML → 消毒 */
const rehypePlugins: PluggableList = [rehypeHighlight, rehypeRaw, [rehypeSanitize, sanitizeSchema]];

/** 行内代码内容为纯 URL 时渲染为可点击链接（对齐 opencode markCodeLinks） */
const URL_PATTERN = /^https?:\/\/[^\s<>()`"']+$/;

/** 提取 code 文本中的有效 URL（去除尾部标点），无效返回 undefined */
function codeUrl(text: string): string | undefined {
  const href = text.trim().replace(/[),.;!?]+$/, '');
  if (!URL_PATTERN.test(href)) return undefined;
  try {
    return new URL(href).toString();
  } catch {
    return undefined;
  }
}

/** 从 rehype-highlight 注入的 className 中提取语言名 */
function extractLanguage(props: Record<string, unknown>): string | undefined {
  const className = (props.className as string) || '';
  const match = className.match(/language-(\w+)/);
  return match?.[1];
}

/** 递归提取 React children 中的纯文本 */
function extractText(children: React.ReactNode): string {
  return React.Children.toArray(children)
    .map((c) => {
      if (typeof c === 'string') return c;
      if (typeof c === 'number') return String(c);
      if (React.isValidElement(c) && (c.props as { children?: React.ReactNode }).children) {
        return extractText((c.props as { children: React.ReactNode }).children);
      }
      return '';
    })
    .join('');
}

/** 去除代码块尾部空行，返回处理后的 children */
function trimCodeTrailingLines(children: React.ReactNode): React.ReactNode {
  return React.Children.map(children, (child) => {
    if (!React.isValidElement(child)) return child;
    const el = child as React.ReactElement<{ children?: React.ReactNode }>;
    if (typeof el.type === 'string' && el.type === 'code') {
      const arr = React.Children.toArray(el.props.children);
      while (arr.length > 0) {
        const last = arr[arr.length - 1];
        if (typeof last === 'string' && last.trim() === '') arr.pop();
        else break;
      }
      if (arr.length > 0) {
        const last = arr[arr.length - 1];
        if (typeof last === 'string' && /\n+$/.test(last)) {
          arr[arr.length - 1] = last.replace(/\n+$/, '');
        }
      }
      return React.cloneElement(el, undefined, ...arr);
    }
    return el;
  });
}

/**
 * URL 转换（react-markdown 的 urlTransform）
 *
 * 默认只放行安全协议（http/https/mailto 等），data: 会被过滤成空导致
 * base64 内联图片无法显示；这里额外放行图片 src 的 data: 协议，
 * 其余 URL 沿用默认安全校验（javascript: 等仍被拦截）。
 */
const urlTransform = (url: string, key: string): string => {
  if (key === 'src' && url.startsWith('data:')) return url;
  return defaultUrlTransform(url);
};

/** 复制按钮 — opencode 风格 SVG */
function CodeCopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <button className={`code-copy-btn${copied ? ' copied' : ''}`} onClick={handleCopy} title="复制">
      <span className="copy-icon">
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeLinecap="round">
          <path d="M6.2513 6.24935V2.91602H17.0846V13.7493H13.7513M13.7513 6.24935V17.0827H2.91797V6.24935H13.7513Z" />
        </svg>
      </span>
      <span className="copy-check">
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeLinecap="square">
          <path d="M5 11.9657L8.37838 14.7529L15 5.83398" />
        </svg>
      </span>
    </button>
  );
}

/** 图片文件 URL 判定（含 query/hash 后缀，如 https://x.com/a.png?raw=1） */
const IMAGE_URL_RE = /\.(png|jpe?g|gif|webp|bmp|svg|avif|ico)(\?.*)?(#.*)?$/i;

/**
 * 自定义 markdown 组件
 *
 * - pre：代码块顶栏（语言名 + 复制按钮）
 * - img：点击在应用内打开图片预览（不跳转外部浏览器，避免桌面端被困）
 * - a：图片链接在应用内预览；外部链接新标签打开并强制 rel="noopener noreferrer"
 *   防 tabnabbing（反向劫持），web 端不离开应用、桌面端由 setWindowOpenHandler 接管
 * - code：行内代码内容为纯 URL 时渲染为可点击链接（对齐 opencode markCodeLinks）
 */
const mdComponents = {
  pre: ({ children, ...rest }: React.ComponentPropsWithoutRef<'pre'>) => {
    const codeChild = children as React.ReactElement<{ className?: string; children?: React.ReactNode }> | undefined;
    const lang = extractLanguage((codeChild?.props as Record<string, unknown>) || {}) || 'text';
    const rawText = extractText(codeChild?.props?.children ?? children);
    return (
      <div className="code-block-wrap">
        <div className="code-block-header">
          <span className="code-lang-label">{lang}</span>
          <CodeCopyButton text={rawText} />
        </div>
        <pre {...rest}>{trimCodeTrailingLines(children)}</pre>
      </div>
    );
  },
  img: ({ src, alt, ...rest }: React.ComponentPropsWithoutRef<'img'>) => {
    // 加载失败（如无效 src）时显示美化占位，保留用户对损坏图片的感知
    const [failed, setFailed] = useState(false);
    if (failed) {
      return (
        <span className="inline-flex items-center gap-1.5 text-xs text-content-disabled bg-surface-card-alt border border-dashed border-border-medium rounded-md px-2 py-1 my-1 select-none" title={src}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <circle cx="8.5" cy="8.5" r="1.5" />
            <path d="M21 15l-5-5L5 21" />
          </svg>
          {alt || '图片加载失败'}
        </span>
      );
    }
    return (
      <img
        {...rest}
        src={src}
        alt={alt}
        loading="lazy"
        onClick={() => src && openImagePreview(src)}
        onError={() => setFailed(true)}
        className="cursor-zoom-in max-w-full h-auto rounded"
      />
    );
  },
  a: ({ href, children, ...rest }: React.ComponentPropsWithoutRef<'a'>) => {
    const isExternal = !!href && /^https?:\/\//i.test(href);
    return (
      <a
        {...rest}
        href={href}
        {...(isExternal ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
        onClick={(e) => {
          // 图片链接在应用内预览（桌面端不会被外链拦截器重定向到系统浏览器）
          if (href && IMAGE_URL_RE.test(href)) {
            e.preventDefault();
            openImagePreview(href);
          }
        }}
      >
        {children}
      </a>
    );
  },
  code: ({ children, className, ...rest }: React.ComponentPropsWithoutRef<'code'>) => {
    // 块级代码（rehype-highlight 注入 language-xxx）不处理；
    // 行内代码内容为纯 URL 时渲染为可点击链接
    const isBlock = !!className?.includes('language-');
    if (!isBlock) {
      const url = codeUrl(extractText(children));
      if (url) {
        return (
          <a href={url} target="_blank" rel="noopener noreferrer" className="break-all">
            {children}
          </a>
        );
      }
    }
    return (
      <code className={className} {...rest}>
        {children}
      </code>
    );
  },
};

/**
 * MessageBubble 组件属性接口
 */
interface MessageBubbleProps {
  /** 转录项 */
  item: TranscriptItem;
  /** 工具输入映射（用于显示工具调用参数） */
  toolInputMap?: Map<string, Record<string, unknown>>;
  /** 当前 UI 语言 */
  lang?: UiLanguage;
  /** 撤销回调（每条 user 消息均可触发，点击后弹出模式选择） */
  onRewind?: () => void;
  /** 重新生成回调（仅最终 assistant 消息显示） */
  onRegenerate?: () => void;
  /** 分叉会话回调（最终 assistant 消息显示：保留到该轮分叉出新会话） */
  onFork?: () => void;
  /** 是否隐藏思考过程块（reasoning 由上层统一渲染时使用） */
  hideReasoning?: boolean;
  /** 是否显示操作按钮（复制/撤销） */
  showActions?: boolean;
  /** 禁用操作按钮（busy 时禁用撤销/重新生成，复制不受影响） */
  actionsDisabled?: boolean;
  /** 助手气泡底部附加区块（渲染于正文之后、操作按钮之前，如单轮变更条；
   * 元素引用需由调用方 memo 保证，避免破坏本组件 memo） */
  footer?: React.ReactNode;
}

/**
 * 消息操作按钮组 —— 复制 + 撤销 + 重新生成（hover 时显示）
 *
 * memo 化：text/onRewind/onRegenerate 引用稳定时跳过重渲染
 * （流式 token 更新不会让历史消息的复制/撤销按钮重建）。
 *
 * @param props.text - 待复制文本
 * @param props.lang - UI 语言
 * @param props.onRewind - 撤销回调（可选，user 消息显示）
 * @param props.onRegenerate - 重新生成回调（可选，assistant 消息显示）
 */
const MessageActions = memo(function MessageActions({ text, lang, onRewind, onRegenerate, onFork, disabled }: { text: string; lang: UiLanguage; onRewind?: () => void; onRegenerate?: () => void; onFork?: () => void; disabled?: boolean }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };
  const dis = disabled ? 'opacity-30 pointer-events-none' : 'cursor-pointer';
  return (
    <div className="flex items-center gap-0.5 mt-1 mr-1 opacity-0 group-hover:opacity-100 transition-opacity duration-150">
      <button
        onClick={handleCopy}
        onMouseDown={(e) => e.preventDefault()}
        className="w-6 h-6 flex items-center justify-center rounded text-content-disabled hover:text-content-primary hover:bg-black/5 transition-colors cursor-pointer"
        title={copied ? t(lang, 'copied') : t(lang, 'copy')}
      >
        {copied ? (
          <CheckIcon className="w-[13px] h-[13px]" />
        ) : (
          <CopyIcon className="w-[13px] h-[13px]" />
        )}
      </button>
      {onRewind && (
        <button
          onClick={onRewind}
          onMouseDown={(e) => e.preventDefault()}
          className={`w-6 h-6 flex items-center justify-center rounded text-content-disabled hover:text-content-primary hover:bg-black/5 transition-colors ${dis}`}
          title={t(lang, 'rewind')}
        >
          <RewindIcon className="w-[13px] h-[13px]" />
        </button>
      )}
      {onFork && (
        <button
          onClick={onFork}
          onMouseDown={(e) => e.preventDefault()}
          className={`w-6 h-6 flex items-center justify-center rounded text-content-disabled hover:text-content-primary hover:bg-black/5 transition-colors ${dis}`}
          title={t(lang, 'fork_session')}
        >
          <GitForkIcon className="w-[13px] h-[13px]" />
        </button>
      )}
      {onRegenerate && (
        <button
          onClick={onRegenerate}
          onMouseDown={(e) => e.preventDefault()}
          className={`w-6 h-6 flex items-center justify-center rounded text-content-disabled hover:text-content-primary hover:bg-black/5 transition-colors ${dis}`}
          title={t(lang, 'regenerate')}
        >
          <RegenerateIcon className="w-[13px] h-[13px]" />
        </button>
      )}
    </div>
  );
});

/**
 * 消息气泡组件
 *
 * 根据消息角色类型渲染不同的消息样式。
 *
 * memo 化：流式输出期间 staticItems 中未变化的 item 引用保持稳定，
 * 已渲染过的历史消息不会因每次 token 更新而重新解析 markdown。
 *
 * @param props - 组件属性
 * @returns 返回消息气泡的 JSX 元素
 */
function MessageBubble({ item, toolInputMap, lang = 'zh-CN', onRewind, onRegenerate, onFork, hideReasoning, showActions = true, actionsDisabled, footer }: MessageBubbleProps) {
  if (item.role === 'user') {
    return (
      <div className="flex justify-end py-1.5 group">
        <div className="flex flex-col items-end max-w-[min(82%,64ch)]">
          {/* overflow-wrap:anywhere（而非 break-words）：断点计入 min-content，
              长文件名等无空格长串在容器边界强制折行、不撑破最大宽度；
              break-word 的断点不参与 min-content 计算，flex 下容器会被
              最长串撑到超宽后才换行 */}
          <div className="bg-surface-card-alt border border-border-light rounded-lg px-3 py-2 text-sm text-content-primary whitespace-pre-wrap [overflow-wrap:anywhere] select-text">
            {highlightMentions(item.text)}
          </div>
          {showActions && <MessageActions text={item.text} lang={lang} onRewind={onRewind} disabled={actionsDisabled} />}
        </div>
      </div>
    );
  }

  if (item.role === 'assistant') {
    const reasoning = !hideReasoning && item.reasoning ? <ThinkingBlock text={item.reasoning} lang={lang} /> : null;
    return (
      <div className="py-1.5 group">
        {reasoning}
        <div className="text-content-primary text-sm prose max-w-full select-text">
          <ReactMarkdown remarkPlugins={[remarkGfm, remarkSuperscript]} rehypePlugins={rehypePlugins} urlTransform={urlTransform} components={mdComponents}>
            {item.text}
          </ReactMarkdown>
        </div>
        {footer}
        {showActions && <MessageActions text={item.text} lang={lang} onRegenerate={onRegenerate} onFork={onFork} disabled={actionsDisabled} />}
      </div>
    );
  }

  if (item.role === 'tool_result') {
    const toolInput = (item.tool_use_id && toolInputMap?.get(item.tool_use_id)) || item.tool_input;
    return <ToolResultBubble name={item.tool_name || 'tool'} text={item.text} isError={item.is_error} toolInput={toolInput} structuredOutput={item.structured_output} />;
  }

  if (item.role === 'tool') {
    return null;
  }

  // plan 角色由 ModalCard 专门展示，不在对话流中重复显示
  if (item.role === 'plan') {
    return null;
  }

  return (
    <div className="py-1.5 text-xs text-content-disabled italic">
      {item.text}
    </div>
  );
}

/**
 * memo 化导出：item 引用稳定时（流式期间历史消息）跳过重渲染，
 * 避免每次 token 更新都重新执行 ReactMarkdown 解析与代码高亮。
 */
export default memo(MessageBubble);

/**
 * 判断文本是否为统一 diff（unified diff）格式
 *
 * 仅当出现 diff 结构标记（@@ 块头、--- 与 +++ 文件头成对）时才判定为 diff，
 * 避免把普通文本（如无序列表、bash 输出）误染成红/绿色。
 */
function isUnifiedDiffText(text: string): boolean {
  const lines = text.split(/\r?\n/);
  let hasFrom = false;
  let hasTo = false;
  for (const line of lines) {
    const trimmed = line.trimStart();
    if (/^@@\s+-\d+/.test(trimmed)) return true;
    if (trimmed.startsWith('--- ')) hasFrom = true;
    else if (trimmed.startsWith('+++ ')) hasTo = true;
    if (hasFrom && hasTo) return true;
  }
  return false;
}

/**
 * 统一 diff 文本渲染组件
 *
 * 按行渲染并为 diff 行着色（与 terminal 端行为一致）：
 * - `+` 新增：绿色
 * - `-` 删除：红色
 * - `@@` 块头：蓝色
 * 上下文行、`---`/`+++` 文件头保持默认色。
 */
function DiffLines({ text }: { text: string }) {
  return (
    <>
      {text.split(/\r?\n/).map((line, i) => {
        // unified diff 的格式标记是行首第一个字符：+ 新增 / - 删除 / 空格 上下文。
        // 不能 trimStart 后再判断，否则上下文行内容本身以 - / + 开头时
        // （如无序列表 "- item"）会被误染成红/绿色
        let color = '';
        if (line.startsWith('+') && !line.startsWith('+++')) {
          color = 'text-diff-add';
        } else if (line.startsWith('-') && !line.startsWith('---')) {
          color = 'text-diff-del';
        } else if (line.startsWith('@@')) {
          color = 'text-diff-hunk';
        }
        return (
          <div key={i} className={`whitespace-pre-wrap [overflow-wrap:anywhere] ${color}`}>
            {line || '\u00A0'}
          </div>
        );
      })}
    </>
  );
}

/**
 * 工具结果气泡组件
 *
 * 显示工具执行结果，支持展开/折叠查看详情。
 *
 * 完成态默认折叠（标题行 + 摘要）；展开后先显示执行期间保留的流式进度
 * （agent 子任务的思考过程——仅供人查看，不进入 LLM 上下文），再显示
 * 工具结果正文。流式阶段由 PendingToolBubble 展示同一份进度，完成后无缝衔接。
 *
 * 结果正文对 edit 等统一 diff 文本按新增/删除行着色；含 ANSI 转义码时
 * 仍走 renderAnsi，避免冲突。
 *
 * memo 化：历史工具结果的 text/name/toolInput 引用稳定时跳过重渲染。
 *
 * @param props - 组件属性
 * @param props.name - 工具名称
 * @param props.text - 结果文本
 * @param props.isError - 是否为错误结果
 * @param props.toolInput - 工具输入参数
 */
const ToolResultBubble = memo(function ToolResultBubble({ name, text, isError, toolInput, structuredOutput }: { name: string; text: string; isError?: boolean; toolInput?: Record<string, unknown>; structuredOutput?: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  // summarizeInput 用原名做大小写不敏感匹配，显示名用映射后的友好名
  const summary = summarizeInput(name, toolInput, name);
  // agent 工具根据 subagent_type 动态显示类型名，其他工具使用映射表
  const displayName = name === 'agent' && toolInput ? getAgentDisplayName(toolInput) : toolDisplayName(name);
  // 任务完成后直接用最终结果替换：流式阶段已累积展示思考过程，
  // 完成后仅以最终结果（text）作为正文，不再保留/判断思考过程
  const hasContent = !!text;
  // 统一 diff 且无 ANSI 转义码时按行着色渲染；错误结果与 terminal 一致整行走错误色，
  // 不启用 diff 着色（text 可能为空，需先守卫避免崩溃）
  const isDiff = !!text && !isError && !text.includes('\x1b[') && isUnifiedDiffText(text);
  // 变更工具（edit_file/write_file）的增删行数：优先 structured_output
  // （直播，工具 metadata 精确值），回退结果文本解析（恢复路径），
  // 两条路径口径与单轮变更条一致
  const diffStat = !isError && (name === 'edit_file' || name === 'write_file')
    ? parseToolResultStat(text ?? '', structuredOutput)
    : null;

  return (
    <div data-tool-row className="py-1.5">
      <button
        onClick={() => hasContent && setOpen(!open)}
        className={`flex items-start text-base transition-colors cursor-pointer text-left ${hasContent ? 'text-content-secondary hover:text-content-primary' : ''}`}
      >
        {/* 圆点右移 3px 使其对称轴（7px）与大脑图标重合；文本缩进不变
            （3px + 8px + 9px = 14px + 6px = 20px）；mt-2 垂直居中 */}
        <span className={`inline-block w-2 h-2 rounded-full shrink-0 mt-2 ml-[3px] mr-[9px] ${isError ? 'bg-danger' : 'bg-primary'}`} />
        <span className="flex-1 min-w-0 break-all">
          <span className={isError ? 'text-danger' : 'text-content-primary'}>{displayName}</span>
          {/* 预览行在展开/折叠时均保留，展开后与结果正文并存；字号介于工具名与正文之间 */}
          {summary && <span className={`text-sm ${isError ? 'text-danger' : 'text-content-disabled'}`}>（{summary}）</span>}
          {isError && <span className="text-xs text-danger font-medium"> ERROR</span>}
          {/* 该次编辑的增删行数（着色加粗数字，无分隔符；无法统计时不显示） */}
          {diffStat && (diffStat.insertions > 0 || diffStat.deletions > 0) && (
            <span className="ml-1.5 font-mono text-xs font-bold whitespace-nowrap">
              {diffStat.insertions > 0 && <span className="text-diff-add">+{diffStat.insertions}</span>}
              {diffStat.insertions > 0 && diffStat.deletions > 0 && <span> </span>}
              {diffStat.deletions > 0 && <span className="text-diff-del">-{diffStat.deletions}</span>}
            </span>
          )}
        </span>
      </button>
      {open && hasContent && (
        <div className={`mt-1 ml-3.5 p-2.5 font-mono text-xs leading-relaxed max-h-96 overflow-y-auto scrollbar-hidden rounded-lg select-text ${isError ? 'text-danger bg-danger/5 border border-danger/20' : 'text-content-primary bg-surface-card-alt border border-border-light'}`}>
          {text && (
            isDiff ? <DiffLines text={text} /> : <div className="whitespace-pre-wrap [overflow-wrap:anywhere]">{renderAnsi(text)}</div>
          )}
        </div>
      )}
    </div>
  );
});

/**
 * 流式进度消息渲染（供 PendingToolBubble 使用）
 *
 * thinking/text 为增量流式片段（token 级累积），
 * tool/status 为完整消息，加 ▸ 前缀。
 */
function ProgressMessages({ messages }: { messages: Array<{message: string; type?: string}> }) {
  return (
    <>
      {messages.map((msg, i) => (
        <div key={i} className="py-px">
          {msg.message.split('\n').map((line, li) => (
            <div key={li}>
              {li === 0 && msg.type !== 'thinking' && msg.type !== 'text' && (
                <span className="text-primary/70 mr-1">▸</span>
              )}
              {line || '\u00A0'}
            </div>
          ))}
        </div>
      ))}
    </>    
  );
}

/**
 * 右键内容区域快速折叠的公共逻辑（对齐思考过程的右键折叠）
 *
 * @param onCollapse - 折叠回调
 * @param skipSelector - 额外跳过的选择器（命中则交给内层处理，不折叠）
 */
export function useContentCollapse(onCollapse: () => void, skipSelector?: string) {
  const handleContextMenu = (e: React.MouseEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    // 交互元素保留原生右键菜单：复制链接地址、代码块复制按钮等
    if (target.closest('a, button, input, textarea')) return;
    // 内层独立折叠区（思考过程块、工具行）交给各自处理
    if (skipSelector && target.closest(skipSelector)) return;
    // 正在选中文本（复制场景）：保留原生右键菜单的"复制"项，不折叠
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed) return;
    // 阻止浏览器原生右键菜单，右键即快速折叠
    e.preventDefault();
    onCollapse();
  };

  return { handleContextMenu };
}

/**
 * 思考过程块组件（独立折叠单元）
 *
 * 显示助手的思考/推理过程，支持折叠/展开。每个思考过程块独立折叠，
 *
 * 自动折叠：`autoCollapsed` 信号变化（如 text 推入）时自动折叠/展开，
 * 但用户手动点击过的块不再被自动信号覆盖，尊重用户选择。
 *
 * 右键内容区域本身也可折叠（无需翻回顶部标题处），左键保留文本选中/复制
 * 等自然交互，不打断链接点击、代码块复制按钮等。
 *
 * @param props - 组件属性
 * @param props.text - 思考过程文本
 * @param props.lang - UI 语言
 * @param props.defaultOpen - 初始展开状态（默认折叠）
 * @param props.autoCollapsed - 自动折叠信号：true 折叠、false 展开，仅对用户未手动操作过的块生效
 * @param props.streaming - 是否正在流式输出（大脑图标切换为与工具行圆点一致的脉冲动画，展开内容底部显示流式光标）
 */
export const ThinkingBlock = memo(function ThinkingBlock({
  text,
  lang,
  defaultOpen = false,
  autoCollapsed,
  streaming,
}: {
  text: string;
  lang: UiLanguage;
  defaultOpen?: boolean;
  autoCollapsed?: boolean;
  streaming?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  // 用户是否手动操作过（展开/折叠）此块：手动操作后自动折叠信号不再覆盖
  const interactedRef = useRef(false);
  // React 官方 "adjusting state during render" 模式：prev 值用 state 存储，
  // 避免并发渲染（Suspense/transition 中断）下 ref 先写而 setState 未提交
  const [prevAutoCollapsed, setPrevAutoCollapsed] = useState(autoCollapsed);

  // autoCollapsed 信号变化时同步状态；用户手动操作过的块保持用户选择
  if (autoCollapsed !== prevAutoCollapsed) {
    setPrevAutoCollapsed(autoCollapsed);
    if (!interactedRef.current) setOpen(!autoCollapsed);
  }

  if (!text?.trim()) return null;

  const handleToggle = () => {
    interactedRef.current = true;
    setOpen(!open);
  };

  // 右键内容区域快速折叠
  const { handleContextMenu: handleContentContextMenu } = useContentCollapse(() => {
    interactedRef.current = true;
    setOpen(false);
  });

  return (
    <div data-thinking-block className="mb-1.5">
      <button
        onClick={handleToggle}
        className="flex items-center gap-1.5 text-base text-content-primary leading-[1.8] transition-colors py-1.5 cursor-pointer"
      >
        {/* 大脑图标：思考过程标识（行高与中间 text 的 prose 1.8 对齐；流式时与工具行圆点一致的脉冲动画） */}
        <svg className={`w-3.5 h-3.5 shrink-0 text-primary ${streaming ? 'animate-pulse-scale' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z" />
          <path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z" />
          <path d="M9 8h.01M15 8h.01M9 12h.01M15 12h.01" />
        </svg>
        <span>{t(lang, 'thinking_process')}</span>
        <svg
          className={`w-3 h-3 transition-transform duration-150 ${open ? 'rotate-90' : ''}`}
          viewBox="0 0 12 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M4.5 2.5L8 6L4.5 9.5" />
        </svg>
      </button>
      {/* 展开/折叠微动画（简洁 fade：纯透明度 150ms） */}
      {open && (
        <div className="animate-fade">
          <div onContextMenu={handleContentContextMenu} className="relative">
            <div className="text-sm text-content-secondary leading-relaxed select-text mt-1.5 opacity-80 py-1">
              <div className="prose prose-sm max-w-full [overflow-wrap:anywhere]">
                <ReactMarkdown remarkPlugins={[remarkGfm, remarkSuperscript]} rehypePlugins={rehypePlugins} urlTransform={urlTransform} components={mdComponents}>
                  {text}
                </ReactMarkdown>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
});

/**
 * 待处理工具调用气泡组件
 *
 * 显示正在执行的工具调用，带有脉冲动画效果。
 *
 * 默认折叠只显示工具名与摘要（对齐 opencode：工具默认折叠为标题行）；
 * 用户可点击标题行展开查看全部进度消息（web 端不受 terminal 行数限制）。
 * 工具完成（completed）后由 ToolResultBubble（同样默认折叠）替代。
 *
 * memo 化：call 引用仅在进度更新时变化（流式进度消息累积），
 * 无更新的工具调用不会因其他消息的 token 刷新而重渲染。
 *
 * @param props - 组件属性
 * @param props.call - 待处理的工具调用信息
 */
export const PendingToolBubble = memo(function PendingToolBubble({ call }: { call: PendingToolCall }) {
  // 工具执行中默认展开（可实时查看执行过程；仅 agent 工具会上报进度消息，
  // 普通工具无进度时展开态只显示标题行）；完成后由 ToolResultBubble 折叠展示
  const [open, setOpen] = useState(true);
  // 与 terminal 端 BlinkingToolIndicator 对齐：tool_input 未到达时 summary 为空，
  // 只显示工具名；到达后始终在同一行显示命令摘要，不随进度区折叠而隐藏
  const summary = call.tool_input ? summarizeInput(call.tool_name, call.tool_input) : '';
  // agent 工具根据 subagent_type 动态显示类型名，其他工具使用映射表
  const displayName = call.tool_name === 'agent' && call.tool_input
    ? getAgentDisplayName(call.tool_input as Record<string, unknown>)
    : toolDisplayName(call.tool_name);
  const progressMessages = call.progressMessages ?? [];
  // 内容累积时的自动跟随：用户未上滑过内部容器时无条件跟随（大段进度增量
  // 也能跟上）；上滑过则仅滚回底部附近时恢复。程序滚动（auto-scroll 赋值）
  // 派生的 scroll 事件用 programmaticScrollRef 忽略。
  const progressRef = useRef<HTMLDivElement>(null);
  const programmaticScrollRef = useRef(false);
  const userScrolledRef = useRef(false); // 用户上滑过容器内部 → 暂停跟随
  useEffect(() => {
    const el = progressRef.current;
    if (!el) return;
    if (userScrolledRef.current) {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      if (distance > 24) return; // 用户上滑中，不打扰
      userScrolledRef.current = false; // 滚回底部附近，恢复跟随
    }
    const prevTop = el.scrollTop;
    el.scrollTop = el.scrollHeight;
    if (el.scrollTop !== prevTop) programmaticScrollRef.current = true;
  }, [progressMessages]);
  const handleProgressScroll = useCallback(() => {
    if (programmaticScrollRef.current) {
      programmaticScrollRef.current = false;
      return; // 程序滚动触发的事件，忽略
    }
    const el = progressRef.current;
    if (!el) return;
    const max = el.scrollHeight - el.clientHeight;
    userScrolledRef.current = el.scrollTop < max - 24; // 滚到接近底部视为"跟随模式"
  }, []);
  return (
    <div data-tool-row className="py-1.5">
      <button
        onClick={() => progressMessages.length > 0 && setOpen(!open)}
        className={`flex items-start text-base transition-colors cursor-pointer text-left ${progressMessages.length > 0 ? 'text-content-secondary hover:text-content-primary' : ''}`}
      >
        {/* 圆点右移 3px 使其对称轴（7px）与大脑图标重合；文本缩进不变；mt-2 垂直居中 */}
        <span className="inline-block w-2 h-2 rounded-full bg-primary animate-pulse-scale shrink-0 mt-2 ml-[3px] mr-[9px]" />
        <span className="flex-1 min-w-0">
          <span className="text-content-primary">{displayName}</span>
          {summary && <span className="text-sm text-content-disabled">（{summary}）</span>}
        </span>
      </button>
      {open && progressMessages.length > 0 && (
        <div
          ref={progressRef}
          onScroll={handleProgressScroll}
          className="mt-1 ml-3.5 p-2.5 whitespace-pre-wrap font-mono text-xs leading-relaxed max-h-96 overflow-y-auto scrollbar-hidden rounded-lg select-text text-content-secondary bg-surface-card-alt border border-border-light"
        >
          <ProgressMessages messages={progressMessages} />
        </div>
      )}
    </div>
  );
});

// ---- Agent 工具显示名辅助函数 ----

/**
 * 根据 tool_input 中的 subagent_type 获取 agent 工具的显示名
 * input 完全未到达时返回 "Agent"，到达后无 subagent_type 返回 "GeneralPurpose"
 */
function getAgentDisplayName(toolInput?: Record<string, unknown>): string {
	// input 完全未到达时显示 "Agent"；到达后无 subagent_type 则默认 "GeneralPurpose"
	if (!toolInput || Object.keys(toolInput).length === 0) {
		return 'Agent';
	}
	const agentType = toolInput.subagent_type ?? 'general-purpose';
	// 转 PascalCase：general-purpose → GeneralPurpose, explore → Explore
	return String(agentType)
		.replace(/_/g, '-')
		.split('-')
		.map(w => w.charAt(0).toUpperCase() + w.slice(1))
		.join('');
}



// ---- 摘要生成（与 terminal 端 summarizeInput 保持一致）----

const MAX_COMMAND_LINES = 2;
const MAX_COMMAND_CHARS = 160;

function summarizeInput(toolName: string, toolInput?: Record<string, unknown>, fallback?: string): string {
  if (!toolInput) return truncateCommand(fallback ?? '');
  const lower = toolName.toLowerCase();

  if ((lower === 'bash' || lower === 'powershell') && toolInput.command) {
    return truncateCommand(String(toolInput.command));
  }
  if ((lower === 'read' || lower === 'fileread' || lower === 'read_file') && (toolInput.path || toolInput.file_path)) {
    return String(toolInput.path ?? toolInput.file_path);
  }
  if ((lower === 'write' || lower === 'filewrite' || lower === 'write_file') && (toolInput.path || toolInput.file_path)) {
    return String(toolInput.path ?? toolInput.file_path);
  }
  if ((lower === 'edit' || lower === 'fileedit' || lower === 'edit_file') && (toolInput.path || toolInput.file_path)) {
    return String(toolInput.path ?? toolInput.file_path);
  }
  if (lower === 'grep' && toolInput.pattern) {
    return `/${String(toolInput.pattern)}/`;
  }
  if (lower === 'glob' && toolInput.pattern) {
    return String(toolInput.pattern);
  }
  if (lower === 'agent' && toolInput.description) {
    return truncateCommand(String(toolInput.description));
  }
  if (lower === 'todowrite' || lower === 'todo_write') {
    const todos = toolInput.todos;
    if (Array.isArray(todos)) {
      const total = todos.length;
      const completed = todos.filter((t: { status: string }) => t.status === 'completed').length;
      return `${completed}/${total} tasks`;
    }
  }
  if (lower === 'ask_user_question') {
    const questions = toolInput.questions;
    if (Array.isArray(questions) && questions.length > 0) {
      const q = questions[0] as Record<string, unknown>;
      return truncateCommand(String(q.question ?? ''));
    }
  }

  const entries = Object.entries(toolInput);
  if (entries.length > 0) {
    const first = entries[0];
    if (first) return truncateCommand(`${first[0]}=${String(first[1])}`);
  }
  return truncateCommand(fallback ?? '');
}

function truncateCommand(str: string): string {
  const lines = str.split('\n');
  const cleanedLines = lines.map(l => l.trim()).filter(l => l.length > 0);
  const truncatedLines = cleanedLines.length > MAX_COMMAND_LINES
    ? [...cleanedLines.slice(0, MAX_COMMAND_LINES)]
    : cleanedLines;
  let result = truncatedLines.join(' ');
  const needsCharTruncation = result.length > MAX_COMMAND_CHARS || cleanedLines.length > MAX_COMMAND_LINES;
  if (needsCharTruncation && result.length > MAX_COMMAND_CHARS) {
    result = result.slice(0, MAX_COMMAND_CHARS);
    const lastSemicolon = result.lastIndexOf(';');
    if (lastSemicolon > MAX_COMMAND_CHARS * 0.3) {
      result = result.slice(0, lastSemicolon + 1);
    } else {
      const lastSpace = result.lastIndexOf(' ');
      if (lastSpace > MAX_COMMAND_CHARS * 0.5) {
        result = result.slice(0, lastSpace);
      }
    }
  }
  if (needsCharTruncation) {
    result += '…';
  }
  return result;
}

/**
 * 流式缓冲区组件
 *
 * 显示正在流式接收的助手回复，包括思考过程和正文。
 *
 * 自动折叠模型（对齐 opencode 的 part 级独立折叠）：
 * - 思考过程流式时默认展开，用户可随时折叠/展开
 * - text 推入时自动折叠其上方思考过程（text 保留可见），
 *   用户手动展开过的思考过程不被自动折叠覆盖
 *
 * @param props - 组件属性
 * @param props.text - 正文文本
 * @param props.reasoning - 思考过程文本（可选）
 * @param props.lang - UI 语言
 */
export function StreamingBuffer({ text, reasoning, reasoningStreaming, lang }: { text: string; reasoning?: string; reasoningStreaming?: boolean; lang: UiLanguage }) {
  const hasReasoning = !!reasoning && !!reasoning.trim();
  const hasText = !!text && !!text.trim();

  return (
    <div className="py-1.5">
      {hasReasoning && (
        <ThinkingBlock
          text={reasoning}
          lang={lang}
          streaming={reasoningStreaming ?? false}
          defaultOpen={!hasText}
          autoCollapsed={hasText}
        />
      )}
      {hasText && (
        <div className="text-content-primary text-sm prose max-w-full select-text [overflow-wrap:anywhere]">
          <ReactMarkdown remarkPlugins={[remarkGfm, remarkSuperscript]} rehypePlugins={rehypePlugins} urlTransform={urlTransform} components={mdComponents}>
            {text}
          </ReactMarkdown>
        </div>
      )}
    </div>
  );
}
