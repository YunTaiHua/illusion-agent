/**
 * @fileoverview 设置表单 - Agent 管理标签页
 *
 * 固化 agent 管理（原 /agent 斜杠指令功能由本组件承担）：
 * - 分组展示全部 agent：内置 / 全局（用户级）/ 项目级（按工作区分组）
 * - 每行可随时切换 agent 使用的模型（inherit 或 env_N.model_M 引用），
 *   声明多模态能力的模型带徽标；内置 agent 仅允许改模型（固化到
 *   settings.json），用户创建的 agent 可编辑全部配置（直接改 .md）并可删除
 * - "创建 agent" 打开内嵌 AgentWizardForm（项目级创建可选择目标工作区）
 *
 * @module AgentsTab
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { t, type UiLanguage } from '../i18n';
import { GlassDropdown, type DropdownOption } from './GlassDropdown';
import { AgentWizardForm } from './AgentWizardForm';
import type {
  AgentCatalog,
  AgentEntry,
  AgentModelOption,
  WebWorkspaceItem,
} from '../types/protocol';

/** 向导/管理所需的会话能力聚合（App 从 useWebSocketSession 组装） */
export interface AgentsSessionApi {
  /** 代理分组目录（web_agents） */
  catalog: AgentCatalog | null;
  /** 目录拉取中 */
  loading: boolean;
  /** 最近一次操作结果（web_agent_op_result） */
  opResult: { op: string; success: boolean; error?: string } | null;
  /** 向导可选工具 */
  tools: { name: string; description: string }[] | null;
  /** 向导可选模型（name 为引用） */
  models: AgentModelOption[] | null;
  /** LLM 生成草稿 */
  generated: { identifier: string; when_to_use: string; system_prompt: string } | null;
  /** 生成中 */
  generateLoading: boolean;
  /** 生成错误 */
  generateError: string | null;
  /** 向导提交结果 */
  wizardResult: { success: boolean; path?: string; errors?: Record<string, string>; error?: string } | null;
  /** 拉取代理目录 */
  requestAgents: () => void;
  /** 更新代理配置 */
  updateAgent: (fields: Record<string, unknown>) => void;
  /** 删除代理 */
  deleteAgent: (fields: Record<string, unknown>) => void;
  /** 清除操作结果 */
  clearOpResult: () => void;
  /** 向导初始化 */
  wizardInit: () => void;
  /** 请求 LLM 生成 */
  wizardGenerate: (prompt: string, model: string) => void;
  /** 提交向导（创建）；编辑模式由本组件路由到 updateAgent */
  wizardSubmit: (fields: Record<string, unknown>, scope: 'user' | 'project', cwd?: string) => void;
  /** 清空向导状态 */
  clearWizardState: () => void;
}

interface AgentsTabProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 工作区列表（目录空间） */
  workspaces: WebWorkspaceItem[];
  /** 默认工作区（当前会话所在目录） */
  defaultWorkspace?: string;
  /** 会话能力聚合 */
  api: AgentsSessionApi;
  /** 可选模型列表（含 env_N.model_M 引用与多模态声明；由设置表单从 envs 配置构建，挂载即就绪） */
  models: AgentModelOption[];
}

/** 条目行的 scope 徽标文案 */
function scopeBadgeKey(scope: AgentEntry['scope']): string {
  if (scope === 'builtin') return 'agentsTabScopeBuiltin';
  if (scope === 'user') return 'agentsTabScopeUser';
  if (scope === 'plugin') return 'agentsTabScopePlugin';
  return 'agentsTabScopeProject';
}

/**
 * 单个 agent 条目行：名称 + 描述 + 徽标 + 模型下拉 + 操作按钮
 */
function AgentRow({
  lang, entry, models, api, onEdit,
}: {
  lang: UiLanguage;
  entry: AgentEntry;
  models: AgentModelOption[];
  api: AgentsSessionApi;
  onEdit: (entry: AgentEntry) => void;
}) {
  /** 删除二次确认态（点击一次后进入确认，再点执行；超时自动复位） */
  const [confirmDelete, setConfirmDelete] = useState(false);
  /** 模型保存中（等待 web_agent_op_result） */
  const [modelSaving, setModelSaving] = useState(false);
  /** 行内错误（模型保存失败） */
  const [rowError, setRowError] = useState<string | null>(null);

  useEffect(() => {
    if (!api.opResult) return;
    setModelSaving(false);
    if (api.opResult.success) {
      setRowError(null);
    } else if (api.opResult.error) {
      setRowError(api.opResult.error);
    }
  }, [api.opResult]);

  // 删除确认态超时复位（3.5s 未确认自动回到初始态）
  useEffect(() => {
    if (!confirmDelete) return;
    const t = setTimeout(() => setConfirmDelete(false), 3500);
    return () => clearTimeout(t);
  }, [confirmDelete]);

  /** 模型下拉选项 */
  const modelOptions: DropdownOption[] = useMemo(() => {
    const visionSuffix = t(lang, 'agentWizardModelVisionBadge');
    const opts: DropdownOption[] = [{ value: '', label: t(lang, 'agentsTabModelInherit') }];
    for (const m of models) {
      if (m.name === 'inherit') continue;
      opts.push({ value: m.name, label: `${m.label}${m.supports_images ? visionSuffix : ''}` });
    }
    return opts;
  }, [models, lang]);

  /** 可编辑性：内置/插件仅模型可改；用户/项目级可完整编辑 */
  const fullyEditable = entry.source === 'user';

  /** 切换模型（即时生效） */
  const handleModelChange = useCallback((value: string) => {
    setRowError(null);
    setModelSaving(true);
    api.updateAgent({
      name: entry.name,
      source: entry.source,
      base_dir: entry.base_dir ?? '',
      model: value || 'inherit',
    });
  }, [api, entry.name, entry.source, entry.base_dir]);

  /** 删除代理 */
  const handleDelete = useCallback(() => {
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    setConfirmDelete(false);
    api.deleteAgent({ name: entry.name, base_dir: entry.base_dir ?? '' });
  }, [api, confirmDelete, entry.name, entry.base_dir]);

  return (
    <div
      className="px-3 py-2.5 rounded-lg border border-border-light bg-surface-card-alt/60 hover:bg-surface-hover/50 transition-colors"
      title={entry.description || undefined}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-content-primary truncate">{entry.name}</span>
            <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-primary-light text-primary shrink-0">
              {t(lang, scopeBadgeKey(entry.scope))}
            </span>
            {entry.goal_specific && (
              <span
                title={t(lang, 'agentsTabGoalSpecificTip')}
                className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-danger/10 text-danger shrink-0 cursor-help"
              >
                {t(lang, 'agentsTabGoalSpecific')}
              </span>
            )}
            {entry.background && (
              <span
                title={t(lang, 'agentsTabBackgroundTip')}
                className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-surface-hover text-content-secondary shrink-0 cursor-help"
              >
                {t(lang, 'agentsTabBackground')}
              </span>
            )}
            {entry.supports_images === true && (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-primary-light text-primary shrink-0">
                {t(lang, 'agentsTabVisionBadge')}
              </span>
            )}
          </div>
          {entry.description && (
            <div className="text-xs text-content-secondary mt-0.5 line-clamp-2">{entry.description}</div>
          )}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {fullyEditable && (
            <button
              onClick={() => onEdit(entry)}
              title={t(lang, 'agentsTabEdit')}
              className="w-6 h-6 flex items-center justify-center rounded text-content-secondary hover:text-primary hover:bg-surface-hover transition-colors cursor-pointer"
            >
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="M11.5 2.5l2 2L6 12l-3 1 1-3 7.5-7.5z" />
              </svg>
            </button>
          )}
          {fullyEditable && (
            <button
              onClick={handleDelete}
              title={t(lang, 'agentsTabDelete')}
              className={`w-6 h-6 flex items-center justify-center rounded transition-colors cursor-pointer ${
                confirmDelete
                  ? 'text-white bg-danger hover:bg-danger/80'
                  : 'text-content-secondary hover:text-danger hover:bg-surface-hover'
              }`}
            >
              {confirmDelete ? (
                <span className="text-[9px] font-bold px-0.5">✓</span>
              ) : (
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
                  <path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.7 8h5.6l.7-8" />
                </svg>
              )}
            </button>
          )}
        </div>
      </div>
      {/* 模型切换行 */}
      <div className="mt-2 flex items-center gap-2">
        <span className="text-xs text-content-secondary shrink-0">{t(lang, 'agentsTabModelLabel')}</span>
        <div className="w-56">
          <GlassDropdown
            value={entry.model ?? ''}
            options={modelOptions}
            onChange={handleModelChange}
          />
        </div>
        {modelSaving && (
          <svg className="w-3.5 h-3.5 animate-spin text-primary shrink-0" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeOpacity="0.25" />
            <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        )}
        {entry.model_resolved && (
          <span className="text-[11px] text-content-disabled truncate">{entry.model_resolved}</span>
        )}
      </div>
      {fullyEditable && entry.base_dir && (
        <div className="mt-1 text-[11px] text-content-disabled font-mono truncate" title={entry.base_dir}>
          {entry.base_dir}
        </div>
      )}
      {rowError && (
        <div className="mt-1 text-xs text-danger">{rowError}</div>
      )}
    </div>
  );
}

/**
 * 设置表单 - 子智能体管理标签页组件
 */
export function AgentsTab({ lang, workspaces, defaultWorkspace, api, models }: AgentsTabProps) {
  /** 内嵌向导模式：null 关闭 / 'create' 创建 / AgentEntry 编辑 */
  const [wizardMode, setWizardMode] = useState<'create' | AgentEntry | null>(null);
  /** 已消费的向导成功结果（避免重复关闭） */
  const handledResultRef = useRef<object | null>(null);

  // 挂载时拉取代理目录与模型列表
  useEffect(() => {
    api.requestAgents();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 向导提交成功 → 关闭向导（目录由后端推送刷新）；失败保持打开显示错误
  useEffect(() => {
    const r = api.wizardResult;
    if (!r || !r.success) return;
    if (handledResultRef.current === r) return;
    handledResultRef.current = r;
    api.clearWizardState();
    setWizardMode(null);
  }, [api.wizardResult, api.clearWizardState, api]);

  // 操作结果显示后 4s 自动清理，避免失败横幅常驻（成功无提示不占位）
  useEffect(() => {
    if (!api.opResult) return;
    const t = setTimeout(() => api.clearOpResult(), 4000);
    return () => clearTimeout(t);
  }, [api.opResult, api.clearOpResult]);

  /** 打开创建向导 */
  const openCreate = useCallback(() => {
    api.clearWizardState();
    api.wizardInit();
    setWizardMode('create');
  }, [api]);

  /** 打开编辑向导 */
  const openEdit = useCallback((entry: AgentEntry) => {
    api.clearWizardState();
    api.wizardInit();
    setWizardMode(entry);
  }, [api]);

  /** 关闭向导 */
  const closeWizard = useCallback(() => {
    api.clearWizardState();
    setWizardMode(null);
  }, [api]);

  /** 创建提交 → wizardSubmit；编辑提交 → updateAgent */
  const handleWizardSubmit = useCallback((fields: Record<string, unknown>, scope: 'user' | 'project', cwd?: string) => {
    if (wizardMode && wizardMode !== 'create') {
      api.updateAgent(fields);
      // 更新无 agent_wizard_result 回执，直接关闭（结果经 web_agent_op_result 行内展示）
      api.clearWizardState();
      setWizardMode(null);
      return;
    }
    api.wizardSubmit(fields, scope, cwd);
  }, [api, wizardMode]);

  /** 项目级分组按默认优先排序 */
  const projectGroups = useMemo(() => {
    const groups = api.catalog?.projects ?? [];
    return [...groups].sort((a, b) => Number(b.is_default) - Number(a.is_default));
  }, [api.catalog]);

  const globalGroups: { title: string; entries: AgentEntry[] }[] = useMemo(() => {
    const catalog = api.catalog;
    if (!catalog) return [];
    return [
      { title: t(lang, 'agentsTabGroupBuiltin'), entries: catalog.global.filter((a) => a.scope === 'builtin') },
      { title: t(lang, 'agentsTabGroupUser'), entries: catalog.global.filter((a) => a.scope === 'user') },
      { title: t(lang, 'agentsTabGroupPlugin'), entries: catalog.global.filter((a) => a.scope === 'plugin') },
    ].filter((g) => g.entries.length > 0);
  }, [api.catalog, lang]);

  // 编辑模式向导（内嵌在 Tab 内容流中，无独立滚动容器）
  if (wizardMode && wizardMode !== 'create') {
    return (
      <AgentWizardForm
        lang={lang}
        tools={api.tools}
        models={models}
        generated={null}
        generateLoading={false}
        generateError={null}
        result={null}
        workspaces={workspaces}
        defaultWorkspace={defaultWorkspace}
        initial={wizardMode}
        embedded
        onInit={() => {}}
        onGenerate={() => {}}
        onSubmit={handleWizardSubmit}
        onClose={closeWizard}
      />
    );
  }

  // 创建模式向导（内嵌在 Tab 内容流中，无独立滚动容器）
  if (wizardMode === 'create') {
    return (
      <AgentWizardForm
        lang={lang}
        tools={api.tools}
        models={models}
        generated={api.generated}
        generateLoading={api.generateLoading}
        generateError={api.generateError}
        result={api.wizardResult}
        workspaces={workspaces}
        defaultWorkspace={defaultWorkspace}
        embedded
        onInit={api.wizardInit}
        onGenerate={api.wizardGenerate}
        onSubmit={handleWizardSubmit}
        onClose={closeWizard}
      />
    );
  }

  return (
    <div className="space-y-4">
      {/* 标题行 + 创建按钮 */}
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-medium text-content-primary">{t(lang, 'agentsTabTitle')}</div>
          <div className="text-xs text-content-secondary mt-0.5">{t(lang, 'agentsTabSubtitle')}</div>
        </div>
        <button
          onClick={openCreate}
          className="px-3 py-1.5 text-sm text-white bg-primary hover:bg-primary-hover rounded-lg transition-colors cursor-pointer shrink-0"
        >
          {t(lang, 'agentsTabCreate')}
        </button>
      </div>

      {/* 操作结果提示 */}
      {api.opResult && !api.opResult.success && api.opResult.error && (
        <div className="px-3 py-2 rounded-md bg-danger/10 border border-danger/30 text-xs text-danger">
          {api.opResult.error}
        </div>
      )}

      {/* 加载中 */}
      {api.loading && !api.catalog && (
        <div className="flex items-center justify-center py-10 text-sm text-content-disabled">
          <svg className="w-4 h-4 animate-spin mr-2" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeOpacity="0.4" />
            <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
          {t(lang, 'agentsTabLoading')}
        </div>
      )}

      {/* 全局分组：内置 / 用户级 / 插件 */}
      {globalGroups.map((group) => (
        <div key={group.title}>
          <div className="text-xs font-medium text-content-secondary mb-1.5">{group.title}</div>
          <div className="space-y-1.5">
            {group.entries.map((entry) => (
              <AgentRow key={`${group.title}-${entry.name}`} lang={lang} entry={entry} models={models} api={api} onEdit={openEdit} />
            ))}
          </div>
        </div>
      ))}

      {/* 项目级分组（按工作区） */}
      {projectGroups.map((group) => {
        if (group.agents.length === 0) return null;
        return (
          <div key={group.workspace}>
            <div className="text-xs font-medium text-content-secondary mb-1.5 flex items-center gap-1.5">
              <span>{t(lang, 'agentsTabGroupProject')}</span>
              <span className="font-mono text-content-disabled" title={group.workspace}>{group.name}</span>
              {group.is_default && (
                <span className="px-1.5 py-0.5 rounded text-[10px] bg-primary-light text-primary">{t(lang, 'agentWizardWorkspaceDefault')}</span>
              )}
            </div>
            <div className="space-y-1.5">
              {group.agents.map((entry) => (
                <AgentRow key={`${group.workspace}-${entry.name}`} lang={lang} entry={entry} models={models} api={api} onEdit={openEdit} />
              ))}
            </div>
          </div>
        );
      })}

      {/* 空态（除内置外无任何代理时仍显示内置，故仅在目录缺失时提示） */}
      {!api.catalog && !api.loading && (
        <div className="text-xs text-content-disabled py-6 text-center">{t(lang, 'agentsTabLoadFailed')}</div>
      )}
    </div>
  );
}
