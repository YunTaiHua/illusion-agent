/**
 * @fileoverview Agent 分步创建/编辑表单组件
 *
 * Web 前端的 agent 表单，支持两种挂载方式：
 * - 模态（默认）：独立对话框
 * - 内嵌（embedded）：渲染在设置表单 AgentsTab 内（"创建 agent" / 编辑按钮触发）
 *
 * 采用多 Tab 分步填写（类似 terminal 端），而非单页全填：
 * - 步骤 1 方式：generate / manual 切换 + LLM 生成 UI + 作用域 + 工作区
 * - 步骤 2 基本信息：name / description
 * - 步骤 3 模型与工具：model / tools / effort / permission_mode / max_turns
 * - 步骤 4 提示词：system_prompt + markdown 预览 + 提交
 *
 * 编辑模式（initial 传入已有 agent）：跳过创建方式步骤，name 只读，
 * 提交走更新路径（payload 附带 name/source/base_dir 定位字段）。
 *
 * 视觉风格：主表单为简洁卡片（无玻璃特效），下拉列表使用 GlassDropdown
 * 玻璃拟态质感，输入框聚焦时外圈阴影散光（shadow-glow）。
 *
 * @module AgentWizardForm
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { t, type UiLanguage } from '../i18n';
import { GlassDropdown, type DropdownOption } from './GlassDropdown';
import type { AgentEntry, AgentModelOption, WebWorkspaceItem } from '../types/protocol';

/** 工具项类型（来自 agent_wizard_init_response.tools） */
type ToolOption = { name: string; description: string };
/** 提交结果类型（来自 agent_wizard_result） */
type WizardResult = { success: boolean; path?: string; errors?: Record<string, string>; error?: string };

/** effort 选项值列表 */
const EFFORT_VALUES = ['low', 'medium', 'high', 'xhigh', 'max'];
/** permission_mode 选项值列表 */
const PERMISSION_VALUES = ['default', 'plan', 'full_auto', 'yolo'];
/** 'skip' 选项值（提交时跳过该字段） */
const SKIP_VALUE = '__skip__';
/** 'inherit' 选项值（继承默认） */
const INHERIT_VALUE = 'inherit';

/** 表单内部字段类型 */
interface FormFields {
  /** 写入范围 */
  scope: 'user' | 'project';
  /** 项目级写入的目标工作区（scope=project 时生效） */
  workspace: string;
  /** 创建方式 */
  method: 'generate' | 'manual';
  /** agent 名称（对应后端 name） */
  identifier: string;
  /** 使用时机（对应后端 description） */
  when_to_use: string;
  /** 系统提示词 */
  system_prompt: string;
  /** 默认模型 */
  model: string;
  /** 已选工具列表 */
  tools: string[];
  /** 思考强度（'__skip__' 表示跳过） */
  effort: string;
  /** 权限模式（'__skip__' 表示跳过） */
  permission_mode: string;
  /** 最大轮次（null 表示不设置） */
  max_turns: string;
}

/** 表单初始字段 */
const INITIAL_FIELDS: FormFields = {
  scope: 'project',
  workspace: '',
  method: 'generate',
  identifier: '',
  when_to_use: '',
  system_prompt: '',
  model: INHERIT_VALUE,
  tools: [],
  effort: SKIP_VALUE,
  permission_mode: SKIP_VALUE,
  max_turns: '',
};

/** 从已有 agent 条目构建编辑模式初始字段 */
function fieldsFromEntry(entry: AgentEntry): FormFields {
  const effort = entry.effort != null ? String(entry.effort) : SKIP_VALUE;
  return {
    scope: entry.scope === 'project' ? 'project' : 'user',
    workspace: entry.workspace ?? '',
    method: 'manual',
    identifier: entry.name,
    when_to_use: entry.description ?? '',
    system_prompt: entry.system_prompt ?? '',
    model: entry.model || INHERIT_VALUE,
    tools: entry.tools ?? [],
    effort: effort !== '' ? effort : SKIP_VALUE,
    permission_mode: entry.permission_mode ?? SKIP_VALUE,
    max_turns: entry.max_turns != null ? String(entry.max_turns) : '',
  };
}

/** 表单字段名 → 后端字段名映射（用于清理 submissionErrors） */
const FIELD_TO_BACKEND_KEY: Record<keyof FormFields, string | null> = {
  scope: null,
  workspace: null,
  method: null,
  identifier: 'name',
  when_to_use: 'description',
  system_prompt: 'system_prompt',
  model: 'model',
  tools: 'tools',
  effort: 'effort',
  permission_mode: 'permission_mode',
  max_turns: 'max_turns',
};

/** 分步标签定义 */
const TABS = [
  'agentWizardTabMethod',
  'agentWizardTabBasic',
  'agentWizardTabModelTools',
  'agentWizardTabPrompt',
] as const;

/**
 * AgentWizardForm 组件属性接口
 */
interface AgentWizardFormProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 可选工具列表（来自 agent_wizard_init_response） */
  tools: ToolOption[] | null;
  /** 可选模型列表（来自 agent_wizard_init_response，name 为引用） */
  models: AgentModelOption[] | null;
  /** LLM 生成的草稿（来自 agent_generate_response） */
  generated: GeneratedAgent | null;
  /** 是否正在生成 */
  generateLoading: boolean;
  /** 生成错误文本 */
  generateError: string | null;
  /** 提交结果（来自 agent_wizard_result） */
  result: WizardResult | null;
  /** 工作区列表（目录空间，项目级创建目标） */
  workspaces: WebWorkspaceItem[];
  /** 默认工作区（当前会话所在目录） */
  defaultWorkspace?: string;
  /** 编辑模式：传入已有 agent 条目（用户/项目级） */
  initial?: AgentEntry | null;
  /** 内嵌模式：渲染在设置表单内（非全屏模态） */
  embedded?: boolean;
  /** 请求初始化（拉取工具/模型列表） */
  onInit: () => void;
  /** 请求 LLM 生成草稿 */
  onGenerate: (prompt: string, model: string) => void;
  /** 提交表单（编辑模式时由父组件路由到更新路径） */
  onSubmit: (fields: Record<string, unknown>, scope: 'user' | 'project', cwd?: string) => void;
  /** 关闭表单 */
  onClose: () => void;
}

/** LLM 生成的 agent 草稿类型（来自 agent_generate_response.agent） */
type GeneratedAgent = { identifier: string; when_to_use: string; system_prompt: string };

/**
 * Agent 分步创建/编辑表单组件
 *
 * 显示简洁卡片对话框，通过多 Tab 引导用户分步填写 agent 配置。
 * - 挂载时请求初始化工具/模型列表
 * - 收到生成草稿时自动填充 name/description/system_prompt（用户仍可二次编辑）
 * - 实时校验 name/description/system_prompt 非空
 * - 后端返回 errors 时高亮对应字段
 * - 项目级创建时选择目标工作区（目录空间），提交携带 cwd
 * - 编辑模式跳过创建方式步骤，name 只读，提交附带定位字段
 *
 * @param props - 组件属性
 * @returns 返回表单的 JSX 元素
 */
export function AgentWizardForm({
  lang, tools, models, generated, generateLoading, generateError, result,
  workspaces, defaultWorkspace, initial, embedded,
  onInit, onGenerate, onSubmit, onClose,
}: AgentWizardFormProps) {
  /** 是否编辑模式 */
  const isEdit = Boolean(initial);
  /** 表单字段（编辑模式从条目预填） */
  const [fields, setFields] = useState<FormFields>(() => (
    initial ? fieldsFromEntry(initial) : INITIAL_FIELDS
  ));
  /** generate 模式下的描述文本 */
  const [describeText, setDescribeText] = useState('');
  /** generate 模式下的生成模型 */
  const [generateModel, setGenerateModel] = useState<string>(INHERIT_VALUE);
  /** 后端返回的字段级错误（键为后端字段名 name/description/system_prompt 等） */
  const [submissionErrors, setSubmissionErrors] = useState<Record<string, string>>({});
  /** markdown 预览是否展开 */
  const [previewExpanded, setPreviewExpanded] = useState(false);
  /** 提交中标志（点击提交后等待 agent_wizard_result 期间为 true） */
  const [submitting, setSubmitting] = useState(false);
  /** 当前步骤索引（0-3；编辑模式从 1 开始，跳过创建方式） */
  const [currentStep, setCurrentStep] = useState(() => (initial ? 1 : 0));
  /** 是否已处理过当前 generated（避免重复消费） */
  const lastHandledGeneratedRef = useRef<GeneratedAgent | null>(null);
  /** 是否已处理过当前 result（避免重复消费） */
  const lastHandledResultRef = useRef<WizardResult | null>(null);

  // 默认工作区：当前会话所在目录优先，其次默认工作区
  useEffect(() => {
    if (initial) return;
    const fallback = defaultWorkspace
      ?? workspaces.find((w) => w.is_default)?.path
      ?? workspaces[0]?.path
      ?? '';
    setFields((f) => (f.workspace ? f : { ...f, workspace: fallback }));
  }, [defaultWorkspace, workspaces, initial]);

  // 挂载时请求初始化工具/模型列表
  useEffect(() => {
    onInit();
  }, [onInit]);

  // 收到生成草稿：自动填充 name/description/system_prompt（仅消费一次）
  useEffect(() => {
    if (!generated) return;
    if (generated === lastHandledGeneratedRef.current) return;
    lastHandledGeneratedRef.current = generated;
    setFields((f) => ({
      ...f,
      identifier: generated.identifier,
      when_to_use: generated.when_to_use,
      system_prompt: generated.system_prompt,
    }));
    // LLM 生成完毕后自动进入下一步（基本信息）
    setCurrentStep(1);
  }, [generated]);

  // 收到提交结果：清除 submitting，失败时填充字段级错误（仅消费一次）
  useEffect(() => {
    if (!result) return;
    if (result === lastHandledResultRef.current) return;
    lastHandledResultRef.current = result;
    setSubmitting(false);
    if (!result.success && result.errors) {
      setSubmissionErrors(result.errors);
    }
  }, [result]);

  // Esc 键关闭（内嵌模式不监听，由设置表单统一处理）
  useEffect(() => {
    if (embedded) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose, embedded]);

  /** 更新单个字段，同时清理该字段对应的后端错误高亮 */
  const updateField = useCallback(<K extends keyof FormFields>(key: K, value: FormFields[K]) => {
    setFields((f) => ({ ...f, [key]: value }));
    const backendKey = FIELD_TO_BACKEND_KEY[key];
    if (backendKey) {
      setSubmissionErrors((prev) => {
        if (!prev[backendKey]) return prev;
        const next = { ...prev };
        delete next[backendKey];
        return next;
      });
    }
  }, []);

  /** 切换工具选中状态 */
  const toggleTool = useCallback((toolName: string) => {
    setFields((f) => {
      const has = f.tools.includes(toolName);
      return { ...f, tools: has ? f.tools.filter((t) => t !== toolName) : [...f.tools, toolName] };
    });
  }, []);

  /** 触发 LLM 生成 */
  const handleGenerate = useCallback(() => {
    const s = describeText.trim();
    if (!s || generateLoading) return;
    onGenerate(s, generateModel);
  }, [describeText, generateModel, generateLoading, onGenerate]);

  /** 提交完整表单 */
  const handleSubmit = useCallback(() => {
    setSubmissionErrors({});
    setSubmitting(true);
    const payload: Record<string, unknown> = {
      description: fields.when_to_use.trim(),
      system_prompt: fields.system_prompt,
      model: fields.model || INHERIT_VALUE,
      tools: fields.tools,
    };
    if (fields.effort !== SKIP_VALUE) payload.effort = fields.effort;
    if (fields.permission_mode !== SKIP_VALUE) payload.permission_mode = fields.permission_mode;
    const maxTurnsStr = fields.max_turns.trim();
    if (maxTurnsStr !== '') {
      const parsed = parseInt(maxTurnsStr, 10);
      if (!Number.isNaN(parsed) && parsed > 0) payload.max_turns = parsed;
    }
    if (isEdit && initial) {
      // 更新路径：附带给后端定位代理的键（name 只读不可改名）
      payload.name = initial.name;
      payload.source = initial.source;
      payload.base_dir = initial.base_dir ?? '';
      payload.filename = initial.filename ?? '';
      if (fields.effort === SKIP_VALUE) payload.effort = null;
      if (fields.permission_mode === SKIP_VALUE) payload.permission_mode = null;
      if (maxTurnsStr === '') payload.max_turns = null;
      // 空工具列表 = 不限制工具（tools: null → 删键回退默认），
      // 不能写成 tools: []——那表示"禁止一切工具"
      payload.tools = fields.tools.length > 0 ? fields.tools : null;
      onSubmit(payload, fields.scope, fields.scope === 'project' ? fields.workspace : undefined);
      return;
    }
    payload.name = fields.identifier.trim();
    onSubmit(payload, fields.scope, fields.scope === 'project' ? fields.workspace : undefined);
  }, [fields, isEdit, initial, onSubmit]);

  /** 实时校验：name/description/system_prompt 非空 */
  const validationErrors = useMemo(() => {
    const errs: Record<string, string> = {};
    if (!isEdit && !fields.identifier.trim()) errs.name = t(lang, 'agentWizardNameLabel');
    if (!fields.when_to_use.trim()) errs.description = t(lang, 'agentWizardDescriptionLabel');
    if (!fields.system_prompt.trim()) errs.system_prompt = t(lang, 'agentWizardSystemPromptLabel');
    if (fields.scope === 'project' && !fields.workspace) errs.workspace = t(lang, 'agentWizardWorkspaceLabel');
    return errs;
  }, [fields.identifier, fields.when_to_use, fields.system_prompt, fields.scope, fields.workspace, isEdit, lang]);

  /** 是否可提交（无本地校验错误、未在提交中、未成功） */
  const canSubmit = Object.keys(validationErrors).length === 0 && !submitting && !result?.success;

  /** 生成 markdown 预览文本（frontmatter + system_prompt） */
  const markdownPreview = useMemo(() => {
    const lines: string[] = ['---'];
    if (fields.identifier.trim()) lines.push(`name: ${fields.identifier.trim()}`);
    if (fields.when_to_use.trim()) lines.push(`description: ${fields.when_to_use.trim()}`);
    lines.push(`model: ${fields.model || INHERIT_VALUE}`);
    if (fields.tools.length > 0) lines.push(`tools: [${fields.tools.join(', ')}]`);
    if (fields.effort !== SKIP_VALUE) lines.push(`effort: ${fields.effort}`);
    if (fields.permission_mode !== SKIP_VALUE) lines.push(`permission_mode: ${fields.permission_mode}`);
    const maxTurnsStr = fields.max_turns.trim();
    if (maxTurnsStr !== '') lines.push(`max_turns: ${maxTurnsStr}`);
    lines.push('---', '');
    lines.push(fields.system_prompt || '');
    return lines.join('\n');
  }, [fields]);

  /** 模型选项（含 inherit；声明多模态能力的模型带徽标后缀） */
  const modelOptions: DropdownOption[] = useMemo(() => {
    const visionSuffix = t(lang, 'agentWizardModelVisionBadge');
    const opts: DropdownOption[] = [{ value: INHERIT_VALUE, label: t(lang, 'agentWizardInherit') }];
    for (const m of models ?? []) {
      if (m.name === INHERIT_VALUE) continue;
      const badge = m.supports_images ? visionSuffix : '';
      opts.push({ value: m.name, label: `${m.label}${badge}` });
    }
    return opts;
  }, [models, lang]);

  /** effort 选项（含 inherit/skip） */
  const effortOptions: DropdownOption[] = useMemo(() => {
    const opts: DropdownOption[] = EFFORT_VALUES.map((v) => ({ value: v, label: v }));
    opts.push({ value: INHERIT_VALUE, label: t(lang, 'agentWizardInherit') });
    opts.push({ value: SKIP_VALUE, label: t(lang, 'agentWizardSkip') });
    return opts;
  }, [lang]);

  /** permission_mode 选项（含 skip） */
  const permissionOptions: DropdownOption[] = useMemo(() => {
    const opts: DropdownOption[] = PERMISSION_VALUES.map((v) => ({ value: v, label: v }));
    opts.push({ value: SKIP_VALUE, label: t(lang, 'agentWizardSkip') });
    return opts;
  }, [lang]);

  /** 工作区下拉选项（项目级创建目标） */
  const workspaceOptions: DropdownOption[] = useMemo(() => {
    const opts: DropdownOption[] = [];
    for (const w of workspaces) {
      if (!w.available) continue;
      opts.push({ value: w.path, label: w.is_default ? `${w.name} (${t(lang, 'agentWizardWorkspaceDefault')})` : w.name });
    }
    return opts;
  }, [workspaces, lang]);

  /** 输入框通用样式（含错误高亮 + 聚焦阴影散光） */
  const inputClass = (hasError: boolean): string =>
    `w-full px-3 py-2 rounded-md bg-surface-card-alt border text-content-primary text-sm focus:outline-none transition-all duration-200 ${
      hasError ? 'border-danger' : 'border-border-light focus:border-primary focus:shadow-glow'
    }`;

  /** 多行文本域样式：固定高度不可拉伸（resize-none） */
  const textareaClass = (hasError: boolean): string =>
    `w-full px-3 py-2 rounded-md bg-surface-card-alt border text-content-primary text-sm focus:outline-none ${
      hasError ? 'border-danger' : 'border-border-light focus:border-primary focus:shadow-glow'
    }`;

  /** 字段错误文案（仅显示后端返回的字段级错误；本地校验仅用于禁用提交按钮） */
  const fieldError = (key: string): string | null => submissionErrors[key] ?? null;

  /** 当前步骤是否可进入下一步（步骤校验） */
  const canProceed = useMemo(() => {
    if (currentStep === 0) {
      return fields.scope === 'user' || fields.workspace !== '';
    }
    if (currentStep === 1) {
      // 基本信息：name（编辑模式只读恒有值）和 description 非空
      return (isEdit || fields.identifier.trim() !== '') && fields.when_to_use.trim() !== '';
    }
    if (currentStep === 3) {
      // 提示词：system_prompt 非空
      return fields.system_prompt.trim() !== '';
    }
    return true;
  }, [currentStep, fields.scope, fields.workspace, fields.identifier, fields.when_to_use, fields.system_prompt, isEdit]);

  const handleNext = useCallback(() => {
    if (currentStep < TABS.length - 1) setCurrentStep((s) => s + 1);
  }, [currentStep]);

  const handlePrev = useCallback(() => {
    if (currentStep > (isEdit ? 1 : 0)) setCurrentStep((s) => s - 1);
  }, [currentStep, isEdit]);

  /** 可见的步骤标签（编辑模式隐藏创建方式） */
  const visibleTabs = useMemo(
    () => TABS.filter((_, idx) => !(isEdit && idx === 0)),
    [isEdit],
  );
  /** 可见步骤索引 → 实际步骤索引 */
  const stepOffset = isEdit ? 1 : 0;

  /** 步骤进度文本 */
  const stepText = t(lang, 'agentWizardStepOf')
    .replace('{cur}', String(currentStep - stepOffset + 1))
    .replace('{total}', String(visibleTabs.length));

  const card = (
    <div
      className={`relative bg-surface-card rounded-2xl border border-border-light shadow-card ${
        embedded
          ? 'w-full'
          : 'w-[560px] max-w-[92vw] max-h-[88vh] flex flex-col animate-scale-in modal-origin-center'
      }`}
      onClick={(e) => e.stopPropagation()}
    >
      {/* 标题栏 */}
      <div className="px-6 py-4 border-b border-border-light flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <h3 className="text-lg font-semibold text-content-primary">
            {isEdit ? t(lang, 'agentWizardEditTitle') : t(lang, 'agentWizardTitle')}
          </h3>
          <span className="text-xs text-content-disabled">{stepText}</span>
        </div>
        {!embedded && (
          <button
            onClick={onClose}
            title={t(lang, 'agentWizardClose')}
            className="shrink-0 w-6 h-6 flex items-center justify-center rounded text-content-disabled hover:text-content-primary hover:bg-surface-hover transition-colors cursor-pointer"
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
              <path d="M2 2l8 8M10 2l-8 8" />
            </svg>
          </button>
        )}
      </div>

      {/* Tab 导航栏 */}
      <div className="px-6 pt-3 flex items-center gap-1.5 shrink-0">
        {visibleTabs.map((tabKey, visibleIdx) => {
          const idx = visibleIdx + stepOffset;
          const isActive = idx === currentStep;
          const isPassed = idx < currentStep;
          return (
            <button
              key={tabKey}
              onClick={() => setCurrentStep(idx)}
              className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all cursor-pointer whitespace-nowrap ${
                isActive
                  ? 'bg-primary text-white'
                  : isPassed
                    ? 'text-primary bg-primary-light hover:bg-primary-light'
                    : 'text-content-secondary hover:bg-surface-hover'
              }`}
            >
              <span className="mr-1">{visibleIdx + 1}.</span>
              {t(lang, tabKey)}
            </button>
          );
        })}
      </div>

      {/* 内容区：模态内独立滚动；内嵌模式无独立滚动容器（由设置表单外层统一滚动，避免双层滚动条） */}
      <div className={embedded ? 'px-6 py-4' : 'flex-1 overflow-y-auto px-6 py-4'}>
        {/* ===== 步骤 1：创建方式（编辑模式跳过）===== */}
        {currentStep === 0 && (
          <div className="space-y-4">
            <div>
              <div className="text-xs font-medium text-content-secondary mb-1.5">{t(lang, 'agentWizardMethodLabel')}</div>
              <div className="flex gap-3">
                {([
                  { value: 'generate', label: t(lang, 'agentWizardMethodGenerate') },
                  { value: 'manual', label: t(lang, 'agentWizardMethodManual') },
                ] as const).map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => updateField('method', opt.value)}
                    className={`flex-1 px-4 py-2.5 rounded-md cursor-pointer text-sm transition-all ${
                      fields.method === opt.value
                        ? 'bg-primary-light text-primary border border-primary/30 font-medium'
                        : 'text-content-secondary border border-border-light hover:bg-surface-hover'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            {/* generate 模式：描述 + 模型 + 生成按钮 */}
            {fields.method === 'generate' && (
              <div className="space-y-3 p-3 rounded-lg bg-surface-card-alt border border-border-light">
                  <div>
                    <div className="text-xs font-medium text-content-secondary mb-1.5">{t(lang, 'agentWizardDescribeLabel')}</div>
                    <textarea
                      value={describeText}
                      onChange={(e) => setDescribeText(e.target.value)}
                      placeholder={t(lang, 'agentWizardDescribePlaceholder')}
                      rows={4}
                      className={`${textareaClass(false)} h-24 resize-none scrollbar-hidden`}
                    />
                  </div>
                <div className="flex items-end gap-2">
                  <div className="flex-1">
                    <div className="text-xs font-medium text-content-secondary mb-1.5">{t(lang, 'agentWizardModelLabel')}</div>
                    <GlassDropdown
                      value={generateModel}
                      options={modelOptions}
                      onChange={setGenerateModel}
                    />
                  </div>
                  <button
                    onClick={handleGenerate}
                    disabled={generateLoading || !describeText.trim()}
                    className="px-4 py-2 text-sm font-medium text-white bg-primary hover:bg-primary-hover rounded-md transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
                  >
                    {generateLoading ? t(lang, 'agentWizardGenerating') : t(lang, 'agentWizardGenerateButton')}
                  </button>
                </div>
                {generateLoading && (
                  <div className="flex items-center gap-2 text-xs text-content-secondary">
                    <svg className="w-3.5 h-3.5 animate-spin text-primary" viewBox="0 0 16 16" fill="none">
                      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeOpacity="0.25" />
                      <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                    </svg>
                    <span>{t(lang, 'agentWizardGenerating')}</span>
                  </div>
                )}
                {generateError && (
                  <div className="text-xs text-danger leading-relaxed">{generateError}</div>
                )}
              </div>
            )}

            {/* 作用域 */}
            <div>
              <div className="text-xs font-medium text-content-secondary mb-1.5">{t(lang, 'agentWizardScopeLabel')}</div>
              <div className="flex gap-3">
                {([
                  { value: 'project', label: t(lang, 'agentWizardScopeProject') },
                  { value: 'user', label: t(lang, 'agentWizardScopeUser') },
                ] as const).map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => updateField('scope', opt.value)}
                    className={`flex-1 px-4 py-2.5 rounded-md cursor-pointer text-sm transition-all ${
                      fields.scope === opt.value
                        ? 'bg-primary-light text-primary border border-primary/30 font-medium'
                        : 'text-content-secondary border border-border-light hover:bg-surface-hover'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            {/* 项目级：选择目标工作区（目录空间） */}
            {fields.scope === 'project' && (
              <div>
                <div className="text-xs font-medium text-content-secondary mb-1.5">{t(lang, 'agentWizardWorkspaceLabel')}</div>
                <GlassDropdown
                  value={fields.workspace}
                  options={workspaceOptions}
                  onChange={(v) => updateField('workspace', v)}
                />
                <div className="text-[11px] text-content-disabled mt-1">{t(lang, 'agentWizardWorkspaceHint')}</div>
              </div>
            )}
          </div>
        )}

        {/* ===== 步骤 2：基本信息 ===== */}
        {currentStep === 1 && (
          <div className="space-y-4">
            <div>
              <div className="text-xs font-medium text-content-secondary mb-1.5">{t(lang, 'agentWizardNameLabel')}</div>
              <input
                type="text"
                value={fields.identifier}
                onChange={(e) => updateField('identifier', e.target.value)}
                placeholder={t(lang, 'agentWizardNamePlaceholder')}
                readOnly={isEdit}
                className={`${inputClass(!isEdit && (!fields.identifier.trim() || !!fieldError('name')))} ${isEdit ? 'opacity-60 cursor-not-allowed' : ''}`}
              />
              {fieldError('name') && (
                <div className="text-xs text-danger mt-1">{fieldError('name')}</div>
              )}
            </div>

            <div>
              <div className="text-xs font-medium text-content-secondary mb-1.5">{t(lang, 'agentWizardDescriptionLabel')}</div>
              <textarea
                value={fields.when_to_use}
                onChange={(e) => updateField('when_to_use', e.target.value)}
                placeholder={t(lang, 'agentWizardDescriptionPlaceholder')}
                rows={3}
                className={`${textareaClass(!fields.when_to_use.trim() || !!fieldError('description'))} h-[72px] resize-none scrollbar-hidden`}
              />
              {fieldError('description') && (
                <div className="text-xs text-danger mt-1">{fieldError('description')}</div>
              )}
            </div>
          </div>
        )}

        {/* ===== 步骤 3：模型与工具 ===== */}
        {currentStep === 2 && (
          <div className="space-y-4">
            <div>
              <div className="text-xs font-medium text-content-secondary mb-1.5">{t(lang, 'agentWizardModelLabel')}</div>
              <GlassDropdown
                value={fields.model}
                options={modelOptions}
                onChange={(v) => updateField('model', v)}
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <div className="text-xs font-medium text-content-secondary mb-1.5">{t(lang, 'agentWizardEffortLabel')}</div>
                <GlassDropdown
                  value={fields.effort}
                  options={effortOptions}
                  onChange={(v) => updateField('effort', v)}
                />
              </div>
              <div>
                <div className="text-xs font-medium text-content-secondary mb-1.5">{t(lang, 'agentWizardPermissionLabel')}</div>
                <GlassDropdown
                  value={fields.permission_mode}
                  options={permissionOptions}
                  onChange={(v) => updateField('permission_mode', v)}
                />
              </div>
            </div>

            <div>
              <div className="text-xs font-medium text-content-secondary mb-1.5">{t(lang, 'agentWizardMaxTurnsLabel')}</div>
              <input
                type="number"
                inputMode="numeric"
                min={1}
                value={fields.max_turns}
                onChange={(e) => updateField('max_turns', e.target.value)}
                placeholder={t(lang, 'agentWizardMaxTurnsPlaceholderHint')}
                className={inputClass(false)}
              />
            </div>

            <div>
              <div className="text-xs font-medium text-content-secondary mb-1.5">{t(lang, 'agentWizardToolsLabel')}</div>
              {!tools || tools.length === 0 ? (
                <div className="text-xs text-content-disabled">
                  {t(lang, 'agentWizardNoToolsHint')}
                </div>
              ) : (
                // contain-paint：隔离工具列表的滚动内容，防止其溢出被计算进
                // 外层滚动区域（否则外层滚到底部出现一片空白）
                <div className="grid grid-cols-2 gap-1.5 max-h-40 overflow-y-auto p-1 contain-paint">
                  {tools.map((tool) => {
                    const checked = fields.tools.includes(tool.name);
                    return (
                      <label
                        key={tool.name}
                        title={tool.description}
                        className={`flex items-start gap-2 px-2 py-1.5 rounded-md cursor-pointer text-xs transition-colors ${
                          checked ? 'bg-primary-light text-primary' : 'text-content-secondary hover:bg-surface-hover'
                        }`}
                      >
                        <span
                          className={`mt-0.5 w-3.5 h-3.5 shrink-0 rounded-sm border-2 flex items-center justify-center transition-colors ${
                            checked
                              ? 'bg-primary border-primary'
                              : 'border-border-medium bg-transparent'
                          }`}
                        >
                          {checked && (
                            <svg className="w-2.5 h-2.5 text-white" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M2 6l3 3 5-6" />
                            </svg>
                          )}
                        </span>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleTool(tool.name)}
                          className="sr-only"
                        />
                        <span className="truncate font-mono">{tool.name}</span>
                      </label>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ===== 步骤 4：系统提示词 + 提交 ===== */}
        {currentStep === 3 && (
          <div className="space-y-4">
            <div>
              <div className="text-xs font-medium text-content-secondary mb-1.5">{t(lang, 'agentWizardSystemPromptLabel')}</div>
              <textarea
                value={fields.system_prompt}
                onChange={(e) => updateField('system_prompt', e.target.value)}
                rows={10}
                className={`${textareaClass(!fields.system_prompt.trim() || !!fieldError('system_prompt'))} h-[200px] resize-none scrollbar-hidden font-mono`}
              />
              {fieldError('system_prompt') && (
                <div className="text-xs text-danger mt-1">{fieldError('system_prompt')}</div>
              )}
              {/* markdown 预览（可折叠） */}
              <button
                onClick={() => setPreviewExpanded((v) => !v)}
                className="mt-1.5 text-xs text-content-secondary hover:text-primary hover:bg-surface-hover rounded px-2 py-0.5 transition-colors cursor-pointer"
              >
                {previewExpanded ? '▼ ' : '▶ '}{t(lang, 'agentWizardPreview')}
              </button>
              {previewExpanded && (
                <pre className="mt-1.5 px-3 py-2 rounded-md bg-card border border-border-light text-xs text-content-secondary font-mono whitespace-pre-wrap break-words max-h-48 overflow-y-auto scrollbar-hidden">
                  {markdownPreview}
                </pre>
              )}
            </div>

            {/* 提交结果 */}
            {result?.success && (
              <div className="px-3 py-2 rounded-md bg-success/10 border border-success/30 text-sm text-success">
                <div className="font-medium">{t(lang, 'agentWizardSuccess')}</div>
                {result.path && (
                  <div className="text-xs text-content-secondary mt-0.5 font-mono break-all">{result.path}</div>
                )}
              </div>
            )}
            {result && !result.success && (
              <div className="px-3 py-2 rounded-md bg-danger/10 border border-danger/30 text-sm text-danger">
                <div className="font-medium">{t(lang, 'agentWizardFailed')}</div>
                {result.error && (
                  <div className="text-xs mt-0.5 break-words">{result.error}</div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* 底部操作栏：上一步 / 下一步 / 提交 */}
      <div className="px-6 py-4 border-t border-border-light flex items-center justify-between gap-2 shrink-0">
        <div className="flex items-center gap-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-content-secondary hover:bg-surface-hover rounded-lg transition-colors cursor-pointer border border-border-light"
          >
            {t(lang, 'agentWizardClose')}
          </button>
          {/* 全选工具按钮（仅步骤 3 显示） */}
          {currentStep === 2 && (
            <button
              disabled={!tools || tools.length === 0}
              onClick={() => updateField('tools', fields.tools.length === tools?.length ? [] : (tools?.map((tl) => tl.name) ?? []))}
              className="px-4 py-2 text-sm text-content-secondary hover:bg-surface-hover rounded-lg transition-colors cursor-pointer border border-border-light disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {tools && fields.tools.length === tools.length ? t(lang, 'agentWizardDeselectAll') : t(lang, 'agentWizardSelectAll')}
            </button>
          )}
        </div>
        <div className="flex items-center gap-2">
          {currentStep > stepOffset && (
            <button
              onClick={handlePrev}
              className="px-4 py-2 text-sm text-content-primary hover:bg-surface-hover rounded-lg transition-colors cursor-pointer border border-border-light"
            >
              {t(lang, 'agentWizardPrev')}
            </button>
          )}
          {currentStep < TABS.length - 1 ? (
            <button
              onClick={handleNext}
              disabled={!canProceed}
              className="px-4 py-2 text-sm text-white bg-primary hover:bg-primary-hover rounded-lg transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {t(lang, 'agentWizardNext')}
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={!canSubmit}
              className="px-4 py-2 text-sm text-white bg-primary hover:bg-primary-hover rounded-lg transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2"
            >
              {submitting && (
                <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 16 16" fill="none">
                  <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeOpacity="0.4" />
                  <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                </svg>
              )}
              {submitting ? t(lang, 'agentWizardSubmitting') : (isEdit ? t(lang, 'agentWizardSaveButton') : t(lang, 'agentWizardSubmitButton'))}
            </button>
          )}
        </div>
      </div>
    </div>
  );

  if (embedded) return card;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 backdrop-blur-md animate-fade-in"
      onClick={onClose}
    >
      {card}
    </div>
  );
}
