/**
 * @fileoverview 首次登录 / 设置配置表单组件
 *
 * 在以下场景弹出：
 * - 首次登录：后端 ready 事件携带 first_login=true 时自动弹出，要求至少配置一个 env
 * - 修改配置：点击左栏底部 settings 齿轮图标弹出，可修改任意配置
 *
 * 两个 Tab：
 * - 基础配置：界面语言（优先填写）、API 环境配置（env_N）、工作目录
 * - 渠道配置：飞书 / 微信 / QQ 渠道配置 + 启用开关
 *
 * 视觉风格与 AgentWizardForm 一致：居中简洁卡片（w-[560px]）、Tab 导航、
 * GlassDropdown 玻璃拟态下拉、输入框聚焦散光。
 *
 * 数据流：
 * - 挂载时并行加载 settings / envs / channels（REST）
 * - ui_language 改动通过 onSetUiLanguage 走 WebSocket 同步（即时生效）
 * - env 增删改 / working_directory / channels 通过 REST 提交
 * - copilot/codex 走 OAuth 设备码流程，token 由后端全局管理，env 创建时 api_key 留空
 *
 * @module SetupForm
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { t, type UiLanguage } from '../i18n';
import { useTheme } from '../hooks/useTheme';
import { GlassDropdown, type DropdownOption } from './GlassDropdown';
import ToggleSwitch from './ToggleSwitch';
import { CronTab } from './CronTab';
import type { WebWorkspaceItem } from '../types/protocol';
import {
  envApi, oauthApi, settingsApi, channelsApi,
  type EnvInfo, type SettingsResponse, type CreateEnvPayload,
  type SandboxSettings, type PermissionRiskSettings,
  type ChannelRuntimeStatusEntry, type ChannelsRuntimeStatus,
} from '../api';

/** 各 api_format 的默认接入地址（与 CLI auth.py _DEFAULT_ENDPOINTS 对齐） */
const DEFAULT_ENDPOINTS: Record<string, string> = {
  anthropic: 'https://api.anthropic.com',
  openai: 'https://api.openai.com/v1',
  copilot: 'https://api.githubcopilot.com',
  codex: 'https://chatgpt.com/backend-api',
};

/** 各 api_format 的默认模型名（与 CLI auth.py _DEFAULT_MODELS 对齐） */
const DEFAULT_MODELS: Record<string, string> = {
  anthropic: 'claude-sonnet-4-6',
  openai: 'gpt-5.4',
  copilot: 'gpt-5.5',
  codex: 'codex-mini',
};

/** api_format 可选值 */
const API_FORMATS = ['anthropic', 'openai', 'copilot', 'codex'] as const;
/** copilot/codex 走 OAuth，其余走密钥输入 */
const OAUTH_FORMATS = new Set(['copilot', 'codex']);
/** OAuth 轮询间隔（毫秒） */
const OAUTH_POLL_INTERVAL = 5000;
/** OAuth 最大轮询次数（约 5 分钟超时） */
const OAUTH_MAX_POLLS = 60;

/** 环境草稿（新增 / 编辑共用） */
interface EnvDraft {
  /** API 格式 */
  api_format: string;
  /** 接入地址 */
  base_url: string;
  /** 认证方式（仅 anthropic/openai） */
  auth_type: 'api_key' | 'auth_token';
  /** API Key */
  api_key: string;
  /** Auth Token（Bearer） */
  auth_token: string;
  /** 模型列表（至少一个） */
  models: string[];
  /** OAuth 是否已完成（仅 copilot/codex） */
  oauth_authorized: boolean;
}

/** OAuth 流程状态 */
interface OauthState {
  status: 'idle' | 'pending' | 'success' | 'failed';
  user_code: string;
  verification_uri: string;
  device_code: string;
  error: string;
}

/** 新建环境草稿初始值 */
function makeInitialDraft(): EnvDraft {
  return {
    api_format: 'anthropic',
    base_url: DEFAULT_ENDPOINTS['anthropic'] ?? '',
    auth_type: 'api_key',
    api_key: '',
    auth_token: '',
    models: [DEFAULT_MODELS['anthropic'] ?? ''],
    oauth_authorized: false,
  };
}

/** 渠道配置类型（与后端 ChannelsConfig 对齐） */
interface GroupPolicy {
  mode: string;
  allowlist: string[];
  blacklist: string[];
  admin_list: string[];
}
interface FeishuCfg {
  enabled: boolean; app_id: string; app_secret: string; domain: string;
  require_mention: boolean; allow_bots: boolean; group_sessions_per_user: boolean;
  show_reasoning: boolean; group_policy: GroupPolicy;
  working_directory?: string;
}
interface WeixinCfg {
  enabled: boolean; account_id: string; token: string; base_url: string;
  cdn_base_url: string; user_id: string; allow_bots: boolean;
  working_directory?: string;
}
interface QQCfg {
  enabled: boolean; app_id: string; client_secret: string; markdown_support: boolean;
  allow_bots: boolean; group_sessions_per_user: boolean; require_mention: boolean;
  show_reasoning: boolean; group_policy: GroupPolicy;
  working_directory?: string;
}
interface ChannelsCfg {
  feishu: FeishuCfg; weixin: WeixinCfg; qq: QQCfg;
}

/** 渠道配置默认值（与后端模型默认对齐） */
function makeEmptyChannels(): ChannelsCfg {
  const gp: GroupPolicy = { mode: 'open', allowlist: [], blacklist: [], admin_list: [] };
  return {
    feishu: { enabled: false, app_id: '', app_secret: '', domain: 'feishu', require_mention: true, allow_bots: false, group_sessions_per_user: true, show_reasoning: true, group_policy: { ...gp } },
    weixin: { enabled: false, account_id: '', token: '', base_url: 'https://ilinkai.weixin.qq.com', cdn_base_url: 'https://novac2c.cdn.weixin.qq.com/c2c', user_id: '', allow_bots: false },
    qq: { enabled: false, app_id: '', client_secret: '', markdown_support: false, allow_bots: false, group_sessions_per_user: true, require_mention: true, show_reasoning: true, group_policy: { ...gp } },
  };
}

/**
 * SetupForm 组件属性接口
 */
interface SetupFormProps {
  /** 当前 UI 语言（由 App 通过 WebSocket state 驱动） */
  lang: UiLanguage;
  /** 是否首次登录模式（true 时 env 必填，标题为初始配置） */
  firstLogin: boolean;
  /** 初始打开的 Tab（目录按钮"管理目录…"直达目录空间页） */
  initialTab?: 'settings' | 'workspaces' | 'channels' | 'cron' | 'sandbox';
  /** 注册的工作区列表（web_workspaces 驱动） */
  workspaces: WebWorkspaceItem[];
  /** 注册新目录空间（WS web_add_workspace，后端校验） */
  onAddWorkspace: (path: string) => void;
  /** 移除已注册目录空间（WS web_remove_workspace） */
  onRemoveWorkspace: (path: string) => void;
  /** 拉取工作区列表（WS web_request_workspaces） */
  onRequestWorkspaces: () => void;
  /** 设置默认工作区（写 settings.working_directory） */
  onSetDefaultWorkspace: (path: string) => void;
  /** ui_language 改动回调（走 WebSocket web_set_setting 即时同步） */
  onSetUiLanguage: (lang: 'zh-CN' | 'en-US') => void;
  /** 保存成功后回调（App 可据此刷新 / 重连） */
  onSaved: () => void;
  /** 关闭表单回调 */
  onClose: () => void;
}

/** 输入框通用样式（聚焦散光） */
const inputClass = 'w-full px-3 py-2 rounded-md bg-surface-card-alt border border-border-light text-content-primary text-sm focus:outline-none focus:border-primary focus:shadow-glow transition-all duration-200';
/** 字段标签样式 */
const labelClass = 'text-xs font-medium text-content-secondary mb-1.5';

/**
 * SetupForm 首次登录 / 设置配置表单组件
 *
 * @param props - 组件属性
 * @returns 表单 JSX
 */
export function SetupForm({ lang, firstLogin, initialTab, workspaces, onAddWorkspace, onRemoveWorkspace, onRequestWorkspaces, onSetDefaultWorkspace, onSetUiLanguage, onSaved, onClose }: SetupFormProps) {
  /** 当前 Tab */
  const [tab, setTab] = useState<'settings' | 'workspaces' | 'channels' | 'cron' | 'sandbox'>(initialTab ?? 'settings');
  /** 加载状态 */
  const [loading, setLoading] = useState(true);
  /** 加载错误 */
  const [loadError, setLoadError] = useState<string | null>(null);
  /** 非敏感 settings 字段 */
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  /** 已配置环境列表 */
  const [envs, setEnvs] = useState<EnvInfo[]>([]);
  /** 活跃环境键名 */
  const [activeEnvKey, setActiveEnvKey] = useState<string | null>(null);
  /** 渠道配置 */
  const [channels, setChannels] = useState<ChannelsCfg>(makeEmptyChannels);
  /** 工作目录输入值 */
  const [workDir, setWorkDir] = useState('');
  /** 记忆功能启用开关 */
  const [memEnabled, setMemEnabled] = useState(true);
  /** 后台 LLM 自动提取/整合开关（false = 仅手动记录） */
  const [memAutoExtract, setMemAutoExtract] = useState(true);
  /** 提取子代理模型（空 = 继承当前） */
  const [memExtractModel, setMemExtractModel] = useState('');
  /** 整合子代理模型（空 = 继承当前） */
  const [memDreamModel, setMemDreamModel] = useState('');
  /** 自定义记忆目录输入值（空 = 使用默认目录） */
  const [memDir, setMemDir] = useState('');
  /** 自动标题启用开关 */
  const [titleEnabled, setTitleEnabled] = useState(false);
  /** 标题生成模型（空 = 继承当前） */
  const [titleModel, setTitleModel] = useState('');
  /** 模型参数（上下文窗口大小 / 最大输出 tokens / 最大轮次，字符串态便于自由编辑） */
  const [contextWindow, setContextWindow] = useState<string>('200000');
  const [maxTokens, setMaxTokens] = useState<string>('16384');
  const [maxTurns, setMaxTurns] = useState<string>('200');
  /** 权限 LLM 自动审核开关（auto 模式高危操作与沙箱拦截由 LLM 审核放行） */
  const [reviewAuto, setReviewAuto] = useState(false);
  /** 审核模型（空 = 继承当前会话模型） */
  const [reviewModel, setReviewModel] = useState('');
  /** 沙箱配置（可删改） */
  const [sandbox, setSandbox] = useState<SandboxSettings | null>(null);
  /** 沙箱保存错误 */
  const [sandboxError, setSandboxError] = useState<string | null>(null);
  /** 沙箱保存成功提示 */
  const [sandboxSaved, setSandboxSaved] = useState(false);
  /** 权限风险分级配置（LOW/MEDIUM/HIGH 三层级，后端内置只读展示） */
  const [permission, setPermission] = useState<PermissionRiskSettings | null>(null);
  /** 界面语言选择值（后端格式 zh-CN / en-US） */
  const [uiLang, setUiLang] = useState<'zh-CN' | 'en-US'>('zh-CN');
  /** 主题：交由 useTheme 统一管理（即改即生效并持久化），此处仅取当前值 */
  const { theme, setTheme } = useTheme();
  /** 新增环境草稿（首次模式 + 修改模式的新增分支共用） */
  const [draft, setDraft] = useState<EnvDraft>(makeInitialDraft);
  /** 修改模式是否展开新增环境表单 */
  const [showAddEnv, setShowAddEnv] = useState(false);
  /** 保存中 */
  const [saving, setSaving] = useState(false);
  /** 保存错误 */
  const [saveError, setSaveError] = useState<string | null>(null);
  /** 操作错误（env 列表即时操作的错误） */
  const [opError, setOpError] = useState<string | null>(null);

  // 挂载时并行加载 settings / envs / channels
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [s, e, c] = await Promise.all([settingsApi.get(), envApi.list(), channelsApi.get()]);
        if (cancelled) return;
        setSettings(s);
        setWorkDir(s.working_directory ?? '');
        setMemEnabled(s.memory?.enabled ?? true);
        setMemAutoExtract(s.memory?.auto_extract ?? true);
        setMemExtractModel(s.memory?.extract_model ?? '');
        setMemDreamModel(s.memory?.dream_model ?? '');
        setMemDir(s.memory?.directory ?? '');
        setTitleEnabled(s.title?.enabled ?? false);
        setTitleModel(s.title?.model ?? '');
        // 模型参数（默认值与后端 Settings 模型对齐）
        setContextWindow(String(s.context_window ?? 200000));
        setMaxTokens(String(s.max_tokens ?? 16384));
        setMaxTurns(String(s.max_turns ?? 200));
        // 权限 LLM 自动审核配置（auto 模式高危操作与沙箱拦截由 LLM 审核放行）
        setReviewAuto(s.permission_review?.auto_review ?? false);
        setReviewModel(s.permission_review?.review_model ?? '');
        // 沙箱配置（默认值由后端保证返回）
        setSandbox(s.sandbox ?? null);
        // 权限风险分级配置（LOW/MEDIUM/HIGH 三层级）
        setPermission(s.permission ?? null);
        // 后端 ui_language 为 en-US / zh-CN / 空串；空串默认 zh-CN
        setUiLang(s.ui_language === 'en-US' ? 'en-US' : 'zh-CN');
        // 主题由 useTheme 独立加载并校正，无需在此赋值
        setEnvs(e.envs);
        setActiveEnvKey(e.active_env_key);
        setChannels(c as unknown as ChannelsCfg);
        setLoading(false);
      } catch (err) {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Esc 键关闭（首次登录时禁用，强制用户完成配置）
  useEffect(() => {
    if (firstLogin) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onClose(); }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose, firstLogin]);

  /** 切换 api_format 时同步默认 base_url 和首个模型 */
  const handleFormatChange = useCallback((fmt: string) => {
    setDraft((d) => ({
      ...d,
      api_format: fmt,
      base_url: DEFAULT_ENDPOINTS[fmt] ?? d.base_url,
      models: d.models.length > 0 ? d.models : [DEFAULT_MODELS[fmt] ?? ''],
      oauth_authorized: false,
      // openai 格式不支持 auth_token（OpenAI SDK 无该参数），强制切回 api_key
      auth_type: fmt === 'openai' ? 'api_key' : d.auth_type,
    }));
  }, []);

  /** 更新草稿中指定模型 */
  const updateModel = useCallback((idx: number, value: string) => {
    setDraft((d) => {
      const next = [...d.models];
      next[idx] = value;
      return { ...d, models: next };
    });
  }, []);

  /** 可用模型下拉选项：所有 env 的 model_N，值为 env_N.model_N 引用，显示模型名 */
  const modelOptions = useMemo<DropdownOption[]>(() => {
    const opts: DropdownOption[] = [
      { value: '', label: t(lang, 'setupFieldMemoryModelInherit') },
    ];
    for (const env of envs) {
      for (const [key, name] of Object.entries(env.models)) {
        opts.push({
          value: `${env.env_key}.${key}`,
          label: name, // 仅显示模型名，不显示 env_N.model_N
        });
      }
    }
    return opts;
  }, [envs, lang]);

  /** 添加空模型行 */
  const addModel = useCallback(() => {
    setDraft((d) => ({ ...d, models: [...d.models, ''] }));
  }, []);

  /** 移除指定模型行 */
  const removeModel = useCallback((idx: number) => {
    setDraft((d) => ({ ...d, models: d.models.filter((_, i) => i !== idx) }));
  }, []);

  /** 草稿是否可提交（env 校验） */
  const draftValid = useCallback((d: EnvDraft): boolean => {
    if (!d.api_format) return false;
    if (d.models.filter((m) => m.trim()).length === 0) return false;
    if (OAUTH_FORMATS.has(d.api_format)) return d.oauth_authorized;
    // anthropic / openai 需要对应认证字段非空
    return d.auth_type === 'api_key' ? d.api_key.trim() !== '' : d.auth_token.trim() !== '';
  }, []);

  /** 提交草稿创建 env（处理 model_3+ 通过 add_models 补充） */
  const createEnvFromDraft = useCallback(async (d: EnvDraft): Promise<string> => {
    const models = d.models.map((m) => m.trim()).filter(Boolean);
    const payload: CreateEnvPayload = {
      api_format: d.api_format,
      base_url: d.base_url.trim(),
      model_1: models[0] ?? '',
    };
    if (models[1]) payload.model_2 = models[1];
    // OAuth 格式不传密钥（token 由后端全局管理）；其余按 auth_type 传
    if (!OAUTH_FORMATS.has(d.api_format)) {
      if (d.auth_type === 'api_key') payload.api_key = d.api_key.trim();
      else payload.auth_token = d.auth_token.trim();
    }
    const res = await envApi.create(payload);
    // 超过 2 个模型：通过 add_models 补充 model_3+
    if (models.length > 2) {
      await envApi.update(res.env_key, {
        add_models: models.slice(2).map((m, i) => ({ key: `model_${i + 3}`, value: m })),
      });
    }
    return res.env_key;
  }, []);

  /** 保存全部配置 */
  const handleSave = useCallback(async () => {
    setSaving(true);
    setSaveError(null);
    try {
      // 1. ui_language 改动走 WebSocket 即时同步
      if (settings && uiLang !== settings.ui_language) {
        onSetUiLanguage(uiLang);
      }
      // 2. 首次模式或修改模式展开新增 env：创建 env
      if ((firstLogin || showAddEnv) && draftValid(draft)) {
        await createEnvFromDraft(draft);
      }
      // 3. 工作目录改动
      if (settings && (workDir.trim() || '') !== (settings.working_directory ?? '')) {
        await settingsApi.updateWorkingDirectory(workDir.trim());
      }
      // 3.1 模型参数改动（context_window / max_tokens / max_turns）
      // 字符串态自由编辑：清空/输入不钳制，保存时统一校验为正整数（max_turns 1~512），
      // 非法输入报错终止保存，避免"清空即变 1"的反人类体验；空字段视为不改动
      const tdCw = parseInt(contextWindow, 10);
      const tdMt = parseInt(maxTokens, 10);
      const tdTurns = parseInt(maxTurns, 10);
      const cwValid = contextWindow.trim() === '' || (Number.isInteger(tdCw) && tdCw > 0);
      const mtValid = maxTokens.trim() === '' || (Number.isInteger(tdMt) && tdMt > 0);
      const turnsValid = maxTurns.trim() === '' || (Number.isInteger(tdTurns) && tdTurns >= 1 && tdTurns <= 512);
      if (!cwValid || !mtValid || !turnsValid) {
        setSaving(false);
        setSaveError(t(lang, 'modelParamsInvalid'));
        return;
      }
      const modelParamsPayload: { context_window?: number; max_tokens?: number; max_turns?: number } = {};
      if (contextWindow.trim() !== '' && cwValid) modelParamsPayload.context_window = tdCw;
      if (maxTokens.trim() !== '' && mtValid) modelParamsPayload.max_tokens = tdMt;
      if (maxTurns.trim() !== '' && turnsValid) modelParamsPayload.max_turns = tdTurns;
      if (settings && Object.keys(modelParamsPayload).length > 0 && (
        (modelParamsPayload.context_window !== undefined && modelParamsPayload.context_window !== settings.context_window) ||
        (modelParamsPayload.max_tokens !== undefined && modelParamsPayload.max_tokens !== settings.max_tokens) ||
        (modelParamsPayload.max_turns !== undefined && modelParamsPayload.max_turns !== settings.max_turns)
      )) {
        await settingsApi.updateModelParams(modelParamsPayload);
      }
      // 3.5 记忆配置改动（任一字段变化即提交）
      if (settings && (
        memEnabled !== settings.memory?.enabled ||
        memAutoExtract !== settings.memory?.auto_extract ||
        (memExtractModel.trim() || '') !== (settings.memory?.extract_model ?? '') ||
        (memDreamModel.trim() || '') !== (settings.memory?.dream_model ?? '') ||
        (memDir.trim() || '') !== (settings.memory?.directory ?? '')
      )) {
        await settingsApi.updateMemory({
          enabled: memEnabled,
          auto_extract: memAutoExtract,
          extract_model: memExtractModel.trim(),
          dream_model: memDreamModel.trim(),
          directory: memDir.trim(),
        });
      }
      // 3.6 自动标题配置改动（启用开关 / 标题模型任一变化即提交）
      if (settings && (
        titleEnabled !== settings.title?.enabled ||
        (titleModel.trim() || '') !== (settings.title?.model ?? '')
      )) {
        await settingsApi.updateTitle({
          enabled: titleEnabled,
          model: titleModel.trim(),
        });
      }
      // 3.7 权限 LLM 自动审核配置改动（开关 / 审核模型任一变化即提交）
      if (settings && (
        reviewAuto !== settings.permission_review?.auto_review ||
        (reviewModel.trim() || '') !== (settings.permission_review?.review_model ?? '')
      )) {
        await settingsApi.updatePermissionReview({
          auto_review: reviewAuto,
          review_model: reviewModel.trim(),
        });
      }
      // 4. 渠道配置（兼容旧配置：enabled 但无运行目录的渠道自动填充默认工作区，
      //    避免后端启用校验（enabled 必填目录）拒绝整个保存；其余清空为 null）
      const defaultWs = workspaces.find((w) => w.is_default)?.path;
      const normalizeChannelWd = (ch: { enabled: boolean; working_directory?: string | null }): Record<string, unknown> => {
        const merged = { ...ch };
        if (merged.enabled && !merged.working_directory) {
          merged.working_directory = defaultWs ?? null;
        } else if (!merged.working_directory) {
          merged.working_directory = null;
        }
        return merged;
      };
      const channelsPayload = {
        feishu: normalizeChannelWd(channels.feishu),
        weixin: normalizeChannelWd(channels.weixin),
        qq: normalizeChannelWd(channels.qq),
      };
      await channelsApi.update(channelsPayload as unknown as Parameters<typeof channelsApi.update>[0]);
      // 5. 沙箱配置
      if (sandbox) {
        try {
          await settingsApi.updateSandbox(sandbox as Parameters<typeof settingsApi.updateSandbox>[0]);
          setSandboxSaved(true);
          setSandboxError(null);
        } catch (err) {
          setSandboxError(err instanceof Error ? err.message : String(err));
        }
      }
      // 6. 权限风险分级为内置只读（LOW/MEDIUM/HIGH），无需保存
      setSaving(false);
      onSaved();
    } catch (err) {
      setSaving(false);
      setSaveError(err instanceof Error ? err.message : String(err));
    }
  }, [settings, uiLang, workDir, contextWindow, maxTokens, maxTurns, memEnabled, memAutoExtract, memExtractModel, memDreamModel, memDir, titleEnabled, titleModel, reviewAuto, reviewModel, channels, firstLogin, showAddEnv, draft, draftValid, createEnvFromDraft, onSetUiLanguage, onSaved, sandbox, lang]);

  /** 删除环境（即时 API） */
  const handleDeleteEnv = useCallback(async (envKey: string) => {
    setOpError(null);
    try {
      await envApi.remove(envKey);
      const e = await envApi.list();
      setEnvs(e.envs);
      setActiveEnvKey(e.active_env_key);
    } catch (err) {
      setOpError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  /** 向已有环境追加模型（即时 API，类似 CLI /model add 流程） */
  const handleAddModel = useCallback(async (envKey: string, modelValue: string) => {
    setOpError(null);
    try {
      // key 不由前端计算：本地 envs 快照可能过期（连续快速添加、
      // 删除中间模型导致编号不连续），重复 key 会相互覆盖只留最后一个。
      // 缺省 key 时后端按现有最大编号 +1 自动分配。
      await envApi.update(envKey, { add_models: [{ value: modelValue }] });
      const e = await envApi.list();
      setEnvs(e.envs);
    } catch (err) {
      setOpError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  /** 从已有环境移除模型（即时 API） */
  const handleRemoveModel = useCallback(async (envKey: string, modelKey: string) => {
    setOpError(null);
    try {
      await envApi.update(envKey, { remove_models: [modelKey] });
      const e = await envApi.list();
      setEnvs(e.envs);
    } catch (err) {
      setOpError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  /** 选择界面语言（即时同步 + 更新本地） */
  const handlePickUiLang = useCallback((val: 'zh-CN' | 'en-US') => {
    setUiLang(val);
    onSetUiLanguage(val);
  }, [onSetUiLanguage]);

  /** 选择主题：即改即生效（useTheme.setTheme 应用 DOM + 即时写 settings.json） */
  const handlePickTheme = useCallback((val: 'light' | 'dark' | 'system') => {
    setTheme(val);
  }, [setTheme]);

  /** 是否可保存 */
  const canSave = !saving && !loading && (
    firstLogin ? draftValid(draft) : true
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 backdrop-blur-md animate-fade-in" onClick={firstLogin ? undefined : onClose}>
      <div
        className="relative bg-surface-card rounded-2xl border border-border-light shadow-card w-[760px] h-[600px] max-w-[95vw] max-h-[90vh] flex flex-col animate-scale-in modal-origin-center"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 标题栏 */}
        <div className="px-6 py-4 border-b border-border-light flex items-center justify-between shrink-0">
          <div className="flex items-center gap-3">
            <h3 className="text-lg font-semibold text-content-primary">
              {firstLogin ? t(lang, 'setupFormTitle') : t(lang, 'settings')}
            </h3>
            {firstLogin && <span className="text-xs text-content-disabled">{t(lang, 'setupFormSubtitle')}</span>}
          </div>
          {!firstLogin && (
            <button
              onClick={onClose}
              title={t(lang, 'setupFormClose')}
              className="shrink-0 w-6 h-6 flex items-center justify-center rounded text-content-disabled hover:text-content-primary hover:bg-surface-hover transition-colors cursor-pointer"
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M2 2l8 8M10 2l-8 8" /></svg>
            </button>
          )}
        </div>

        {/* 主体：左侧 Tab 栏 + 右侧内容区 */}
        <div className="flex flex-1 min-h-0">
          {/* 左侧 Tab 导航栏（垂直） */}
          <div className="w-fit shrink-0 border-r border-border-light px-3 py-3 flex flex-col gap-1 overflow-y-auto">
            {(['settings', 'workspaces', 'channels', 'cron', 'sandbox'] as const).map((tabKey) => {
              const isActive = tab === tabKey;
              const labelKey = tabKey === 'settings' ? 'setupFormSettingsTitle'
                : tabKey === 'workspaces' ? 'setupFormWorkspacesTitle'
                : tabKey === 'channels' ? 'setupFormChannelsTitle'
                : tabKey === 'cron' ? 'setupFormCronTitle'
                : 'setupFormSandboxTitle';
              return (
                <button
                  key={tabKey}
                  onClick={() => setTab(tabKey)}
                  className={`px-3 py-2 rounded-md text-sm font-medium transition-all cursor-pointer text-left whitespace-nowrap ${
                    isActive
                      ? 'bg-primary text-white'
                      : 'text-content-secondary hover:bg-surface-hover'
                  }`}
                >
                  {t(lang, labelKey)}
                </button>
              );
            })}
          </div>

          {/* 内容区：cron/目录空间 Tab 独立加载数据，不依赖 settings/envs/channels 主加载流程 */}
          <div className="flex-1 min-w-0 overflow-y-auto px-6 py-4 [scrollbar-gutter:stable]">
          {tab === 'cron' ? (
            <CronTab lang={lang} workspaces={workspaces} />
          ) : tab === 'workspaces' ? (
            <WorkspacesTab
              lang={lang}
              workspaces={workspaces}
              onAdd={onAddWorkspace}
              onRemove={onRemoveWorkspace}
              onRefresh={onRequestWorkspaces}
              onSetDefault={onSetDefaultWorkspace}
            />
          ) : loading ? (
            <div className="flex items-center justify-center py-12 text-sm text-content-disabled">
              <svg className="w-4 h-4 animate-spin mr-2" viewBox="0 0 16 16" fill="none">
                <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeOpacity="0.4" />
                <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
              {t(lang, 'setupFormSaving')}
            </div>
          ) : loadError ? (
            <div className="text-sm text-danger py-4">{t(lang, 'setupFormLoadFailed')}: {loadError}</div>
          ) : tab === 'settings' ? (
            <SettingsTab
              lang={lang}
              firstLogin={firstLogin}
              uiLang={uiLang}
              onPickUiLang={handlePickUiLang}
              theme={theme}
              onPickTheme={handlePickTheme}
              workDir={workDir}
              onWorkDirChange={setWorkDir}
              onManageWorkspaces={() => setTab('workspaces')}
              memEnabled={memEnabled}
              onMemEnabledChange={setMemEnabled}
              memAutoExtract={memAutoExtract}
              onMemAutoExtractChange={setMemAutoExtract}
              memExtractModel={memExtractModel}
              onMemExtractModelChange={setMemExtractModel}
              memDreamModel={memDreamModel}
              onMemDreamModelChange={setMemDreamModel}
              memDir={memDir}
              onMemDirChange={setMemDir}
              titleEnabled={titleEnabled}
              onTitleEnabledChange={setTitleEnabled}
              titleModel={titleModel}
              onTitleModelChange={setTitleModel}
              contextWindow={contextWindow}
              onContextWindowChange={setContextWindow}
              maxTokens={maxTokens}
              onMaxTokensChange={setMaxTokens}
              maxTurns={maxTurns}
              onMaxTurnsChange={setMaxTurns}
              reviewAuto={reviewAuto}
              onReviewAutoChange={setReviewAuto}
              reviewModel={reviewModel}
              onReviewModelChange={setReviewModel}
              modelOptions={modelOptions}
              envs={envs}
              activeEnvKey={activeEnvKey}
              onDeleteEnv={handleDeleteEnv}
              onAddModelToEnv={handleAddModel}
              onRemoveModelFromEnv={handleRemoveModel}
              showAddEnv={showAddEnv}
              onToggleAddEnv={() => setShowAddEnv(!showAddEnv)}
              draft={draft}
              onDraftChange={setDraft}
              onFormatChange={handleFormatChange}
              onUpdateModel={updateModel}
              onAddModel={addModel}
              onRemoveModel={removeModel}
              opError={opError}
            />
          ) : tab === 'sandbox' ? (
            <SandboxTab
              lang={lang}
              sandbox={sandbox}
              onSandboxChange={setSandbox}
              error={sandboxError}
              saved={sandboxSaved}
              permission={permission}
            />
          ) : (
            <ChannelsTab lang={lang} channels={channels} onChannelsChange={setChannels} workspaces={workspaces} />
          )}
          </div>
        </div>

        {/* 底部操作栏（cron Tab 操作即时生效，非首次登录时隐藏保存按钮） */}
        <div className="px-6 py-4 border-t border-border-light flex items-center justify-between gap-2 shrink-0">
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-content-secondary hover:bg-surface-hover rounded-lg transition-colors cursor-pointer border border-border-light"
            >
              {t(lang, 'setupFormClose')}
            </button>
            {saveError && <span className="text-xs text-danger">{t(lang, 'setupFormSaveFailed')}: {saveError}</span>}
          </div>
          {!(tab === 'cron' && !firstLogin) && (
            <button
              onClick={handleSave}
              disabled={!canSave}
              className="px-4 py-2 text-sm text-white bg-primary hover:bg-primary-hover rounded-lg transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2"
            >
              {saving && (
                <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 16 16" fill="none">
                  <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeOpacity="0.4" />
                  <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                </svg>
              )}
              {saving ? t(lang, 'setupFormSaving') : t(lang, 'setupFormSave')}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ===== 环境选择器（每个端点独立触发器，点击展开配置） =====

interface EnvSelectorProps {
  lang: UiLanguage;
  envs: EnvInfo[];
  activeEnvKey: string | null;
  onDelete: (key: string) => void;
  /** 向已有环境追加模型 */
  onAddModel: (envKey: string, modelValue: string) => void;
  /** 从已有环境移除模型（envKey, modelKey） */
  onRemoveModel: (envKey: string, modelKey: string) => void;
  opError: string | null;
}

/**
 * 环境选择器：每个环境（端点）显示一个独立的可折叠触发器，
 * 触发器显示 base_url（https 端点），点击展开该环境的配置
 * （api_format、模型列表、追加/移除模型、删除）。
 * 不显示裸 env_N、不显示"已配置凭据"、不显示"设为当前"。
 */
function EnvSelector(p: EnvSelectorProps) {
  const { lang } = p;
  return (
    <div>
      <div className={labelClass}>{t(lang, 'setupEnvListTitle')}</div>
      <div className="space-y-1.5">
        {p.envs.map((env) => (
          <EnvTriggerCard key={env.env_key} lang={lang} env={env} isActive={env.env_key === p.activeEnvKey} onDelete={p.onDelete} onAddModel={p.onAddModel} onRemoveModel={p.onRemoveModel} />
        ))}
      </div>
      {p.opError && <div className="text-xs text-danger mt-1">{p.opError}</div>}
    </div>
  );
}

/** 单个环境触发卡片属性 */
interface EnvTriggerCardProps {
  lang: UiLanguage;
  env: EnvInfo;
  isActive: boolean;
  onDelete: (key: string) => void;
  onAddModel: (envKey: string, modelValue: string) => void;
  onRemoveModel: (envKey: string, modelKey: string) => void;
}

/** 单个环境触发卡片：触发器显示 base_url，点击展开配置和模型编辑 */
function EnvTriggerCard({ lang, env, isActive, onDelete, onAddModel, onRemoveModel }: EnvTriggerCardProps) {
  /** 是否展开 */
  const [expanded, setExpanded] = useState(false);
  /** 新增模型输入值 */
  const [newModel, setNewModel] = useState('');
  /** 模型字典的键值对列表 */
  const modelEntries = Object.entries(env.models);

  return (
    <div className={`rounded-lg border overflow-hidden transition-colors ${isActive ? 'border-primary/40' : 'border-border-light'}`}>
      {/* 触发器行：base_url + 活跃指示灯 */}
      <button
        onClick={() => setExpanded(!expanded)}
        className={`w-full flex items-center gap-2 px-3 py-2 text-sm cursor-pointer transition-colors ${isActive ? 'bg-primary-light/30' : 'bg-surface-card-alt hover:bg-surface-hover'}`}
      >
        {/* 活跃指示灯 */}
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${isActive ? 'bg-primary' : 'bg-content-disabled/40'}`} />
        {/* base_url（https 端点） */}
        <span className={`flex-1 text-left truncate ${isActive ? 'text-primary font-medium' : 'text-content-primary'}`}>{env.base_url}</span>
        {/* api_format 标签 */}
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary shrink-0 font-medium">{env.api_format}</span>
        {/* 展开/收起箭头 */}
        <svg className={`w-3 h-3 shrink-0 transition-transform text-content-disabled ${expanded ? 'rotate-90' : ''}`} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M4.5 3L7.5 6L4.5 9" /></svg>
      </button>

      {/* 展开区：模型列表 + 追加/移除 + 切换/删除 */}
      {expanded && (
        <div className="px-3 py-2.5 border-t border-border-light space-y-2.5 bg-surface-card">
          {/* 模型列表 */}
          <div>
            <div className="text-[11px] text-content-disabled mb-1.5">{t(lang, 'setupFieldModels')}</div>
            {modelEntries.length === 0 ? (
              <div className="text-xs text-content-disabled italic">{t(lang, 'setupEnvModelsHint')}</div>
            ) : (
              <div className="space-y-1">
                {modelEntries.map(([modelKey, modelValue]) => (
                  <div key={modelKey} className="flex items-center gap-2">
                    <input
                      type="text"
                      value={modelValue}
                      readOnly
                      className="flex-1 px-2 py-1 rounded text-xs bg-surface-card-alt border border-border-light text-content-primary font-mono"
                    />
                    <button
                      onClick={() => onRemoveModel(env.env_key, modelKey)}
                      className="shrink-0 w-5 h-5 flex items-center justify-center rounded text-content-disabled hover:text-danger hover:bg-surface-hover transition-colors cursor-pointer"
                      title={t(lang, 'setupFieldRemoveModel')}
                    >
                      <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M2 6h8" /></svg>
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 追加模型输入 */}
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={newModel}
              onChange={(e) => setNewModel(e.target.value)}
              className={inputClass}
              placeholder={t(lang, 'setupFieldModel')}
            />
            <button
              onClick={() => {
                if (newModel.trim()) {
                  onAddModel(env.env_key, newModel.trim());
                  setNewModel('');
                }
              }}
              disabled={!newModel.trim()}
              className="shrink-0 px-2 py-1.5 rounded-md text-xs text-primary border border-primary/30 hover:bg-primary/10 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
            >
              + {t(lang, 'setupFieldAddModel')}
            </button>
          </div>

          {/* 底部操作：删除（非活跃环境可删除） */}
          <div className="flex items-center gap-2 pt-1">
            {!isActive && (
              <button
                onClick={() => onDelete(env.env_key)}
                className="px-2.5 py-1 rounded-md text-xs text-danger border border-danger/30 hover:bg-danger/10 transition-colors cursor-pointer"
              >
                {t(lang, 'setupEnvDelete')}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ===== 基础配置 Tab =====

interface SettingsTabProps {
  lang: UiLanguage;
  firstLogin: boolean;
  uiLang: 'zh-CN' | 'en-US';
  onPickUiLang: (v: 'zh-CN' | 'en-US') => void;
  /** 主题选择值（light / dark / system） */
  theme: 'light' | 'dark' | 'system';
  onPickTheme: (v: 'light' | 'dark' | 'system') => void;
  workDir: string;
  onWorkDirChange: (v: string) => void;
  /** 跳转到目录空间管理页 */
  onManageWorkspaces: () => void;
  /** 记忆功能启用开关 */
  memEnabled: boolean;
  onMemEnabledChange: (v: boolean) => void;
  /** 后台 LLM 自动提取/整合开关（false = 仅手动记录） */
  memAutoExtract: boolean;
  onMemAutoExtractChange: (v: boolean) => void;
  /** 提取子代理模型（空 = 继承当前） */
  memExtractModel: string;
  onMemExtractModelChange: (v: string) => void;
  /** 整合子代理模型（空 = 继承当前） */
  memDreamModel: string;
  onMemDreamModelChange: (v: string) => void;
  /** 自定义记忆目录输入值（空 = 使用默认目录） */
  memDir: string;
  onMemDirChange: (v: string) => void;
  /** 自动标题启用开关 */
  titleEnabled: boolean;
  onTitleEnabledChange: (v: boolean) => void;
  /** 标题生成模型（空 = 继承当前） */
  titleModel: string;
  onTitleModelChange: (v: string) => void;
  /** 上下文窗口大小（token，字符串态：保存时校验为正整数） */
  contextWindow: string;
  onContextWindowChange: (v: string) => void;
  /** 最大输出 tokens（字符串态：保存时校验为正整数） */
  maxTokens: string;
  onMaxTokensChange: (v: string) => void;
  /** 最大对话轮次（字符串态：保存时校验为 1~512） */
  maxTurns: string;
  onMaxTurnsChange: (v: string) => void;
  /** 权限 LLM 自动审核开关（auto 模式高危操作与沙箱拦截由 LLM 审核放行） */
  reviewAuto: boolean;
  onReviewAutoChange: (v: boolean) => void;
  /** 审核模型（空 = 继承当前会话模型） */
  reviewModel: string;
  onReviewModelChange: (v: string) => void;
  /** 模型下拉选项（env_N.model_N 引用） */
  modelOptions: DropdownOption[];
  envs: EnvInfo[];
  activeEnvKey: string | null;
  onDeleteEnv: (k: string) => void;
  /** 向已有环境追加模型（envKey, modelValue） */
  onAddModelToEnv: (envKey: string, modelValue: string) => void;
  /** 从已有环境移除模型（envKey, modelKey） */
  onRemoveModelFromEnv: (envKey: string, modelKey: string) => void;
  showAddEnv: boolean;
  onToggleAddEnv: () => void;
  draft: EnvDraft;
  onDraftChange: (d: EnvDraft) => void;
  onFormatChange: (f: string) => void;
  onUpdateModel: (i: number, v: string) => void;
  onAddModel: () => void;
  onRemoveModel: (i: number) => void;
  opError: string | null;
}

/** 目录空间 Tab：注册目录列表（默认标记/设为默认/移除）+ 新增入口 */
function WorkspacesTab({ lang, workspaces, onAdd, onRemove, onRefresh, onSetDefault }: {
  lang: UiLanguage;
  workspaces: WebWorkspaceItem[];
  onAdd: (path: string) => void;
  onRemove: (path: string) => void;
  onRefresh: () => void;
  onSetDefault: (path: string) => void;
}) {
  const [addMode, setAddMode] = useState(false);
  const [addValue, setAddValue] = useState('');
  const addInputRef = useRef<HTMLInputElement>(null);

  // 进入添加模式时自动聚焦
  useEffect(() => {
    if (addMode) requestAnimationFrame(() => addInputRef.current?.focus());
  }, [addMode]);

  const submitAdd = () => {
    const v = addValue.trim();
    if (v) { onAdd(v); setAddValue(''); setAddMode(false); }
  };

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        {workspaces.length === 0 && (
          <div className="text-sm text-content-disabled py-4 text-center">{t(lang, 'workspace_empty')}</div>
        )}
        {workspaces.map((ws) => (
          <div key={ws.path} className={`flex items-center gap-2 px-3 py-2.5 rounded-lg border ${
            ws.is_default ? 'border-primary/40 bg-primary-light/40' : 'border-border-light bg-surface-card-alt/50'
          } ${!ws.available ? 'opacity-50' : ''}`} title={ws.path}>
            <svg className="w-4 h-4 text-content-secondary shrink-0" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M1.5 4.5v7a1.5 1.5 0 001.5 1.5h10a1.5 1.5 0 001.5-1.5V6.5a1.5 1.5 0 00-1.5-1.5H8L6.4 3.1a1.5 1.5 0 00-1.1-.6H3a1.5 1.5 0 00-1.5 1.5v.5z" />
            </svg>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-sm text-content-primary truncate font-medium">{ws.name}</span>
                {ws.is_default && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-primary/15 text-primary font-medium shrink-0">{t(lang, 'workspace_default_badge')}</span>
                )}
              </div>
              <div className="text-[11px] text-content-disabled truncate font-mono">{ws.path}</div>
            </div>
            {!ws.available && <span className="text-[10px] text-danger shrink-0">{t(lang, 'workspace_unavailable')}</span>}
            <div className="flex items-center gap-1 shrink-0">
              {!ws.is_default && (
                <>
                  <button
                    onClick={() => onSetDefault(ws.path)}
                    title={t(lang, 'workspace_set_default')}
                    className="px-2 py-1 text-[11px] text-content-secondary hover:text-content-primary glass-option-hover rounded-md transition-colors cursor-pointer"
                  >
                    {t(lang, 'workspace_set_default')}
                  </button>
                  <button
                    onClick={() => onRemove(ws.path)}
                    title={t(lang, 'workspace_remove')}
                    className="px-2 py-1 text-[11px] text-danger hover:bg-danger/10 rounded-md transition-colors cursor-pointer"
                  >
                    {t(lang, 'workspace_remove')}
                  </button>
                </>
              )}
            </div>
          </div>
        ))}
      </div>
      {addMode ? (
        <div className="flex items-center gap-2">
          <input
            ref={addInputRef}
            type="text"
            value={addValue}
            onChange={(e) => setAddValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submitAdd();
              else if (e.key === 'Escape') { setAddMode(false); setAddValue(''); }
            }}
            placeholder={t(lang, 'workspace_add_placeholder')}
            className={inputClass}
          />
          <button onClick={submitAdd} disabled={!addValue.trim()}
            className="shrink-0 px-3 py-2 text-sm text-white bg-primary hover:bg-primary-hover rounded-md transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed">
            {t(lang, 'workspace_add_confirm')}
          </button>
          <button onClick={() => { setAddMode(false); setAddValue(''); }}
            className="shrink-0 px-3 py-2 text-sm text-content-secondary glass-option-hover rounded-md transition-colors cursor-pointer">
            {t(lang, 'cancel')}
          </button>
        </div>
      ) : (
        <div className="flex gap-2">
          <button onClick={() => setAddMode(true)}
            className="px-3 py-1.5 rounded-md text-sm border border-border-light text-content-secondary hover:bg-surface-hover transition-colors cursor-pointer">
            + {t(lang, 'workspace_add')}
          </button>
          <button onClick={onRefresh}
            className="px-3 py-1.5 rounded-md text-sm border border-border-light text-content-secondary hover:bg-surface-hover transition-colors cursor-pointer">
            {t(lang, 'refresh')}
          </button>
        </div>
      )}
    </div>
  );
}

/** 基础配置 Tab：界面语言 + 环境配置 + 工作目录 */
function SettingsTab(p: SettingsTabProps) {
  const { lang } = p;
  return (    <div className="space-y-5">
      {/* 界面语言（优先填写） */}
      <div>
        <div className={labelClass}>{t(lang, 'setupFieldUiLanguage')}</div>
        <div className="flex gap-3">
          {([
            { value: 'zh-CN', label: '中文 (zh-CN)' },
            { value: 'en-US', label: 'English (en-US)' },
          ] as const).map((opt) => (
            <button
              key={opt.value}
              onClick={() => p.onPickUiLang(opt.value)}
              className={`flex-1 px-3 py-2 rounded-md text-sm border transition-all cursor-pointer ${
                p.uiLang === opt.value
                  ? 'border-primary bg-primary-light text-primary'
                  : 'border-border-light text-content-secondary hover:bg-surface-hover'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* 主题（风格与界面语言一致的三选一分段按钮） */}
      <div>
        <div className={labelClass}>{t(lang, 'setupFieldTheme')}</div>
        <div className="flex gap-3">
          {([
            { value: 'light', label: t(lang, 'theme_light') },
            { value: 'dark', label: t(lang, 'theme_dark') },
            { value: 'system', label: t(lang, 'theme_system') },
          ] as const).map((opt) => (
            <button
              key={opt.value}
              onClick={() => p.onPickTheme(opt.value)}
              className={`flex-1 px-3 py-2 rounded-md text-sm border transition-all cursor-pointer ${
                p.theme === opt.value
                  ? 'border-primary bg-primary-light text-primary'
                  : 'border-border-light text-content-secondary hover:bg-surface-hover'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* 环境选择器（修改模式）：触发器显示 base_url，点击展开下拉列表 */}
      {!p.firstLogin && p.envs.length > 0 && (
        <EnvSelector
          lang={lang}
          envs={p.envs}
          activeEnvKey={p.activeEnvKey}
          onDelete={p.onDeleteEnv}
          onAddModel={p.onAddModelToEnv}
          onRemoveModel={p.onRemoveModelFromEnv}
          opError={p.opError}
        />
      )}

      {/* 新增环境入口（修改模式） */}
      {!p.firstLogin && (
        <div>
          <button
            onClick={p.onToggleAddEnv}
            className="px-3 py-1.5 rounded-md text-sm border border-border-light text-content-secondary hover:bg-surface-hover transition-colors cursor-pointer"
          >
            {p.showAddEnv ? '−' : '+'} {t(lang, 'setupEnvAdd')}
          </button>
        </div>
      )}

      {/* 环境编辑器（首次模式始终显示；修改模式展开时显示） */}
      {(p.firstLogin || p.showAddEnv) && (
        <EnvEditor
          lang={lang}
          draft={p.draft}
          onDraftChange={p.onDraftChange}
          onFormatChange={p.onFormatChange}
          onUpdateModel={p.onUpdateModel}
          onAddModel={p.onAddModel}
          onRemoveModel={p.onRemoveModel}
        />
      )}

      {/* 首次模式未配置 env 提示 */}
      {p.firstLogin && p.envs.length === 0 && !p.draft.models.some((m) => m.trim()) && (
        <div className="text-xs text-content-disabled">{t(lang, 'setupEnvAtLeastOne')}</div>
      )}

      {/* 工作目录（多目录空间改造：输入框收敛到目录空间页管理，此处展示当前值 + 跳转） */}
      <div>
        <div className={labelClass}>{t(lang, 'setupFieldWorkingDirectory')}</div>
        <div className="flex gap-2">
          <div className="flex-1 flex items-center gap-2 px-3 py-2 rounded-md bg-surface-card-alt border border-border-light min-w-0">
            <svg className="w-3.5 h-3.5 text-content-disabled shrink-0" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M1.5 4.5v7a1.5 1.5 0 001.5 1.5h10a1.5 1.5 0 001.5-1.5V6.5a1.5 1.5 0 00-1.5-1.5H8L6.4 3.1a1.5 1.5 0 00-1.1-.6H3a1.5 1.5 0 00-1.5 1.5v.5z" />
            </svg>
            <span className={`text-sm truncate ${p.workDir ? 'text-content-primary' : 'text-content-disabled'}`}>
              {p.workDir || t(lang, 'workspace_default_hint')}
            </span>
          </div>
          <button
            onClick={p.onManageWorkspaces}
            className="shrink-0 px-3 py-2 rounded-md text-sm border border-border-light text-content-secondary hover:bg-surface-hover transition-colors cursor-pointer"
          >
            {t(lang, 'workspace_manage')}
          </button>
        </div>
      </div>

      {/* 模型参数（context_window / max_tokens / max_turns，放工作目录下方） */}
      <div className="space-y-3">
        <div>
          <div className={labelClass}>{t(lang, 'setupFieldContextWindow')}</div>
          <input
            type="number" min={1} step={1} value={p.contextWindow}
            onChange={(e) => p.onContextWindowChange(e.target.value)}
            className={`${inputClass} no-spinner`}
          />
        </div>
        <div>
          <div className={labelClass}>{t(lang, 'setupFieldMaxTokens')}</div>
          <input
            type="number" min={1} step={1} value={p.maxTokens}
            onChange={(e) => p.onMaxTokensChange(e.target.value)}
            className={`${inputClass} no-spinner`}
          />
        </div>
        <div>
          <div className={labelClass}>{t(lang, 'setupFieldMaxTurns')}</div>
          <input
            type="number" min={1} max={512} step={1} value={p.maxTurns}
            onChange={(e) => p.onMaxTurnsChange(e.target.value)}
            className={`${inputClass} no-spinner`}
          />
        </div>
      </div>

      {/* 记忆配置 */}
      <div className="space-y-3 rounded-lg border border-border-light p-4 bg-surface-card-alt/50">
        <div className={labelClass}>{t(lang, 'setupFieldMemory')}</div>
        {/* 启用开关 */}
        <div className="flex items-center justify-between">
          <span className="text-sm text-content-primary">{t(lang, 'setupFieldMemoryEnabled')}</span>
          <ToggleSwitch checked={p.memEnabled} onChange={p.onMemEnabledChange} label={t(lang, 'setupFieldMemoryEnabled')} />
        </div>
        {/* 后台自动提取开关 */}
        <div className="flex items-center justify-between">
          <span className="text-sm text-content-primary">{t(lang, 'setupFieldMemoryAutoExtract')}</span>
          <ToggleSwitch checked={p.memAutoExtract} onChange={p.onMemAutoExtractChange} label={t(lang, 'setupFieldMemoryAutoExtract')} />
        </div>
        {/* 记忆目录输入 */}
        <div>
          <div className={labelClass}>{t(lang, 'setupFieldMemoryDirectory')}</div>
          <input
            type="text"
            value={p.memDir}
            onChange={(e) => p.onMemDirChange(e.target.value)}
            className={inputClass}
            placeholder={t(lang, 'setupFieldMemoryDirectoryHint')}
          />
        </div>
        {/* 提取模型 */}
        <div>
          <div className={labelClass}>{t(lang, 'setupFieldMemoryExtractModel')}</div>
          <GlassDropdown
            value={p.memExtractModel}
            options={p.modelOptions}
            onChange={p.onMemExtractModelChange}
            placeholder={t(lang, 'setupFieldMemoryModelHint')}
          />
        </div>
        {/* 整合模型 */}
        <div>
          <div className={labelClass}>{t(lang, 'setupFieldMemoryDreamModel')}</div>
          <GlassDropdown
            value={p.memDreamModel}
            options={p.modelOptions}
            onChange={p.onMemDreamModelChange}
            placeholder={t(lang, 'setupFieldMemoryModelHint')}
          />
        </div>
      </div>

      {/* 自动标题配置 */}
      <div className="space-y-3 rounded-lg border border-border-light p-4 bg-surface-card-alt/50">
        <div className={labelClass}>{t(lang, 'setupFieldTitle')}</div>
        {/* 启用开关 */}
        <div className="flex items-center justify-between">
          <span className="text-sm text-content-primary">{t(lang, 'setupFieldTitleEnabled')}</span>
          <ToggleSwitch checked={p.titleEnabled} onChange={p.onTitleEnabledChange} label={t(lang, 'setupFieldTitleEnabled')} />
        </div>
        <div className="text-[11px] text-content-disabled">{t(lang, 'setupFieldTitleEnabledHint')}</div>
        {/* 标题生成模型 */}
        <div>
          <div className={labelClass}>{t(lang, 'setupFieldTitleModel')}</div>
          <GlassDropdown
            value={p.titleModel}
            options={p.modelOptions}
            onChange={p.onTitleModelChange}
            placeholder={t(lang, 'setupFieldTitleModelHint')}
          />
        </div>
      </div>

      {/* 权限 LLM 自动审核配置（auto 模式高危操作与沙箱拦截由 LLM 审核放行） */}
      <div className="space-y-3 rounded-lg border border-border-light p-4 bg-surface-card-alt/50">
        <div className={labelClass}>{t(lang, 'setupFieldPermissionReview')}</div>
        {/* 启用开关 */}
        <div className="flex items-center justify-between">
          <span className="text-sm text-content-primary">{t(lang, 'setupFieldPermissionReviewEnabled')}</span>
          <ToggleSwitch checked={p.reviewAuto} onChange={p.onReviewAutoChange} label={t(lang, 'setupFieldPermissionReviewEnabled')} />
        </div>
        <div className="text-[11px] text-content-disabled">{t(lang, 'setupFieldPermissionReviewHint')}</div>
        {/* 审核模型 */}
        <div>
          <div className={labelClass}>{t(lang, 'setupFieldPermissionReviewModel')}</div>
          <GlassDropdown
            value={p.reviewModel}
            options={p.modelOptions}
            onChange={p.onReviewModelChange}
            placeholder={t(lang, 'setupFieldPermissionReviewModelHint')}
          />
        </div>
      </div>
    </div>
  );
}

// ===== 环境编辑器 =====

interface EnvEditorProps {
  lang: UiLanguage;
  draft: EnvDraft;
  onDraftChange: (d: EnvDraft) => void;
  onFormatChange: (f: string) => void;
  onUpdateModel: (i: number, v: string) => void;
  onAddModel: () => void;
  onRemoveModel: (i: number) => void;
}

/** 环境编辑器：api_format / base_url / 认证 / 模型列表 / OAuth */
function EnvEditor({ lang, draft, onDraftChange, onFormatChange, onUpdateModel, onAddModel, onRemoveModel }: EnvEditorProps) {
  const isOauth = OAUTH_FORMATS.has(draft.api_format);
  /** OAuth 流程状态 */
  const [oauth, setOauth] = useState<OauthState>({ status: 'idle', user_code: '', verification_uri: '', device_code: '', error: '' });
  /** 轮询计数 ref */
  const pollCountRef = useRef(0);

  // OAuth 轮询：status=pending 时定时 poll
  useEffect(() => {
    if (oauth.status !== 'pending') return;
    pollCountRef.current = 0;
    const provider = draft.api_format as 'copilot' | 'codex';
    const timer = setInterval(async () => {
      pollCountRef.current += 1;
      if (pollCountRef.current > OAUTH_MAX_POLLS) {
        clearInterval(timer);
        setOauth((s) => ({ ...s, status: 'failed', error: 'timeout' }));
        return;
      }
      try {
        const res = await oauthApi.poll(provider, oauth.device_code);
        if (res.success) {
          clearInterval(timer);
          setOauth((s) => ({ ...s, status: 'success' }));
          onDraftChange({ ...draft, oauth_authorized: true });
        } else if (res.error) {
          clearInterval(timer);
          setOauth((s) => ({ ...s, status: 'failed', error: res.error ?? 'failed' }));
        }
      } catch (err) {
        clearInterval(timer);
        setOauth((s) => ({ ...s, status: 'failed', error: err instanceof Error ? err.message : String(err) }));
      }
    }, OAUTH_POLL_INTERVAL);
    return () => clearInterval(timer);
  }, [oauth.status, oauth.device_code, draft.api_format, draft, onDraftChange]);

  /** 启动 OAuth 设备码流程 */
  const startOauth = useCallback(async () => {
    setOauth({ status: 'pending', user_code: '', verification_uri: '', device_code: '', error: '' });
    try {
      const provider = draft.api_format as 'copilot' | 'codex';
      const res = await oauthApi.start(provider);
      setOauth({ status: 'pending', user_code: res.user_code, verification_uri: res.verification_uri, device_code: res.device_code, error: '' });
    } catch (err) {
      setOauth({ status: 'failed', user_code: '', verification_uri: '', device_code: '', error: err instanceof Error ? err.message : String(err) });
    }
  }, [draft.api_format]);

  return (
    <div className="space-y-4 rounded-lg border border-border-light p-4 bg-surface-card-alt/50">
      {/* api_format */}
      <div>
        <div className={labelClass}>{t(lang, 'setupFieldApiFormat')}</div>
        <GlassDropdown value={draft.api_format} options={formatOptionsLocal(lang)} onChange={onFormatChange} />
      </div>

      {/* base_url */}
      <div>
        <div className={labelClass}>{t(lang, 'setupFieldBaseUrl')}</div>
        <input
          type="text"
          value={draft.base_url}
          onChange={(e) => onDraftChange({ ...draft, base_url: e.target.value })}
          className={inputClass}
        />
      </div>

      {/* 认证：OAuth 格式走设备码，其余走密钥输入 */}
      {isOauth ? (
        <div className="space-y-2">
          <div className={labelClass}>{t(lang, 'setupFieldAuthType')}</div>
          {oauth.status === 'idle' && (
            <button onClick={startOauth} className="px-3 py-2 text-sm text-white bg-primary hover:bg-primary-hover rounded-md transition-colors cursor-pointer">
              {t(lang, 'setupOauthStart')}
            </button>
          )}
          {oauth.status === 'pending' && (
            <div className="space-y-1.5 rounded-md border border-border-light p-3 bg-surface-card">
              <div className="text-xs text-content-secondary">{t(lang, 'setupOauthOpenUrl')}</div>
              <div className="text-sm text-primary break-all">{oauth.verification_uri}</div>
              <div className="text-xs text-content-secondary">{t(lang, 'setupOauthDeviceCode')}</div>
              <div className="text-sm font-mono text-content-primary tracking-widest">{oauth.user_code}</div>
              <div className="flex items-center gap-2 text-xs text-content-disabled">
                <svg className="w-3 h-3 animate-spin" viewBox="0 0 16 16" fill="none">
                  <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeOpacity="0.4" />
                  <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                </svg>
                {t(lang, 'setupOauthPolling')}
              </div>
            </div>
          )}
          {oauth.status === 'success' && (
            <div className="text-sm text-success">{t(lang, 'setupOauthSuccess')}</div>
          )}
          {oauth.status === 'failed' && (
            <div className="text-sm text-danger">{t(lang, 'setupOauthFailed')}: {oauth.error}</div>
          )}
        </div>
      ) : (
        <>
          <div>
            <div className={labelClass}>{t(lang, 'setupFieldAuthType')}</div>
            <GlassDropdown
              value={draft.auth_type}
              options={[
                { value: 'api_key', label: t(lang, 'setupAuthApiKey') },
                // 仅 anthropic 格式支持 auth_token（Bearer Token）；openai 格式只有 api_key
                ...(draft.api_format === 'anthropic' ? [{ value: 'auth_token', label: t(lang, 'setupAuthAuthToken') }] : []),
              ]}
              onChange={(v) => onDraftChange({ ...draft, auth_type: v as 'api_key' | 'auth_token' })}
            />
          </div>
          <div>
            <div className={labelClass}>{draft.auth_type === 'api_key' ? t(lang, 'setupFieldApiKey') : t(lang, 'setupFieldAuthToken')}</div>
            <input
              type="password"
              value={draft.auth_type === 'api_key' ? draft.api_key : draft.auth_token}
              onChange={(e) => onDraftChange({
                ...draft,
                [draft.auth_type === 'api_key' ? 'api_key' : 'auth_token']: e.target.value,
              })}
              className={inputClass}
            />
          </div>
        </>
      )}

      {/* 模型列表 */}
      <div>
        <div className={labelClass}>{t(lang, 'setupFieldModels')}</div>
        <div className="space-y-2">
          {draft.models.map((m, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <input
                type="text"
                value={m}
                onChange={(e) => onUpdateModel(idx, e.target.value)}
                className={inputClass}
                placeholder={t(lang, 'setupFieldModel')}
              />
              {draft.models.length > 1 && (
                <button onClick={() => onRemoveModel(idx)} className="shrink-0 w-7 h-7 flex items-center justify-center rounded text-content-disabled hover:text-danger hover:bg-surface-hover transition-colors cursor-pointer" title={t(lang, 'setupFieldRemoveModel')}>
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M2 6h8" /></svg>
                </button>
              )}
            </div>
          ))}
          <button onClick={onAddModel} className="text-xs text-primary hover:underline cursor-pointer">+ {t(lang, 'setupFieldAddModel')}</button>
        </div>
      </div>
    </div>
  );
}

/** api_format 下拉选项（EnvEditor 局部） */
function formatOptionsLocal(lang: UiLanguage): DropdownOption[] {
  return API_FORMATS.map((f) => {
    const keyMap: Record<string, string> = {
      anthropic: 'setupFormatAnthropic', openai: 'setupFormatOpenai',
      copilot: 'setupFormatCopilot', codex: 'setupFormatCodex',
    };
    return { value: f, label: t(lang, keyMap[f] ?? f) };
  });
}

// ===== 渠道配置 Tab =====

interface ChannelsTabProps {
  lang: UiLanguage;
  channels: ChannelsCfg;
  onChannelsChange: (c: ChannelsCfg) => void;
  /** 注册的工作区列表（渠道运行目录选择数据源） */
  workspaces: WebWorkspaceItem[];
}

/** 渠道配置 Tab：飞书 / 微信 / QQ */
function ChannelsTab({ lang, channels, onChannelsChange, workspaces }: ChannelsTabProps) {
  /** 各渠道运行时状态（守护进程内 runner 活跃情况） */
  const [runtimeStatus, setRuntimeStatus] = useState<ChannelsRuntimeStatus>({});
  /** 状态加载中 */
  const [statusLoading, setStatusLoading] = useState(false);
  /** 守护进程初始化中（runner 尚未全部就绪），期间禁用启停 toggle */
  const [initializing, setInitializing] = useState(true);

  /** 检查初始化是否完成：所有 enabled 渠道都出现在 runtimeStatus 中 */
  const checkInitDone = useCallback((status: ChannelsRuntimeStatus): boolean => {
    const enabledNames = (['feishu', 'weixin', 'qq'] as const).filter((n) => channels[n].enabled);
    if (enabledNames.length === 0) return true;
    return enabledNames.every((n) => status[n] != null);
  }, [channels]);

  /** 刷新运行时状态
   *
   * @param silent 静默模式（轮询用）：不置 statusLoading，避免每 2s
   *   状态文本抖动/按钮闪烁；仅挂载时的首次加载显示加载态。
   */
  const refreshStatus = useCallback(async (silent = false) => {
    if (!silent) setStatusLoading(true);
    try {
      const s = await channelsApi.getStatus();
      setRuntimeStatus(s);
      if (checkInitDone(s)) setInitializing(false);
    } catch {
      // 守护进程未运行时静默
    } finally {
      if (!silent) setStatusLoading(false);
    }
  }, [checkInitDone]);

  // ref 持有最新 refreshStatus，避免轮询 useEffect 因 refreshStatus 变化频繁重启
  const refreshStatusRef = useRef(refreshStatus);
  refreshStatusRef.current = refreshStatus;

  // 挂载时加载运行时状态 + 初始化轮询（每 2s 静默刷新直到完成或 60s 超时）
  // 依赖空数组：仅挂载时启动一次，轮询内部通过 ref 调用最新函数
  useEffect(() => {
    refreshStatusRef.current();
    let elapsed = 0;
    const timer = setInterval(async () => {
      elapsed += 2;
      await refreshStatusRef.current(true);
      if (elapsed >= 60) {
        setInitializing(false);
        clearInterval(timer);
      }
    }, 2000);
    return () => clearInterval(timer);
  }, []);

  /** 启动渠道 runner
   *
   * 多目录空间：启动时携带渠道配置的运行目录（未配置时用默认工作区），
   * 后端先落盘再按需拉起守护进程——web 端不再自动启动渠道守护进程。
   * 守护进程收到 start_channel 后通过 call_soon_threadsafe 异步调度
   * _start_channel_internal 创建 runner，前端立即查询状态可能尚未创建完。
   * 故启动成功后延迟 600ms 再刷新状态，避免偶发显示"已停止"。
   */
  const handleStart = useCallback(async (name: string) => {
    try {
      // 渠道配置的运行目录（working_directory）或默认工作区
      const cfg = channels[name as 'feishu' | 'weixin' | 'qq'];
      const wd = (cfg?.working_directory as string | undefined)
        || workspaces.find((w) => w.is_default)?.path;
      await channelsApi.start(name, wd);
      await new Promise((r) => setTimeout(r, 600));
      await refreshStatus();
    } catch { /* 静默 */ }
  }, [refreshStatus, channels, workspaces]);

  /** 停止渠道 runner */
  const handleStop = useCallback(async (name: string) => {
    try {
      await channelsApi.stop(name);
      await refreshStatus();
    } catch { /* 静默 */ }
  }, [refreshStatus]);

  /** 启用渠道时的目录缺失错误（本地即时提示，后端 PATCH 亦校验兜底） */
  const [enableError, setEnableError] = useState<string | null>(null);

  /** 切换渠道 enabled 配置（即时保存到后端，不依赖保存按钮）
   *
   * 多目录空间：启用渠道（v=true）必须已配置运行目录（working_directory），
   * 未配置时阻止并提示——渠道 agent 需要锚定工作区。
   * 停用（v=false）时先通过 IPC 停止运行中的 runner，再保存 enabled=false。
   * 启用（v=true）时只保存配置，不自动启动 runner（由用户通过 toggle 手动启动）。
   */
  const handleToggleEnabled = useCallback(async (name: 'feishu' | 'weixin' | 'qq', v: boolean) => {
    setEnableError(null);
    if (v && !channels[name].working_directory) {
      const channelLabel = name === 'feishu' ? t(lang, 'setupChannelFeishu')
        : name === 'weixin' ? t(lang, 'setupChannelWeixin')
        : t(lang, 'setupChannelQQ');
      setEnableError(t(lang, 'setupChannelNeedWorkDir').replace('{channel}', channelLabel));
      return;
    }
    if (!v) {
      try { await channelsApi.stop(name); } catch { /* 静默 */ }
    }
    onChannelsChange({ ...channels, [name]: { ...channels[name], enabled: v } });
    channelsApi.update({ [name]: { enabled: v } } as Partial<{ feishu: { enabled: boolean }; weixin: { enabled: boolean }; qq: { enabled: boolean } }>).catch(() => { /* 静默 */ });
  }, [channels, onChannelsChange, lang]);

  return (
    <div className="space-y-4">
      {enableError && <div className="text-xs text-danger">{enableError}</div>}
      <ChannelSection
        lang={lang}
        channelName="feishu"
        title={t(lang, 'setupChannelFeishu')}
        enabled={channels.feishu.enabled}
        onToggle={(v) => handleToggleEnabled('feishu', v)}
        runtimeStatus={runtimeStatus.feishu}
        statusLoading={statusLoading}
        initializing={initializing}
        onStart={handleStart}
        onStop={handleStop}
        footer={
          <TestConnectionButton
            lang={lang}
            channelName="feishu"
            payload={{
              app_id: channels.feishu.app_id,
              app_secret: channels.feishu.app_secret,
              domain: channels.feishu.domain,
            }}
          />
        }
      >
        <ChannelWorkDirField
          lang={lang}
          workspaces={workspaces}
          value={channels.feishu.working_directory ?? ''}
          onChange={(v) => onChannelsChange({ ...channels, feishu: { ...channels.feishu, working_directory: v } })}
        />
        <TextField lang={lang} labelKey="setupChannelAppId" value={channels.feishu.app_id} onChange={(v) => onChannelsChange({ ...channels, feishu: { ...channels.feishu, app_id: v } })} />
        <TextField lang={lang} labelKey="setupChannelAppSecret" value={channels.feishu.app_secret} onChange={(v) => onChannelsChange({ ...channels, feishu: { ...channels.feishu, app_secret: v } })} />
        <SelectField lang={lang} labelKey="setupChannelDomain" value={channels.feishu.domain}
          options={[{ value: 'feishu', label: t(lang, 'setupChannelDomainFeishu') }, { value: 'lark', label: t(lang, 'setupChannelDomainLark') }]}
          onChange={(v) => onChannelsChange({ ...channels, feishu: { ...channels.feishu, domain: v } })} />
        <BoolField lang={lang} labelKey="setupChannelRequireMention" checked={channels.feishu.require_mention} onChange={(v) => onChannelsChange({ ...channels, feishu: { ...channels.feishu, require_mention: v } })} />
        <BoolField lang={lang} labelKey="setupChannelAllowBots" checked={channels.feishu.allow_bots} onChange={(v) => onChannelsChange({ ...channels, feishu: { ...channels.feishu, allow_bots: v } })} />
        <BoolField lang={lang} labelKey="setupChannelGroupSessionsPerUser" checked={channels.feishu.group_sessions_per_user} onChange={(v) => onChannelsChange({ ...channels, feishu: { ...channels.feishu, group_sessions_per_user: v } })} />
        <BoolField lang={lang} labelKey="setupChannelShowReasoning" checked={channels.feishu.show_reasoning} onChange={(v) => onChannelsChange({ ...channels, feishu: { ...channels.feishu, show_reasoning: v } })} />
        <GroupPolicyFields lang={lang} policy={channels.feishu.group_policy} onChange={(gp) => onChannelsChange({ ...channels, feishu: { ...channels.feishu, group_policy: gp } })} />
      </ChannelSection>

      <ChannelSection
        lang={lang}
        channelName="weixin"
        title={t(lang, 'setupChannelWeixin')}
        enabled={channels.weixin.enabled}
        onToggle={(v) => handleToggleEnabled('weixin', v)}
        runtimeStatus={runtimeStatus.weixin}
        statusLoading={statusLoading}
        initializing={initializing}
        onStart={handleStart}
        onStop={handleStop}
        footer={
          <WeixinQrLogin
            lang={lang}
            onLoginSuccess={(creds) => onChannelsChange({
              ...channels,
              weixin: {
                ...channels.weixin,
                account_id: creds.account_id,
                token: creds.token,
                base_url: creds.base_url,
                user_id: creds.user_id,
              },
            })}
          />
        }
      >
        <ChannelWorkDirField
          lang={lang}
          workspaces={workspaces}
          value={channels.weixin.working_directory ?? ''}
          onChange={(v) => onChannelsChange({ ...channels, weixin: { ...channels.weixin, working_directory: v } })}
        />
        <TextField lang={lang} labelKey="setupChannelAccountId" value={channels.weixin.account_id} onChange={(v) => onChannelsChange({ ...channels, weixin: { ...channels.weixin, account_id: v } })} />
        <TextField lang={lang} labelKey="setupChannelToken" value={channels.weixin.token} onChange={(v) => onChannelsChange({ ...channels, weixin: { ...channels.weixin, token: v } })} />
        <TextField lang={lang} labelKey="setupChannelBaseUrl" value={channels.weixin.base_url} onChange={(v) => onChannelsChange({ ...channels, weixin: { ...channels.weixin, base_url: v } })} />
        <TextField lang={lang} labelKey="setupChannelCdnBaseUrl" value={channels.weixin.cdn_base_url} onChange={(v) => onChannelsChange({ ...channels, weixin: { ...channels.weixin, cdn_base_url: v } })} />
        <TextField lang={lang} labelKey="setupChannelUserId" value={channels.weixin.user_id} onChange={(v) => onChannelsChange({ ...channels, weixin: { ...channels.weixin, user_id: v } })} />
        <BoolField lang={lang} labelKey="setupChannelAllowBots" checked={channels.weixin.allow_bots} onChange={(v) => onChannelsChange({ ...channels, weixin: { ...channels.weixin, allow_bots: v } })} />
      </ChannelSection>

      <ChannelSection
        lang={lang}
        channelName="qq"
        title={t(lang, 'setupChannelQQ')}
        enabled={channels.qq.enabled}
        onToggle={(v) => handleToggleEnabled('qq', v)}
        runtimeStatus={runtimeStatus.qq}
        statusLoading={statusLoading}
        initializing={initializing}
        onStart={handleStart}
        onStop={handleStop}
        footer={
          <TestConnectionButton
            lang={lang}
            channelName="qq"
            payload={{
              app_id: channels.qq.app_id,
              client_secret: channels.qq.client_secret,
            }}
          />
        }
      >
        <ChannelWorkDirField
          lang={lang}
          workspaces={workspaces}
          value={channels.qq.working_directory ?? ''}
          onChange={(v) => onChannelsChange({ ...channels, qq: { ...channels.qq, working_directory: v } })}
        />
        <TextField lang={lang} labelKey="setupChannelAppId" value={channels.qq.app_id} onChange={(v) => onChannelsChange({ ...channels, qq: { ...channels.qq, app_id: v } })} />
        <TextField lang={lang} labelKey="setupChannelClientSecret" value={channels.qq.client_secret} onChange={(v) => onChannelsChange({ ...channels, qq: { ...channels.qq, client_secret: v } })} />
        <BoolField lang={lang} labelKey="setupChannelMarkdownSupport" checked={channels.qq.markdown_support} onChange={(v) => onChannelsChange({ ...channels, qq: { ...channels.qq, markdown_support: v } })} />
        <BoolField lang={lang} labelKey="setupChannelAllowBots" checked={channels.qq.allow_bots} onChange={(v) => onChannelsChange({ ...channels, qq: { ...channels.qq, allow_bots: v } })} />
        <BoolField lang={lang} labelKey="setupChannelGroupSessionsPerUser" checked={channels.qq.group_sessions_per_user} onChange={(v) => onChannelsChange({ ...channels, qq: { ...channels.qq, group_sessions_per_user: v } })} />
        <BoolField lang={lang} labelKey="setupChannelRequireMention" checked={channels.qq.require_mention} onChange={(v) => onChannelsChange({ ...channels, qq: { ...channels.qq, require_mention: v } })} />
        <BoolField lang={lang} labelKey="setupChannelShowReasoning" checked={channels.qq.show_reasoning} onChange={(v) => onChannelsChange({ ...channels, qq: { ...channels.qq, show_reasoning: v } })} />
        <GroupPolicyFields lang={lang} policy={channels.qq.group_policy} onChange={(gp) => onChannelsChange({ ...channels, qq: { ...channels.qq, group_policy: gp } })} />
      </ChannelSection>
    </div>
  );
}

// ===== 沙箱配置 Tab =====

interface SandboxTabProps {
  lang: UiLanguage;
  sandbox: SandboxSettings | null;
  onSandboxChange: (s: SandboxSettings) => void;
  error: string | null;
  saved: boolean;
  permission: PermissionRiskSettings | null;
}

/** 沙箱配置 Tab：文件系统/网络/高级选项 + 风险分级（LOW/MEDIUM/HIGH）只读展示 */
function SandboxTab({ lang, sandbox, onSandboxChange, error, saved, permission }: SandboxTabProps) {
  if (!sandbox) {
    return <div className="text-sm text-content-disabled py-4">{t(lang, 'setupFormLoadFailed')}</div>;
  }
  const update = (patch: Partial<SandboxSettings>) => onSandboxChange({ ...sandbox, ...patch });
  const updateFs = (patch: Partial<SandboxSettings['filesystem']>) =>
    onSandboxChange({ ...sandbox, filesystem: { ...sandbox.filesystem, ...patch } });
  const updateNet = (patch: Partial<SandboxSettings['network']>) =>
    onSandboxChange({ ...sandbox, network: { ...sandbox.network, ...patch } });

  return (
    <div className="space-y-3">
      {error && <div className="text-xs text-danger">{t(lang, 'setupSandboxSaveFailed')}: {error}</div>}
      {saved && <div className="text-xs text-success">{t(lang, 'setupSandboxSaveSuccess')}</div>}

      {/* 平台与排除命令 */}
      <SandboxSection lang={lang} titleKey="setupFieldSandboxPlatform" >
        <TextFieldWithHint
          lang={lang}
          labelKey="setupFieldSandboxEnabledPlatforms"
          hintKey="setupFieldSandboxEnabledPlatformsHint"
          value={sandbox.enabled_platforms.join(', ')}
          onChange={(v) => update({ enabled_platforms: v.split(',').map((s) => s.trim()).filter(Boolean) })}
        />
        <StringListField
          lang={lang}
          labelKey="setupFieldSandboxExcludedCommands"
          hintKey="setupFieldSandboxExcludedCommandsHint"
          value={sandbox.excluded_commands}
          onChange={(v) => update({ excluded_commands: v })}
        />
      </SandboxSection>

      {/* 文件系统 */}
      <SandboxSection lang={lang} titleKey="setupFieldSandbox" >
        <StringListField lang={lang} labelKey="setupFieldSandboxAllowWrite" value={sandbox.filesystem.allow_write} onChange={(v) => updateFs({ allow_write: v })} />
        <StringListField lang={lang} labelKey="setupFieldSandboxDenyWrite" value={sandbox.filesystem.deny_write} onChange={(v) => updateFs({ deny_write: v })} />
        <StringListField lang={lang} labelKey="setupFieldSandboxDenyRead" value={sandbox.filesystem.deny_read} onChange={(v) => updateFs({ deny_read: v })} />
        <StringListField
          lang={lang}
          labelKey="setupFieldSandboxAllowRead"
          hintKey="setupFieldSandboxAllowReadHint"
          value={sandbox.filesystem.allow_read}
          onChange={(v) => updateFs({ allow_read: v })}
        />
      </SandboxSection>

      {/* 网络 */}
      <SandboxSection lang={lang} titleKey="setupFieldSandboxNetwork" >
        <TextFieldWithHint lang={lang} labelKey="setupFieldSandboxAllowDomains" value={sandbox.network.allowed_domains.join(', ')} onChange={(v) => updateNet({ allowed_domains: v.split(',').map((s) => s.trim()).filter(Boolean) })} />
        <TextFieldWithHint lang={lang} labelKey="setupFieldSandboxDenyDomains" value={sandbox.network.denied_domains.join(', ')} onChange={(v) => updateNet({ denied_domains: v.split(',').map((s) => s.trim()).filter(Boolean) })} />
        <BoolField lang={lang} labelKey="setupFieldSandboxAllowAllUnixSockets" checked={sandbox.network.allow_all_unix_sockets} onChange={(v) => updateNet({ allow_all_unix_sockets: v })} />
        <BoolField lang={lang} labelKey="setupFieldSandboxAllowLocalBinding" checked={sandbox.network.allow_local_binding} onChange={(v) => updateNet({ allow_local_binding: v })} />
      </SandboxSection>

      {/* 高级选项（默认折叠，减少视觉噪音） */}
      <SandboxSection lang={lang} titleKey="setupFieldSandboxAdvanced">
        <BoolWithHint lang={lang} labelKey="setupFieldSandboxWeakerNetworkIsolation" checked={sandbox.enable_weaker_network_isolation} onChange={(v) => update({ enable_weaker_network_isolation: v })} />
        <BoolField lang={lang} labelKey="setupFieldSandboxWeakerNested" checked={sandbox.enable_weaker_nested_sandbox} onChange={(v) => update({ enable_weaker_nested_sandbox: v })} />
        <BoolField lang={lang} labelKey="setupFieldSandboxAllowGitConfig" checked={sandbox.allow_git_config} onChange={(v) => update({ allow_git_config: v })} />
        <div>
          <div className={labelClass}>{t(lang, 'setupFieldSandboxMandatoryDepth')}</div>
          <input
            type="number"
            min={1}
            max={10}
            value={sandbox.mandatory_deny_search_depth}
            onChange={(e) => update({ mandatory_deny_search_depth: Math.max(1, Math.min(10, Number(e.target.value) || 3)) })}
            className="no-spinner w-full px-3 py-2 rounded-md bg-surface-card-alt border border-border-light text-content-primary text-sm focus:outline-none focus:border-primary focus:shadow-glow transition-all duration-200"
          />
        </div>
        <TextFieldWithHint lang={lang} labelKey="setupFieldSandboxRipgrepCommand" value={sandbox.ripgrep?.command ?? 'rg'} onChange={(v) => update({ ripgrep: { command: v || 'rg', args: sandbox.ripgrep?.args ?? [] } })} />
      </SandboxSection>

      {/* 风险分级（LOW / MEDIUM / HIGH 三层级，内置只读展示，默认折叠） */}
      {permission && (
        <SandboxSection lang={lang} titleKey="setupFieldSandboxRiskLevels">
          <div className="space-y-3">
            {/* HIGH 层级 */}
            <RiskLevelCard
              lang={lang}
              level="HIGH"
              titleKey="setupFieldSandboxRiskHigh"
              hintKey="setupFieldSandboxRiskHighHint"
              badgeClass="text-danger bg-danger/10"
            >
              <RiskPatternGroup
                lang={lang}
                titleKey="setupFieldSandboxRiskHighBash"
                examplesTitleKey="setupFieldSandboxRiskHighBashExamples"
                patterns={permission.dangerous_bash_patterns}
                examples={[
                  { cmd: 'rm -rf build/', desc: t(lang, 'setupRiskExampleRmRf') },
                  { cmd: 'sudo rm /etc/hosts', desc: t(lang, 'setupRiskExampleSudoRm') },
                  { cmd: 'git reset --hard HEAD~1', desc: t(lang, 'setupRiskExampleGitHard') },
                  { cmd: 'git clean -fd src/', desc: t(lang, 'setupRiskExampleGitClean') },
                ]}
              />
              <RiskPatternGroup
                lang={lang}
                titleKey="setupFieldSandboxRiskHighPowershell"
                examplesTitleKey="setupFieldSandboxRiskHighPowershellExamples"
                patterns={permission.dangerous_powershell_patterns}
                examples={[
                  { cmd: 'Remove-Item -Recurse C:\\Temp', desc: t(lang, 'setupRiskExamplePsRecurse') },
                  { cmd: 'Clear-Content .\\log.txt', desc: t(lang, 'setupRiskExamplePsClear') },
                  { cmd: 'Format-Volume -DriveLetter D', desc: t(lang, 'setupRiskExamplePsFormat') },
                ]}
              />
            </RiskLevelCard>

            {/* MEDIUM 层级 */}
            <RiskLevelCard
              lang={lang}
              level="MEDIUM"
              titleKey="setupFieldSandboxRiskMedium"
              hintKey="setupFieldSandboxRiskMediumHint"
              badgeClass="text-warning bg-warning/10"
            >
              <RiskGrid lang={lang} labelKey="setupFieldSandboxRiskMediumTools" items={permission.medium_risk_tools} />
            </RiskLevelCard>

            {/* LOW 层级 */}
            <RiskLevelCard
              lang={lang}
              level="LOW"
              titleKey="setupFieldSandboxRiskLow"
              hintKey="setupFieldSandboxRiskLowHint"
              badgeClass="text-success bg-success/10"
            >
              <RiskGrid lang={lang} labelKey="setupFieldSandboxRiskLowCommands" items={permission.read_only_commands} />
            </RiskLevelCard>
          </div>
        </SandboxSection>
      )}
    </div>
  );
}

/** 可折叠区块：沙箱设置的折叠面板，点击标题展开/收起
 *
 * @param props - 组件属性
 * @param props.titleKey - 区块标题 i18n key
 * @param props.subtitleKey - 可选副标题（右对齐，如"折叠后仍展示"的信息）
 * @param props.defaultOpen - 默认是否展开
 * @param props.children - 区块内容
 */
function SandboxSection({ lang, titleKey, subtitleKey, defaultOpen = false, children }: {
  lang: UiLanguage; titleKey: string; subtitleKey?: string; defaultOpen?: boolean; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-lg border border-border-light overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2.5 px-4 py-2.5 bg-surface-card-alt hover:bg-surface-hover transition-colors cursor-pointer"
      >
        <svg className={`w-3 h-3 shrink-0 text-content-disabled transition-transform ${open ? 'rotate-90' : ''}`} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M4.5 3L7.5 6L4.5 9" /></svg>
        <span className="text-sm font-medium text-content-primary">{t(lang, titleKey)}</span>
        {subtitleKey && <span className="text-[11px] text-content-disabled truncate flex-1 text-right">{t(lang, subtitleKey)}</span>}
      </button>
      {open && (
        <div className="px-4 py-3 space-y-3">
          {children}
        </div>
      )}
    </div>
  );
}

/** 风险等级卡片：可折叠面板，带彩色等级徽章；默认折叠，展开后展示规则与示例
 *
 * @param props - 组件属性
 * @param props.level - 等级徽章文本（HIGH / MEDIUM / LOW）
 * @param props.titleKey - 标题 i18n key
 * @param props.hintKey - 说明 i18n key
 * @param props.badgeClass - 徽章配色（text-xx bg-xx/10）
 * @param props.children - 卡片内容（字段与示例）
 */
function RiskLevelCard({ lang, level, titleKey, hintKey, badgeClass, children }: {
  lang: UiLanguage; level: string; titleKey: string; hintKey: string; badgeClass: string; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-border-light overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2.5 px-3 py-2 bg-surface-card-alt hover:bg-surface-hover transition-colors cursor-pointer"
      >
        <svg className={`w-3 h-3 shrink-0 text-content-disabled transition-transform ${open ? 'rotate-90' : ''}`} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M4.5 3L7.5 6L4.5 9" /></svg>
        <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-bold tracking-wide ${badgeClass}`}>{level}</span>
        <span className="text-sm font-medium text-content-primary flex-1 text-left">{t(lang, titleKey)}</span>
      </button>
      {open && (
        <div className="px-4 py-3 space-y-3">
          <div className="text-[11px] text-content-disabled leading-relaxed">{t(lang, hintKey)}</div>
          {children}
        </div>
      )}
    </div>
  );
}

/** 风险规则分组：bash / powershell 各自为一个带标题的边框分组，
 * 组内为只读正则 textarea + 匹配示例，层级清晰、不再杂乱堆叠
 *
 * @param props - 组件属性
 * @param props.titleKey - 分组标题 i18n key（如"高危 bash 正则"）
 * @param props.examplesTitleKey - 示例区标题 i18n key
 * @param props.patterns - 只读正则列表
 * @param props.examples - 匹配示例列表（命令 + 说明）
 */
function RiskPatternGroup({ lang, titleKey, examplesTitleKey, patterns, examples }: {
  lang: UiLanguage; titleKey: string; examplesTitleKey: string; patterns: string[]; examples: { cmd: string; desc: string }[];
}) {
  return (
    <div className="rounded-md border border-border-light overflow-hidden">
      <div className="px-3 py-1.5 text-xs font-medium text-content-secondary bg-surface-card border-b border-border-light">{t(lang, titleKey)}</div>
      {/* 只读规则列表：固定高度 + 平滑滚动，避免禁写光标与原生 textarea 的粗糙滚动体验 */}
      <ReadOnlyList items={patterns} />
      <div className="p-3">
        <RiskExamples lang={lang} titleKey={examplesTitleKey} examples={examples} />
      </div>
    </div>
  );
}

/** 只读规则列表：固定高度 + 平滑滚动的只读展示，替代禁写的 textarea
 * 圆角 + 边框自洽呈现，与周围卡片过渡平滑
 *
 * @param props - 组件属性
 * @param props.items - 只读字符串列表（每条一行）
 */
function ReadOnlyList({ items }: { items: string[] }) {
  return (
    <div className="max-h-40 overflow-y-auto rounded-md border border-border-light bg-surface-card-alt px-3 py-2 font-mono text-xs text-content-primary leading-relaxed [scrollbar-gutter:stable]">
      {items.map((p, i) => (
        <div key={i} className="whitespace-pre-wrap break-all">{p}</div>
      ))}
    </div>
  );
}

/** 变更/只读工具网格：grid-cols-2 展示命令本身（无说明）
 *
 * @param props - 组件属性
 * @param props.labelKey - 字段标签 i18n key
 * @param props.items - 命令列表
 */
function RiskGrid({ lang, labelKey, items }: { lang: UiLanguage; labelKey: string; items: string[] }) {
  return (
    <div>
      <div className={labelClass}>{t(lang, labelKey)}</div>
      <div className="grid grid-cols-2 gap-2">
        {items.map((item, i) => (
          <div key={i} className="rounded-md bg-surface-card border border-border-light px-3 py-1.5 font-mono text-xs text-content-primary truncate">{item}</div>
        ))}
      </div>
    </div>
  );
}

/** 正则匹配示例区：用具体命令帮助零基础用户理解正则规则
 * 采用左右分栏网格，命令与说明更紧凑对齐
 *
 * @param props - 组件属性
 * @param props.titleKey - 示例区标题 i18n key
 * @param props.examples - 示例列表（命令 + 说明）
 */
function RiskExamples({ lang, titleKey, examples }: {
  lang: UiLanguage; titleKey: string; examples: { cmd: string; desc: string }[];
}) {
  return (
    <div>
      <div className={labelClass}>{t(lang, titleKey)}</div>
      <div className="grid grid-cols-2 gap-2">
        {examples.map((ex, i) => (
          <div key={i} className="rounded-md bg-surface-card border border-border-light px-3 py-2">
            <code className="font-mono text-content-primary text-xs block whitespace-nowrap overflow-hidden text-ellipsis">{ex.cmd}</code>
            <div className="text-[11px] text-content-disabled mt-0.5 truncate">{ex.desc}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** 带提示的布尔开关行 */
function BoolWithHint({ lang, labelKey, hintKey, checked, onChange }: { lang: UiLanguage; labelKey: string; hintKey?: string; checked: boolean; onChange: (v: boolean) => void; }) {
  return (
    <div>
      <div className="flex items-center justify-between">
        <span className="text-sm text-content-secondary">{t(lang, labelKey)}</span>
        <ToggleSwitch checked={checked} onChange={onChange} label={t(lang, labelKey)} />
      </div>
      {hintKey && <div className="text-[11px] text-content-disabled mt-1">{t(lang, hintKey)}</div>}
    </div>
  );
}

/** 带提示的文本输入行 */
function TextFieldWithHint({ lang, labelKey, hintKey, value, onChange }: { lang: UiLanguage; labelKey: string; hintKey?: string; value: string; onChange: (v: string) => void; }) {
  return (
    <div>
      <div className={labelClass}>{t(lang, labelKey)}</div>
      <input type="text" value={value} onChange={(e) => onChange(e.target.value)} className={inputClass} />
      {hintKey && <div className="text-[11px] text-content-disabled mt-1">{t(lang, hintKey)}</div>}
    </div>
  );
}

/** 字符串列表编辑（多行文本，每行一个元素；readOnly 时只读展示） */
function StringListField({ lang, labelKey, hintKey, value, onChange, readOnly }: { lang: UiLanguage; labelKey: string; hintKey?: string; value: string[]; onChange?: (v: string[]) => void; readOnly?: boolean; }) {
  return (
    <div>
      <div className={labelClass}>{t(lang, labelKey)}</div>
      <textarea
        rows={3}
        value={value.join('\n')}
        readOnly={readOnly}
        onChange={(e) => onChange?.(e.target.value.split('\n').map((s) => s.trim()).filter(Boolean))}
        className={`${inputClass} font-mono text-xs resize-y ${readOnly ? 'opacity-70 cursor-not-allowed' : ''}`}
      />
      {hintKey && <div className="text-[11px] text-content-disabled mt-1">{t(lang, hintKey)}</div>}
    </div>
  );
}

// ===== 渠道通用子组件 =====

interface ChannelSectionProps {
  lang: UiLanguage;
  /** 渠道名（feishu/weixin/qq，用于 start/stop API 调用） */
  channelName: string;
  title: string;
  enabled: boolean;
  onToggle: (v: boolean) => void;
  /** 运行时状态（守护进程内 runner 活跃情况） */
  runtimeStatus?: ChannelRuntimeStatusEntry;
  /** 状态加载中 */
  statusLoading: boolean;
  /** 守护进程初始化中（runner 尚未全部就绪），期间禁用启停 toggle */
  initializing: boolean;
  /** 启动渠道 runner */
  onStart: (name: string) => void;
  /** 停止渠道 runner */
  onStop: (name: string) => void;
  /** 渠道专属底部操作区（测试连接 / 扫码登录等） */
  footer?: React.ReactNode;
  children: React.ReactNode;
}

/** 单个渠道折叠卡片（标题 + 运行时启停开关 + 字段）
 *
 * UI 布局：
 * - 标题行右侧：左右 toggle switch 控制运行时启停（start/stop runner）
 * - 标题行左侧：展开箭头 + 渠道名 + 运行状态指示灯
 * - 展开区内：首个字段为"启用状态"下拉框（控制 channels.json 的 enabled 配置）
 */
function ChannelSection({ lang, channelName, title, enabled, onToggle, runtimeStatus, statusLoading, initializing, onStart, onStop, footer, children }: ChannelSectionProps) {
  const [expanded, setExpanded] = useState(false);
  /** 操作中（启动/停止） */
  const [actionLoading, setActionLoading] = useState(false);
  const isRunning = runtimeStatus?.running ?? false;
  /** 启停 toggle 是否禁用（初始化中、未 enabled、操作中、状态加载中） */
  const toggleDisabled = initializing || !enabled || actionLoading || statusLoading;

  /** 处理右侧 toggle：运行时启停（仅 enabled 且非初始化时可操作） */
  const handleToggle = useCallback(async () => {
    if (toggleDisabled) return;
    setActionLoading(true);
    try {
      if (isRunning) await onStop(channelName);
      else await onStart(channelName);
    } finally {
      setActionLoading(false);
    }
  }, [toggleDisabled, isRunning, channelName, onStart, onStop]);

  return (
    <div className="rounded-lg border border-border-light overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 bg-surface-card-alt">
        <div className="flex items-center gap-2">
          <button onClick={() => setExpanded(!expanded)} className="flex items-center gap-2 text-sm text-content-primary cursor-pointer">
            <svg className={`w-3 h-3 transition-transform ${expanded ? 'rotate-90' : ''}`} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M4.5 3L7.5 6L4.5 9" /></svg>
            {title}
          </button>
          {/* 运行状态指示灯（仅启用时显示） */}
          {enabled && (
            <div className="flex items-center gap-1.5 ml-2">
              {initializing ? (
                <span className="flex items-center gap-1 text-[11px] text-content-disabled">
                  <svg className="w-3 h-3 animate-spin" viewBox="0 0 16 16" fill="none">
                    <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeOpacity="0.4" />
                    <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                  </svg>
                  {t(lang, 'setupChannelInitializing')}
                </span>
              ) : (
                <span className={`flex items-center gap-1 text-[11px] ${isRunning ? 'text-success' : 'text-content-disabled'}`}>
                  <span className={`w-1.5 h-1.5 rounded-full ${isRunning ? 'bg-success' : 'bg-content-disabled'}`} />
                  {isRunning ? t(lang, 'setupChannelRunning') : t(lang, 'setupChannelStopped')}
                </span>
              )}
            </div>
          )}
        </div>
        {/* 右侧 toggle：运行时启停（未 enabled 或初始化中时禁用） */}
        <ToggleSwitch
          checked={isRunning}
          onChange={() => handleToggle()}
          disabled={toggleDisabled}
          label={isRunning ? t(lang, 'setupChannelStop') : t(lang, 'setupChannelStart')}
          title={isRunning ? t(lang, 'setupChannelStop') : t(lang, 'setupChannelStart')}
        />
      </div>
      {expanded && (
        <div className="px-4 py-3 space-y-3">
          {/* 启用状态下拉框（替代原标题行右侧 enable toggle） */}
          <SelectField
            lang={lang}
            labelKey="setupChannelEnabled"
            value={enabled ? 'enabled' : 'disabled'}
            options={[
              { value: 'enabled', label: t(lang, 'setupChannelEnabledOption') },
              { value: 'disabled', label: t(lang, 'setupChannelDisabledOption') },
            ]}
            onChange={(v) => onToggle(v === 'enabled')}
          />
          {children}
          {footer && <div className="pt-2 border-t border-border-light">{footer}</div>}
        </div>
      )}
    </div>
  );
}

/** 渠道运行目录选择行（多目录空间：渠道 agent 固定在该目录运行） */
function ChannelWorkDirField({ lang, workspaces, value, onChange }: {
  lang: UiLanguage;
  workspaces: WebWorkspaceItem[];
  value: string;
  onChange: (v: string) => void;
}) {
  const options: DropdownOption[] = [
    // 当前值未在注册表中时保留（兼容外部设置）
    ...(value && !workspaces.some((w) => w.path === value)
      ? [{ value, label: value.split(/[\\/]/).filter(Boolean).pop() || value }]
      : []),
    ...workspaces.map((w) => ({
      value: w.path,
      label: w.is_default ? `${w.name} · ${t(lang, 'workspace_default_badge')}` : w.name,
    })),
  ];
  return (
    <div>
      <div className={labelClass}>{t(lang, 'setupChannelWorkDir')}</div>
      <GlassDropdown
        value={value}
        placeholder={t(lang, 'setupChannelWorkDirPlaceholder')}
        options={options}
        onChange={onChange}
      />
      <div className="text-[11px] text-content-disabled mt-1">{t(lang, 'setupChannelWorkDirHint')}</div>
    </div>
  );
}

/** 文本字段行 */
function TextField({ lang, labelKey, value, onChange }: { lang: UiLanguage; labelKey: string; value: string; onChange: (v: string) => void; }) {  return (
    <div>
      <div className={labelClass}>{t(lang, labelKey)}</div>
      <input type="text" value={value} onChange={(e) => onChange(e.target.value)} className={inputClass} />
    </div>
  );
}

/** 布尔字段行（开关） */
function BoolField({ lang, labelKey, checked, onChange }: { lang: UiLanguage; labelKey: string; checked: boolean; onChange: (v: boolean) => void; }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-content-secondary">{t(lang, labelKey)}</span>
      <ToggleSwitch checked={checked} onChange={onChange} label={t(lang, labelKey)} />
    </div>
  );
}

/** 下拉字段行 */
function SelectField({ lang, labelKey, value, options, onChange }: { lang: UiLanguage; labelKey: string; value: string; options: DropdownOption[]; onChange: (v: string) => void; }) {
  return (
    <div>
      <div className={labelClass}>{t(lang, labelKey)}</div>
      <GlassDropdown value={value} options={options} onChange={onChange} />
    </div>
  );
}

/** 群组策略字段（mode + 三个逗号分隔列表） */
function GroupPolicyFields({ lang, policy, onChange }: { lang: UiLanguage; policy: GroupPolicy; onChange: (p: GroupPolicy) => void; }) {
  return (
    <div className="space-y-2 rounded-md border border-border-light p-3 bg-surface-card">
      <SelectField lang={lang} labelKey="setupChannelGroupPolicyMode" value={policy.mode}
        options={[
          { value: 'open', label: t(lang, 'setupChannelGroupPolicyModeOpen') },
          { value: 'disabled', label: t(lang, 'setupChannelGroupPolicyModeDisabled') },
          { value: 'allowlist', label: t(lang, 'setupChannelGroupPolicyModeAllowlist') },
          { value: 'blacklist', label: t(lang, 'setupChannelGroupPolicyModeBlacklist') },
        ]}
        onChange={(v) => onChange({ ...policy, mode: v })} />
      <TextField lang={lang} labelKey="setupChannelGroupPolicyAllowlist" value={policy.allowlist.join(', ')} onChange={(v) => onChange({ ...policy, allowlist: v.split(',').map((s) => s.trim()).filter(Boolean) })} />
      <TextField lang={lang} labelKey="setupChannelGroupPolicyBlacklist" value={policy.blacklist.join(', ')} onChange={(v) => onChange({ ...policy, blacklist: v.split(',').map((s) => s.trim()).filter(Boolean) })} />
      <TextField lang={lang} labelKey="setupChannelGroupPolicyAdminList" value={policy.admin_list.join(', ')} onChange={(v) => onChange({ ...policy, admin_list: v.split(',').map((s) => s.trim()).filter(Boolean) })} />
    </div>
  );
}

// ===== 测试连接按钮 =====

interface TestConnectionButtonProps {
  lang: UiLanguage;
  channelName: string;
  payload: { app_id?: string; app_secret?: string; client_secret?: string; domain?: string };
}

/** 测试连接按钮（飞书/QQ 校验凭据） */
function TestConnectionButton({ lang, channelName, payload }: TestConnectionButtonProps) {
  /** 测试状态：idle / testing / success / failed */
  const [status, setStatus] = useState<'idle' | 'testing' | 'success' | 'failed'>('idle');
  /** 结果消息 */
  const [message, setMessage] = useState('');

  /** 执行测试连接 */
  const handleTest = useCallback(async () => {
    setStatus('testing');
    setMessage('');
    try {
      const res = await channelsApi.test(channelName, payload);
      setStatus(res.ok ? 'success' : 'failed');
      setMessage(res.message);
    } catch (err) {
      setStatus('failed');
      setMessage(err instanceof Error ? err.message : String(err));
    }
  }, [channelName, payload]);

  return (
    <div className="flex items-center gap-3 pt-1">
      <button
        onClick={handleTest}
        disabled={status === 'testing'}
        className="px-3 py-1.5 rounded-md text-xs font-medium text-primary border border-primary/30 hover:bg-primary/10 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1.5"
      >
        {status === 'testing' && (
          <svg className="w-3 h-3 animate-spin" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeOpacity="0.4" />
            <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        )}
        {status === 'testing' ? t(lang, 'setupChannelTesting') : t(lang, 'setupChannelTestConnection')}
      </button>
      {status === 'success' && <span className="text-xs text-success">{t(lang, 'setupChannelTestSuccess')}</span>}
      {status === 'failed' && <span className="text-xs text-danger">{t(lang, 'setupChannelTestFailed')}: {message}</span>}
    </div>
  );
}

// ===== 微信扫码登录 =====

interface WeixinQrLoginProps {
  lang: UiLanguage;
  /** 扫码成功后回调，传入凭据供表单回填 */
  onLoginSuccess: (creds: { account_id: string; token: string; base_url: string; user_id: string }) => void;
}

/** 微信扫码状态 */
type WeixinQrState = 'idle' | 'fetching' | 'waiting' | 'scanned' | 'expired' | 'success' | 'failed';

/** 微信扫码登录组件（获取二维码 → 显示图片 → 轮询状态 → 确认后回填凭据） */
function WeixinQrLogin({ lang, onLoginSuccess }: WeixinQrLoginProps) {
  /** 扫码流程状态 */
  const [qrState, setQrState] = useState<WeixinQrState>('idle');
  /** 二维码 PNG base64 */
  const [qrImageB64, setQrImageB64] = useState('');
  /** 错误消息 */
  const [errorMsg, setErrorMsg] = useState('');
  /** 二维码 hex token（轮询用） */
  const qrcodeRef = useRef('');
  /** 当前 API 入口（扫码重定向后可能改变） */
  const baseUrlRef = useRef('');
  /** 轮询计数 ref（超时控制） */
  const pollCountRef = useRef(0);
  /** 轮询定时器 ref */
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /** 清理轮询定时器 */
  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // 组件卸载时清理定时器
  useEffect(() => clearTimer, [clearTimer]);

  /** 启动扫码流程：获取二维码并开始轮询 */
  const startQrLogin = useCallback(async () => {
    clearTimer();
    setQrState('fetching');
    setErrorMsg('');
    try {
      const res = await channelsApi.weixinQrStart();
      qrcodeRef.current = res.qrcode;
      setQrImageB64(res.qr_image_b64);
      setQrState('waiting');
      pollCountRef.current = 0;
      // 开始轮询扫码状态（每 2 秒，最多 240 次 = 8 分钟）
      timerRef.current = setInterval(async () => {
        pollCountRef.current += 1;
        if (pollCountRef.current > 240) {
          clearTimer();
          setQrState('failed');
          setErrorMsg(t(lang, 'setupChannelWeixinQrTimeout'));
          return;
        }
        try {
          const statusResp = await channelsApi.weixinQrStatus(qrcodeRef.current, baseUrlRef.current);
          switch (statusResp.status) {
            case 'wait':
              // 继续等待
              break;
            case 'scaned':
              setQrState('scanned');
              break;
            case 'scaned_but_redirect':
              if (statusResp.base_url) baseUrlRef.current = statusResp.base_url;
              break;
            case 'expired':
              clearTimer();
              setQrState('expired');
              break;
            case 'confirmed':
              clearTimer();
              if (statusResp.credentials) {
                setQrState('success');
                onLoginSuccess(statusResp.credentials);
              } else {
                setQrState('failed');
                setErrorMsg('credentials missing');
              }
              break;
            default:
              // 未知状态，继续轮询
              break;
          }
        } catch {
          // 单次轮询失败不中断流程，下次重试
        }
      }, 2000);
    } catch (err) {
      setQrState('failed');
      setErrorMsg(err instanceof Error ? err.message : String(err));
    }
  }, [clearTimer, lang, onLoginSuccess]);

  return (
    <div className="pt-1 space-y-2">
      {/* 扫码登录按钮 / 状态 */}
      {qrState === 'idle' && (
        <button
          onClick={startQrLogin}
          className="px-3 py-1.5 rounded-md text-xs font-medium text-primary border border-primary/30 hover:bg-primary/10 transition-colors cursor-pointer"
        >
          {t(lang, 'setupChannelWeixinQrLogin')}
        </button>
      )}
      {qrState === 'fetching' && (
        <div className="flex items-center gap-2 text-xs text-content-disabled">
          <svg className="w-3 h-3 animate-spin" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeOpacity="0.4" />
            <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
          {t(lang, 'setupChannelWeixinQrStart')}
        </div>
      )}
      {qrState === 'expired' && (
        <div className="space-y-2">
          <div className="text-xs text-warning">{t(lang, 'setupChannelWeixinQrExpired')}</div>
          <button
            onClick={startQrLogin}
            className="px-3 py-1.5 rounded-md text-xs font-medium text-primary border border-primary/30 hover:bg-primary/10 transition-colors cursor-pointer"
          >
            {t(lang, 'setupChannelWeixinQrStart')}
          </button>
        </div>
      )}
      {qrState === 'failed' && (
        <div className="space-y-2">
          <div className="text-xs text-danger">{t(lang, 'setupChannelWeixinQrFailed')}: {errorMsg}</div>
          <button
            onClick={startQrLogin}
            className="px-3 py-1.5 rounded-md text-xs font-medium text-primary border border-primary/30 hover:bg-primary/10 transition-colors cursor-pointer"
          >
            {t(lang, 'setupChannelWeixinQrStart')}
          </button>
        </div>
      )}
      {qrState === 'success' && (
        <div className="text-xs text-success">{t(lang, 'setupChannelWeixinQrSuccess')}</div>
      )}

      {/* 二维码图片 + 状态提示 */}
      {(qrState === 'waiting' || qrState === 'scanned') && qrImageB64 && (
        <div className="flex flex-col items-center gap-2 py-2">
          <img
            src={`data:image/png;base64,${qrImageB64}`}
            alt="QR Code"
            className="w-48 h-48 rounded-lg border border-border-light"
          />
          <div className="text-xs text-content-secondary">
            {qrState === 'scanned'
              ? t(lang, 'setupChannelWeixinQrScanned')
              : t(lang, 'setupChannelWeixinQrWaiting')}
          </div>
        </div>
      )}
    </div>
  );
}
