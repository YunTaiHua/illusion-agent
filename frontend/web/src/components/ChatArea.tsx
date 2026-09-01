/**
 * @fileoverview 聊天区域组件
 *
 * Web 前端的主要对话显示区域，负责：
 * - 显示对话历史（按轮次分组）
 * - 显示待处理的工具调用
 * - 显示流式回复和思考过程
 * - 显示权限确认和问答模态框
 * - 自动滚动到底部
 *
 * @module ChatArea
 */

import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { t, type UiLanguage } from '../i18n';
import MessageBubble, { PendingToolBubble, StreamingBuffer, ThinkingBlock, useContentCollapse } from './MessageBubble';
import { computeTurnFileStats, type TurnFileStat, useStableTurns, useStableToolInputMap } from '../utils/turnGrouping';
import TurnFilesBar from './TurnFilesBar';
import TurnNavigator, { type TurnNavItem } from './TurnNavigator';
import WelcomeScreen from './WelcomeScreen';
import { PermissionCard, QuestionCard } from './ModalCard';
import type { PendingToolCall, TranscriptItem, TurnOutlineEntry } from '../types/protocol';

/** 消息列表收缩阈值：超过此轮次时折叠更早的消息 */
const COLLAPSE_TURN_THRESHOLD = 5;
/** 判定"已在底部附近"的像素容差（向下滚动到此范围内恢复跟随 */
const BOTTOM_THRESHOLD_PX = 80;
/** 平滑滚动保护窗：点击"回到底部"后此期间内跳过 instant 跟随滚动，避免打断动画 */
const SMOOTH_SCROLL_GUARD_MS = 420;
/** 平滑滚动事件忽略窗：平滑滚动进行中的 scroll 事件不参与跟随判定 */
const SMOOTH_EVENT_IGNORE_MS = 100;

/**
 * 将一轮对话的 items 拆分为三部分：
 * - userItems：用户消息（始终可见）
 * - thinkingItems：工具调用 + 中间 assistant 消息（各自独立折叠）
 * - finalAssistant：最后一条含文本的 assistant 消息（始终可见，其 reasoning 独立折叠）
 *
 * 流式阶段（streaming=true）所有 assistant 消息都视作中间消息：
 * 最终回复由 StreamingBuffer 实时展示，避免中间 LLM 消息被误判为最终回复，
 * 导致后续工具调用显示在消息上方、以及复制/回退按钮闪烁等问题。
 *
 * @param items - 单轮转录项列表
 * @param streaming - 是否处于流式输出阶段
 * @returns 拆分结果
 */
function splitTurnItems(items: TranscriptItem[], streaming: boolean = false) {
  const userItems: TranscriptItem[] = [];
  const thinkingItems: TranscriptItem[] = [];
  let finalAssistant: TranscriptItem | null = null;

  // 流式阶段：所有 assistant 消息归入思考过程，不区分"最终回复"
  // plan 角色由 ModalCard 专门展示，不在对话流中重复显示
  if (streaming) {
    for (const item of items) {
      if (item.role === 'plan') {
        continue; // 跳过 plan 消息，由 ModalCard 处理
      }
      if (item.role === 'user') {
        userItems.push(item);
      } else {
        thinkingItems.push(item);
      }
    }
    return { userItems, thinkingItems, finalAssistant };
  }

  // 完成态：找最后一条有非空 text 的 assistant 消息作为"最终回复"
  let lastAssistantIdx = -1;
  for (let i = items.length - 1; i >= 0; i--) {
    if (items[i]!.role === 'assistant' && items[i]!.text.trim()) {
      lastAssistantIdx = i;
      break;
    }
  }

  for (let i = 0; i < items.length; i++) {
    const item = items[i]!;
    if (item.role === 'plan') {
      continue; // 跳过 plan 消息，由 ModalCard 处理
    }
    if (item.role === 'user') {
      userItems.push(item);
    } else if (i === lastAssistantIdx) {
      finalAssistant = item;
    } else {
      thinkingItems.push(item);
    }
  }

  return { userItems, thinkingItems, finalAssistant };
}

/**
 * "任务完成"折叠区组件（三级标题样式）
 *
 * 将一轮对话中最终回复之前的所有内容（中间 text、工具调用、思考过程、
 * 最终回复的思考过程）再折叠一次（二级折叠），折叠样式与普通折叠区分：
 * - 三级标题"任务完成 >"（展开后箭头变为向下"任务完成 ∨"），字号/字重/颜色
 *   与最终回复 markdown 渲染的 h3（.prose h3）保持一致
 * - 流式阶段标题显示"任务进行中"
 * - 标题下方是一条分隔直线
 *
 * 流式阶段自动展开（内容可见）；轮次完成（streaming=false）时自动折叠。
 * 用户手动操作过的折叠区不被自动状态覆盖（尊重用户选择）。
 *
 * memo 化：children 由 TurnView useMemo 提供（引用稳定），
 * 历史轮次的折叠区不会因其他消息的 token 更新而重渲染。
 *
 * @param props.streaming - 轮次是否仍在流式（流式展开、完成折叠）
 * @param props.lang - UI 语言
 * @param props.hasContent - 折叠内容是否非空（空内容时展开态不渲染内容区）
 * @param props.children - 折叠内容（中间 text、工具行、思考过程）
 */
const TaskCompleteSection = memo(function TaskCompleteSection({ streaming, lang, hasContent, children }: { streaming: boolean; lang: UiLanguage; hasContent: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(streaming);
  // 用户是否手动操作过（展开/折叠）：手动操作后自动状态变化不再覆盖
  const interactedRef = useRef(false);
  // React 官方 "adjusting state during render" 模式：prev 值用 state 存储
  const [prevStreaming, setPrevStreaming] = useState(streaming);

  // streaming 变化（流式开始/完成）时自动同步展开状态；用户手动操作过则不覆盖
  if (streaming !== prevStreaming) {
    setPrevStreaming(streaming);
    if (!interactedRef.current) setOpen(streaming);
  }

  const handleToggle = () => {
    interactedRef.current = true;
    setOpen(!open);
  };

  // 右键内容区域快速折叠（对齐思考过程的右键折叠）；忽略内层独立折叠区
  // （思考过程块、工具行）与交互元素，右键中间 text 空白处即收起整个区
  const { handleContextMenu: handleContentContextMenu } = useContentCollapse(() => {
    interactedRef.current = true;
    setOpen(false);
  }, '[data-thinking-block], [data-tool-row]');

  return (
    <div className="my-2">
      {/* 三级标题：与最终回复 markdown 渲染的 h3（.prose h3 = 1.125em/700/主色）保持一致 */}
      <h3 className="text-lg font-bold text-content-primary">
        <button
          onClick={handleToggle}
          className="flex items-center gap-2 transition-colors py-1.5 cursor-pointer"
        >
          <span>{t(lang, streaming ? 'task_in_progress' : 'task_complete')}</span>
          <svg
            className={`w-4 h-4 transition-transform duration-150 ${open ? 'rotate-90' : ''}`}
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
      </h3>
      {/* 标题下方的分隔直线：浅色下 --border-medium 比 border-border-light 清晰可见 */}
      <div className="border-t border-border-medium" />
      {/* 展开/折叠微动画（简洁 fade：纯透明度 150ms） */}
      {hasContent && open && (
        <div className="animate-fade">
          <div className="mt-1.5" onContextMenu={handleContentContextMenu}>
            {children}
          </div>
        </div>
      )}
    </div>
  );
});

/**
 * TurnView 组件属性接口
 */
interface TurnViewProps {
  /** 单轮转录项列表（引用稳定：useStableTurns 结构化共享） */
  turn: TranscriptItem[];
  /** 该轮的绝对轮号（1-based：firstLoadedTurn + 本地序号；分叉/导航的定位键） */
  turnNumber: number;
  /** 是否为最后一轮 */
  isLastTurn: boolean;
  /** 回退到该轮需回退的轮次数（= 轮数 - 轮序号） */
  turnsToRewind: number;
  /** 是否忙碌（决定 turnStreaming 判定） */
  busy: boolean;
  /** 是否存在待处理工具调用（引用稳定，避免 pendingToolCalls 数组变化导致全量重渲染） */
  hasPendingTools: boolean;
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 工具输入映射（引用稳定） */
  toolInputMap: Map<string, Record<string, unknown>>;
  /** 撤销到指定轮次回调（App 传入，ChatArea 稳定包装） */
  onRewindToTurn?: (turnsToRewind: number) => void;
  /** 重新生成回调（App 传入，ChatArea 经 ref 稳定包装） */
  onRegenerate?: () => void;
  /** 分叉会话到该轮（保留前 turnNumber 轮；ChatArea 经 ref 稳定包装） */
  onForkTurn?: (turnsToKeep: number) => void;
  /** 是否为列表首轮（控制轮间间距） */
  hasTopGap: boolean;
  /** 点击变更文件打开预览：added/modified 传 'diff'，否则 + 'content'（引用稳定） */
  onOpenSessionFile: (path: string, kind: 'content' | 'diff') => void;
}

/**
 * 单轮渲染单元（memo 化）
 *
 * 流式 token 更新时，历史轮的 turn / isLastTurn / turnsToRewind / busy /
 * hasPendingTools / toolInputMap / 回调引用全部稳定 → memo 直接短路，
 * 本轮内所有子组件（MessageBubble / TaskCompleteSection）也因引用稳定
 * 跳过 reconcile，最终只有流式缓冲区真正重渲染。
 *
 * @param props - 组件属性
 * @returns 单轮对话的 JSX
 */
const TurnView = memo(function TurnView({
  turn, turnNumber, isLastTurn, turnsToRewind, busy, hasPendingTools, lang, toolInputMap,
  onRewindToTurn, onRegenerate, onForkTurn, hasTopGap, onOpenSessionFile,
}: TurnViewProps) {
  // 轮是否仍在流式：busy=true 与 user 消息（transcript_item）存在网络往返
  // 窗口期——若仅按 busy && isLastTurn 判定，窗口期内旧轮会被误判为流式轮，
  // 上一条回复的思考过程闪开又折叠。"轮已完成"判定：最后一条是 assistant
  // 完成消息（tool_started 时 pushStatic 的中间 assistant 消息伴随
  // pendingToolCalls 非空，排除）。
  const turnFinished =
    turn.length > 0 &&
    turn[turn.length - 1]!.role === 'assistant' &&
    !hasPendingTools;
  const turnStreaming = busy && isLastTurn && !turnFinished;

  // splitTurnItems 结果仅随 turn / turnStreaming 变化重建
  const { userItems, thinkingItems, finalAssistant } = useMemo(
    () => splitTurnItems(turn, turnStreaming),
    [turn, turnStreaming],
  );

  // 单轮变更统计（本轮对话增量，本地从工具结果累计；流式期间结果未到齐不渲染）。
  // changedFiles 取首次出现的原样路径（map 键为归一化路径），供展示与预览
  const turnFileStats = useMemo(
    () => (turnStreaming ? new Map<string, TurnFileStat>() : computeTurnFileStats(turn)),
    [turn, turnStreaming],
  );
  const changedFiles = useMemo(
    () => [...turnFileStats.values()].map((s) => s.raw),
    [turnFileStats],
  );

  // 单轮变更条（footer 插槽）：渲染于最终回复正文之后、复制/重新生成按钮
  // 上方。统计为该轮转录的纯本地派生（引用随 turn 稳定），不依赖会话级
  // 缓存，历史轮的 memo(MessageBubble) 不受其他轮次影响。
  const turnFilesFooter = useMemo(
    () => (
      changedFiles.length > 0 ? (
        <TurnFilesBar
          lang={lang}
          rawPaths={changedFiles}
          stats={turnFileStats}
          onOpenFile={onOpenSessionFile}
        />
      ) : null
    ),
    [changedFiles, turnFileStats, lang, onOpenSessionFile],
  );

  // 分叉回调引用稳定：turnNumber 变化时才重建
  const handleFork = useCallback(() => {
    onForkTurn?.(turnNumber);
  }, [onForkTurn, turnNumber]);

  // 回调引用稳定：turnsToRewind / onRewindToTurn 变化时才重建
  const handleRewind = useCallback(() => {
    onRewindToTurn?.(turnsToRewind);
  }, [onRewindToTurn, turnsToRewind]);

  // 折叠区 children 引用稳定：thinkingItems / toolInputMap 不变则复用，
  // 保证 memo(TaskCompleteSection) 生效
  const thinkingChildren = useMemo(
    () => thinkingItems.map((item, msgIdx) => (
      <MessageBubble
        key={`t-${msgIdx}`}
        item={item}
        toolInputMap={toolInputMap}
        lang={lang}
        showActions={false}
      />
    )),
    [thinkingItems, toolInputMap, lang],
  );

  // TaskCompleteSection 的整个 children（含 thinking 列表与最终回复思考块）
  // 整体 memo，避免 fragment 每次重建导致折叠区 memo 失效
  const sectionChildren = useMemo(
    () => (
      <>
        {thinkingChildren}
        {finalAssistant?.reasoning?.trim() && <ThinkingBlock text={finalAssistant.reasoning} lang={lang} />}
      </>
    ),
    [thinkingChildren, finalAssistant, lang],
  );

  return (
    <div className={hasTopGap ? 'pt-12' : ''} data-turn={turnNumber}>
      {userItems.map((item, msgIdx) => (
        <MessageBubble
          key={`u-${msgIdx}`}
          item={item}
          lang={lang}
          onRewind={onRewindToTurn ? handleRewind : undefined}
          actionsDisabled={busy}
        />
      ))}
      {/* 二级折叠："任务完成"大标题区——折叠最终回复之前的所有内容
          （中间 text、工具行、思考过程、最终回复的思考过程）；
          流式阶段（turnStreaming）强制渲染显示"任务进行中"标题
          （即使中间内容尚未推入），完成后自动折叠 */}
      {(turnStreaming || thinkingItems.length > 0 || finalAssistant?.reasoning?.trim()) && (
        <TaskCompleteSection
          streaming={turnStreaming}
          lang={lang}
          hasContent={thinkingItems.length > 0 || !!finalAssistant?.reasoning?.trim()}
        >
          {sectionChildren}
        </TaskCompleteSection>
      )}
      {finalAssistant && (
        <MessageBubble
          key="final"
          item={finalAssistant}
          toolInputMap={toolInputMap}
          lang={lang}
          hideReasoning
          onRegenerate={isLastTurn ? onRegenerate : undefined}
          onFork={onForkTurn ? handleFork : undefined}
          actionsDisabled={busy}
          footer={turnFilesFooter}
        />
      )}
    </div>
  );
});


/**
 * ChatArea 组件属性接口
 */
interface ChatAreaProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 静态转录项列表 */
  staticItems: TranscriptItem[];
  /** 助手回复缓冲区 */
  assistantBuffer: string;
  /** 流式推理文本 */
  streamingReasoning: string;
  /** 待处理的工具调用列表 */
  pendingToolCalls: PendingToolCall[];
  /** reasoning 是否正在流式（大脑脉冲动画跟随） */
  reasoningStreaming: boolean;
  /** 是否忙碌 */
  busy: boolean;
  /** 是否已连接 */
  connected: boolean;
  /** 模态对话框配置 */
  modal: Record<string, unknown> | null;
  /** 权限响应回调 */
  onPermissionResponse: (requestId: string, allowed: boolean, sessionAllow: boolean, toolName: string) => void;
  /** 问答响应回调 */
  onQuestionResponse: (requestId: string, answer: string) => void;
  /** 正在恢复的会话 ID（可选，非空时显示居中加载卡片覆盖转录区） */
  restoringSessionId?: string | null;
  /** 撤销到指定轮次回调（参数为待回退轮次数） */
  onRewindToTurn?: (turnsToRewind: number) => void;
  /** 重新生成回调（回退最后一轮并重发 user 消息） */
  onRegenerate?: () => void;
  /** 分叉会话回调（参数为保留的前 N 轮；点击气泡底部 fork 按钮触发） */
  onForkTurn?: (turnsToKeep: number) => void;
  /** 全量轮次大纲（null = 无后端大纲，导航退化为本地已载入轮次） */
  turnOutline: TurnOutlineEntry[] | null;
  /** 已载入最小轮号（1-based；分页恢复的头部边界） */
  firstLoadedTurn: number;
  /** 历史轮次分页加载中 */
  loadingHistory: boolean;
  /** 加载更早的轮次分页（导航跳转未载入轮 / 加载更多按钮共用） */
  onRequestHistory: () => void;
  /** 转录整体替换信号（rewind/compact 时 bump）：收到后强制回到底部 */
  transcriptReplaceTick: number;
  /** 点击变更文件打开预览：added/modified 传 'diff'，否则 + 'content'（引用稳定） */
  onOpenSessionFile: (path: string, kind: 'content' | 'diff') => void;
  /** 欢迎态注入到标题下方的内容（输入框 + 工具栏卡片；非欢迎态不渲染） */
  children?: ReactNode;
}

/**
 * 聊天区域组件
 *
 * Web 前端的主要对话显示区域。
 *
 * @param props - 组件属性
 * @returns 返回聊天区域的 JSX 元素
 */
export default function ChatArea({
  lang, staticItems, assistantBuffer, streamingReasoning, pendingToolCalls, reasoningStreaming, busy, connected,
  modal, onPermissionResponse, onQuestionResponse, restoringSessionId, onRewindToTurn, onRegenerate,
  onForkTurn, turnOutline, firstLoadedTurn, loadingHistory, onRequestHistory, transcriptReplaceTick, onOpenSessionFile, children,
}: ChatAreaProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [showScrollDown, setShowScrollDown] = useState(false);
  // 本地折叠：除最新 COLLAPSE_TURN_THRESHOLD 轮外，点"加载更多"每次多显示
  // LOCAL_REVEAL_TURNS 轮；与服务端分页（unloadedTurns）共用同一个按钮
  const [expandedCount, setExpandedCount] = useState(0);
  // 是否跟随底部（对齐 kimi-code 的弱跟随）：用户向上滚动即停止跟随，
  // 恢复仅通过"向下滚动回底部附近"或点击"回到底部"按钮
  const followingRef = useRef(true);
  const lastScrollTopRef = useRef(0);
  // 平滑滚动保护（参考 kimi-code）：平滑滚动期间跳过 instant 跟随与滚动事件判定
  const lastSmoothScrollAtRef = useRef(0);
  const smoothScrollGuardUntilRef = useRef(0);
  // 程序滚动标记：auto-scroll 赋值 scrollTop 后派生的 scroll 事件用此忽略
  const programmaticScrollRef = useRef(false);

  // 按用户消息分组为轮次(turn)，每轮以用户消息开头。
  // 结构化共享：流式追加期间历史轮的数组引用稳定（memo(TurnView) 生效前提）。
  // 注意：hooks 必须在任何条件返回之前调用（React Rules of Hooks）
  const turns = useStableTurns(staticItems);

  // 本地折叠裁剪：始终显示最新 (阈值 + expandedCount) 轮；本地全部显示后
  // 由服务端分页（加载更多）继续向前取数
  const visibleTurns = useMemo(() => {
    const visibleCount = COLLAPSE_TURN_THRESHOLD + expandedCount;
    return turns.length <= visibleCount ? turns : turns.slice(turns.length - visibleCount);
  }, [turns, expandedCount]);
  const localHidden = Math.max(0, turns.length - (COLLAPSE_TURN_THRESHOLD + expandedCount));

  // 计算可见轮次在原 turns 中的起始偏移
  const turnOffset = turns.length - visibleTurns.length;

  // rewind 步数按"产生 checkpoint 的轮"加权：命令开头的轮（斜杠指令，
  // 后端不产生 checkpoint）不计入回退步数。当前所有摄入点都会过滤
  // is_command 消息（useWebSocketSession 三处 pushStatic 前置过滤），
  // 本计算实际恒等于 turns.length - turnIdx——作为纵深防御保留：
  // 若未来命令消息开始进入转录，此处自动保证与后端 checkpoint 对齐。
  // 「回退第一条消息需两次」的活跃修复在后端（query_engine 移除 goal
  // 快照的额外 checkpoint + checkpoint_store.align_checkpoint_id 对齐
  // runtime 重建场景的重复 id 行）。
  const turnRewindCounts = useMemo(() => {
    const weights = turns.map((turn) => (turn[0]?.is_command ? 0 : 1));
    const prefixSums: number[] = [];
    let acc = 0;
    for (const w of weights) {
      acc += w;
      prefixSums.push(acc);
    }
    const total = acc;
    return turns.map((_, i) => total - (i > 0 ? prefixSums[i - 1]! : 0));
  }, [turns]);
  const turnIsCommand = useMemo(
    () => turns.map((turn) => !!turn[0]?.is_command),
    [turns],
  );

  // tool_use_id → tool_input 映射（增量缓存：追加期间 Map 引用稳定）
  const toolInputMap = useStableToolInputMap(staticItems);

  // 最后一条静态消息是否为已完成的 assistant 回复。
  // assistant_complete 已将最终回复推入 staticItems，但 busy 需等 line_complete
  // 才置 false；此窗口内 buffers 已清空、无待处理工具，"思考中"指标会误触发。
  // 用该标志抑制，保证最终回复结束后不再闪现"思考中"。
  const lastReplyDone = useMemo(() => {
    if (staticItems.length === 0) return false;
    return staticItems[staticItems.length - 1]!.role === 'assistant';
  }, [staticItems]);

  // onRegenerate / onRewindToTurn 经 ref 稳定包装：App 传入的 onRegenerate
  // 依赖 session 对象（每次 patchView 重建），直接透传会导致所有 TurnView
  // 的 memo 失效；经 ref 转发后回调引用恒定，始终调用最新实现。
  const onRegenerateRef = useRef(onRegenerate);
  onRegenerateRef.current = onRegenerate;
  const stableOnRegenerate = useCallback(() => {
    onRegenerateRef.current?.();
  }, []);
  const onRewindToTurnRef = useRef(onRewindToTurn);
  onRewindToTurnRef.current = onRewindToTurn;
  const stableOnRewindToTurn = useCallback((turnsToRewind: number) => {
    onRewindToTurnRef.current?.(turnsToRewind);
  }, []);
  const onForkTurnRef = useRef(onForkTurn);
  onForkTurnRef.current = onForkTurn;
  const stableOnForkTurn = useCallback((turnsToKeep: number) => {
    onForkTurnRef.current?.(turnsToKeep);
  }, []);
  const onRequestHistoryRef = useRef(onRequestHistory);
  onRequestHistoryRef.current = onRequestHistory;

  // === 左侧轮次导航（预览 + 跳转未载入轮次）===

  // 导航条目 = 后端全量大纲的未载入前缀 + 本地已载入轮次（按轮号对齐：
  // 本地 turns[i] 的绝对轮号 = firstLoadedTurn + i）。流式新增的轮次天然
  // 进入 loaded 侧（prompt/response 实时派生），无需回写大纲。
  const navItems = useMemo<TurnNavItem[]>(() => {
    const outline = turnOutline ?? [];
    const unloaded: TurnNavItem[] = outline
      .slice(0, Math.max(0, firstLoadedTurn - 1))
      .map((o) => ({ turn: o.turn, prompt: o.prompt, response: o.response, loaded: false }));
    const loaded: TurnNavItem[] = turns.map((turn, i) => {
      let prompt = '';
      let response = '';
      for (const item of turn) {
        if (item.role === 'user' && !prompt) prompt = item.text.slice(0, 80);
        if (item.role === 'assistant' && item.text.trim()) response = item.text.trim().slice(0, 120);
      }
      return { turn: firstLoadedTurn + i, prompt, response, loaded: true };
    });
    return [...unloaded, ...loaded];
  }, [turnOutline, firstLoadedTurn, turns]);

  // 当前阅读位置所在轮（视口顶部阅读线命中的 data-turn 行）
  const [activeTurn, setActiveTurn] = useState<number | null>(null);
  const syncActiveTurn = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const rows = el.querySelectorAll<HTMLElement>('[data-turn]');
    if (rows.length === 0) {
      setActiveTurn(null);
      return;
    }
    // 已滚动到（接近）底部：最后两轮常同屏，顶部阅读线会命中倒数第二
    // 轮——底部即视为阅读最后一轮（回到底部按钮/自动跟随都走此分支）
    if (el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_THRESHOLD_PX) {
      setActiveTurn(firstLoadedTurn + turns.length - 1);
      return;
    }
    const readingLine = el.getBoundingClientRect().top + Math.min(96, el.clientHeight * 0.2);
    let current = Number(rows[0]!.dataset.turn);
    for (const row of rows) {
      if (row.getBoundingClientRect().top <= readingLine) {
        current = Number(row.dataset.turn);
      } else {
        break;
      }
    }
    setActiveTurn(current);
  }, [firstLoadedTurn, turns.length]);

  // 未载入轮跳转的挂起目标：分页加载循环逐页向前，目标载入后滚动落地
  const pendingJumpRef = useRef<number | null>(null);
  const [busyJumpTurn, setBusyJumpTurn] = useState<number | null>(null);

  /** 滚动到指定轮（data-turn 锚点）；轮不存在（未载入/被折叠）返回 false */
  const scrollToTurn = useCallback((turn: number): boolean => {
    const el = scrollRef.current;
    if (!el) return false;
    const row = el.querySelector<HTMLElement>(`[data-turn="${turn}"]`);
    if (!row) return false;
    // 离开 live tail：跳转后不恢复底部跟随（用户向上浏览历史）
    followingRef.current = false;
    setShowScrollDown(true);
    const top = row.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop - 8;
    el.scrollTo({ top: Math.max(0, top) });
    setActiveTurn(turn);
    return true;
  }, []);

  const handleNavigate = useCallback((item: TurnNavItem) => {
    if (item.loaded) {
      // 已载入：直接滚动（可能在折叠区，先全部展开保证行存在）
      setExpandedCount(9999);
      requestAnimationFrame(() => scrollToTurn(item.turn));
      return;
    }
    // 未载入：挂起目标并逐页向前加载，落地由下方 effect 编排
    pendingJumpRef.current = item.turn;
    setBusyJumpTurn(item.turn);
    setExpandedCount(9999);
    onRequestHistoryRef.current?.();
  }, [scrollToTurn]);

  // 挂起跳转的落地循环：目标已载入 → 滚动并清除；仍未载入 → 继续翻页
  useEffect(() => {
    const target = pendingJumpRef.current;
    if (target == null) return;
    if (target >= firstLoadedTurn) {
      if (scrollToTurn(target)) {
        pendingJumpRef.current = null;
        setBusyJumpTurn(null);
      }
    } else if (!loadingHistory) {
      // 还有更早页未到：继续请求（loadingHistory 防重，每页到达后重跑本 effect）
      onRequestHistoryRef.current?.();
    }
  }, [firstLoadedTurn, loadingHistory, staticItems, scrollToTurn]);

  /** 未载入的更早轮次数（导航刻度 + 加载更多按钮的依据） */
  const unloadedTurns = Math.max(0, firstLoadedTurn - 1);

  // === "加载更多"统一入口：服务端分页（每次 5 轮）优先，本地折叠其次 ===

  /** 每次点击多显示的本地轮数 */
  const LOCAL_REVEAL_TURNS = 5;
  /** 视口稳定锚点：点击时记录滚动高度，内容增长（前插/展开）后补偿 scrollTop */
  const scrollAnchorRef = useRef<number | null>(null);

  /** 统一"加载更多"：不指定目标，向前加载一页（服务端或本地展开） */
  const handleLoadMore = useCallback(() => {
    const el = scrollRef.current;
    if (el) {
      // 记录锚点：新内容渲染后按高度差补偿 scrollTop，保持视口不跳动
      scrollAnchorRef.current = el.scrollHeight;
      scrollAnchorAtRef.current = Date.now();
    }
    // 按钮在顶部可见即说明用户已向上浏览，停止底部跟随
    followingRef.current = false;
    if (unloadedTurns > 0) {
      onRequestHistoryRef.current?.();
    } else if (localHidden > 0) {
      setExpandedCount((c) => c + LOCAL_REVEAL_TURNS);
    } else {
      scrollAnchorRef.current = null;
    }
  }, [unloadedTurns, localHidden]);

  // 转录变更的统一布局编排（单 effect 内区分两类变更，避免相互打架）：
  // - 整体替换（rewind/compact，transcriptReplaceTick 变化）：重置本地
  //   可见窗口到默认折叠态，并无视跟随状态强制回到底部——用户往往停在
  //   被回退消息处且已停止跟随，不强制会留在错误的相对位置。
  // - 历史前插（加载更多，firstLoadedTurn 前移）：同帧扩大本地可见窗口
  //   （新载入轮次立即可见），并按高度锚点补偿 scrollTop——用户当前阅读
  //   的内容保持在原视口位置（不跳动）；锚点 5s 未消费自动作废。
  // expandedCount 的重置/扩大在本 effect 内同步触发，随下一次布局提交
  // 一起完成高度变化，浏览器对收缩的 scrollTop 钳制天然落在新列表底部。
  const prevFirstLoadedRef = useRef(firstLoadedTurn);
  const scrollAnchorAtRef = useRef(0);
  const prevReplaceTickRef = useRef(transcriptReplaceTick);
  useLayoutEffect(() => {
    const el = scrollRef.current;
    const isReplace = transcriptReplaceTick !== prevReplaceTickRef.current;
    prevReplaceTickRef.current = transcriptReplaceTick;
    if (isReplace) {
      prevFirstLoadedRef.current = firstLoadedTurn;
      scrollAnchorRef.current = null;
      // 挂起的未载入轮跳转一并取消：目标轮已随回退消失或处于折叠区，
      // 不清除会让该刻度脉冲永久滞留、行出现时中途拽走视口
      pendingJumpRef.current = null;
      setBusyJumpTurn(null);
      setExpandedCount(0);
      followingRef.current = true;
      setShowScrollDown(false);
      setActiveTurn(firstLoadedTurn + turns.length - 1);
      if (el) {
        programmaticScrollRef.current = true;
        el.scrollTop = el.scrollHeight;
        lastScrollTopRef.current = el.scrollHeight;
      }
      return;
    }
    const deltaTurns = prevFirstLoadedRef.current - firstLoadedTurn;
    prevFirstLoadedRef.current = firstLoadedTurn;
    if (deltaTurns > 0) {
      setExpandedCount((c) => c + deltaTurns);
    }
    const anchor = scrollAnchorRef.current;
    if (el == null || anchor == null) return;
    if (Date.now() - scrollAnchorAtRef.current > 5000) {
      scrollAnchorRef.current = null;
      return;
    }
    const grew = el.scrollHeight - anchor;
    if (grew > 0) {
      // 标记程序滚动：补偿引发的 scroll 事件不走跟随判定（否则内容
      // 刚好接近底部时会被误判为"用户滚回底部"而重新开启跟随）
      programmaticScrollRef.current = true;
      el.scrollTop += grew;
      scrollAnchorRef.current = null;
    }
  }, [staticItems, firstLoadedTurn, expandedCount, transcriptReplaceTick, turns.length]);

  // 转录变化（恢复/历史前插/新轮）后同步一次阅读线轮次
  // （无滚动事件时导航高亮也要对齐）
  useEffect(() => {
    syncActiveTurn();
  }, [staticItems, syncActiveTurn]);

  /** 滚动事件处理：
   *  向上滚动 → 停止跟随并立即显示"回到底部"按钮（跟随消失即显现）；
   *  向下滚动到接近底部（≤ BOTTOM_THRESHOLD_PX）→ 恢复跟随并隐藏按钮。
   *  程序滚动（auto-scroll 赋值）与平滑滚动（按钮触发）派生的事件不参与
   *  跟随状态判定，但轮次导航高亮在所有路径下都同步刷新——否则点
   *  "回到底部"后导航高亮停留在旧轮次。 */
  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (performance.now() - lastSmoothScrollAtRef.current < SMOOTH_EVENT_IGNORE_MS) {
      // 平滑滚动动画早期事件：只刷新导航高亮
      lastScrollTopRef.current = el.scrollTop;
      syncActiveTurn();
      return;
    }
    if (programmaticScrollRef.current) {
      programmaticScrollRef.current = false;
      lastScrollTopRef.current = el.scrollTop;
      syncActiveTurn();
      return;
    }
    // 用户滚动：跟随状态判定 + 导航高亮刷新
    const top = el.scrollTop;
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (top < lastScrollTopRef.current - 1 && dist > 1) {
      // 用户向上滚动：停止跟随，显示回到底部按钮
      followingRef.current = false;
      setShowScrollDown(true);
    } else if (dist <= BOTTOM_THRESHOLD_PX && top > lastScrollTopRef.current + 1) {
      // 用户向下滚动回底部附近：恢复跟随
      followingRef.current = true;
      setShowScrollDown(false);
    }
    lastScrollTopRef.current = top;
    syncActiveTurn();
  }, [syncActiveTurn]);

  /** 一键回到底部：恢复自动跟随 + 平滑滚动（同时收起"显示更多"展开的历史轮次）。
   *  导航高亮立即指到最后一轮（平滑滚动动画中的 scroll 事件只覆盖中段，
   *  结束事件可能被忽略窗吞掉——不直接设置会停留在中间轮次）。 */
  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setExpandedCount(0);
    followingRef.current = true;
    setShowScrollDown(false);
    setActiveTurn(firstLoadedTurn + turns.length - 1);
    lastSmoothScrollAtRef.current = performance.now();
    smoothScrollGuardUntilRef.current = performance.now() + SMOOTH_SCROLL_GUARD_MS;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    lastScrollTopRef.current = el.scrollHeight;
  }, [firstLoadedTurn, turns.length]);

  // 内容变化时自动滚动到底部：
  // 仅当 following 时才跟随（用户向上滚过即不打扰，不会因内容更新被拉回）；
  // 恢复跟随只能通过用户向下滚回底部附近或点击"回到底部"按钮。
  // 卡片弹出时强制回到底部：模态卡片是交互元素，必须保证卡片可见。
  const prevModalRef = useRef<boolean | null>(null);
  useEffect(() => {
    // 先更新状态机再取容器：restore 分支（无滚动容器）下 ref 保持最新，
    // 避免恢复会话后首个 modal 的"出现"检测被陈旧值干扰
    const modalAppeared = prevModalRef.current === false && !!modal;
    prevModalRef.current = !!modal;
    const el = scrollRef.current;
    if (!el) return;
    if (modalAppeared) {
      followingRef.current = true;
      const prevTop = el.scrollTop;
      el.scrollTop = el.scrollHeight;
      if (el.scrollTop !== prevTop) programmaticScrollRef.current = true;
      setShowScrollDown(false);
      return;
    }
    if (!followingRef.current) return; // 用户已停止跟随：不打扰
    // 平滑滚动动画中（刚点击回到底部按钮）跳过 instant 跟随，避免打断动画
    if (performance.now() < smoothScrollGuardUntilRef.current) return;
    const prevTop = el.scrollTop;
    el.scrollTop = el.scrollHeight;
    if (el.scrollTop !== prevTop) programmaticScrollRef.current = true;
    setShowScrollDown(false); // 跟随到底后隐藏"回到底部"按钮
  }, [staticItems, assistantBuffer, streamingReasoning, pendingToolCalls, modal]);

  // 用户发送新消息时强制回到底部（忽略用户是否已停止跟随）。
  // 判据必须是"新 user 消息追加在末尾"而非数量增加：加载更早轮次会把
  // 历史 user 消息前插进转录，数量同样增长——若只看数量，每次加载更多
  // 都会把视口强行拽回底部（与视口稳定锚点相互打架）
  const userMsgCount = useMemo(() => staticItems.filter((i) => i.role === 'user').length, [staticItems]);
  const lastItemIsUser = staticItems.length > 0 && staticItems[staticItems.length - 1]!.role === 'user';
  const prevUserMsgCountRef = useRef(0);
  useEffect(() => {
    if (userMsgCount > prevUserMsgCountRef.current && lastItemIsUser) {
      followingRef.current = true;
      const el = scrollRef.current;
      if (el) {
        el.scrollTop = el.scrollHeight;
        lastScrollTopRef.current = el.scrollHeight;
      }
      setShowScrollDown(false);
    }
    prevUserMsgCountRef.current = userMsgCount;
  }, [userMsgCount, lastItemIsUser]);

  // 新会话或恢复后重置展开状态与跟随
  useEffect(() => {
    setExpandedCount(0);
    scrollAnchorRef.current = null;
    followingRef.current = true;
    // 回到空会话（欢迎界面）时无可滚动内容，收起"回到底部"按钮
    setShowScrollDown(false);
  }, [staticItems.length === 0]);

  const hasContent = staticItems.length > 0 || assistantBuffer || streamingReasoning || pendingToolCalls.length > 0 || !!modal;

  // 会话恢复中 / 新建会话等待中：显示居中加载卡片，覆盖正常转录区
  // （'__pending_new__' 为新建会话等待态的哨兵值，文案区分创建/恢复）
  // 此条件返回在所有 hooks 之后，不违反 React Rules of Hooks
  if (restoringSessionId) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <svg className="animate-spin w-8 h-8 text-primary" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <span className="text-sm text-content-secondary">
            {t(lang, restoringSessionId === '__pending_new__' ? 'creating_session' : 'restoring_session')}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 relative flex">
      {/* 左侧轮次导航：全量轮次刻度（未载入轮次可预览 + 点击加载跳转） */}
      <TurnNavigator
        lang={lang}
        items={navItems}
        activeTurn={activeTurn}
        busyTurn={busyJumpTurn}
        loading={loadingHistory}
        onNavigate={handleNavigate}
      />
      <div className="flex-1 min-w-0 min-h-0 relative flex flex-col">
      <div className="flex-1 min-w-0 min-h-0 overflow-y-auto mr-1.5 chat-scroll-cap [overflow-anchor:none]" ref={scrollRef} onScroll={handleScroll} style={{ scrollbarGutter: 'stable' }}>
      {!connected && !hasContent && (
        <div className="flex items-center justify-center h-full text-content-disabled text-sm font-medium">
          {t(lang, 'connecting')}
        </div>
      )}
      {connected && !hasContent && !busy && (
        <WelcomeScreen>{children}</WelcomeScreen>
      )}

      {(hasContent || busy) && (
      <div className="mx-auto max-w-[var(--chat-content-width)] px-6 md:px-10 lg:px-16 pt-6 pb-8">
        {/* "加载更多"统一入口：服务端未载入轮次优先（每次 5 轮），
            本地折叠轮次其次（每次 5 轮）；点击后按高度锚点补偿滚动，
            新内容在上方展开而用户当前视口不跳动 */}
        {(unloadedTurns > 0 || localHidden > 0) && (
          <div className="flex justify-center mb-4">
            <button
              onClick={handleLoadMore}
              disabled={loadingHistory}
              className="px-4 py-2 text-sm text-content-secondary hover:text-content-primary glass-surface rounded-full transition-colors cursor-pointer hover:scale-105 active:scale-95 disabled:opacity-60 disabled:cursor-default"
            >
              {loadingHistory ? t(lang, 'turn_nav_loading') : t(lang, 'load_more')}
            </button>
          </div>
        )}

        {visibleTurns.map((turn, visIdx) => {
          const turnIdx = turnOffset + visIdx;
          const isCommandTurn = turnIsCommand[turnIdx] ?? false;
          return (
            <TurnView
              key={firstLoadedTurn + turnIdx}
              turn={turn}
              turnNumber={firstLoadedTurn + turnIdx}
              isLastTurn={turnIdx === turns.length - 1}
              turnsToRewind={turnRewindCounts[turnIdx] ?? turns.length - turnIdx}
              busy={busy}
              hasPendingTools={pendingToolCalls.length > 0}
              lang={lang}
              toolInputMap={toolInputMap}
              onRewindToTurn={!isCommandTurn ? stableOnRewindToTurn : undefined}
              onRegenerate={stableOnRegenerate}
              onForkTurn={!isCommandTurn ? stableOnForkTurn : undefined}
              hasTopGap={visIdx > 0}
              onOpenSessionFile={onOpenSessionFile}
            />
          );
        })}
        {pendingToolCalls.length > 0 && (
          <div className={turns.length > 0 ? 'mt-4' : ''}>
            {pendingToolCalls.map((call) => (
              <PendingToolBubble key={call.tool_use_id} call={call} />
            ))}
          </div>
        )}
        {busy && !assistantBuffer && !streamingReasoning && pendingToolCalls.length === 0 && !lastReplyDone && (
          <div className={turns.length > 0 ? 'mt-4' : ''}>
            <ThinkingIndicator lang={lang} />
          </div>
        )}
        {busy && (assistantBuffer || streamingReasoning) && (
          <div className={turns.length > 0 ? 'mt-4' : ''}>
            <StreamingBuffer text={assistantBuffer} reasoning={streamingReasoning} reasoningStreaming={reasoningStreaming} lang={lang} />
          </div>
        )}
        {modal?.kind === 'permission' && (
          <PermissionCard modal={modal} lang={lang} onRespond={onPermissionResponse} />
        )}
        {/* key 绑定 request_id：新模态框（新问题）到来时整体重置 QuestionCard 内部状态；
            多问题切题时由 QuestionCard 回调 onTabChange 复用本组件的回到底部滚动 */}
        {modal?.kind === 'question' && (
          <QuestionCard key={modal?.request_id ? String(modal.request_id) : 'q'} modal={modal} lang={lang} onRespond={onQuestionResponse} onTabChange={scrollToBottom} />
        )}
      </div>
      )}

      </div>
      {/* 一键回到底部浮动按钮：绝对定位于内层列容器（非滚动）底部，紧贴输入框上方，
          随输入框高度自动调整；不硬编码视口 bottom 偏移 */}
      {showScrollDown && (
        <button
          onClick={scrollToBottom}
          className="absolute left-1/2 -translate-x-1/2 bottom-3 z-30 w-9 h-9 flex items-center justify-center rounded-full glass-surface text-content-secondary hover:text-content-primary shadow-lg transition-all duration-200 hover:scale-110 active:scale-95 cursor-pointer animate-fade-in-up"
          title={t(lang, 'scroll_to_bottom')}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 5v14M5 12l7 7 7-7" />
          </svg>
        </button>
      )}
      </div>
    </div>
  );
}

function ThinkingIndicator({ lang }: { lang: UiLanguage }) {
  return (
    <div className="flex items-center gap-2.5 py-2">
      <span className="flex gap-1">
        <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce" style={{ animationDelay: '0ms' }} />
        <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce" style={{ animationDelay: '150ms' }} />
        <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce" style={{ animationDelay: '300ms' }} />
      </span>
      <span className="text-xs text-content-secondary animate-pulse">
        {t(lang, 'thinking')}
      </span>
    </div>
  );
}
