/**
 * @fileoverview 转录项轮次分组工具（纯函数 + 结构化共享缓存 hooks）
 *
 * 流式阶段 staticItems 每次更新都是不可变追加（[...items, item]），
 * 旧 item 引用稳定。轮次分组结果若每次全量重建，turns 内每轮数组引用
 * 都会变化 → memo(TurnView) 失效 → 历史轮全部重渲染（含 ReactMarkdown
 * 重解析）。本模块通过"增量归组 + 复用已有轮引用"实现结构化共享：
 * 追加时仅最后一轮引用更新（或新增一轮），其余轮引用保持不变。
 *
 * 核心纯函数（buildTurns / appendTurns / isAppendOf / pushItemToTurn /
 * buildToolInputMap）不依赖 React，与 hooks 分离便于逻辑独立演进。
 */

import { useRef } from 'react';
import type { TranscriptItem } from '../types/protocol.ts';

/**
 * 判断 next 是否为 prev 的纯追加（前缀项引用逐一相同）
 *
 * 流式阶段 staticItems 每次更新都是不可变追加（[...items, item]），
 * 旧 item 引用不变；replace/rewind/清空等场景则整体替换，无法增量。
 *
 * @param prev - 上一次的转录项列表
 * @param next - 本次的转录项列表
 * @returns next 是否为 prev 的纯追加
 */
export function isAppendOf(prev: TranscriptItem[], next: TranscriptItem[]): boolean {
  if (prev.length >= next.length) return false;
  for (let i = 0; i < prev.length; i++) {
    if (prev[i] !== next[i]) return false;
  }
  return true;
}

/**
 * 按分组规则将单个 item 归入轮次列表（与全量构建逻辑保持一致）：
 * user 消息或当前无轮时新开一轮，否则追加到最后一轮
 *
 * @param turns - 轮次列表（原地修改）
 * @param item - 待归入的转录项
 */
export function pushItemToTurn(turns: TranscriptItem[][], item: TranscriptItem): void {
  const lastTurn = turns[turns.length - 1];
  if (item.role === 'user' || !lastTurn) {
    turns.push([item]);
  } else {
    turns[turns.length - 1] = [...lastTurn, item];
  }
}

/**
 * 全量构建轮次分组（基准实现，增量结果须与之等价）
 *
 * @param items - 转录项列表
 * @returns 轮次分组（每轮以 user 消息开头）
 */
export function buildTurns(items: TranscriptItem[]): TranscriptItem[][] {
  const turns: TranscriptItem[][] = [];
  for (const item of items) {
    pushItemToTurn(turns, item);
  }
  return turns;
}

/**
 * 增量追加轮次：在缓存基础上按 delta 范围逐个归组
 *
 * 逐个归组保证与全量构建严格等价（支持单次渲染合并的多条追加，
 * 如 tool + tool_result 同批到达）；已有轮的数组引用全部复用，
 * 仅最后一轮重建或新增一轮。
 *
 * @param cached - 上一次的缓存（items 与 turns）
 * @param next - 本次的转录项列表（须满足 isAppendOf(cached.items, next)）
 * @returns 新的缓存（复用已有轮引用）
 */
export function appendTurns(
  cached: { items: TranscriptItem[]; turns: TranscriptItem[][] },
  next: TranscriptItem[],
): { items: TranscriptItem[]; turns: TranscriptItem[][] } {
  const turns = [...cached.turns];
  for (let i = cached.items.length; i < next.length; i++) {
    pushItemToTurn(turns, next[i]!);
  }
  return { items: next, turns };
}

/**
 * 从 staticItems 中提取 tool_use_id → tool_input 映射
 *
 * @param items - 转录项列表
 * @returns tool_use_id 到 tool_input 的映射
 */
export function buildToolInputMap(items: TranscriptItem[]): Map<string, Record<string, unknown>> {
  const map = new Map<string, Record<string, unknown>>();
  for (const item of items) {
    if (item.role === 'tool' && item.tool_use_id && item.tool_input) {
      map.set(item.tool_use_id, item.tool_input);
    }
  }
  return map;
}

/** 变更类工具名单（单轮变更条追踪范围；bash/powershell 不追踪） */
const CHANGE_TOOLS = new Set(['edit_file', 'write_file']);

/**
 * 提取单轮内被变更工具成功修改的文件路径列表
 *
 * 扫描该轮转录项：role='tool' 且 tool_name 属于变更类工具的条目取其
 * file_path/path 输入；对应 tool_result 标记 is_error=true 的调用剔除。
 * 按原始输入串去重保序（后端按原始串解析并回填绝对路径与统计）。
 * 仅在轮次完成后调用（流式期间 tool_result 未到齐，失败判定不完整）。
 *
 * @param turn - 单轮转录项列表（useStableTurns 切分的一轮）
 * @returns 原始路径串列表（可能为空）
 */
export function buildTurnChangedFiles(turn: TranscriptItem[]): string[] {
  const failedUseIds = new Set<string>();
  for (const item of turn) {
    if (item.role === 'tool_result' && item.is_error && item.tool_use_id) {
      failedUseIds.add(item.tool_use_id);
    }
  }
  const seen = new Set<string>();
  const files: string[] = [];
  for (const item of turn) {
    if (item.role !== 'tool' || !item.tool_name || !CHANGE_TOOLS.has(item.tool_name)) continue;
    if (!item.tool_use_id || failedUseIds.has(item.tool_use_id)) continue;
    const input = item.tool_input as { file_path?: unknown; path?: unknown } | undefined;
    const raw = input?.file_path ?? input?.path;
    if (typeof raw !== 'string' || !raw.trim()) continue;
    // push trim 后的串：请求键/缓存键/后端回显键三方一致——未 trim 的原串
    // 会使后端 exists() 误判 deleted 且预览回显失配（载荷被丢弃永久加载）
    const trimmed = raw.trim();
    if (seen.has(trimmed)) continue;
    seen.add(trimmed);
    files.push(trimmed);
  }
  return files;
}

/**
 * 按用户消息分组为轮次（turns），带结构化共享缓存
 *
 * 追加场景（流式 flush / 新消息）下复用已有轮的数组引用——仅最后一轮
 * 重建或新增一轮。增量按 delta 范围逐个归组，与全量构建结果等价。
 * 这是 memo(TurnView) 生效的前提：流式期间历史轮的 turn 引用稳定，
 * React 可完全跳过其 reconcile。
 *
 * @param staticItems - 转录项列表
 * @returns 轮次分组（TranscriptItem[][]）
 */
export function useStableTurns(staticItems: TranscriptItem[]) {
  const cacheRef = useRef<{ items: TranscriptItem[]; turns: TranscriptItem[][] } | null>(null);
  const cached = cacheRef.current;

  // 引用未变（流式 token 只更新 assistantBuffer，不 touch items）→ 直接返回缓存
  if (cached && cached.items === staticItems) return cached.turns;

  const next = cached && isAppendOf(cached.items, staticItems)
    ? appendTurns(cached, staticItems)
    : { items: staticItems, turns: buildTurns(staticItems) };
  cacheRef.current = next;
  return next.turns;
}

/**
 * tool_use_id → tool_input 映射，带结构化共享缓存
 *
 * 追加场景增量写入（Map 引用保持不变）→ 依赖它的 memo 组件不失效；
 * 替换/回退场景全量重建。增量写入在 render 期间修改 ref 对象，
 * 属幂等缓存更新（React 官方 manual cache 模式），无副作用。
 *
 * 协议前提：tool 条目（携带 tool_input）必须先于对应 tool_result 到达
 * （live 流程 tool_started 先 pushStatic tool 条目，满足；restore/replace
 * 走全量重建，不依赖此前提）。若未来协议变化导致 tool_input 补发到已
 * 渲染条目，本缓存的 Map 不会更新 → 消费组件显示陈旧摘要。
 *
 * @param staticItems - 转录项列表
 * @returns tool 输入映射（追加期间引用稳定）
 */
export function useStableToolInputMap(staticItems: TranscriptItem[]) {
  const mapRef = useRef<Map<string, Record<string, unknown>> | null>(null);
  const itemsRef = useRef<TranscriptItem[] | null>(null);

  const prevItems = itemsRef.current;
  if (prevItems !== staticItems) {
    if (mapRef.current && isAppendOf(prevItems ?? [], staticItems)) {
      // 追加场景：只写入新条目，Map 引用不变
      for (let i = prevItems!.length; i < staticItems.length; i++) {
        const item = staticItems[i]!;
        if (item.role === 'tool' && item.tool_use_id && item.tool_input) {
          mapRef.current.set(item.tool_use_id, item.tool_input);
        }
      }
    } else {
      // 替换/回退/清空场景：全量重建（引用变化，依赖组件重渲染一次）
      mapRef.current = buildToolInputMap(staticItems);
    }
    itemsRef.current = staticItems;
  }

  return mapRef.current!;
}
