/**
 * @fileoverview @ 提及与斜杠资源高亮
 *
 * web 前端输入框镜像与转录中的 @ 提及、斜杠资源渲染，与 terminal 端
 * utils/mention.ts 同构：
 * - fileMentionChoices 归并会话与文件候选，会话行排在文件行之前；
 * - SlashCatalogContext 提供当前工作区资源目录，历史消息与实时输入
 *   均基于该目录解析资源；
 * - highlightMentions 对原始未展开文本着色，镜像模式下用显式换行
 *   与空行 NBSP 维持 textarea 高度。
 *
 * @module mention
 */

import { createContext, Fragment, useContext, type ReactNode } from 'react';
import { buildSlashCatalog, resourceRanges, scanPromptTokens, type SlashCatalog } from './slashTokens';
import type { BackendEvent, FileMentionCandidate, TranscriptItem } from '../types/protocol';

/** 会话行排在文件行之前；旧版服务端也可能单独返回技能列表。 */
export function fileMentionChoices(payload: NonNullable<BackendEvent['web_file_mentions']>): FileMentionCandidate[] {
  const files = payload.candidates ?? [];
  return [
    ...(payload.sessions ?? []).map(s => ({ path: s.path, kind: 'session' as const, description: s.description, sessionId: s.sessionId })),
    ...files.filter(c => c.kind === 'session'),
    ...files.filter(c => c.kind === 'file' || c.kind === 'dir'),
  ];
}

// 历史消息与实时输入框均基于当前工作区目录解析资源。
export const SlashCatalogContext = createContext(buildSlashCatalog([], []));

/** 输入框镜像与转录都渲染原始未展开文本。 */
export function highlightMentions(
  text: string, catalog: SlashCatalog,
  { tokens = scanPromptTokens(text), mirror = false, references }: {
    tokens?: ReturnType<typeof scanPromptTokens>; mirror?: boolean; references?: TranscriptItem['slash_references'];
  } = {},
): ReactNode[] {
  const lines: ReactNode[][] = [[]];
  let line = lines[0]!;
  const append = (plain: string) => {
    for (const [i, part] of (mirror ? plain.split('\n') : [plain]).entries()) {
      if (i > 0) { line = []; lines.push(line); }
      if (part) line.push(part);
    }
  };
  let last = 0;
  for (const { start, end, kind } of resourceRanges(text, tokens, catalog, references)) {
    append(text.slice(last, start));
    line.push(<span key={start} className="text-primary" data-mention-kind={kind}>{text.slice(start, end)}</span>);
    last = end;
  }
  append(text.slice(last));
  // 镜像模式下显式换行与空行 NBSP 用于维持 textarea 的高度与换行。
  return mirror ? lines.map((nodes, index) => (
    <Fragment key={index}>{index > 0 && <br />}{nodes.length ? nodes : '\u00a0'}</Fragment>
  )) : line;
}

export function TranscriptMentions({ text, references }: { text: string; references?: TranscriptItem['slash_references'] }) {
  return <>{highlightMentions(text, useContext(SlashCatalogContext), { references })}</>;
}
