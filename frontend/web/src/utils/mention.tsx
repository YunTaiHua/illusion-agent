/**
 * @fileoverview @ 提及文本高亮工具
 *
 * 识别文本中的 @ 提及 token（会话引用 / 文件路径 / 技能名），在输入框
 * 镜像层与用户气泡中以主题色渲染原始文本（无图标、无背景等美化）。
 *
 * 会话提及为规范 Markdown 链接形式 `@[label](illusion-session:<id>)`：
 * 输入框插入、提交、持久化与重放转录全程保持该格式一致（引擎在提交
 * 边界解析并注入只读快照）。纯文本中的 @xxx（无 URI）不是会话引用。
 *
 * 语法与补全检测（PromptInput.detectMentionToken）一致：'@' 须位于行首
 * 或空白后；@"..." 引号形式允许空格。
 *
 * @module mention
 */

import type { ReactNode } from 'react';

// ---------------------------------------------------------------------------
// token 文法（与后端 session_reference.MENTION_PATTERN、补全检测逐字对齐，
// 修改需同步）
// ---------------------------------------------------------------------------

/** 会话引用规范形式（持久化/提交线格式；id 量词与后端 _SESSION_ID_RE 一致） */
const SESSION_CANONICAL_SRC = '@\\[(?:\\\\.|[^\\\\\\]\n])*\\]\\(illusion-session:[0-9a-fA-F]{6,64}\\)';
/** 引号形式（路径/标题含空格） */
const QUOTED_SRC = '@"[^"\\n]*"';
/** 未引号形式（路径/技能名；排除 [ ] ( ) 避免吞并规范形式） */
const PLAIN_SRC = '@[^\\s"\\[\\]()]+';

/** @ 提及 token 总匹配（规范形式最前；裸 @ 也命中覆盖输入中的瞬间） */
const MENTION_REGEX = new RegExp(
  `(^|\\s)(${SESSION_CANONICAL_SRC}|${QUOTED_SRC}|${PLAIN_SRC})`,
  'g',
);

/**
 * 将文本按提及 token 切分为渲染节点：提及片段渲染主题色，其余原样。
 *
 * @param text - 原始文本
 * @param mentionClassName - 提及片段的样式类（默认主题色）
 * @returns 渲染节点数组（key 已设置）
 */
export function highlightMentions(
  text: string,
  mentionClassName = 'text-primary',
): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const match of text.matchAll(MENTION_REGEX)) {
    const lead = match[1] ?? '';
    const mention = match[2] ?? '';
    const start = (match.index ?? 0) + lead.length;
    if (start > last) nodes.push(text.slice(last, start));
    nodes.push(
      <span key={key++} className={mentionClassName}>
        {mention}
      </span>,
    );
    last = start + mention.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}
