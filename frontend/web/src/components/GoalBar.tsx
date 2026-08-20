/**
 * @fileoverview Goal 状态栏组件
 *
 * 停靠在输入框上方的一条卡片——goal 图标 + 相位标签 + 截断的目标文本
 * + 行内错误 + 图标操作（active 显示暂停、paused 显示恢复、恒有编辑与清除；
 * 编辑切换为行内表单：预填目标、Enter 保存、Esc 取消、空白禁用保存）。
 * 加载中（undefined）/ 无目标（null）/ 已完成不渲染。创建走 /goal 命令。
 * 样式采用 illusion web 的 glass/Tailwind 风格。
 *
 * @module GoalBar
 */

import { useEffect, useRef, useState } from 'react';
import { t, type UiLanguage } from '../i18n';
import type { GoalStatus } from '../types/protocol';

/** 相位 → 标签键（complete 不渲染整条，无需标签） */
const PHASE_LABEL_KEYS: Record<'active' | 'paused' | 'blocked', string> = {
  active: 'goal:phase.active',
  paused: 'goal:phase.paused',
  blocked: 'goal:phase.blocked',
};

/** 操作名（回执/成功后处理依据） */
type GoalAction = 'pause' | 'resume' | 'edit' | 'clear';

/**
 * GoalBar 组件属性接口
 */
interface GoalBarProps {
  /** 当前 goal 快照；undefined = 尚未加载，null = 无目标 */
  goal: GoalStatus | null | undefined;
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** goal_action 最近一次失败（行内显示；dsh actionError 的事件化对应物） */
  actionError: { code: string; message: string } | null;
  /** 编辑目标（CAS ref 由调用方调用时读取） */
  onEdit: (objective: string) => void;
  /** 暂停目标 */
  onPause: () => void;
  /** 恢复目标 */
  onResume: () => void;
  /** 清除目标 */
  onClear: () => void;
  /** 清除行内错误 */
  onDismissError: () => void;
}

/* ---- 16px 线性图标（IconGoal/Pause/Play/Edit/Trash/Check/Close） ---- */

const iconProps = {
  width: 14,
  height: 14,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
} as const;

const GoalGlyph = () => (
  <svg {...iconProps} aria-hidden="true">
    <circle cx="12" cy="12" r="9" />
    <circle cx="12" cy="12" r="5" />
    <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
  </svg>
);
const PauseIcon = () => (
  <svg {...iconProps} aria-hidden="true">
    <rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor" stroke="none" />
    <rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor" stroke="none" />
  </svg>
);
const PlayIcon = () => (
  <svg {...iconProps} aria-hidden="true">
    <path d="M7 4.5v15l13-7.5z" fill="currentColor" stroke="none" />
  </svg>
);
const EditIcon = () => (
  <svg {...iconProps} aria-hidden="true">
    <path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
  </svg>
);
const TrashIcon = () => (
  <svg {...iconProps} aria-hidden="true">
    <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6" />
  </svg>
);
const CheckIcon = () => (
  <svg {...iconProps} aria-hidden="true">
    <path d="M20 6 9 17l-5-5" />
  </svg>
);
const CloseIcon = () => (
  <svg {...iconProps} aria-hidden="true">
    <path d="M18 6 6 18M6 6l12 12" />
  </svg>
);

/**
 * GoalBar 组件
 *
 * 停靠于输入框卡片正上方的目标状态条。
 */
export function GoalBar({ goal, lang, actionError, onEdit, onPause, onResume, onClear, onDismissError }: GoalBarProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState(false);
  // 乐观清除：发出 clear 后本地隐藏该 goal id，权威 null 到达前避免闪烁
  const [clearedGoalId, setClearedGoalId] = useState<string | null>(null);
  const pendingRef = useRef(false);
  // 发起操作时的 goal (id, revision) 快照 + 操作名：goal 变化/错误到达时解除 pending
  const actRef = useRef<{ id?: string; revision?: number; action?: GoalAction }>({});

  // 新 goal 身份（清除/完成/外部替换）使本地编辑态失效：
  // 不重置的话残留草稿的 Enter 会写覆盖新 goal（dsh 同款防护）
  const goalId = goal?.id;
  useEffect(() => {
    setEditing(false);
    setClearedGoalId(null);
  }, [goalId]);

  // 操作回执（错误到达 / goal 前进）→ 解除 pending；edit 成功关闭表单，
  // clear 失败撤销乐观隐藏
  useEffect(() => {
    if (!pendingRef.current) return;
    if (actionError) {
      pendingRef.current = false;
      setPending(false);
      setClearedGoalId((id) => (id != null ? null : id));
      return;
    }
    const g = goal;
    const at = actRef.current;
    if (g == null || g.id !== at.id || g.revision !== at.revision) {
      pendingRef.current = false;
      setPending(false);
      if (at.action === 'edit') setEditing(false);
    }
  }, [goal, actionError]);

  // React state 在下一次渲染才禁用按钮；ref 封闭同渲染窗口，
  // 防止快速连点重复提交同一 CAS（单飞）
  const runAction = (action: GoalAction, run: () => void) => {
    if (pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    onDismissError();
    actRef.current = { id: goal?.id, revision: goal?.revision, action };
    run();
  };

  const handleEdit = () => {
    const trimmed = draft.trim();
    if (trimmed === '') return;
    runAction('edit', () => onEdit(trimmed));
  };

  const handleClear = () => {
    if (goal == null) return;
    const id = goal.id;
    runAction('clear', () => {
      onClear();
      setClearedGoalId(id);
    });
  };

  // 加载中 / 无目标 / 已完成 / 乐观清除中：整条不渲染
  if (goal === undefined || goal === null || goal.phase === 'complete' || goal.id === clearedGoalId) {
    return null;
  }

  const iconBtn =
    'inline-flex items-center justify-center w-7 h-7 rounded-full text-content-disabled ' +
    'hover:text-content-secondary hover:bg-black/5 dark:hover:bg-white/10 transition-colors ' +
    'disabled:opacity-40 disabled:cursor-default cursor-pointer';

  if (editing) {
    return (
      <div data-goal-bar className="glass-surface rounded-xl flex items-center gap-2.5 min-w-0 h-9 px-3 py-1 overflow-hidden">
        <input
          type="text"
          aria-label={t(lang, 'goal:objective.aria')}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleEdit();
            if (e.key === 'Escape') setEditing(false);
          }}
          autoFocus
          className="flex-1 min-w-0 h-[26px] px-2 rounded-md border border-border-light bg-black/5 dark:bg-white/5 text-[13px] leading-5 text-content-primary outline-none focus:border-primary"
        />
        {actionError !== null && (
          <span role="alert" className="min-w-0 max-w-[40%] shrink truncate text-xs text-danger">
            {actionError.message} ({actionError.code})
          </span>
        )}
        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            title={t(lang, 'goal:action.save')}
            aria-label={t(lang, 'goal:action.save')}
            className={iconBtn}
            onClick={handleEdit}
            disabled={pending || draft.trim() === ''}
          >
            <CheckIcon />
          </button>
          <button
            type="button"
            title={t(lang, 'goal:action.cancel')}
            aria-label={t(lang, 'goal:action.cancel')}
            className={iconBtn}
            onClick={() => setEditing(false)}
            disabled={pending}
          >
            <CloseIcon />
          </button>
        </div>
      </div>
    );
  }

  // blockedReason.message 可能很长：title 悬浮提示仅保留前 200 字符并压平换行，避免 tooltip 溢出
  const title =
    goal.phase === 'blocked' ? goal.blockedReason?.message?.slice(0, 200).replace(/\n/g, ' ') : undefined;
  return (
    <div data-goal-bar title={title} className="glass-surface rounded-xl flex items-center gap-2.5 min-w-0 h-9 px-3 py-1 overflow-hidden">
      <span className="inline-flex shrink-0 text-content-disabled">
        <GoalGlyph />
      </span>
      <span className="shrink-0 text-[13px] leading-6 font-medium text-content-primary">
        {t(lang, PHASE_LABEL_KEYS[goal.phase as 'active' | 'paused' | 'blocked'])}
      </span>
      <span className="flex-1 min-w-0 overflow-hidden text-[13px] leading-5 text-content-secondary whitespace-nowrap text-ellipsis">
        {goal.objective}
      </span>
      {/* 常驻轮次分数：roundsStarted/maxGoalRounds（blocked 时显示受阻原因） */}
      {goal.phase === 'blocked' && goal.blockedReason ? (
        <span
          className="min-w-0 max-w-[40%] shrink truncate text-xs text-danger"
          title={goal.blockedReason.message}
        >
          {goal.blockedReason.message}
        </span>
      ) : (
        <span className="shrink-0 text-xs text-content-disabled whitespace-nowrap tabular-nums">
          {goal.roundsStarted}/{goal.maxGoalRounds}
        </span>
      )}
      {actionError !== null && (
        <span role="alert" className="min-w-0 max-w-[40%] shrink truncate text-xs text-danger">
          {actionError.message} ({actionError.code})
        </span>
      )}
      <div className="flex items-center gap-2 shrink-0">
        {goal.phase === 'active' && (
          <button
            type="button"
            title={t(lang, 'goal:action.pause')}
            aria-label={t(lang, 'goal:action.pause')}
            className={iconBtn}
            disabled={pending}
            onClick={() => runAction('pause', onPause)}
          >
            <PauseIcon />
          </button>
        )}
        {goal.phase === 'paused' && (
          <button
            type="button"
            title={t(lang, 'goal:action.resume')}
            aria-label={t(lang, 'goal:action.resume')}
            className={iconBtn}
            disabled={pending}
            onClick={() => runAction('resume', onResume)}
          >
            <PlayIcon />
          </button>
        )}
        <button
          type="button"
          title={t(lang, 'goal:action.edit')}
          aria-label={t(lang, 'goal:action.edit')}
          className={iconBtn}
          disabled={pending}
          onClick={() => {
            setDraft(goal.objective);
            setEditing(true);
          }}
        >
          <EditIcon />
        </button>
        <button
          type="button"
          title={t(lang, 'goal:action.clear')}
          aria-label={t(lang, 'goal:action.clear')}
          className={iconBtn}
          disabled={pending}
          onClick={handleClear}
        >
          <TrashIcon />
        </button>
      </div>
    </div>
  );
}
