/**
 * @fileoverview Cron 定时任务管理 Tab 组件
 *
 * 设置弹窗（SetupForm）中的第三个 Tab，用于管理 / 创建 cron 定时任务：
 * - 顶部状态条：调度器运行状态 + 任务统计
 * - 任务列表：折叠卡片展示名称 / cron 表达式 / 启用状态 / 运行记录
 * - 操作：新建 / 编辑 / 删除 / 启用切换 / 手动运行，全部即时生效
 *
 * 数据流（与设置弹窗底部「保存」按钮解耦）：
 * - 挂载时并行加载调度器状态 + 任务列表（独立 loading，不阻塞其他 Tab）
 * - 每次操作完成后静默刷新列表（不置 loading、不重置展开状态，避免 UI 抖动）
 * - 不做定时轮询：调度器每 30s tick 更新磁盘注册表，弹窗内无需主动跟随
 *
 * @module CronTab
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { t, type UiLanguage } from '../i18n';
import { cronApi, type CronJob, type CronSchedulerStatus, type CronSessionSummary, type CronChannelSession } from '../api';
import { GlassDropdown } from './GlassDropdown';
import ToggleSwitch from './ToggleSwitch';
import type { WebWorkspaceItem } from '../types/protocol';

/** 输入框通用样式（聚焦散光，与 SetupForm 保持一致） */
const inputClass = 'w-full px-3 py-2 rounded-md bg-surface-card-alt border border-border-light text-content-primary text-sm focus:outline-none focus:border-primary focus:shadow-glow transition-all duration-200';
/** 字段标签样式 */
const labelClass = 'text-xs font-medium text-content-secondary mb-1.5';

/** 前端 cron 表达式基础校验（5 字段 + 合法字符；后端 croniter 严格校验兜底） */
function isValidCron(s: string): boolean {
  const parts = s.trim().split(/\s+/);
  if (parts.length !== 5) return false;
  return parts.every((p) => /^[0-9*?,/\-#Lw]+$/i.test(p));
}

/** 时间字符串截断显示（本地时间 ISO，如 2026-08-10T14:30:00 → 2026-08-10 14:30） */
function formatTime(s: string | null | undefined): string {
  if (!s) return '';
  return s.length > 16 ? s.slice(0, 16).replace('T', ' ') : s;
}

/** 任务表单草稿（新建 / 编辑共用） */
interface JobDraft {
  name: string;
  schedule: string;
  prompt: string;
  recurring: boolean;
  enabled: boolean;
  delete_after_run: boolean;
  deliver_to: string[];
  session_id: string;
  cwd: string;
}

/** 空表单草稿初始值 */
function makeEmptyDraft(): JobDraft {
  return {
    name: '',
    schedule: '',
    prompt: '',
    recurring: true,
    enabled: true,
    delete_after_run: false,
    deliver_to: [],
    session_id: '',
    cwd: '',
  };
}

/** 从任务填充表单草稿（编辑用） */
function draftFromJob(job: CronJob): JobDraft {
  return {
    name: job.name,
    schedule: job.schedule,
    prompt: job.prompt,
    recurring: job.recurring,
    enabled: job.enabled,
    delete_after_run: job.delete_after_run,
    deliver_to: [...job.deliver_to],
    session_id: job.session_id ?? '',
    cwd: job.cwd ?? '',
  };
}

/**
 * CronTab 组件属性接口
 */
interface CronTabProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 注册的工作区列表（任务执行目录选择 + 会话按目录过滤） */
  workspaces: WebWorkspaceItem[];
}

/**
 * Cron 定时任务管理 Tab 组件
 *
 * @param props - 组件属性
 * @returns Tab JSX
 */
export function CronTab({ lang, workspaces }: CronTabProps) {
  /** 任务列表 */
  const [jobs, setJobs] = useState<CronJob[]>([]);
  /** 调度器状态 */
  const [status, setStatus] = useState<CronSchedulerStatus | null>(null);
  /** 首次加载中 */
  const [loading, setLoading] = useState(true);
  /** 首次加载错误 */
  const [loadError, setLoadError] = useState<string | null>(null);
  /** 操作错误（增删改/运行/切换的即时错误） */
  const [opError, setOpError] = useState<string | null>(null);
  /** 展开的任务卡片 id */
  const [expandedId, setExpandedId] = useState<string | null>(null);
  /** 手动运行中的任务 id 集合（来自后端 running_jobs，跨弹窗重开保持） */
  const [runningIds, setRunningIds] = useState<string[]>([]);
  /** 新建/编辑表单是否可见 */
  const [formVisible, setFormVisible] = useState(false);
  /** 编辑中的任务（null = 新建模式） */
  const [editingJob, setEditingJob] = useState<CronJob | null>(null);
  /** 表单草稿 */
  const [draft, setDraft] = useState<JobDraft>(makeEmptyDraft);
  /** 表单提交错误 */
  const [formError, setFormError] = useState<string | null>(null);
  /** 项目会话列表（session_id dropdown 数据源，按任务执行目录过滤） */
  const [sessions, setSessions] = useState<CronSessionSummary[]>([]);
  /** 各渠道活跃会话（deliver_to dropdown 数据源） */
  const [channelSessions, setChannelSessions] = useState<Record<string, CronChannelSession[]>>({});

  /** 加载指定目录的项目会话（cronApi.sessions 按 cwd 过滤） */
  const loadSessions = useCallback(async (cwd?: string) => {
    try {
      const res = await cronApi.sessions(cwd || undefined);
      setSessions(res.sessions);
    } catch {
      setSessions([]);
    }
  }, []);

  /** 加载调度器状态 + 任务列表
   *
   * @param silent 静默模式（操作后刷新用）：不置 loading、不覆盖 loadError，
   *   避免列表闪烁 / 错误闪现；仅挂载时的首次加载显示加载态。
   */
  const loadAll = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [s, j] = await Promise.all([cronApi.status(), cronApi.list()]);
      setStatus(s);
      setJobs(j.jobs);
      setRunningIds(j.running_jobs ?? []);
      setLoadError(null);
    } catch (err) {
      if (!silent) {
        setLoadError(err instanceof Error ? err.message : String(err));
      }
      // 静默刷新失败不打扰用户（下次操作会再次刷新）
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  // 挂载时加载一次；卸载后不再设置状态
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [s, j, chSess] = await Promise.all([
          cronApi.status(),
          cronApi.list(),
          cronApi.channelSessions().catch(() => ({ channels: {} })),
        ]);
        if (cancelled) return;
        setStatus(s);
        setJobs(j.jobs);
        setRunningIds(j.running_jobs ?? []);
        setChannelSessions(chSess.channels);
      } catch (err) {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // workspaces 异步到达（web_workspaces 事件晚于 ready）：默认目录就绪后
  // 加载会话下拉；默认目录变化（设置中修改）时同样刷新
  useEffect(() => {
    const defaultCwd = workspaces.find((w) => w.is_default)?.path;
    if (defaultCwd) loadSessions(defaultCwd);
  }, [workspaces, loadSessions]);

  /** 切换任务启用状态（即时 API + 静默刷新） */
  const handleToggleEnabled = useCallback(async (job: CronJob, enabled: boolean) => {
    setOpError(null);
    try {
      await cronApi.update(job.id, { enabled });
      await loadAll(true);
    } catch (err) {
      setOpError(err instanceof Error ? err.message : String(err));
    }
  }, [loadAll]);

  /** 手动触发运行（即时 API + 静默刷新；运行后 last_run/last_status 更新）
   *
   * 运行中状态由后端 running_jobs 持久化：点击后本地立即加入（即时禁用），
   * 请求完成后 loadAll 刷新（后端已移除该任务）；退出设置弹窗再进入时
   * 从后端重新加载，运行中的任务按钮保持禁用，避免重复触发。
   */
  const handleRun = useCallback(async (job: CronJob) => {
    setOpError(null);
    setRunningIds((prev) => (prev.includes(job.id) ? prev : [...prev, job.id]));
    try {
      const result = await cronApi.run(job.id);
      if (result.status !== 'success') {
        const detail = result.stderr ? `: ${result.stderr.slice(0, 200)}` : '';
        setOpError(`${t(lang, 'cronRunFailed')} (${result.status})${detail}`);
      }
      await loadAll(true);
    } catch (err) {
      setOpError(err instanceof Error ? err.message : String(err));
      // 请求失败（如连接断开）时移除本地标记，允许重试
      setRunningIds((prev) => prev.filter((id) => id !== job.id));
    }
  }, [lang, loadAll]);

  /** 删除任务（确认后即时 API + 静默刷新） */
  const handleDelete = useCallback(async (job: CronJob) => {
    if (!window.confirm(t(lang, 'cronDeleteConfirm'))) return;
    setOpError(null);
    try {
      await cronApi.remove(job.id);
      setExpandedId((cur) => (cur === job.id ? null : cur));
      await loadAll(true);
    } catch (err) {
      setOpError(err instanceof Error ? err.message : String(err));
    }
  }, [lang, loadAll]);

  /** 打开新建表单（执行目录必选：初始预选默认工作区，用户可改） */
  const openCreate = useCallback(() => {
    setEditingJob(null);
    const defaultCwd = workspaces.find((w) => w.is_default)?.path ?? '';
    setDraft({ ...makeEmptyDraft(), cwd: defaultCwd });
    setFormError(null);
    setFormVisible(true);
    if (defaultCwd) loadSessions(defaultCwd);
  }, [workspaces, loadSessions]);

  /** 打开编辑表单（预填任务字段） */
  const openEdit = useCallback((job: CronJob) => {
    setEditingJob(job);
    setDraft(draftFromJob(job));
    setFormError(null);
    setFormVisible(true);
  }, []);

  /** 提交表单（新建走 POST，编辑走 PATCH；提交后静默刷新） */
  const handleSubmit = useCallback(async () => {
    setFormError(null);
    // 前端基础校验（后端严格校验兜底）
    if (!isValidCron(draft.schedule)) {
      setFormError(t(lang, 'cronInvalidSchedule'));
      return;
    }
    if (!draft.prompt.trim()) {
      setFormError(t(lang, 'cronPromptRequired'));
      return;
    }
    // 执行目录必选（任务运行的工作区锚点，缺省会落到不可预期的目录）
    if (!draft.cwd.trim()) {
      setFormError(t(lang, 'cronFieldCwdRequired'));
      return;
    }
    const deliverTo = draft.deliver_to;
    // 指定会话（可选）：编辑时清空传空串显式清除（后端据此移除 session_id）
    const sessionId = draft.session_id.trim();
    // 任务执行目录（可选；空 = 后端默认工作区）
    const cwd = draft.cwd.trim() || undefined;
    try {
      if (editingJob) {
        await cronApi.update(editingJob.id, {
          name: draft.name.trim() || undefined,
          schedule: draft.schedule.trim(),
          prompt: draft.prompt.trim(),
          recurring: draft.recurring,
          enabled: draft.enabled,
          delete_after_run: draft.delete_after_run,
          deliver_to: deliverTo,
          session_id: sessionId,
          cwd,
        });
      } else {
        await cronApi.create({
          name: draft.name.trim() || undefined,
          schedule: draft.schedule.trim(),
          prompt: draft.prompt.trim(),
          recurring: draft.recurring,
          enabled: draft.enabled,
          delete_after_run: draft.delete_after_run,
          deliver_to: deliverTo,
          session_id: sessionId || undefined,
          cwd,
        });
      }
      setFormVisible(false);
      setEditingJob(null);
      await loadAll(true);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err));
    }
  }, [lang, editingJob, draft, loadAll]);

  /** 调度器状态文本与指示灯 */
  const schedulerRunning = status?.running ?? false;
  const statusText = schedulerRunning
    ? t(lang, 'cronSchedulerRunning')
    : t(lang, 'cronSchedulerStopped');

  return (
    <div className="space-y-4">
      {/* 调度器状态条 */}
      <div className="flex items-center justify-between gap-2 px-3 py-2.5 rounded-lg border border-border-light bg-surface-card-alt">
        <div className="flex items-center gap-2">
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${schedulerRunning ? 'bg-success' : 'bg-content-disabled'}`} />
          <span className={`text-sm ${schedulerRunning ? 'text-success' : 'text-content-secondary'}`}>{statusText}</span>
        </div>
        {status && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-surface-hover text-content-secondary font-medium tabular-nums shrink-0">
            {t(lang, 'cronJobsCount')
              .replace('{enabled}', String(status.enabled_jobs))
              .replace('{total}', String(status.total_jobs))}
          </span>
        )}
      </div>

      {/* 操作错误 */}
      {opError && <div className="text-xs text-danger">{opError}</div>}

      {/* 任务列表 */}
      {loading ? (
        <div className="flex items-center justify-center py-8 text-sm text-content-disabled">
          <svg className="w-4 h-4 animate-spin mr-2" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeOpacity="0.4" />
            <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
          {t(lang, 'setupFormSaving')}
        </div>
      ) : loadError ? (
        <div className="space-y-2">
          <div className="text-sm text-danger">{t(lang, 'setupFormLoadFailed')}: {loadError}</div>
          <button
            onClick={() => loadAll(false)}
            className="px-3 py-1.5 rounded-md text-xs text-primary border border-primary/30 hover:bg-primary/10 transition-colors cursor-pointer"
          >
            {t(lang, 'cronLoadRetry')}
          </button>
        </div>
      ) : jobs.length === 0 ? (
        <div className="text-sm text-content-disabled italic py-4 text-center">{t(lang, 'cronJobsNone')}</div>
      ) : (
        <div className="space-y-1.5">
          {jobs.map((job) => (
            <CronJobCard
              key={job.id}
              lang={lang}
              job={job}
              workspaces={workspaces}
              expanded={expandedId === job.id}
              onToggleExpand={() => setExpandedId((cur) => (cur === job.id ? null : job.id))}
              running={runningIds.includes(job.id)}
              onToggleEnabled={(v) => handleToggleEnabled(job, v)}
              onRun={() => handleRun(job)}
              onEdit={() => openEdit(job)}
              onDelete={() => handleDelete(job)}
            />
          ))}
        </div>
      )}

      {/* 新建 / 编辑表单 */}
      {formVisible ? (
        <div className="rounded-lg border border-border-light p-4 space-y-3 bg-surface-card-alt/50">
          <div className="text-sm font-medium text-content-primary">
            {editingJob ? t(lang, 'cronJobEdit') : t(lang, 'cronJobAdd')}
          </div>

          {/* 名称（可选） */}
          <div>
            <div className={labelClass}>{t(lang, 'cronFieldName')}</div>
            <input
              type="text"
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              className={inputClass}
              placeholder="daily-report"
            />
          </div>

          {/* cron 表达式 */}
          <div>
            <div className={labelClass}>{t(lang, 'cronFieldSchedule')}</div>
            <input
              type="text"
              value={draft.schedule}
              onChange={(e) => setDraft({ ...draft, schedule: e.target.value })}
              className={`${inputClass} font-mono`}
              placeholder="0 9 * * *"
            />
            <div className="text-[11px] text-content-disabled mt-1">{t(lang, 'cronFieldScheduleHint')}</div>
          </div>

          {/* 提示词 */}
          <div>
            <div className={labelClass}>{t(lang, 'cronFieldPrompt')}</div>
            <textarea
              value={draft.prompt}
              onChange={(e) => setDraft({ ...draft, prompt: e.target.value })}
              className={`${inputClass} resize-none`}
              rows={3}
            />
            <div className="text-[11px] text-content-disabled mt-1">{t(lang, 'cronFieldPromptHint')}</div>
          </div>

          {/* 执行目录（必选）：任务运行的目录空间，会话下拉随其过滤；
              直接显示所选目录，无"跟随默认工作区"选项 */}
          <div>
            <div className={labelClass}>{t(lang, 'cronFieldCwd')}</div>
            <GlassDropdown
              value={draft.cwd}
              placeholder={t(lang, 'cronFieldCwdPlaceholder')}
              options={workspaces.map((w) => ({
                value: w.path,
                label: w.is_default
                  ? `${w.name} · ${t(lang, 'workspace_default_badge')}`
                  : w.name,
              }))}
              onChange={(v) => {
                setDraft({ ...draft, cwd: v, session_id: '' });
                loadSessions(v || undefined);
              }}
            />
            <div className="text-[11px] text-content-disabled mt-1">{t(lang, 'cronFieldCwdHint')}</div>
          </div>

          {/* 重复执行 / 启用 / 执行后自动删除 */}
          <div className="space-y-2.5 pt-1">
            <BoolFieldRow lang={lang} labelKey="cronFieldRecurring" checked={draft.recurring} onChange={(v) => setDraft({ ...draft, recurring: v })} />
            <BoolFieldRow lang={lang} labelKey="cronFieldEnabled" checked={draft.enabled} onChange={(v) => setDraft({ ...draft, enabled: v })} />
            <BoolFieldRow lang={lang} labelKey="cronFieldDeleteAfterRun" checked={draft.delete_after_run} onChange={(v) => setDraft({ ...draft, delete_after_run: v })} />
          </div>

          {/* 投递目标（渠道会话多选） */}
          <div>
            <div className={labelClass}>{t(lang, 'cronFieldDeliverTo')}</div>
            <DeliverToPicker
              lang={lang}
              channels={channelSessions}
              value={draft.deliver_to}
              onChange={(v) => setDraft({ ...draft, deliver_to: v })}
            />
            <div className="text-[11px] text-content-disabled mt-1">{t(lang, 'cronFieldDeliverToHint')}</div>
          </div>

          {/* 指定会话（可选）：项目会话下拉（随执行目录过滤） */}
          <div>
            <div className={labelClass}>{t(lang, 'cronFieldSession')}</div>
            <GlassDropdown
              value={draft.session_id}
              options={[
                { value: '', label: t(lang, 'cronSessionNone') },
                ...sessions.map((s) => ({
                  value: s.session_id,
                  label: `${s.session_id} · ${(s.summary || '').slice(0, 40)}`,
                })),
              ]}
              onChange={(v) => setDraft({ ...draft, session_id: v })}
            />
            <div className="text-[11px] text-content-disabled mt-1">{t(lang, 'cronFieldSessionHint')}</div>
          </div>

          {/* 表单错误 */}
          {formError && <div className="text-xs text-danger">{formError}</div>}

          {/* 操作按钮 */}
          <div className="flex items-center gap-2 pt-1">
            <button
              onClick={handleSubmit}
              className="px-4 py-2 text-sm text-white bg-primary hover:bg-primary-hover rounded-lg transition-colors cursor-pointer"
            >
              {t(lang, 'cronJobSave')}
            </button>
            <button
              onClick={() => { setFormVisible(false); setEditingJob(null); setFormError(null); }}
              className="px-4 py-2 text-sm text-content-secondary hover:bg-surface-hover rounded-lg transition-colors cursor-pointer border border-border-light"
            >
              {t(lang, 'cronJobCancel')}
            </button>
          </div>
        </div>
      ) : (
        <button
          onClick={openCreate}
          className="px-3 py-1.5 rounded-md text-sm border border-border-light text-content-secondary hover:bg-surface-hover transition-colors cursor-pointer"
        >
          + {t(lang, 'cronJobAdd')}
        </button>
      )}
    </div>
  );
}

// ===== 子组件 =====

/** 单个任务卡片属性 */
interface CronJobCardProps {
  lang: UiLanguage;
  job: CronJob;
  /** 注册的工作区列表（非默认目录徽标展示） */
  workspaces: WebWorkspaceItem[];
  /** 是否展开 */
  expanded: boolean;
  /** 展开/收起回调 */
  onToggleExpand: () => void;
  /** 该任务是否正在手动运行（按钮显示 spinner） */
  running: boolean;
  /** 启用状态切换 */
  onToggleEnabled: (v: boolean) => void;
  /** 手动运行 */
  onRun: () => void;
  /** 编辑 */
  onEdit: () => void;
  /** 删除 */
  onDelete: () => void;
}

/** 单个任务折叠卡片：折叠头显示名称/表达式/标签，展开显示详情与操作 */
function CronJobCard({ lang, job, workspaces, expanded, onToggleExpand, running, onToggleEnabled, onRun, onEdit, onDelete }: CronJobCardProps) {
  const enabled = job.enabled;
  /** 任务执行目录是否非默认（多目录空间下显示目录徽标） */
  const defaultCwd = workspaces.find((w) => w.is_default)?.path;
  const cwdName = job.cwd
    ? (workspaces.find((w) => w.path === job.cwd)?.name ?? job.cwd.split(/[\\/]/).filter(Boolean).pop() ?? job.cwd)
    : '';
  const lastStatusLabel = job.last_status
    ? ({
        success: t(lang, 'cronStatusSuccess'),
        failed: t(lang, 'cronStatusFailed'),
        timeout: t(lang, 'cronStatusTimeout'),
        error: t(lang, 'cronStatusError'),
      })[job.last_status] ?? job.last_status
    : '';
  const lastRunText = job.last_run ? formatTime(job.last_run) : t(lang, 'cronNever');
  const nextRunText = job.next_run ? formatTime(job.next_run) : '-';

  return (
    <div className={`rounded-lg border overflow-hidden transition-colors ${enabled ? 'border-border-light' : 'border-border-light opacity-80'}`}>
      {/* 折叠头：指示灯 + 名称 + 表达式 + 类型标签 + 箭头 */}
      <button
        onClick={onToggleExpand}
        className="w-full flex items-center gap-2 px-3 py-2 text-sm bg-surface-card-alt hover:bg-surface-hover transition-colors cursor-pointer"
      >
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${enabled ? 'bg-primary' : 'bg-content-disabled/40'}`} />
        <span className={`flex-1 text-left truncate ${enabled ? 'text-content-primary' : 'text-content-disabled'}`}>
          {job.name}
        </span>
        <span className="font-mono text-[11px] text-content-secondary bg-surface-hover px-1.5 py-0.5 rounded shrink-0">{job.schedule}</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary shrink-0 font-medium">
          {job.recurring ? t(lang, 'cronRecurringTag') : t(lang, 'cronOneShotTag')}
        </span>
        {job.session_id && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-hover text-content-secondary shrink-0 font-mono max-w-[120px] truncate" title={job.session_id}>
            {t(lang, 'cronSessionTag')}: {job.session_id.slice(0, 8)}
          </span>
        )}
        {job.cwd && job.cwd !== defaultCwd && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-hover text-content-secondary shrink-0 max-w-[110px] truncate" title={job.cwd}>
            {cwdName}
          </span>
        )}
        <svg className={`w-3 h-3 shrink-0 transition-transform text-content-disabled ${expanded ? 'rotate-90' : ''}`} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M4.5 3L7.5 6L4.5 9" /></svg>
      </button>

      {/* 展开区：详情 + 操作 */}
      {expanded && (
        <div className="px-3 py-2.5 border-t border-border-light space-y-2.5 bg-surface-card">
          {/* 提示词预览 */}
          <div>
            <div className="text-[11px] text-content-disabled mb-1">{t(lang, 'cronFieldPrompt')}</div>
            <div className="text-xs text-content-secondary break-words leading-relaxed">{job.prompt}</div>
          </div>

          {/* 运行记录 */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-content-secondary">
            <span>
              <span className="text-content-disabled">{t(lang, 'cronLastRun')}:</span>{' '}
              {lastRunText}
              {lastStatusLabel && (
                <span className={`ml-1.5 ${job.last_status === 'success' ? 'text-success' : 'text-danger'}`}>{lastStatusLabel}</span>
              )}
            </span>
            <span>
              <span className="text-content-disabled">{t(lang, 'cronNextRun')}:</span> {nextRunText}
            </span>
            {job.consecutive_errors > 0 && (
              <span className="text-warning">{t(lang, 'cronErrors').replace('{n}', String(job.consecutive_errors))}</span>
            )}
          </div>

          {/* 投递目标 */}
          <div className="text-xs text-content-secondary">
            <span className="text-content-disabled">{t(lang, 'cronFieldDeliverTo')}:</span>{' '}
            {job.deliver_to.length > 0 ? job.deliver_to.join(', ') : t(lang, 'cronEmptyDeliverTo')}
          </div>

          {/* 操作行：启用开关 + 运行 / 编辑 / 删除 */}
          <div className="flex items-center gap-3 pt-1 border-t border-border-light">
            <ToggleSwitch
              checked={enabled}
              onChange={(v) => onToggleEnabled(v)}
              disabled={running}
              label={t(lang, 'cronFieldEnabled')}
              title={t(lang, 'cronFieldEnabled')}
            />

            <button
              onClick={onRun}
              disabled={running}
              className="px-2.5 py-1 rounded-md text-xs text-primary border border-primary/30 hover:bg-primary/10 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1.5"
            >
              {running && (
                <svg className="w-3 h-3 animate-spin" viewBox="0 0 16 16" fill="none">
                  <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeOpacity="0.4" />
                  <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                </svg>
              )}
              {running ? t(lang, 'cronJobRunning') : t(lang, 'cronJobRun')}
            </button>

            <button
              onClick={onEdit}
              disabled={running}
              className="px-2.5 py-1 rounded-md text-xs text-content-secondary border border-border-light hover:bg-surface-hover transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {t(lang, 'cronJobEdit')}
            </button>

            <button
              onClick={onDelete}
              disabled={running}
              className="px-2.5 py-1 rounded-md text-xs text-danger border border-danger/30 hover:bg-danger/10 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {t(lang, 'cronJobDelete')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** 布尔字段行（开关，统一使用共享 ToggleSwitch） */
function BoolFieldRow({ lang, labelKey, checked, onChange }: {
  lang: UiLanguage; labelKey: string; checked: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-content-secondary">{t(lang, labelKey)}</span>
      <ToggleSwitch checked={checked} onChange={onChange} label={t(lang, labelKey)} />
    </div>
  );
}

// ===== 投递目标选择器（渠道会话多选 chips + 下拉） =====

/** 投递目标选择器属性 */
interface DeliverToPickerProps {
  lang: UiLanguage;
  /** 各渠道活跃会话 {channel: [会话]} */
  channels: Record<string, CronChannelSession[]>;
  /** 已选投递目标（channel:chat_id 格式数组） */
  value: string[];
  /** 变更回调 */
  onChange: (v: string[]) => void;
}

/** 渠道会话多选选择器：下拉勾选 + 已选 chips 展示
 *
 * 选项值为 `channel:chat_id`（与后端 parse_deliver_targets 格式一致）。
 * 渠道未启用或无会话时显示空态提示（不提供手动输入，保证提交的 ID 存在）。
 */
function DeliverToPicker({ lang, channels, value, onChange }: DeliverToPickerProps) {
  const [open, setOpen] = useState(false);
  const [panelPos, setPanelPos] = useState<{ top: number; left: number; width: number } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const channelNames = Object.keys(channels);
  const totalCount = channelNames.reduce((n, c) => n + (channels[c]?.length ?? 0), 0);

  // 面板位置：基于容器 rect 计算（Portal 到 body 后 fixed 定位，避免被父级
  // overflow-y-auto 容器裁剪；与 GlassDropdown 同方案）
  const updatePanelPosition = useCallback(() => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    setPanelPos({ top: rect.bottom + 4, left: rect.left, width: rect.width });
  }, []);

  // 打开时立即定位 + 窗口 resize 时更新
  useLayoutEffect(() => {
    if (!open) return;
    updatePanelPosition();
    const handleResize = () => updatePanelPosition();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [open, updatePanelPosition]);

  // 滚动时更新位置；触发器移出视口则关闭
  useEffect(() => {
    if (!open) return;
    const handleScroll = () => {
      if (!containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      if (rect.bottom < 0 || rect.top > window.innerHeight) {
        setOpen(false);
        return;
      }
      updatePanelPosition();
    };
    window.addEventListener('scroll', handleScroll, true);
    return () => window.removeEventListener('scroll', handleScroll, true);
  }, [open, updatePanelPosition]);

  // 点击外部关闭：容器和 Portal 面板都视为内部——面板渲染在 body 下，
  // 不检查 panelRef 的话 mousedown 会先卸载面板、吞掉选项的 click 事件
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      if (containerRef.current?.contains(target)) return;
      if (panelRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  /** 切换投递目标（勾选加入 / 取消移除） */
  const toggleTarget = useCallback((target: string) => {
    onChange(value.includes(target)
      ? value.filter((v) => v !== target)
      : [...value, target]);
  }, [value, onChange]);

  /** 移除单个投递目标 */
  const removeTarget = useCallback((target: string) => {
    onChange(value.filter((v) => v !== target));
  }, [value, onChange]);

  return (
    <div ref={containerRef} className="relative">
      {/* 已选 chips + 展开按钮（聚焦/展开态与 GlassDropdown 触发器一致：主色边框 + 散光） */}
      <div className={`flex flex-wrap items-center gap-1.5 px-2 py-1.5 rounded-md bg-surface-card-alt border border-border-light min-h-[38px] transition-all duration-200 ${open ? 'border-primary shadow-glow' : 'focus-within:border-primary focus-within:shadow-glow'}`}>
        {value.length === 0 && (
          <span className="text-sm text-content-disabled px-1">-</span>
        )}
        {value.map((target) => (
          <span key={target} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-primary/10 text-primary text-xs font-mono">
            {target}
            <button
              onClick={() => removeTarget(target)}
              title={t(lang, 'cronDeliverToRemove')}
              className="text-primary/60 hover:text-danger transition-colors cursor-pointer"
            >
              <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M2 6h8" /></svg>
            </button>
          </span>
        ))}
        <button
          onClick={() => setOpen(!open)}
          disabled={totalCount === 0}
          className="ml-auto shrink-0 w-6 h-6 flex items-center justify-center rounded text-content-secondary hover:text-content-primary hover:bg-surface-hover transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <svg className={`w-3.5 h-3.5 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M3 4.5l3 3 3-3" /></svg>
        </button>
      </div>

      {/* 下拉面板：Portal 到 body（fixed 定位，父级 overflow-y-auto 无法裁剪） */}
      {open && panelPos && createPortal(
        <div
          ref={panelRef}
          className="fixed z-50 bg-surface-card-alt border border-border-medium rounded-lg max-h-56 overflow-y-auto p-1 animate-fade dropdown-scroll shadow-card dropdown-panel"
          style={{ top: `${panelPos.top}px`, left: `${panelPos.left}px`, width: `${panelPos.width}px` }}
        >
            {totalCount === 0 ? (
              <div className="px-3 py-2 text-xs text-content-disabled">{t(lang, 'cronChannelSessionsNone')}</div>
            ) : (
              (() => {
                // 渲染计数器：空组不占分组序号，保证"首组顶部无分割线"按可见顺序生效
                let rendered = 0;
                return channelNames.map((name) => {
                  const list = channels[name] ?? [];
                  if (list.length === 0) return null;
                  const groupIdx = rendered++;
                  return (
                  <div key={name}>
                    {/* 渠道分组标题：首组顶部无分割线，后续组与上一组隔开 */}
                    <div className={`px-3 py-1 text-[10px] text-content-disabled font-semibold uppercase tracking-widest border-b border-border-light mb-1 ${groupIdx > 0 ? 'mt-1 border-t' : ''}`}>
                      {t(lang, name === 'feishu' ? 'setupChannelFeishu' : name === 'weixin' ? 'setupChannelWeixin' : 'setupChannelQQ')}
                    </div>
                    {list.map((s) => {
                      const target = `${name}:${s.chat_id}`;
                      const checked = value.includes(target);
                      return (
                        <button
                          key={target}
                          onClick={() => toggleTarget(target)}
                          // 显式 rounded-lg：选项包在分组 div 内，不是面板直接子元素，
                          // .dropdown-panel > button 的圆角继承规则匹配不到，必须自带圆角
                          className={`w-full text-left px-3 py-2 rounded-lg border border-transparent hover:border-border-light text-sm transition-colors cursor-pointer flex items-center gap-2 ${checked ? 'text-primary font-medium glass-option-hover' : 'text-content-secondary glass-option-hover'}`}
                        >
                          {/* 多选矩形框（checkbox）：选中填充主色 + 白色对勾 */}
                          <span className={`w-3.5 h-3.5 rounded-sm border shrink-0 flex items-center justify-center ${checked ? 'bg-primary border-primary' : 'border-border-medium'}`}>
                            {checked && (
                              <svg className="w-2.5 h-2.5 text-white" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M2.5 6.5l2.5 2.5 4.5-5" /></svg>
                            )}
                          </span>
                          <span className="flex-1 truncate">
                            <span className="font-mono">{s.chat_id}</span>
                            {s.user_name && s.user_name !== s.chat_id && (
                              <span className="text-content-disabled"> · {s.user_name}</span>
                            )}
                            <span className="text-content-disabled"> · {s.chat_type === 'group' ? '群' : '私聊'}</span>
                          </span>
                        </button>
                      );
                    })}
                  </div>
                  );
                });
              })()
            )}
        </div>,
        document.body,
      )}
    </div>
  );
}
