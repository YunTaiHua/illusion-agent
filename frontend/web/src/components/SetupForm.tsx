/**
 * @fileoverview 设置配置表单组件
 *
 * 弹出场景：
 * - 首次登录：后端 ready 事件携带 first_login=true 时自动弹出（可随时关闭，
 *   不强制完成配置）
 * - 修改配置：点击左栏底部 settings 齿轮图标弹出
 *
 * 七个 Tab：基础配置 / 子智能体 / 目录空间 / 渠道配置 / 定时任务 / 沙箱设置 / 扩展能力
 *
 * 视觉风格：居中简洁卡片、左侧 Tab 导航、GlassDropdown 玻璃拟态下拉、
 * 输入框聚焦散光；样式常量统一取自 settingsUi（tabTitle/label/input）。
 *
 * 数据流（无统一保存按钮，所有设置变更即时生效）：
 * - 挂载时并行加载 settings / envs / channels（REST）
 * - 开关/下拉类改动即时提交；文本类输入失焦或回车提交（避免逐键请求）
 * - ui_language / 主题 / 扩展能力卡片即改即存（原有语义不变）
 * - env 增删改 / 渠道 / 沙箱 / 模型参数通过 REST 即时提交
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
import { AgentsTab, type AgentsSessionApi } from './AgentsTab';
import { BrowserTab } from './BrowserTab';
import { inputClass, labelClass, cardTitleClass, FieldLabel, TabHeader } from './settingsUi';
import type { AgentEntry, AgentModelOption, PluginSnapshot, WebWorkspaceItem } from '../types/protocol';
import {
  envApi, oauthApi, settingsApi, channelsApi,
  type EnvInfo, type ModelConfig, type SettingsResponse, type CreateEnvPayload,
  type SandboxSettings, type PermissionRiskSettings, type UpdateSandboxPayload,
  type ChannelRuntimeStatusEntry, type ChannelsRuntimeStatus,
} from '../api';

/** 各 api_format 的默认接入地址（与 CLI auth.py _DEFAULT_ENDPOINTS 对齐） */
const DEFAULT_ENDPOINTS: Record<string, string> = {
  anthropic: 'https://api.anthropic.com',
  openai: 'https://api.openai.com/v1',
  response: 'https://api.openai.com/v1',
  copilot: 'https://api.githubcopilot.com',
  codex: 'https://chatgpt.com/backend-api',
};

/** 各 api_format 的默认模型名（与 CLI auth.py _DEFAULT_MODELS 对齐） */
const DEFAULT_MODELS: Record<string, string> = {
  anthropic: 'claude-sonnet-4-6',
  openai: 'gpt-5.4',
  response: 'gpt-5.4',
  copilot: 'gpt-5.5',
  codex: 'codex-mini',
};

/** api_format 可选值 */
const API_FORMATS = ['anthropic', 'openai', 'response', 'copilot', 'codex'] as const;
/** copilot/codex 走 OAuth，其余走密钥输入 */
const OAUTH_FORMATS = new Set(['copilot', 'codex']);
/** OAuth 轮询间隔（毫秒） */
const OAUTH_POLL_INTERVAL = 5000;
/** OAuth 最大轮询次数（约 5 分钟超时） */
const OAUTH_MAX_POLLS = 60;

/** 设置表单全部 Tab（左侧导航与内容区共用） */
const SETUP_TAB_KEYS = ['settings', 'agents', 'workspaces', 'channels', 'cron', 'sandbox', 'extensions'] as const;
type SetupTabKey = typeof SETUP_TAB_KEYS[number];

/** Tab 对应的标题 i18n key（左侧导航与内容区统一大标题共用） */
function tabLabelKey(tabKey: SetupTabKey): string {
  switch (tabKey) {
    case 'settings': return 'setupFormSettingsTitle';
    case 'agents': return 'setupFormAgentsTitle';
    case 'workspaces': return 'setupFormWorkspacesTitle';
    case 'channels': return 'setupFormChannelsTitle';
    case 'cron': return 'setupFormCronTitle';
    case 'extensions': return 'extensions_tab_title';
    default: return 'setupFormSandboxTitle';
  }
}

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
  /** 模型列表（至少一个；name 必填，capabilities 声明图片/视频能力） */
  models: ModelConfig[];
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
    models: [{ name: DEFAULT_MODELS['anthropic'] ?? '', capabilities: [] }],
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
  /** 初始打开的 Tab（目录按钮"管理目录…"直达目录空间页） */
  initialTab?: 'settings' | 'agents' | 'workspaces' | 'channels' | 'cron' | 'sandbox' | 'extensions';
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
  /** agent 管理会话能力聚合（AgentsTab 数据源；原 /agent 指令功能的 UI 承载） */
  agentsApi: AgentsSessionApi;
  /** 当前会话所在工作区（agent 创建时默认目标目录） */
  defaultWorkspace?: string;
  /** 模型参数等会影响运行中主机状态的设置变更后请求一次状态刷新 */
  onRequestStatusRefresh: () => void;
  /** 关闭表单回调 */
  onClose: () => void;
  /** === 插件 Tab（App 透传会话数据）=== */
  /** 插件快照（web_resources） */
  plugins?: PluginSnapshot[];
  /** 切换插件启用状态（WS web_plugin_toggle） */
  onPluginToggle?: (name: string, enabled: boolean) => void;
  /** 全局设置写入（WS web_set_setting；浏览器配置等） */
  sendSetting?: (key: string, value: string | number | boolean) => void;
  /** Goal 开关切换回调（App 同步其本地 goalEnabled，供 /goal 命令前置判断） */
  onGoalToggle?: (enabled: boolean) => void;
}

/**
 * SetupForm 首次登录 / 设置配置表单组件
 *
 * @param props - 组件属性
 * @returns 表单 JSX
 */
export function SetupForm({ lang, initialTab, workspaces, onAddWorkspace, onRemoveWorkspace, onRequestWorkspaces, onSetDefaultWorkspace, onSetUiLanguage, agentsApi, defaultWorkspace, onRequestStatusRefresh, onClose, plugins, onPluginToggle, sendSetting, onGoalToggle }: SetupFormProps) {
  /** 当前 Tab */
  const [tab, setTab] = useState<'settings' | 'agents' | 'workspaces' | 'channels' | 'cron' | 'sandbox' | 'extensions'>(initialTab ?? 'settings');
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
  /** Toast 通知总开关（任务完成/终止、询问、权限提醒） */
  const [toastEnabled, setToastEnabled] = useState(true);
  /** 提示音效开关（仅在 toast 总开关开启时生效） */
  const [toastSound, setToastSound] = useState(true);
  /** 模型参数（上下文窗口大小 / 最大输出 tokens / 最大轮次，字符串态便于自由编辑） */
  const [contextWindow, setContextWindow] = useState<string>('200000');
  const [maxTokens, setMaxTokens] = useState<string>('16384');
  const [maxTurns, setMaxTurns] = useState<string>('200');
  /** 权限 LLM 自动审核开关（auto 模式高危操作与沙箱拦截由 LLM 审核放行） */
  const [reviewAuto, setReviewAuto] = useState(false);
  /** 审核模型（空 = 继承当前会话模型） */
  const [reviewModel, setReviewModel] = useState('');
  /** Goal 长任务功能开关（关闭后 /goal 命令与 goal 工具均拒绝） */
  const [goalEnabled, setGoalEnabled] = useState(true);
  /** 扩展能力卡片即时保存错误（变更即存，无统一保存按钮） */
  const [capError, setCapError] = useState<string | null>(null);
  /** 沙箱配置（可删改） */
  const [sandbox, setSandbox] = useState<SandboxSettings | null>(null);
  /** 沙箱保存错误 */
  const [sandboxError, setSandboxError] = useState<string | null>(null);
  /** 权限风险分级配置（LOW/MEDIUM/HIGH 三层级，后端内置只读展示） */
  const [permission, setPermission] = useState<PermissionRiskSettings | null>(null);
  /** 界面语言选择值（后端格式 zh-CN / en-US） */
  const [uiLang, setUiLang] = useState<'zh-CN' | 'en-US'>('zh-CN');
  /** 主题：交由 useTheme 统一管理（即改即生效并持久化），此处仅取当前值 */
  const { theme, setTheme } = useTheme();
  /** 新增环境草稿（新增分支） */
  const [draft, setDraft] = useState<EnvDraft>(makeInitialDraft);
  /** 是否展开新增环境表单（无任何环境时始终展开） */
  const [showAddEnv, setShowAddEnv] = useState(false);
  /** 新增环境提交中 */
  const [creating, setCreating] = useState(false);
  /** 新增环境错误 */
  const [createError, setCreateError] = useState<string | null>(null);
  /** 渠道即时保存错误 */
  const [channelsError, setChannelsError] = useState<string | null>(null);
  /** 模型参数即时保存错误（正整数校验 / PATCH 失败） */
  const [modelParamsError, setModelParamsError] = useState<string | null>(null);
  /** 操作错误（env 列表即时操作的错误） */
  const [opError, setOpError] = useState<string | null>(null);
  /** 子智能体内嵌向导模式（null 关闭 / 'create' 创建 / AgentEntry 编辑；受控传给 AgentsTab） */
  const [agentWizardMode, setAgentWizardMode] = useState<'create' | AgentEntry | null>(null);

  // 组件是否仍挂载：reload 异步完成后避免对已卸载组件 setState
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  /**
   * 挂载加载 settings / envs / channels 回显
   */
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [s, e, c] = await Promise.all([settingsApi.get(), envApi.list(), channelsApi.get()]);
      if (!mountedRef.current) return;
      setSettings(s);
      setWorkDir(s.working_directory ?? '');
      setMemEnabled(s.memory?.enabled ?? true);
      setMemAutoExtract(s.memory?.auto_extract ?? true);
      setMemExtractModel(s.memory?.extract_model ?? '');
      setMemDreamModel(s.memory?.dream_model ?? '');
      setMemDir(s.memory?.directory ?? '');
      setTitleEnabled(s.title?.enabled ?? false);
      setTitleModel(s.title?.model ?? '');
      // Goal 长任务功能开关（旧后端响应缺省时按启用兜底）
      setGoalEnabled(s.goal?.enabled ?? true);
      // 通知开关（旧后端响应缺省时按全开兜底）
      setToastEnabled(s.notifications?.enabled ?? true);
      setToastSound(s.notifications?.sound ?? true);
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
      setLoadError(null);
    } catch (err) {
      if (!mountedRef.current) return;
      setLoadError(err instanceof Error ? err.message : String(err));
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  // 挂载时并行加载 settings / envs / channels
  useEffect(() => {
    reload();
  }, [reload]);

  // Esc 键关闭
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onClose(); }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  /** 切换 api_format 时同步默认 base_url 和首个模型 */
  const handleFormatChange = useCallback((fmt: string) => {
    setDraft((d) => ({
      ...d,
      api_format: fmt,
      base_url: DEFAULT_ENDPOINTS[fmt] ?? d.base_url,
      models: d.models.length > 0 ? d.models : [{ name: DEFAULT_MODELS[fmt] ?? '', capabilities: [] }],
      oauth_authorized: false,
      // openai/response 格式使用 Bearer API Key（无 auth_token 分支），强制切回 api_key
      auth_type: fmt === 'openai' || fmt === 'response' ? 'api_key' : d.auth_type,
    }));
  }, []);

  /** 更新草稿中指定模型行 */
  const updateModel = useCallback((idx: number, patch: Partial<ModelConfig>) => {
    setDraft((d) => {
      const next = [...d.models];
      const current = next[idx];
      if (!current) return d;
      next[idx] = {
        name: patch.name ?? current.name,
        capabilities: patch.capabilities ?? current.capabilities,
      };
      return { ...d, models: next };
    });
  }, []);

  /** 可用模型下拉选项：所有 env 的 model_N，值为 env_N.model_N 引用，显示模型名 */
  const modelOptions = useMemo<DropdownOption[]>(() => {
    const opts: DropdownOption[] = [
      { value: '', label: t(lang, 'setupFieldMemoryModelInherit') },
    ];
    for (const env of envs) {
      for (const [key, mc] of Object.entries(env.models)) {
        opts.push({
          value: `${env.env_key}.${key}`,
          label: mc.name, // 仅显示模型名，不显示 env_N.model_N
        });
      }
    }
    return opts;
  }, [envs, lang]);

  /** agent 管理用模型选项（envs 已随表单挂载加载，首次进入子智能体 Tab 即完整可用） */
  const agentModelOptions = useMemo<AgentModelOption[]>(() => {
    const opts: AgentModelOption[] = [
      { name: 'inherit', label: t(lang, 'agentsTabModelInherit') },
    ];
    for (const env of envs) {
      for (const [key, mc] of Object.entries(env.models)) {
        opts.push({
          name: `${env.env_key}.${key}`,
          label: mc.name,
          supports_images: mc.capabilities?.includes('image') ?? false,
        });
      }
    }
    return opts;
  }, [envs, lang]);

  /** 添加模型行（名称来自追加输入框） */
  const addModel = useCallback((name: string) => {
    setDraft((d) => ({ ...d, models: [...d.models, { name, capabilities: [] }] }));
  }, []);

  /** 移除指定模型行 */
  const removeModel = useCallback((idx: number) => {
    setDraft((d) => ({ ...d, models: d.models.filter((_, i) => i !== idx) }));
  }, []);

  /** 草稿是否可提交（env 校验） */
  const draftValid = useCallback((d: EnvDraft): boolean => {
    if (!d.api_format) return false;
    if (d.models.filter((m) => m.name.trim()).length === 0) return false;
    if (OAUTH_FORMATS.has(d.api_format)) return d.oauth_authorized;
    // anthropic / openai 需要对应认证字段非空
    return d.auth_type === 'api_key' ? d.api_key.trim() !== '' : d.auth_token.trim() !== '';
  }, []);

  /** 提交草稿创建 env（处理 model_3+ 通过 add_models 补充） */
  const createEnvFromDraft = useCallback(async (d: EnvDraft): Promise<string> => {
    const models = d.models
      .map((m) => ({ ...m, name: m.name.trim() }))
      .filter((m) => m.name);
    const payload: CreateEnvPayload = {
      api_format: d.api_format,
      base_url: d.base_url.trim(),
      model_1: models[0] ?? { name: '', capabilities: [] },
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

  /** 模型参数字符串态是否合法（空 = 不改动） */
  const modelParamsValid = useCallback((cw: string, mt: string, turns: string): boolean => {
    const tdCw = parseInt(cw, 10);
    const tdMt = parseInt(mt, 10);
    const tdTurns = parseInt(turns, 10);
    const cwOk = cw.trim() === '' || (Number.isInteger(tdCw) && tdCw > 0);
    const mtOk = mt.trim() === '' || (Number.isInteger(tdMt) && tdMt > 0);
    const turnsOk = turns.trim() === '' || (Number.isInteger(tdTurns) && tdTurns >= 1 && tdTurns <= 512);
    return cwOk && mtOk && turnsOk;
  }, []);

  /**
   * 模型参数失焦提交：校验为正整数（max_turns 1~512）后仅提交与
   * 持久化值有差异的字段，成功后请求一次状态刷新（热应用到运行中主机）。
   */
  const commitModelParams = useCallback(async () => {
    if (!settings) return;
    if (!modelParamsValid(contextWindow, maxTokens, maxTurns)) {
      setModelParamsError(t(lang, 'modelParamsInvalid'));
      return;
    }
    setModelParamsError(null);
    const payload: { context_window?: number; max_tokens?: number; max_turns?: number } = {};
    const cw = parseInt(contextWindow, 10);
    const mt = parseInt(maxTokens, 10);
    const turns = parseInt(maxTurns, 10);
    if (contextWindow.trim() !== '' && cw !== settings.context_window) payload.context_window = cw;
    if (maxTokens.trim() !== '' && mt !== settings.max_tokens) payload.max_tokens = mt;
    if (maxTurns.trim() !== '' && turns !== settings.max_turns) payload.max_turns = turns;
    if (Object.keys(payload).length === 0) return;
    try {
      await settingsApi.updateModelParams(payload);
      if (!mountedRef.current) return;
      // 同步回显基线（下次失焦以此为差异基准）
      setSettings((s) => (s ? { ...s, ...payload } : s));
      onRequestStatusRefresh();
    } catch (err) {
      if (mountedRef.current) setModelParamsError(err instanceof Error ? err.message : String(err));
    }
  }, [settings, contextWindow, maxTokens, maxTurns, modelParamsValid, lang, onRequestStatusRefresh]);

  /** 新增环境：创建成功后刷新列表、清空草稿并收起表单 */
  const handleCreateEnv = useCallback(async () => {
    if (!draftValid(draft)) return;
    setCreating(true);
    setCreateError(null);
    try {
      await createEnvFromDraft(draft);
      const e = await envApi.list();
      if (!mountedRef.current) return;
      setEnvs(e.envs);
      setActiveEnvKey(e.active_env_key);
      setDraft(makeInitialDraft());
      setShowAddEnv(false);
    } catch (err) {
      if (mountedRef.current) setCreateError(err instanceof Error ? err.message : String(err));
    } finally {
      if (mountedRef.current) setCreating(false);
    }
  }, [draft, draftValid, createEnvFromDraft]);

  /** 沙箱配置即时保存：变更即 PATCH（整体提交，后端热重载生效） */
  const patchSandbox = useCallback(async (next: SandboxSettings) => {
    setSandbox(next);
    setSandboxError(null);
    try {
      await settingsApi.updateSandbox(next as UpdateSandboxPayload);
    } catch (err) {
      if (mountedRef.current) setSandboxError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  /**
   * 渠道配置即时保存：合并指定渠道的局部变更并 PATCH。
   * 兼容旧校验：启用中的渠道未配置运行目录时自动填充默认工作区，
   * 避免后端启用校验拒绝整次提交。
   */
  const updateChannel = useCallback((name: 'feishu' | 'weixin' | 'qq', patch: Record<string, unknown>) => {
    const mergedRaw = { ...channels[name], ...patch };
    const defaultWs = workspaces.find((w) => w.is_default)?.path;
    const merged = { ...mergedRaw } as Record<string, unknown>;
    if (merged.enabled && !merged.working_directory) merged.working_directory = defaultWs ?? null;
    else if (!merged.working_directory) merged.working_directory = null;
    setChannels((prev) => ({ ...prev, [name]: merged }) as ChannelsCfg);
    setChannelsError(null);
    channelsApi.update({ [name]: merged } as Parameters<typeof channelsApi.update>[0]).catch((err) => {
      if (mountedRef.current) setChannelsError(err instanceof Error ? err.message : String(err));
    });
  }, [channels, workspaces]);

  /**
   * 扩展能力卡片即时保存：变更即存（与浏览器插件开关同语义，无统一保存按钮）。
   * 仅提交变更字段（PATCH 语义），失败在扩展页顶部提示，不打断编辑。
   */
  const saveCapability = useCallback(async (save: () => Promise<unknown>) => {
    try {
      await save();
      if (mountedRef.current) setCapError(null);
    } catch (err) {
      if (mountedRef.current) setCapError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  /** Goal 开关切换：即时 PATCH（后端热应用到活跃会话）+ 通知 App 同步 */
  const handleGoalToggle = useCallback((enabled: boolean) => {
    setGoalEnabled(enabled);
    onGoalToggle?.(enabled);
    void saveCapability(() => settingsApi.updateGoal({ enabled }));
  }, [onGoalToggle, saveCapability]);

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
  const handleAddModel = useCallback(async (envKey: string, modelValue: ModelConfig) => {
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

  /** 切换已有环境模型的媒体能力（即时 API：PATCH add_models 带 key 覆盖写回） */
  const handleToggleModelCapability = useCallback(async (envKey: string, modelKey: string, modelValue: ModelConfig) => {
    setOpError(null);
    try {
      await envApi.update(envKey, { add_models: [{ key: modelKey, value: modelValue }] });
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

  /** 打开子智能体创建向导（创建按钮位于 Tab 标题行右侧） */
  const openAgentCreate = useCallback(() => {
    agentsApi.clearWizardState();
    agentsApi.wizardInit();
    setAgentWizardMode('create');
  }, [agentsApi]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 backdrop-blur-md animate-fade-in" onClick={onClose}>
      <div
        className="relative bg-surface-card rounded-2xl border border-border-light shadow-card w-[760px] h-[600px] max-w-[95vw] max-h-[90vh] flex flex-col animate-scale-in modal-origin-center"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 标题栏：右上角 X 为唯一关闭入口 */}
        <div className="px-6 py-4 border-b border-border-light flex items-center justify-between shrink-0">
          <h3 className="text-lg font-semibold text-content-primary">{t(lang, 'settings')}</h3>
          <button
            onClick={onClose}
            title={t(lang, 'window_close')}
            className="shrink-0 w-6 h-6 flex items-center justify-center rounded text-content-disabled hover:text-content-primary hover:bg-surface-hover transition-colors cursor-pointer"
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M2 2l8 8M10 2l-8 8" /></svg>
          </button>
        </div>

        {/* 主体：左侧 Tab 栏 + 右侧内容区 */}
        <div className="flex flex-1 min-h-0">
          {/* 左侧 Tab 导航栏（垂直） */}
          <div className="w-fit shrink-0 border-r border-border-light px-3 py-3 flex flex-col gap-1 overflow-y-auto">
            {SETUP_TAB_KEYS.map((tabKey) => {
              const isActive = tab === tabKey;
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
                  {t(lang, tabLabelKey(tabKey))}
                </button>
              );
            })}
          </div>

          {/* 内容区：cron/目录空间 Tab 独立加载数据，不依赖 settings/envs/channels 主加载流程 */}
          <div className="flex-1 min-w-0 overflow-y-auto px-6 py-4 [scrollbar-gutter:stable]">
          {/* 统一 Tab 标题：层级为「左侧导航 → 内容区大标题 → 字段标签 → 正文」；
              子智能体 Tab 的创建按钮与标题同行 */}
          <TabHeader
            title={t(lang, tabLabelKey(tab))}
            action={tab === 'agents' && !agentWizardMode ? (
              <button
                onClick={openAgentCreate}
                className="px-3 py-1.5 text-sm text-white bg-primary hover:bg-primary-hover rounded-lg transition-colors cursor-pointer shrink-0"
              >
                {t(lang, 'agentsTabCreate')}
              </button>
            ) : undefined}
          />
          {tab === 'cron' ? (
            <CronTab lang={lang} workspaces={workspaces} />
          ) : tab === 'agents' ? (
            <AgentsTab
              lang={lang}
              workspaces={workspaces}
              defaultWorkspace={defaultWorkspace}
              api={agentsApi}
              models={agentModelOptions}
              wizardMode={agentWizardMode}
              onWizardModeChange={setAgentWizardMode}
            />
          ) : tab === 'workspaces' ? (
            <WorkspacesTab
              lang={lang}
              workspaces={workspaces}
              onAdd={onAddWorkspace}
              onRemove={onRemoveWorkspace}
              onRefresh={onRequestWorkspaces}
              onSetDefault={onSetDefaultWorkspace}
            />
          ) : tab === 'extensions' ? (
            <div className="w-full flex flex-col gap-3">
              {/* 即时保存错误提示（变更即存） */}
              {capError && (
                <div className="text-sm text-danger py-2">{t(lang, 'setupFormSaveFailed')}: {capError}</div>
              )}
              <GoalCard lang={lang} enabled={goalEnabled} onToggle={handleGoalToggle} />
              <BrowserTab
                lang={lang}
                browserUse={(plugins ?? []).find((p) => p.name === 'browser-use')}
                onToggle={(enabled) => onPluginToggle?.('browser-use', enabled)}
                sendSetting={(key, value) => sendSetting?.(key, value)}
                isDesktop={Boolean(window.illusionDesktop)}
              />
              <MemoryCard
                lang={lang}
                enabled={memEnabled}
                onEnabledChange={(v) => { setMemEnabled(v); void saveCapability(() => settingsApi.updateMemory({ enabled: v })); }}
                autoExtract={memAutoExtract}
                onAutoExtractChange={(v) => { setMemAutoExtract(v); void saveCapability(() => settingsApi.updateMemory({ auto_extract: v })); }}
                directory={memDir}
                onDirectoryChange={setMemDir}
                onDirectoryCommit={() => void saveCapability(() => settingsApi.updateMemory({ directory: memDir.trim() }))}
                extractModel={memExtractModel}
                onExtractModelChange={(v) => { setMemExtractModel(v); void saveCapability(() => settingsApi.updateMemory({ extract_model: v })); }}
                dreamModel={memDreamModel}
                onDreamModelChange={(v) => { setMemDreamModel(v); void saveCapability(() => settingsApi.updateMemory({ dream_model: v })); }}
                modelOptions={modelOptions}
              />
              <TitleCard
                lang={lang}
                enabled={titleEnabled}
                onEnabledChange={(v) => { setTitleEnabled(v); void saveCapability(() => settingsApi.updateTitle({ enabled: v })); }}
                model={titleModel}
                onModelChange={(v) => { setTitleModel(v); void saveCapability(() => settingsApi.updateTitle({ model: v })); }}
                modelOptions={modelOptions}
              />
              <ReviewCard
                lang={lang}
                autoReview={reviewAuto}
                onAutoReviewChange={(v) => { setReviewAuto(v); void saveCapability(() => settingsApi.updatePermissionReview({ auto_review: v })); }}
                model={reviewModel}
                onModelChange={(v) => { setReviewModel(v); void saveCapability(() => settingsApi.updatePermissionReview({ review_model: v })); }}
                modelOptions={modelOptions}
              />
              <NotificationsCard
                lang={lang}
                toastEnabled={toastEnabled}
                onToastEnabledChange={(v) => { setToastEnabled(v); void saveCapability(() => settingsApi.updateNotifications({ enabled: v })); }}
                toastSound={toastSound}
                onToastSoundChange={(v) => { setToastSound(v); void saveCapability(() => settingsApi.updateNotifications({ sound: v })); }}
              />
            </div>
          ) : loading ? (
            <div className="flex items-center justify-center py-12 text-sm text-content-disabled">
              <svg className="w-4 h-4 animate-spin mr-2" viewBox="0 0 16 16" fill="none">
                <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeOpacity="0.4" />
                <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
              {t(lang, 'loading')}
            </div>
          ) : loadError ? (
            <div className="text-sm text-danger py-4">{t(lang, 'setupFormLoadFailed')}: {loadError}</div>
          ) : tab === 'settings' ? (
            <SettingsTab
              lang={lang}
              uiLang={uiLang}
              onPickUiLang={handlePickUiLang}
              theme={theme}
              onPickTheme={handlePickTheme}
              workDir={workDir}
              onManageWorkspaces={() => setTab('workspaces')}
              contextWindow={contextWindow}
              onContextWindowChange={setContextWindow}
              maxTokens={maxTokens}
              onMaxTokensChange={setMaxTokens}
              maxTurns={maxTurns}
              onMaxTurnsChange={setMaxTurns}
              onModelParamsCommit={commitModelParams}
              modelParamsError={modelParamsError}
              envs={envs}
              activeEnvKey={activeEnvKey}
              onDeleteEnv={handleDeleteEnv}
              onAddModelToEnv={handleAddModel}
              onRemoveModelFromEnv={handleRemoveModel}
              onToggleModelCapability={handleToggleModelCapability}
              showAddEnv={showAddEnv}
              onToggleAddEnv={() => setShowAddEnv(!showAddEnv)}
              draft={draft}
              onDraftChange={setDraft}
              onFormatChange={handleFormatChange}
              onUpdateModel={updateModel}
              onAddModel={addModel}
              onRemoveModel={removeModel}
              onCreateEnv={handleCreateEnv}
              creating={creating}
              createError={createError}
              opError={opError}
            />
          ) : tab === 'sandbox' ? (
            <SandboxTab
              lang={lang}
              sandbox={sandbox}
              onSandboxPatch={patchSandbox}
              error={sandboxError}
              permission={permission}
            />
          ) : (
            <ChannelsTab lang={lang} channels={channels} onChannelChange={updateChannel} workspaces={workspaces} error={channelsError} />
          )}
          </div>
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
  onAddModel: (envKey: string, modelValue: ModelConfig) => void;
  /** 从已有环境移除模型（envKey, modelKey） */
  onRemoveModel: (envKey: string, modelKey: string) => void;
  /** 切换已有环境模型的图片能力 */
  onToggleCapability: (envKey: string, modelKey: string, modelValue: ModelConfig) => void;
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
          <EnvTriggerCard key={env.env_key} lang={lang} env={env} isActive={env.env_key === p.activeEnvKey} onDelete={p.onDelete} onAddModel={p.onAddModel} onRemoveModel={p.onRemoveModel} onToggleCapability={p.onToggleCapability} />
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
  onAddModel: (envKey: string, modelValue: ModelConfig) => void;
  onRemoveModel: (envKey: string, modelKey: string) => void;
  onToggleCapability: (envKey: string, modelKey: string, modelValue: ModelConfig) => void;
}

/** 单个环境触发卡片：触发器显示 base_url，点击展开配置和模型编辑 */
function EnvTriggerCard({ lang, env, isActive, onDelete, onAddModel, onRemoveModel, onToggleCapability }: EnvTriggerCardProps) {
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
            <div className={labelClass}>{t(lang, 'setupFieldModels')}</div>
            {modelEntries.length === 0 ? (
              <div className="text-xs text-content-disabled italic">{t(lang, 'setupEnvModelsHint')}</div>
            ) : (
              <div className="space-y-1">
                {modelEntries.map(([modelKey, mc]) => (
                  <div key={modelKey} className="flex items-center gap-2">
                    <div className="flex-1 min-w-0 px-2 py-1 rounded-md text-xs bg-surface-card-alt border border-border-light text-content-primary">
                      <span className="block truncate font-mono">{mc.name}</span>
                    </div>
                    {/* 图片能力勾选：位于名称卡片外层，随时勾选/取消，即时 PATCH 落盘 */}
                    <label className="shrink-0 flex items-center gap-1 text-[11px] text-content-secondary cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={mc.capabilities?.includes('image') ?? false}
                        onChange={(e) => onToggleCapability(env.env_key, modelKey, {
                          name: mc.name,
                          capabilities: e.target.checked
                            ? Array.from(new Set([...(mc.capabilities ?? []), 'image']))
                            : (mc.capabilities ?? []).filter((c) => c !== 'image'),
                        })}
                        className="w-3.5 h-3.5 accent-[var(--primary)] cursor-pointer"
                      />
                      {t(lang, 'setupCapabilityImage')}
                    </label>
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
                  onAddModel(env.env_key, { name: newModel.trim(), capabilities: [] });
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
  uiLang: 'zh-CN' | 'en-US';
  onPickUiLang: (v: 'zh-CN' | 'en-US') => void;
  /** 主题选择值（light / dark / system） */
  theme: 'light' | 'dark' | 'system';
  onPickTheme: (v: 'light' | 'dark' | 'system') => void;
  /** 当前工作目录（只读展示，多目录空间由目录空间页管理） */
  workDir: string;
  /** 跳转到目录空间管理页 */
  onManageWorkspaces: () => void;
  /** 上下文窗口大小（token，字符串态：失焦提交时校验为正整数） */
  contextWindow: string;
  onContextWindowChange: (v: string) => void;
  /** 最大输出 tokens（字符串态：失焦提交时校验为正整数） */
  maxTokens: string;
  onMaxTokensChange: (v: string) => void;
  /** 最大对话轮次（字符串态：失焦提交时校验为 1~512） */
  maxTurns: string;
  onMaxTurnsChange: (v: string) => void;
  /** 模型参数失焦提交（校验 + 即时 PATCH + 状态刷新） */
  onModelParamsCommit: () => void;
  /** 模型参数即时保存错误 */
  modelParamsError: string | null;
  envs: EnvInfo[];
  activeEnvKey: string | null;
  onDeleteEnv: (k: string) => void;
  /** 向已有环境追加模型（envKey, modelValue） */
  onAddModelToEnv: (envKey: string, modelValue: ModelConfig) => void;
  /** 切换已有环境模型的图片能力（即时 PATCH 落盘） */
  onToggleModelCapability: (envKey: string, modelKey: string, modelValue: ModelConfig) => void;
  /** 从已有环境移除模型（envKey, modelKey） */
  onRemoveModelFromEnv: (envKey: string, modelKey: string) => void;
  showAddEnv: boolean;
  onToggleAddEnv: () => void;
  draft: EnvDraft;
  onDraftChange: (d: EnvDraft) => void;
  onFormatChange: (f: string) => void;
  /** 更新草稿中指定模型行（name / capabilities 局部更新） */
  onUpdateModel: (i: number, patch: Partial<ModelConfig>) => void;
  onAddModel: (name: string) => void;
  onRemoveModel: (i: number) => void;
  /** 提交新增环境（即时创建） */
  onCreateEnv: () => void;
  /** 新增环境提交中 */
  creating: boolean;
  /** 新增环境错误 */
  createError: string | null;
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

/** 基础配置 Tab：界面语言 + 环境配置 + 工作目录 + 模型参数 */
function SettingsTab(p: SettingsTabProps) {
  const { lang } = p;
  /** 无任何环境时始终展开新增表单（替代旧"必须配置"强制逻辑，仅做引导） */
  const editorVisible = p.showAddEnv || p.envs.length === 0;
  return (    <div className="space-y-5">
      {/* 界面语言（优先填写） */}
      <div>
        <FieldLabel label={t(lang, 'setupFieldUiLanguage')} />
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
        <FieldLabel label={t(lang, 'setupFieldTheme')} />
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

      {/* 环境选择器：触发器显示 base_url，点击展开配置 */}
      {p.envs.length > 0 && (
        <EnvSelector
          lang={lang}
          envs={p.envs}
          activeEnvKey={p.activeEnvKey}
          onDelete={p.onDeleteEnv}
          onAddModel={p.onAddModelToEnv}
          onRemoveModel={p.onRemoveModelFromEnv}
          onToggleCapability={p.onToggleModelCapability}
          opError={p.opError}
        />
      )}

      {/* 新增环境入口（已有环境时可折叠；无环境时表单始终展开） */}
      {p.envs.length > 0 && (
        <div>
          <button
            onClick={p.onToggleAddEnv}
            className="px-3 py-1.5 rounded-md text-sm border border-border-light text-content-secondary hover:bg-surface-hover transition-colors cursor-pointer"
          >
            {p.showAddEnv ? '−' : '+'} {t(lang, 'setupEnvAdd')}
          </button>
        </div>
      )}

      {/* 环境编辑器（新增环境；无环境时始终显示以引导完成首次配置） */}
      {editorVisible && (
        <EnvEditor
          lang={lang}
          draft={p.draft}
          onDraftChange={p.onDraftChange}
          onFormatChange={p.onFormatChange}
          onUpdateModel={p.onUpdateModel}
          onAddModel={p.onAddModel}
          onRemoveModel={p.onRemoveModel}
          onSubmit={p.onCreateEnv}
          submitting={p.creating}
          submitError={p.createError}
        />
      )}

      {/* 工作目录（多目录空间改造：输入框收敛到目录空间页管理，此处展示当前值 + 跳转） */}
      <div>
        <FieldLabel label={t(lang, 'setupFieldWorkingDirectory')} />
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

      {/* 模型参数（context_window / max_tokens / max_turns；失焦或回车即时提交） */}
      <div className="space-y-3">
        <div>
          <FieldLabel label={t(lang, 'setupFieldContextWindow')} />
          <input
            type="number" min={1} step={1} value={p.contextWindow}
            onChange={(e) => p.onContextWindowChange(e.target.value)}
            onBlur={p.onModelParamsCommit}
            onKeyDown={(e) => { if (e.key === 'Enter') p.onModelParamsCommit(); }}
            className={`${inputClass} no-spinner`}
          />
        </div>
        <div>
          <FieldLabel label={t(lang, 'setupFieldMaxTokens')} />
          <input
            type="number" min={1} step={1} value={p.maxTokens}
            onChange={(e) => p.onMaxTokensChange(e.target.value)}
            onBlur={p.onModelParamsCommit}
            onKeyDown={(e) => { if (e.key === 'Enter') p.onModelParamsCommit(); }}
            className={`${inputClass} no-spinner`}
          />
        </div>
        <div>
          <FieldLabel label={t(lang, 'setupFieldMaxTurns')} />
          <input
            type="number" min={1} max={512} step={1} value={p.maxTurns}
            onChange={(e) => p.onMaxTurnsChange(e.target.value)}
            onBlur={p.onModelParamsCommit}
            onKeyDown={(e) => { if (e.key === 'Enter') p.onModelParamsCommit(); }}
            className={`${inputClass} no-spinner`}
          />
        </div>
        {p.modelParamsError && <div className="text-xs text-danger">{p.modelParamsError}</div>}
      </div>

      {/* 记忆/自动标题/通知/权限 LLM 审核：见「扩展能力」tab，变更即存 */}
    </div>
  );
}

// ===== 扩展能力卡片（变更即存，无统一保存按钮；卡片壳样式与内置浏览器卡片一致） =====

/** 卡片壳样式（与 BrowserTab 浏览器卡片同款；底色用 surface-card 与下拉框的 surface-card-alt 区分） */
const capCardClass = 'rounded-xl border border-border-light bg-surface-card px-4 py-3';

/** Goal 长任务功能卡片：启用开关（关闭后 /goal 命令与 goal 工具均拒绝） */
function GoalCard({ lang, enabled, onToggle }: {
  lang: UiLanguage;
  enabled: boolean;
  onToggle: (enabled: boolean) => void;
}) {
  return (
    <div className={capCardClass}>
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className={`${cardTitleClass} cursor-help w-fit`} title={t(lang, 'goal_setting_subtitle')}>{t(lang, 'goal_setting_title')}</div>
        </div>
        <ToggleSwitch checked={enabled} onChange={onToggle} label={t(lang, 'goal_setting_title')} />
      </div>
    </div>
  );
}

/** 记忆配置卡片（开关/模型变更即存，目录失焦提交） */
function MemoryCard(p: {
  lang: UiLanguage;
  enabled: boolean;
  onEnabledChange: (v: boolean) => void;
  autoExtract: boolean;
  onAutoExtractChange: (v: boolean) => void;
  directory: string;
  onDirectoryChange: (v: string) => void;
  onDirectoryCommit: () => void;
  extractModel: string;
  onExtractModelChange: (v: string) => void;
  dreamModel: string;
  onDreamModelChange: (v: string) => void;
  modelOptions: DropdownOption[];
}) {
  return (
    <div className={`space-y-3 ${capCardClass}`}>
      <div className={cardTitleClass}>{t(p.lang, 'setupFieldMemory')}</div>
      {/* 启用开关 */}
      <div className="flex items-center justify-between">
        <span className="text-sm text-content-primary">{t(p.lang, 'setupFieldMemoryEnabled')}</span>
        <ToggleSwitch checked={p.enabled} onChange={p.onEnabledChange} label={t(p.lang, 'setupFieldMemoryEnabled')} />
      </div>
      {/* 后台自动提取开关：记忆功能关闭时置灰（与后端判定链一致——
          auto_extract 仅在 memory.enabled 时才被处理） */}
      <div className={`flex items-center justify-between ${p.enabled ? '' : 'opacity-50'}`}>
        <span className="text-sm text-content-primary cursor-help" title={t(p.lang, 'setupFieldMemoryAutoExtractHint')}>{t(p.lang, 'setupFieldMemoryAutoExtract')}</span>
        <ToggleSwitch
          checked={p.autoExtract}
          onChange={p.onAutoExtractChange}
          label={t(p.lang, 'setupFieldMemoryAutoExtract')}
          disabled={!p.enabled}
        />
      </div>
      {/* 记忆目录输入（失焦提交；留空使用默认目录——提示收进占位符） */}
      <div>
        <FieldLabel label={t(p.lang, 'setupFieldMemoryDirectory')} />
        <input
          type="text"
          value={p.directory}
          onChange={(e) => p.onDirectoryChange(e.target.value)}
          onBlur={p.onDirectoryCommit}
          className={inputClass}
          placeholder={t(p.lang, 'setupFieldMemoryDirectoryHint')}
        />
      </div>
      {/* 提取模型 */}
      <div>
        <FieldLabel label={t(p.lang, 'setupFieldMemoryExtractModel')} />
        <GlassDropdown
          value={p.extractModel}
          options={p.modelOptions}
          onChange={p.onExtractModelChange}
          placeholder={t(p.lang, 'setupFieldMemoryModelHint')}
        />
      </div>
      {/* 整合模型 */}
      <div>
        <FieldLabel label={t(p.lang, 'setupFieldMemoryDreamModel')} />
        <GlassDropdown
          value={p.dreamModel}
          options={p.modelOptions}
          onChange={p.onDreamModelChange}
          placeholder={t(p.lang, 'setupFieldMemoryModelHint')}
        />
      </div>
    </div>
  );
}

/** 自动标题配置卡片（开关/模型变更即存） */
function TitleCard(p: {
  lang: UiLanguage;
  enabled: boolean;
  onEnabledChange: (v: boolean) => void;
  model: string;
  onModelChange: (v: string) => void;
  modelOptions: DropdownOption[];
}) {
  return (
    <div className={`space-y-3 ${capCardClass}`}>
      <div className={cardTitleClass}>{t(p.lang, 'setupFieldTitle')}</div>
      {/* 启用开关 */}
      <div className="flex items-center justify-between">
        <span className="text-sm text-content-primary">{t(p.lang, 'setupFieldTitleEnabled')}</span>
        <ToggleSwitch checked={p.enabled} onChange={p.onEnabledChange} label={t(p.lang, 'setupFieldTitleEnabled')} />
      </div>
      {/* 标题生成模型 */}
      <div>
        <FieldLabel label={t(p.lang, 'setupFieldTitleModel')} />
        <GlassDropdown
          value={p.model}
          options={p.modelOptions}
          onChange={p.onModelChange}
          placeholder={t(p.lang, 'setupFieldTitleModelHint')}
        />
      </div>
    </div>
  );
}

/** 通知开关卡片（toast 总开关/音效变更即存） */
function NotificationsCard(p: {
  lang: UiLanguage;
  toastEnabled: boolean;
  onToastEnabledChange: (v: boolean) => void;
  toastSound: boolean;
  onToastSoundChange: (v: boolean) => void;
}) {
  return (
    <div className={`space-y-3 ${capCardClass}`}>
      <div className={cardTitleClass}>{t(p.lang, 'setupFieldNotifications')}</div>
      {/* Toast 总开关 */}
      <div className="flex items-center justify-between">
        <span className="text-sm text-content-primary">{t(p.lang, 'setupFieldToastEnabled')}</span>
        <ToggleSwitch checked={p.toastEnabled} onChange={p.onToastEnabledChange} label={t(p.lang, 'setupFieldToastEnabled')} />
      </div>
      {/* 音效开关：toast 关闭时置灰（联动规则——音效只在 toast 开启时生效） */}
      <div className={`flex items-center justify-between ${p.toastEnabled ? '' : 'opacity-50'}`}>
        <span className="text-sm text-content-primary">{t(p.lang, 'setupFieldSoundEnabled')}</span>
        <ToggleSwitch
          checked={p.toastSound}
          onChange={p.onToastSoundChange}
          label={t(p.lang, 'setupFieldSoundEnabled')}
          disabled={!p.toastEnabled}
        />
      </div>
    </div>
  );
}

/** 权限 LLM 自动审核卡片（开关/审核模型变更即存） */
function ReviewCard(p: {
  lang: UiLanguage;
  autoReview: boolean;
  onAutoReviewChange: (v: boolean) => void;
  model: string;
  onModelChange: (v: string) => void;
  modelOptions: DropdownOption[];
}) {
  return (
    <div className={`space-y-3 ${capCardClass}`}>
      <div className={cardTitleClass}>{t(p.lang, 'setupFieldPermissionReview')}</div>
      {/* 启用开关 */}
      <div className="flex items-center justify-between">
        <span className="text-sm text-content-primary">{t(p.lang, 'setupFieldPermissionReviewEnabled')}</span>
        <ToggleSwitch checked={p.autoReview} onChange={p.onAutoReviewChange} label={t(p.lang, 'setupFieldPermissionReviewEnabled')} />
      </div>
      {/* 审核模型 */}
      <div>
        <FieldLabel label={t(p.lang, 'setupFieldPermissionReviewModel')} />
        <GlassDropdown
          value={p.model}
          options={p.modelOptions}
          onChange={p.onModelChange}
          placeholder={t(p.lang, 'setupFieldPermissionReviewModelHint')}
        />
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
  /** 更新草稿中指定模型行（name / capabilities 局部更新） */
  onUpdateModel: (i: number, patch: Partial<ModelConfig>) => void;
  onAddModel: (name: string) => void;
  onRemoveModel: (i: number) => void;
  /** 提交创建环境（草稿校验通过后可点） */
  onSubmit: () => void;
  /** 提交中 */
  submitting: boolean;
  /** 提交错误 */
  submitError: string | null;
}

/** 环境编辑器：api_format / base_url / 认证 / 模型列表 / OAuth / 创建按钮 */
function EnvEditor({ lang, draft, onDraftChange, onFormatChange, onUpdateModel, onAddModel, onRemoveModel, onSubmit, submitting, submitError }: EnvEditorProps) {
  const isOauth = OAUTH_FORMATS.has(draft.api_format);
  /** 追加模型输入值（与已有环境卡片的追加行同布局） */
  const [newModel, setNewModel] = useState('');
  /** OAuth 流程状态 */
  const [oauth, setOauth] = useState<OauthState>({ status: 'idle', user_code: '', verification_uri: '', device_code: '', error: '' });
  /** 轮询计数 ref */
  const pollCountRef = useRef(0);
  /** 草稿是否可提交（与 SetupForm 的 draftValid 同规则） */
  const canSubmit = draft.api_format !== ''
    && draft.models.some((m) => m.name.trim())
    && (isOauth ? draft.oauth_authorized : (draft.auth_type === 'api_key' ? draft.api_key.trim() !== '' : draft.auth_token.trim() !== ''));

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
                value={m.name}
                onChange={(e) => onUpdateModel(idx, { name: e.target.value })}
                className={inputClass}
                placeholder={t(lang, 'setupFieldModel')}
              />
              {/* 媒体能力勾选（图片） */}
              <label className="shrink-0 flex items-center gap-1 text-[11px] text-content-secondary cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={m.capabilities.includes('image')}
                  onChange={(e) => {
                    const caps = e.target.checked
                      ? [...m.capabilities, 'image']
                      : m.capabilities.filter((c) => c !== 'image');
                    onUpdateModel(idx, { capabilities: caps });
                  }}
                  className="w-3.5 h-3.5 accent-[var(--primary)] cursor-pointer"
                />
                {t(lang, 'setupCapabilityImage')}
              </label>
              {draft.models.length > 1 && (
                <button onClick={() => onRemoveModel(idx)} className="shrink-0 w-7 h-7 flex items-center justify-center rounded text-content-disabled hover:text-danger hover:bg-surface-hover transition-colors cursor-pointer" title={t(lang, 'setupFieldRemoveModel')}>
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M2 6h8" /></svg>
                </button>
              )}
            </div>
          ))}
          {/* 追加模型行：与已有环境卡片的追加行同布局同风格 */}
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
                  onAddModel(newModel.trim());
                  setNewModel('');
                }
              }}
              disabled={!newModel.trim()}
              className="shrink-0 px-2 py-1.5 rounded-md text-xs text-primary border border-primary/30 hover:bg-primary/10 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
            >
              + {t(lang, 'setupFieldAddModel')}
            </button>
          </div>
        </div>
      </div>

      {/* 创建按钮（环境创建为显式动作；其余设置项均即时生效） */}
      <div className="flex items-center gap-3">
        <button
          onClick={onSubmit}
          disabled={!canSubmit || submitting}
          className="px-4 py-2 text-sm text-white bg-primary hover:bg-primary-hover rounded-md transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2"
        >
          {submitting && (
            <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 16 16" fill="none">
              <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeOpacity="0.4" />
              <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          )}
          {t(lang, 'setupEnvCreate')}
        </button>
        {submitError && <span className="text-xs text-danger">{t(lang, 'setupFormSaveFailed')}: {submitError}</span>}
      </div>
    </div>
  );
}

/** api_format 下拉选项（EnvEditor 局部） */
function formatOptionsLocal(lang: UiLanguage): DropdownOption[] {
  return API_FORMATS.map((f) => {
    const keyMap: Record<string, string> = {
      anthropic: 'setupFormatAnthropic', openai: 'setupFormatOpenai',
      response: 'setupFormatResponse',
      copilot: 'setupFormatCopilot', codex: 'setupFormatCodex',
    };
    return { value: f, label: t(lang, keyMap[f] ?? f) };
  });
}

// ===== 渠道配置 Tab =====

interface ChannelsTabProps {
  lang: UiLanguage;
  channels: ChannelsCfg;
  /** 单渠道局部变更（即时合并进状态并 PATCH 落盘） */
  onChannelChange: (name: 'feishu' | 'weixin' | 'qq', patch: Record<string, unknown>) => void;
  /** 注册的工作区列表（渠道运行目录选择数据源） */
  workspaces: WebWorkspaceItem[];
  /** 渠道配置即时保存错误 */
  error: string | null;
}

/** 渠道配置 Tab：飞书 / 微信 / QQ（字段变更即时生效，无保存按钮） */
function ChannelsTab({ lang, channels, onChannelChange, workspaces, error }: ChannelsTabProps) {
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

  /** 切换渠道 enabled 配置（即时保存，不依赖保存按钮）
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
    onChannelChange(name, { enabled: v });
  }, [channels, onChannelChange, lang]);

  return (
    <div className="space-y-4">
      {(error || enableError) && <div className="text-xs text-danger">{error || enableError}</div>}
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
          onChange={(v) => onChannelChange('feishu', { working_directory: v })}
        />
        <TextField lang={lang} labelKey="setupChannelAppId" value={channels.feishu.app_id} onChange={(v) => onChannelChange('feishu', { app_id: v })} />
        <TextField lang={lang} labelKey="setupChannelAppSecret" value={channels.feishu.app_secret} onChange={(v) => onChannelChange('feishu', { app_secret: v })} />
        <SelectField lang={lang} labelKey="setupChannelDomain" value={channels.feishu.domain}
          options={[{ value: 'feishu', label: t(lang, 'setupChannelDomainFeishu') }, { value: 'lark', label: t(lang, 'setupChannelDomainLark') }]}
          onChange={(v) => onChannelChange('feishu', { domain: v })} />
        <BoolField lang={lang} labelKey="setupChannelRequireMention" checked={channels.feishu.require_mention} onChange={(v) => onChannelChange('feishu', { require_mention: v })} />
        <BoolField lang={lang} labelKey="setupChannelAllowBots" checked={channels.feishu.allow_bots} onChange={(v) => onChannelChange('feishu', { allow_bots: v })} />
        <BoolField lang={lang} labelKey="setupChannelGroupSessionsPerUser" checked={channels.feishu.group_sessions_per_user} onChange={(v) => onChannelChange('feishu', { group_sessions_per_user: v })} />
        <BoolField lang={lang} labelKey="setupChannelShowReasoning" checked={channels.feishu.show_reasoning} onChange={(v) => onChannelChange('feishu', { show_reasoning: v })} />
        <GroupPolicyFields lang={lang} policy={channels.feishu.group_policy} onChange={(gp) => onChannelChange('feishu', { group_policy: gp })} />
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
            onLoginSuccess={(creds) => onChannelChange('weixin', {
              account_id: creds.account_id,
              token: creds.token,
              base_url: creds.base_url,
              user_id: creds.user_id,
            })}
          />
        }
      >
        <ChannelWorkDirField
          lang={lang}
          workspaces={workspaces}
          value={channels.weixin.working_directory ?? ''}
          onChange={(v) => onChannelChange('weixin', { working_directory: v })}
        />
        <TextField lang={lang} labelKey="setupChannelAccountId" value={channels.weixin.account_id} onChange={(v) => onChannelChange('weixin', { account_id: v })} />
        <TextField lang={lang} labelKey="setupChannelToken" value={channels.weixin.token} onChange={(v) => onChannelChange('weixin', { token: v })} />
        <TextField lang={lang} labelKey="setupChannelBaseUrl" value={channels.weixin.base_url} onChange={(v) => onChannelChange('weixin', { base_url: v })} />
        <TextField lang={lang} labelKey="setupChannelCdnBaseUrl" value={channels.weixin.cdn_base_url} onChange={(v) => onChannelChange('weixin', { cdn_base_url: v })} />
        <TextField lang={lang} labelKey="setupChannelUserId" value={channels.weixin.user_id} onChange={(v) => onChannelChange('weixin', { user_id: v })} />
        <BoolField lang={lang} labelKey="setupChannelAllowBots" checked={channels.weixin.allow_bots} onChange={(v) => onChannelChange('weixin', { allow_bots: v })} />
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
          onChange={(v) => onChannelChange('qq', { working_directory: v })}
        />
        <TextField lang={lang} labelKey="setupChannelAppId" value={channels.qq.app_id} onChange={(v) => onChannelChange('qq', { app_id: v })} />
        <TextField lang={lang} labelKey="setupChannelClientSecret" value={channels.qq.client_secret} onChange={(v) => onChannelChange('qq', { client_secret: v })} />
        <BoolField lang={lang} labelKey="setupChannelMarkdownSupport" checked={channels.qq.markdown_support} onChange={(v) => onChannelChange('qq', { markdown_support: v })} />
        <BoolField lang={lang} labelKey="setupChannelAllowBots" checked={channels.qq.allow_bots} onChange={(v) => onChannelChange('qq', { allow_bots: v })} />
        <BoolField lang={lang} labelKey="setupChannelGroupSessionsPerUser" checked={channels.qq.group_sessions_per_user} onChange={(v) => onChannelChange('qq', { group_sessions_per_user: v })} />
        <BoolField lang={lang} labelKey="setupChannelRequireMention" checked={channels.qq.require_mention} onChange={(v) => onChannelChange('qq', { require_mention: v })} />
        <BoolField lang={lang} labelKey="setupChannelShowReasoning" checked={channels.qq.show_reasoning} onChange={(v) => onChannelChange('qq', { show_reasoning: v })} />
        <GroupPolicyFields lang={lang} policy={channels.qq.group_policy} onChange={(gp) => onChannelChange('qq', { group_policy: gp })} />
      </ChannelSection>
    </div>
  );
}

// ===== 沙箱配置 Tab =====

interface SandboxTabProps {
  lang: UiLanguage;
  sandbox: SandboxSettings | null;
  /** 沙箱配置变更（即时 PATCH 落盘并热重载生效） */
  onSandboxPatch: (s: SandboxSettings) => void;
  error: string | null;
  permission: PermissionRiskSettings | null;
}

/** 沙箱配置 Tab：文件系统/网络/高级选项 + 风险分级（LOW/MEDIUM/HIGH）只读展示 */
function SandboxTab({ lang, sandbox, onSandboxPatch, error, permission }: SandboxTabProps) {
  if (!sandbox) {
    return <div className="text-sm text-content-disabled py-4">{t(lang, 'setupFormLoadFailed')}</div>;
  }
  const update = (patch: Partial<SandboxSettings>) => onSandboxPatch({ ...sandbox, ...patch });
  const updateFs = (patch: Partial<SandboxSettings['filesystem']>) =>
    onSandboxPatch({ ...sandbox, filesystem: { ...sandbox.filesystem, ...patch } });
  const updateNet = (patch: Partial<SandboxSettings['network']>) =>
    onSandboxPatch({ ...sandbox, network: { ...sandbox.network, ...patch } });

  return (
    <div className="space-y-3">
      {error && <div className="text-xs text-danger">{t(lang, 'setupSandboxSaveFailed')}: {error}</div>}

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
        <BoolWithHint lang={lang} labelKey="setupFieldSandboxWeakerNetworkIsolation" hintKey="setupFieldSandboxWeakerNetworkIsolationHint" checked={sandbox.enable_weaker_network_isolation} onChange={(v) => update({ enable_weaker_network_isolation: v })} />
        <BoolField lang={lang} labelKey="setupFieldSandboxWeakerNested" checked={sandbox.enable_weaker_nested_sandbox} onChange={(v) => update({ enable_weaker_nested_sandbox: v })} />
        <BoolField lang={lang} labelKey="setupFieldSandboxAllowGitConfig" checked={sandbox.allow_git_config} onChange={(v) => update({ allow_git_config: v })} />
        <div>
          <FieldLabel label={t(lang, 'setupFieldSandboxMandatoryDepth')} />
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
        title={t(lang, hintKey)}
        className="w-full flex items-center gap-2.5 px-3 py-2 bg-surface-card-alt hover:bg-surface-hover transition-colors cursor-pointer"
      >
        <svg className={`w-3 h-3 shrink-0 text-content-disabled transition-transform ${open ? 'rotate-90' : ''}`} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M4.5 3L7.5 6L4.5 9" /></svg>
        <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-bold tracking-wide ${badgeClass}`}>{level}</span>
        <span className="text-sm font-medium text-content-primary flex-1 text-left">{t(lang, titleKey)}</span>
      </button>
      {open && (
        <div className="px-4 py-3 space-y-3">
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

/** 带悬浮提示的布尔开关行（说明收敛进标签的 title 提示） */
function BoolWithHint({ lang, labelKey, hintKey, checked, onChange }: { lang: UiLanguage; labelKey: string; hintKey?: string; checked: boolean; onChange: (v: boolean) => void; }) {
  return (
    <div className="flex items-center justify-between">
      <span className={`text-sm text-content-secondary ${hintKey ? 'cursor-help' : ''}`} title={hintKey ? t(lang, hintKey) : undefined}>
        {t(lang, labelKey)}
      </span>
      <ToggleSwitch checked={checked} onChange={onChange} label={t(lang, labelKey)} />
    </div>
  );
}

/** 带悬浮提示的文本输入行（失焦或回车提交） */
function TextFieldWithHint({ lang, labelKey, hintKey, value, onChange }: { lang: UiLanguage; labelKey: string; hintKey?: string; value: string; onChange: (v: string) => void; }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const commit = () => {
    if (draft !== value) onChange(draft);
  };
  return (
    <div>
      <FieldLabel label={t(lang, labelKey)} hint={hintKey ? t(lang, hintKey) : undefined} />
      <input
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
        className={inputClass}
      />
    </div>
  );
}

/** 字符串列表编辑（多行文本，每行一个元素；失焦提交；readOnly 时只读展示） */
function StringListField({ lang, labelKey, hintKey, value, onChange, readOnly }: { lang: UiLanguage; labelKey: string; hintKey?: string; value: string[]; onChange?: (v: string[]) => void; readOnly?: boolean; }) {
  const [draft, setDraft] = useState(value.join('\n'));
  useEffect(() => setDraft(value.join('\n')), [value]);
  const commit = () => {
    if (readOnly || !onChange) return;
    const next = draft.split('\n').map((s) => s.trim()).filter(Boolean);
    if (next.join('\n') !== value.join('\n')) onChange(next);
  };
  return (
    <div>
      <FieldLabel label={t(lang, labelKey)} hint={hintKey ? t(lang, hintKey) : undefined} />
      <textarea
        rows={3}
        value={draft}
        readOnly={readOnly}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        className={`${inputClass} font-mono text-xs resize-y ${readOnly ? 'opacity-70 cursor-not-allowed' : ''}`}
      />
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

/** 渠道运行目录选择行（多目录空间：渠道 agent 固定在该目录运行）
 * 说明文字收敛进标签悬浮提示（title），不再平铺小字注释 */
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
      <FieldLabel label={t(lang, 'setupChannelWorkDir')} hint={t(lang, 'setupChannelWorkDirHint')} />
      <GlassDropdown
        value={value}
        placeholder={t(lang, 'setupChannelWorkDirPlaceholder')}
        options={options}
        onChange={onChange}
      />
    </div>
  );
}

/** 文本字段行（失焦或回车提交，避免逐键请求） */
function TextField({ lang, labelKey, value, onChange }: { lang: UiLanguage; labelKey: string; value: string; onChange: (v: string) => void; })  {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <div>
      <FieldLabel label={t(lang, labelKey)} />
      <input
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => { if (draft !== value) onChange(draft); }}
        onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
        className={inputClass}
      />
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
