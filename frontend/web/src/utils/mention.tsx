/**
 * @fileoverview @ 提及文本高亮工具
 *
 * 识别文本中的 @ 提及 token（文件路径 / 技能名），供两处渲染复用：
 * - PromptInput 的镜像层（输入框内 @token 主题色渲染）
 * - MessageBubble 的 user 消息气泡（发送后的 @token 主题色渲染）
 *
 * 语法与补全检测（PromptInput.detectMentionToken）一致：
 * '@' 须位于行首或空白后（邮箱不触发）；@"..." 引号形式允许空格，
 * 闭合引号后提及结束。
 *
 * @module mention
 */

import type { ReactNode } from 'react';

/**
 * @ 提及 token 匹配正则
 *
 * 捕获组：1 = 前导空白/行首（原样保留）；2 = 完整提及（@ + 路径/技能名）。
 * 引号形式：@"..."（含闭合）或 @"...（未闭合，输入中状态）。
 * 未引号形式：@ 后跟非空白字符序列（裸 @ 也命中，覆盖正在输入的瞬间）。
 */
const MENTION_REGEX = /(^|\s)(@"[^"\n]*"?|@[^\s"]*)/g;

/**
 * 将文本按提及 token 切分为渲染节点：提及片段渲染主题色，其余原样。
 *
 * @param text - 原始文本
 * @param mentionClassName - 提及片段的样式类（默认主题色）
 * @returns 渲染节点数组（key 已设置）
 */
export function highlightMentions(text: string, mentionClassName = 'text-primary'): ReactNode[] {
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
