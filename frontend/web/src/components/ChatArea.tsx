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

import { memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { t, type UiLanguage } from '../i18n';
import MessageBubble, { PendingToolBubble, StreamingBuffer, ThinkingBlock, useContentCollapse } from './MessageBubble';
import { buildTurnChangedFiles, useStableTurns, useStableToolInputMap } from '../utils/turnGrouping';
import TurnFilesBar from './TurnFilesBar';
import WelcomeScreen from './WelcomeScreen';
import { PermissionCard, QuestionCard } from './ModalCard';
import type { FileStatItem, TranscriptItem, PendingToolCall } from '../types/protocol';

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

  // 点击内容区域快速折叠（对齐思考过程的单击折叠）；跳过内层独立折叠区
  // （思考过程块、工具行）与交互元素，点击中间 text 空白处即收起整个区
  const { handleClick: handleContentClick, handleDoubleClick: handleContentDoubleClick } = useContentCollapse(() => {
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
          <div className="mt-1.5" onClick={handleContentClick} onDoubleClick={handleContentDoubleClick}>
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
  /** 是否为列表首轮（控制轮间间距） */
  hasTopGap: boolean;
  /** 文件增删行数统计缓存：原始路径串 → 条目（单轮变更条数据源） */
  fileStats: Map<string, FileStatItem>;
  /** 批量拉取文件行数统计（hook 内去重，引用稳定） */
  onRequestFileStats: (paths: string[]) => void;
  /** 点击变更文件打开预览：Git 内传绝对路径 + 'diff'，否则 + 'content'（引用稳定） */
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
  turn, isLastTurn, turnsToRewind, busy, hasPendingTools, lang, toolInputMap,
  onRewindToTurn, onRegenerate, hasTopGap,
  fileStats, onRequestFileStats, onOpenSessionFile,
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

  // 单轮变更文件（轮次完成后提取；流式期间 tool_result 未到齐不渲染）
  const changedFiles = useMemo(
    () => (turnStreaming ? [] : buildTurnChangedFiles(turn)),
    [turn, turnStreaming],
  );

  // 该轮统计签名：仅当"本轮路径的缓存内容"变化时才改变（到达/数值更新）。
  // fileStats 是会话级整表——任何轮次的合并都会替换其引用，若 footer 直接
  // 依赖 Map，所有历史轮的 memo(MessageBubble) 会全量失效（长会话恢复时
  // O(N²) 次 Markdown 重解析）。签名作为值依赖隔离跨轮影响。
  const turnStatsSig = useMemo(
    () => changedFiles.map((p) => {
      const s = fileStats.get(p);
      return s ? `${s.status ?? ''}:${s.insertions}:${s.deletions}` : '';
    }).join('\u0001'),
    [changedFiles, fileStats],
  );

  // 单轮变更条（footer 插槽）：渲染于最终回复正文之后、复制/重新生成按钮
  // 上方。元素经 memo 缓存保证引用稳定——仅 changedFiles/统计签名变化时
  // 重建，不破坏 memo(MessageBubble)。TurnFilesBar 内部仍从 fileStats 取值
  // （Map 引用变化只让轻量的变更条自身重渲染，不波及 Markdown 正文）。
  const turnFilesFooter = useMemo(
    () => (
      changedFiles.length > 0 ? (
        <TurnFilesBar
          lang={lang}
          rawPaths={changedFiles}
          stats={fileStats}
          onRequestStats={onRequestFileStats}
          onOpenFile={onOpenSessionFile}
        />
      ) : null
    ),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 以签名为值依赖，见上
    [changedFiles, turnStatsSig, lang, onRequestFileStats, onOpenSessionFile],
  );

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
    <div className={hasTopGap ? 'pt-12' : ''}>
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
  /** 文件增删行数统计缓存：原始路径串 → 条目（单轮变更条数据源） */
  fileStats: Map<string, FileStatItem>;
  /** 批量拉取文件行数统计（hook 内去重，引用稳定） */
  onRequestFileStats: (paths: string[]) => void;
  /** 点击变更文件打开预览：Git 内传绝对路径 + 'diff'，否则 + 'content'（引用稳定） */
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
  fileStats, onRequestFileStats, onOpenSessionFile, children,
}: ChatAreaProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [showScrollDown, setShowScrollDown] = useState(false);
  const [expanded, setExpanded] = useState(false);
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

  // 消息列表收缩：超过阈值时仅展示最新 N 轮，其余折叠
  const { visibleTurns, hiddenCount } = useMemo(() => {
    if (expanded || turns.length <= COLLAPSE_TURN_THRESHOLD) {
      return { visibleTurns: turns, hiddenCount: 0 };
    }
    return {
      visibleTurns: turns.slice(turns.length - COLLAPSE_TURN_THRESHOLD),
      hiddenCount: turns.length - COLLAPSE_TURN_THRESHOLD,
    };
  }, [turns, expanded]);

  // 计算可见轮次在原 turns 中的起始偏移
  const turnOffset = turns.length - visibleTurns.length;

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

  /** 滚动事件处理：
   *  向上滚动 → 停止跟随并立即显示"回到底部"按钮（跟随消失即显现）；
   *  向下滚动到接近底部（≤ BOTTOM_THRESHOLD_PX）→ 恢复跟随并隐藏按钮。
   *  程序滚动（auto-scroll 赋值）与平滑滚动（按钮触发）派生的事件忽略。 */
  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (programmaticScrollRef.current) {
      programmaticScrollRef.current = false;
      lastScrollTopRef.current = el.scrollTop;
      return;
    }
    // 平滑滚动动画中的事件不参与跟随判定
    if (performance.now() - lastSmoothScrollAtRef.current < SMOOTH_EVENT_IGNORE_MS) return;
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
  }, []);

  /** 一键回到底部：恢复自动跟随 + 平滑滚动（同时收起"显示更多"展开的历史轮次） */
  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setExpanded(false);
    followingRef.current = true;
    setShowScrollDown(false);
    lastSmoothScrollAtRef.current = performance.now();
    smoothScrollGuardUntilRef.current = performance.now() + SMOOTH_SCROLL_GUARD_MS;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    lastScrollTopRef.current = el.scrollHeight;
  }, []);

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

  // 用户发送新消息时强制回到底部（忽略用户是否已停止跟随）
  const userMsgCount = useMemo(() => staticItems.filter((i) => i.role === 'user').length, [staticItems]);
  const prevUserMsgCountRef = useRef(0);
  useEffect(() => {
    if (userMsgCount > prevUserMsgCountRef.current) {
      followingRef.current = true;
      const el = scrollRef.current;
      if (el) {
        el.scrollTop = el.scrollHeight;
        lastScrollTopRef.current = el.scrollHeight;
      }
      setShowScrollDown(false);
    }
    prevUserMsgCountRef.current = userMsgCount;
  }, [userMsgCount]);

  // 新会话或恢复后重置展开状态与跟随
  useEffect(() => {
    setExpanded(false);
    followingRef.current = true;
    // 回到空会话（欢迎界面）时无可滚动内容，收起"回到底部"按钮
    setShowScrollDown(false);
  }, [staticItems.length === 0]);

  const hasContent = staticItems.length > 0 || assistantBuffer || streamingReasoning || pendingToolCalls.length > 0 || !!modal;

  // 会话恢复中：显示居中加载卡片，覆盖正常转录区
  // 此条件返回在所有 hooks 之后，不违反 React Rules of Hooks
  if (restoringSessionId) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <svg className="animate-spin w-8 h-8 text-primary" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <span className="text-sm text-content-secondary">{t(lang, 'restoring_session')}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 relative flex flex-col">
      <div className="flex-1 min-h-0 overflow-y-auto mr-1.5" ref={scrollRef} onScroll={handleScroll} style={{ scrollbarGutter: 'stable' }}>
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
        {/* 折叠的更早消息入口 */}
        {hiddenCount > 0 && (
          <div className="flex justify-center mb-6">
            <button
              onClick={() => setExpanded(true)}
              className="px-4 py-2 text-sm text-content-secondary hover:text-content-primary glass-surface rounded-full transition-colors cursor-pointer hover:scale-105 active:scale-95"
            >
              {t(lang, 'show_earlier').replace('{count}', String(hiddenCount))}
            </button>
          </div>
        )}

        {visibleTurns.map((turn, visIdx) => {
          const turnIdx = turnOffset + visIdx;
          return (
            <TurnView
              key={turnIdx}
              turn={turn}
              isLastTurn={turnIdx === turns.length - 1}
              turnsToRewind={turns.length - turnIdx}
              busy={busy}
              hasPendingTools={pendingToolCalls.length > 0}
              lang={lang}
              toolInputMap={toolInputMap}
              onRewindToTurn={stableOnRewindToTurn}
              onRegenerate={stableOnRegenerate}
              hasTopGap={visIdx > 0}
              fileStats={fileStats}
              onRequestFileStats={onRequestFileStats}
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
        {/* key 绑定 request_id：新模态框（新问题）到来时整体重置 QuestionCard 内部状态 */}
        {modal?.kind === 'question' && (
          <QuestionCard key={modal?.request_id ? String(modal.request_id) : 'q'} modal={modal} lang={lang} onRespond={onQuestionResponse} />
        )}
      </div>
      )}

      </div>
      {/* 一键回到底部浮动按钮：绝对定位于外层（非滚动）包装底部，紧贴输入框上方，
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
