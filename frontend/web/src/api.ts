/**
 * @fileoverview Web 前端 REST API 客户端
 *
 * 封装与后端 HTTP REST 端点的交互（env / oauth / settings / channels）。
 * WebSocket 继续承载实时聊天流，此处仅处理配置类 CRUD，语义更贴合表单场景。
 *
 * 所有请求使用相对路径（/api/...），与前端同源部署的后端配合。
 *
 * @module api
 */

/** 环境配置信息（GET /api/envs 返回的单个 env 条目） */
export interface EnvInfo {
  /** 环境键名，如 env_1 */
  env_key: string;
  /** API 格式：anthropic/openai/copilot/codex */
  api_format: string;
  /** 接入地址 */
  base_url: string;
  /** 是否已配置凭据（api_key 或 auth_token） */
  has_credential: boolean;
  /** 是否为当前活跃环境 */
  active: boolean;
  /** 模型字典：{model_1: "claude-sonnet-4-5", model_2: "..."} */
  models: Record<string, string>;
}

/** GET /api/envs 响应 */
export interface EnvsResponse {
  /** 所有环境列表 */
  envs: EnvInfo[];
  /** 当前活跃环境键名（无环境时为 null） */
  active_env_key: string | null;
}

/** GET /api/settings 响应（非敏感字段，供表单回显） */
export interface SettingsResponse {
  /** 界面语言（zh-CN / en-US / 空串） */
  ui_language: string;
  /** 工作目录（未设置时为 null） */
  working_directory: string | null;
  /** 上下文窗口大小（token） */
  context_window: number;
  /** 最大输出 tokens */
  max_tokens: number;
  /** 最大对话轮次（1~512） */
  max_turns: number;
  /** 当前活跃模型引用，如 env_1.model_1 */
  model: string;
  /** Web 端主题：light（浅色）/ dark（深色）/ system（跟随系统） */
  theme: 'light' | 'dark' | 'system';
  /** 通知开关（toast 与音效独立保存，音效仅在 toast 开启时生效） */
  notifications: {
    /** toast 总开关（关闭后后端不再下发 toast 事件） */
    enabled: boolean;
    /** 提示音效开关 */
    sound: boolean;
  };
  /** 记忆系统配置 */
  memory: {
    /** 是否启用记忆功能 */
    enabled: boolean;
    /** 是否允许后台 LLM 自动提取/整合（false = 仅手动记录） */
    auto_extract: boolean;
    /** 提取子代理模型（env_N.model_M），未设置时为 null */
    extract_model: string | null;
    /** 整合子代理模型（env_N.model_M），未设置时为 null */
    dream_model: string | null;
    /** 自定义记忆目录（绝对路径或 ~/ 开头），未设置时为 null */
    directory: string | null;
  };
  /** 会话自动标题配置 */
  title: {
    /** 是否启用自动标题 */
    enabled: boolean;
    /** 标题生成子代理模型（env_N.model_M），未设置时为 null（继承当前） */
    model: string | null;
  };
  /** 沙箱配置（可删改） */
  sandbox: SandboxSettings;
  /** 权限模式（default / plan / full_auto / yolo） */
  permission_mode?: string;
  /** 权限风险分级配置（LOW/MEDIUM/HIGH 三层级） */
  permission: PermissionRiskSettings;
  /** 权限 LLM 自动审核配置（auto 模式下高危操作与沙箱拦截由 LLM 审核放行） */
  permission_review: PermissionReviewSettings;
}

/** 权限 LLM 自动审核配置 */
export interface PermissionReviewSettings {
  /** 是否启用 LLM 自动审核（仅 auto 模式生效，关闭时走现有人工确认） */
  auto_review: boolean;
  /** 审核模型（env_N.model_M），为 null 时继承当前会话模型 */
  review_model: string | null;
}

/** 权限风险分级配置（明确区分 LOW/MEDIUM/HIGH 三层级） */
export interface PermissionRiskSettings {
  /** HIGH：高危 bash 命令正则（rm、git restore 等） */
  dangerous_bash_patterns: string[];
  /** HIGH：高危 powershell 命令正则（Remove-Item 等） */
  dangerous_powershell_patterns: string[];
  /** LOW：只读命令前缀（ls、cat、git status 等） */
  read_only_commands: string[];
  /** MEDIUM：变更类工具（write_file、edit_file 等） */
  medium_risk_tools: string[];
}

/** 沙箱网络配置 */
export interface SandboxNetworkSettings {
  allowed_domains: string[];
  denied_domains: string[];
  allow_unix_sockets: string[];
  allow_all_unix_sockets: boolean;
  allow_local_binding: boolean;
  http_proxy_port: number | null;
  socks_proxy_port: number | null;
}

/** 沙箱文件系统配置 */
export interface SandboxFilesystemSettings {
  allow_read: string[];
  deny_read: string[];
  allow_write: string[];
  deny_write: string[];
}

/** 沙箱内置 ripgrep 配置 */
export interface SandboxRipgrepSettings {
  command: string;
  args: string[];
}

/** 沙箱配置（与后端 SandboxSettings 对齐，snake_case） */
export interface SandboxSettings {
  enabled_platforms: string[];
  excluded_commands: string[];
  network: SandboxNetworkSettings;
  filesystem: SandboxFilesystemSettings;
  ignore_violations: Record<string, string[]>;
  enable_weaker_nested_sandbox: boolean;
  enable_weaker_network_isolation: boolean;
  mandatory_deny_search_depth: number;
  allow_git_config: boolean;
  ripgrep: SandboxRipgrepSettings | null;
}

/** PATCH /api/settings/sandbox 请求体（字段可选，只更新提供的字段） */
export interface UpdateSandboxPayload {
  enabled_platforms?: string[];
  excluded_commands?: string[];
  network?: Partial<SandboxNetworkSettings>;
  filesystem?: Partial<SandboxFilesystemSettings>;
  ignore_violations?: Record<string, string[]>;
  enable_weaker_nested_sandbox?: boolean;
  enable_weaker_network_isolation?: boolean;
  mandatory_deny_search_depth?: number;
  allow_git_config?: boolean;
  ripgrep?: SandboxRipgrepSettings | null;
}

/** PATCH /api/settings/memory 请求体（字段可选，只更新提供的字段） */
export interface UpdateMemoryPayload {
  /** 是否启用记忆功能 */
  enabled?: boolean;
  /** 是否允许后台 LLM 自动提取/整合 */
  auto_extract?: boolean;
  /** 提取子代理模型（空字符串清除） */
  extract_model?: string;
  /** 整合子代理模型（空字符串清除） */
  dream_model?: string;
  /** 自定义记忆目录（空字符串清除） */
  directory?: string;
}

/** PATCH /api/settings/title 请求体（字段可选，只更新提供的字段） */
export interface UpdateTitlePayload {
  /** 是否启用自动标题 */
  enabled?: boolean;
  /** 标题生成子代理模型（空字符串清除 = 继承当前） */
  model?: string;
}

/** PATCH /api/settings/permission-review 请求体（字段可选，只更新提供的字段） */
export interface UpdatePermissionReviewPayload {
  /** 是否启用 LLM 自动审核（仅 auto 模式生效） */
  auto_review?: boolean;
  /** 审核模型（空字符串清除 = 继承当前会话模型） */
  review_model?: string;
}

/** OAuth device flow 启动响应 */
export interface OauthStartResponse {
  /** 设备码（用于轮询） */
  device_code: string;
  /** 用户码（展示给用户输入） */
  user_code: string;
  /** 验证网址（用户在浏览器打开） */
  verification_uri: string;
}

/** OAuth 轮询响应 */
export interface OauthPollResponse {
  /** 是否授权成功 */
  success: boolean;
  /** 失败时的错误文本（可选） */
  error?: string;
}

/** 渠道配置（GET/PATCH /api/channels，结构与后端 ChannelsConfig 对齐） */
export interface ChannelsConfig {
  feishu: Record<string, unknown>;
  weixin: Record<string, unknown>;
  qq: Record<string, unknown>;
}

/** 渠道运行时状态条目（GET /api/channels/status） */
export interface ChannelRuntimeStatusEntry {
  /** 渠道是否健康（看门狗判定） */
  healthy: boolean;
  /** 渠道 runner 是否正在运行 */
  running: boolean;
}

/** 渠道运行时状态响应（GET /api/channels/status） */
export type ChannelsRuntimeStatus = Record<string, ChannelRuntimeStatusEntry>;

/** 测试连接请求体（POST /api/channels/{name}/test） */
export interface TestConnectionPayload {
  /** 飞书/QQ 应用 ID */
  app_id?: string;
  /** 飞书应用密钥 */
  app_secret?: string;
  /** QQ 应用密钥 */
  client_secret?: string;
  /** 飞书域名（feishu/lark） */
  domain?: string;
}

/** 测试连接响应 */
export interface TestConnectionResponse {
  /** 是否成功 */
  ok: boolean;
  /** 结果消息 */
  message: string;
}

/** 微信二维码启动响应（POST /api/channels/weixin/qr/start） */
export interface WeixinQrStartResponse {
  /** 二维码 hex token（用于轮询状态） */
  qrcode: string;
  /** 二维码内容（URL 或 hex，供前端渲染为图片） */
  qr_content: string;
  /** 二维码 PNG 图片的 base64 编码（可直接用于 <img src="data:image/png;base64,...">） */
  qr_image_b64: string;
}

/** 微信扫码状态响应（GET /api/channels/weixin/qr/status） */
export interface WeixinQrStatusResponse {
  /** 扫码状态：wait/scaned/scaned_but_redirect/confirmed/expired */
  status: string;
  /** 重定向后的新 API 入口（status=scaned_but_redirect 时返回） */
  base_url?: string;
  /** 确认后的凭据（status=confirmed 时返回） */
  credentials?: {
    account_id: string;
    token: string;
    base_url: string;
    user_id: string;
  };
}

/** cron 任务（GET /api/cron/jobs 返回的单个任务条目） */
export interface CronJob {
  /** 唯一标识符 */
  id: string;
  /** 人类可读的任务名称 */
  name: string;
  /** 5 字段 cron 表达式（本地时间） */
  schedule: string;
  /** 触发时执行的提示词 */
  prompt: string;
  /** 是否启用 */
  enabled: boolean;
  /** 是否重复执行（False 为一次性任务） */
  recurring: boolean;
  /** 一次性任务执行后是否自动删除 */
  delete_after_run: boolean;
  /** 工作目录 */
  cwd: string | null;
  /** 创建时间（本地时间 ISO） */
  created_at: string;
  /** 最后更新时间 */
  updated_at: string;
  /** 下次运行时间（无时区本地时间 ISO，无效时缺失） */
  next_run: string | null;
  /** 上次运行时间 */
  last_run: string | null;
  /** 上次执行状态：success/failed/timeout/error */
  last_status: string | null;
  /** 连续错误次数（成功时重置为 0） */
  consecutive_errors: number;
  /** 投递目标列表（channel:chat_id 格式） */
  deliver_to: string[];
  /** 来源渠道 */
  origin_channel: string;
  /** 来源会话 */
  chat_id: string;
  /** 指定会话执行：目标会话 ID（null/缺失 = 独立新会话执行，旧任务无该字段） */
  session_id: string | null | undefined;
}

/** 创建 cron 任务请求体 */
export interface CreateCronJobPayload {
  /** 任务名称（可选，缺省自动生成） */
  name?: string;
  /** 5 字段 cron 表达式（必填） */
  schedule: string;
  /** 触发时执行的提示词（必填） */
  prompt: string;
  /** 是否重复执行（默认 True） */
  recurring?: boolean;
  /** 是否启用（默认 True） */
  enabled?: boolean;
  /** 一次性任务执行后是否自动删除 */
  delete_after_run?: boolean;
  /** 投递目标列表（channel:chat_id 格式，可选） */
  deliver_to?: string[];
  /** 指定会话执行：目标会话 ID（可选；缺省 = 独立新会话） */
  session_id?: string;
  /** 任务执行的工作区目录（可选；缺省 = 默认工作区，须为已注册目录） */
  cwd?: string;
}

/** 更新 cron 任务请求体（仅提供需要修改的字段） */
export type UpdateCronJobPayload = Partial<CreateCronJobPayload>;

/** 调度器状态（GET /api/cron/status） */
export interface CronSchedulerStatus {
  /** 调度器是否运行 */
  running: boolean;
  /** 调度器进程 PID（未运行时为 null） */
  pid: number | null;
  /** 任务总数 */
  total_jobs: number;
  /** 启用任务数 */
  enabled_jobs: number;
}

/** 手动运行结果（POST /api/cron/jobs/{id}/run） */
export interface CronRunResult {
  /** 执行状态：success/failed/timeout/error */
  status: string;
  /** 子进程退出码 */
  returncode: number;
  /** 开始时间 */
  started_at: string;
  /** 结束时间 */
  ended_at: string;
  /** 标准输出（截断） */
  stdout: string;
  /** 标准错误（截断） */
  stderr: string;
}

/** cron 任务列表响应（GET /api/cron/jobs） */
export interface CronJobsResponse {
  /** 全部任务（含禁用） */
  jobs: CronJob[];
  /** 手动运行中的任务 ID（前端据此禁用 run 按钮） */
  running_jobs?: string[];
}

/** 项目会话摘要（GET /api/cron/sessions，dropdown 数据源） */
export interface CronSessionSummary {
  /** 会话 ID */
  session_id: string;
  /** 会话摘要 */
  summary: string;
  /** 消息数 */
  message_count: number;
  /** 最后更新时间戳 */
  updated_at: number;
}

/** 渠道会话条目（GET /api/cron/channel_sessions） */
export interface CronChannelSession {
  /** 渠道内会话标识（如 ou_xxx / oc_xxx / wxid_xxx / openid_xxx） */
  chat_id: string;
  /** 用户显示名（如有） */
  user_name: string;
  /** 会话类型：dm / group */
  chat_type: string;
  /** 最后活跃时间（如 "2026-06-28 10:30"） */
  last_active: string;
}

/** 创建 env 请求体 */
export interface CreateEnvPayload {
  api_format: string;
  base_url?: string | null;
  api_key?: string;
  auth_token?: string;
  model_1: string;
  model_2?: string | null;
}

/** 更新 env 请求体 */
export interface UpdateEnvPayload {
  api_format?: string;
  base_url?: string;
  api_key?: string;
  auth_token?: string;
  /** key 缺省时后端按现有最大编号 +1 自动分配 */
  add_models?: { key?: string; value: string }[];
  remove_models?: string[];
}

/**
 * 统一请求封装：拼接相对路径、JSON 序列化、错误提取。
 *
 * @param url - 相对路径（如 /api/envs）
 * @param options - fetch options
 * @returns 解析后的 JSON 响应
 * @throws Error 当 HTTP 状态非 2xx 时，抛出包含 detail 的错误
 */
async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch { /* 非 JSON 响应，保留默认 detail */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

/** env 相关 API */
export const envApi = {
  /** 列出所有环境配置 */
  list: () => request<EnvsResponse>('/api/envs'),
  /** 新增环境 */
  create: (payload: CreateEnvPayload) =>
    request<{ env_key: string; success: boolean }>('/api/envs', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  /** 修改环境字段 */
  update: (envKey: string, payload: UpdateEnvPayload) =>
    request<{ success: boolean }>(`/api/envs/${encodeURIComponent(envKey)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  /** 删除环境 */
  remove: (envKey: string) =>
    request<{ success: boolean }>(`/api/envs/${encodeURIComponent(envKey)}`, {
      method: 'DELETE',
    }),
  /** 切换活跃环境 */
  activate: (envKey: string) =>
    request<{ success: boolean }>(`/api/envs/${encodeURIComponent(envKey)}/activate`, {
      method: 'POST',
    }),
};

/** OAuth 相关 API（copilot / codex 设备码流程） */
export const oauthApi = {
  /** 启动设备码授权流程 */
  start: (provider: 'copilot' | 'codex') =>
    request<OauthStartResponse>(`/api/oauth/${provider}/start`, { method: 'POST' }),
  /** 轮询授权完成状态 */
  poll: (provider: 'copilot' | 'codex', deviceCode: string) =>
    request<OauthPollResponse>(`/api/oauth/${provider}/poll`, {
      method: 'POST',
      body: JSON.stringify({ device_code: deviceCode }),
    }),
};

/** settings 相关 API（非敏感字段读写） */
export const settingsApi = {
  /** 读取非敏感 settings 字段 */
  get: () => request<SettingsResponse>('/api/settings'),
  /** 修改界面语言 */
  updateUiLanguage: (uiLanguage: string) =>
    request<{ success: boolean }>('/api/settings/ui_language', {
      method: 'PATCH',
      body: JSON.stringify({ ui_language: uiLanguage }),
    }),
  /** 修改工作目录（空字符串清除） */
  updateWorkingDirectory: (workingDirectory: string) =>
    request<{ success: boolean; working_directory: string | null }>('/api/settings/working_directory', {
      method: 'PATCH',
      body: JSON.stringify({ working_directory: workingDirectory }),
    }),
  /** 修改模型参数（context_window / max_tokens / max_turns，仅更新提供的字段） */
  updateModelParams: (payload: { context_window?: number; max_tokens?: number; max_turns?: number }) =>
    request<{ success: boolean }>('/api/settings/model-params', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  /** 修改记忆配置（enabled / extract_model / dream_model / directory，只更新提供的字段） */
  updateMemory: (payload: UpdateMemoryPayload) =>
    request<{ success: boolean }>('/api/settings/memory', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  /** 修改会话自动标题配置（enabled / model，只更新提供的字段） */
  updateTitle: (payload: UpdateTitlePayload) =>
    request<{ success: boolean }>('/api/settings/title', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  /** 修改权限 LLM 自动审核配置（auto_review / review_model，只更新提供的字段） */
  updatePermissionReview: (payload: UpdatePermissionReviewPayload) =>
    request<{ success: boolean; permission_review: PermissionReviewSettings }>('/api/settings/permission-review', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  /** 修改 Web 端主题（light / dark / system），同步写入 settings.json */
  updateTheme: (theme: 'light' | 'dark' | 'system') =>
    request<{ success: boolean }>('/api/settings/theme', {
      method: 'PATCH',
      body: JSON.stringify({ theme }),
    }),
  /** 修改通知开关（enabled = toast 总开关，sound = 音效；只更新提供的字段） */
  updateNotifications: (payload: { enabled?: boolean; sound?: boolean }) =>
    request<{
      success: boolean;
      notifications: { enabled: boolean; sound: boolean };
    }>('/api/settings/notifications', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  /** 修改沙箱配置（只更新提供的字段），保存并热重载生效 */
  updateSandbox: (payload: UpdateSandboxPayload) =>
    request<{ success: boolean; sandbox: SandboxSettings }>('/api/settings/sandbox', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
};

/** channels 相关 API（channels.json 读写 + 运行时控制 + 微信扫码 + 测试连接） */
export const channelsApi = {
  /** 读取当前渠道配置 */
  get: () => request<ChannelsConfig>('/api/channels'),
  /** 部分更新渠道配置（仅合并提供的渠道字段） */
  update: (payload: Partial<ChannelsConfig>) =>
    request<ChannelsConfig>('/api/channels', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  /** 查询各渠道运行时状态（守护进程内 runner 活跃情况） */
  getStatus: () => request<ChannelsRuntimeStatus>('/api/channels/status'),
  /** 启动指定渠道 runner（通过 IPC 通知守护进程；可携带运行目录） */
  start: (name: string, workingDirectory?: string) =>
    request<{ ok: boolean; daemon_running: boolean }>(`/api/channels/${encodeURIComponent(name)}/start`, {
      method: 'POST',
      body: JSON.stringify(workingDirectory ? { working_directory: workingDirectory } : {}),
    }),
  /** 停止指定渠道 runner（通过 IPC 通知守护进程） */
  stop: (name: string) =>
    request<{ ok: boolean }>(`/api/channels/${encodeURIComponent(name)}/stop`, {
      method: 'POST',
    }),
  /** 测试渠道连接（飞书/QQ 校验凭据） */
  test: (name: string, payload: TestConnectionPayload) =>
    request<TestConnectionResponse>(`/api/channels/${encodeURIComponent(name)}/test`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  /** 获取微信登录二维码（不开浏览器） */
  weixinQrStart: () =>
    request<WeixinQrStartResponse>('/api/channels/weixin/qr/start', { method: 'POST' }),
  /** 轮询微信扫码状态 */
  weixinQrStatus: (qrcode: string, baseUrl?: string) => {
    const params = new URLSearchParams({ qrcode });
    if (baseUrl) params.set('base_url', baseUrl);
    return request<WeixinQrStatusResponse>(`/api/channels/weixin/qr/status?${params.toString()}`);
  },
};

/** cron 相关 API（定时任务注册表 CRUD + 调度器状态 + 手动触发） */
export const cronApi = {
  /** 查询调度器运行状态与任务统计 */
  status: () => request<CronSchedulerStatus>('/api/cron/status'),
  /** 列出全部任务（含禁用）及手动运行中的任务 ID */
  list: () => request<CronJobsResponse>('/api/cron/jobs'),
  /** 创建任务（创建后后端自动确保调度器运行） */
  create: (payload: CreateCronJobPayload) =>
    request<{ id: string; job: CronJob }>('/api/cron/jobs', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  /** 更新任务（id 或名称定位；仅合并提供的字段） */
  update: (identifier: string, payload: UpdateCronJobPayload) =>
    request<{ success: boolean; job: CronJob }>(`/api/cron/jobs/${encodeURIComponent(identifier)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  /** 删除任务 */
  remove: (identifier: string) =>
    request<{ success: boolean }>(`/api/cron/jobs/${encodeURIComponent(identifier)}`, {
      method: 'DELETE',
    }),
  /** 手动触发执行任务（请求等待执行完成，返回结果摘要） */
  run: (identifier: string) =>
    request<CronRunResult>(`/api/cron/jobs/${encodeURIComponent(identifier)}/run`, {
      method: 'POST',
    }),
  /** 列出项目会话（session_id dropdown 数据源；可按工作区目录过滤） */
  sessions: (cwd?: string) => {
    const params = cwd ? `?cwd=${encodeURIComponent(cwd)}` : '';
    return request<{ sessions: CronSessionSummary[] }>(`/api/cron/sessions${params}`);
  },
  /** 列出各渠道活跃会话（deliver_to dropdown 数据源） */
  channelSessions: () => request<{ channels: Record<string, CronChannelSession[]> }>('/api/cron/channel_sessions'),
};
