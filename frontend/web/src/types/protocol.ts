/**
 * @fileoverview 前后端通信协议类型定义
 *
 * 与 src/illusion/ui/protocol.py 中的模型一一对应。
 * 定义了 Web 前端与后端之间通信所使用的所有数据类型。
 *
 * @module protocol
 */

// ---- 转录项 ----

/**
 * 转录项接口
 *
 * 表示对话中的一个消息项，可以是用户消息、助手回复、工具调用等。
 */
export interface TranscriptItem {
  /**
   * 消息角色类型：
   * - 'system': 系统消息
   * - 'user': 用户消息
   * - 'assistant': 助手回复
   * - 'tool': 工具调用
   * - 'tool_result': 工具执行结果
   * - 'log': 日志消息
   * - 'plan': 计划内容
   */
  role: 'system' | 'user' | 'assistant' | 'tool' | 'tool_result' | 'log' | 'plan';
  /** 消息文本内容 */
  text: string;
  /** 工具名称（仅在 role 为 'tool' 或 'tool_result' 时存在） */
  tool_name?: string;
  /** 工具输入参数（仅在 role 为 'tool' 时存在） */
  tool_input?: Record<string, unknown>;
  /** 是否为错误消息（仅在 role 为 'tool_result' 时存在） */
  is_error?: boolean;
  /** 助手的思考/推理过程（仅在 role 为 'assistant' 时存在） */
  reasoning?: string;
  /** 工具调用的唯一标识符（仅在 role 为 'tool' 或 'tool_result' 时存在） */
  tool_use_id?: string;
  /** 工具执行期间的流式进度消息（仅在 role 为 'tool_result' 时存在，
   *  由前端在 tool_completed 时从 pending 状态转移保留——agent 子任务的
   *  思考过程在完成后折叠展示，不随 pending 移除而丢失） */
  progress_messages?: Array<{message: string; type?: string}>;
  /** 命令产物标记：命令选择器产生的转录（如 /context set 512000），非真实用户输入 */
  is_command?: boolean;
}

// ---- 任务快照 ----

/**
 * 任务快照接口
 *
 * 表示后台任务的当前状态快照。
 */
export interface TaskSnapshot {
  /** 任务唯一标识符 */
  id: string;
  /** 任务类型 */
  type: string;
  /** 任务状态 */
  status: string;
  /** 任务描述 */
  description: string;
  /** 任务元数据 */
  metadata: Record<string, string>;
}

// ---- 选择 / 模态框 ----

/**
 * 选择选项接口
 *
 * 用于选择模态对话框中的单个选项。
 */
export interface SelectOption {
  /** 选项值（提交到后端的值） */
  value: string;
  /** 选项显示标签 */
  label: string;
  /** 选项描述（可选） */
  description?: string;
  /** 是否为当前活跃选项（可选） */
  active?: boolean;
}

/**
 * 选择请求载荷接口
 *
 * 后端发送到前端的选择请求，用于显示选择模态对话框。
 */
export interface SelectRequestPayload {
  /** 对话框标题 */
  title: string;
  /** 关联的命令名称 */
  command: string;
  /** 可选项列表 */
  options: SelectOption[];
}

/**
 * 待处理工具调用接口
 *
 * 表示一个已经开始但尚未完成的工具调用。
 */
export interface PendingToolCall {
  /** 工具名称 */
  tool_name: string;
  /** 工具调用的唯一标识符 */
  tool_use_id: string;
  /** 工具输入参数（可选） */
  tool_input?: Record<string, unknown>;
  /** 流式进度消息列表（可选，由 tool_progress 事件累积；message 为内容，type 为进度类型） */
  progressMessages?: Array<{message: string; type?: string}>;
}

/**
 * 待办事项快照接口
 *
 * 表示单个待办事项的当前状态。
 */
export interface TodoItemSnapshot {
  /** 待办事项内容 */
  content: string;
  /** 待办事项状态：'pending'（待处理）、'in_progress'（进行中）、'completed'（已完成） */
  status: string;
  /** 当前活动形式描述（如 "正在编写代码"） */
  activeForm: string;
}

/**
 * MCP 服务器快照接口
 *
 * 表示 MCP（Model Context Protocol）服务器的当前状态快照。
 */
export interface McpServerSnapshot {
  /** 服务器名称 */
  name: string;
  /** 服务器状态（如 'connected', 'disconnected' 等） */
  state: string;
  /** 状态详情（可选） */
  detail?: string;
  /** 传输协议类型（可选，如 'stdio', 'sse' 等） */
  transport?: string;
  /** 是否已配置认证（可选） */
  auth_configured?: boolean;
  /** 可用工具数量（可选） */
  tool_count?: number;
  /** 可用资源数量（可选） */
  resource_count?: number;
}

/**
 * 群体协作者快照接口
 *
 * 表示群体协作模式中的一个协作者的当前状态。
 */
export interface SwarmTeammateSnapshot {
  /** 协作者唯一标识符 */
  id: string;
  /** 协作者名称 */
  name: string;
  /** 协作者状态：'running'（运行中）、'idle'（空闲）、'done'（完成）、'error'（错误） */
  status: string;
}

/**
 * 群体协作通知快照接口
 *
 * 表示群体协作模式中的一个通知消息。
 */
export interface SwarmNotificationSnapshot {
  /** 通知消息内容 */
  message: string;
  /** 通知时间戳（Unix 时间戳，毫秒，可选） */
  timestamp?: number;
}

/**
 * 技能快照接口
 *
 * 表示一个可用的技能。
 */
export interface SkillSnapshot {
  /** 技能名称 */
  name: string;
  /** 技能描述 */
  description: string;
  /** 技能来源：'project'（项目级）、'user'（用户级）、'builtin'（内置） */
  source: string;
}

/**
 * 插件快照接口
 *
 * 表示一个已安装的插件。
 */
export interface PluginSnapshot {
  /** 插件名称 */
  name: string;
  /** 插件描述 */
  description: string;
  /** 是否已启用 */
  enabled: boolean;
  /** 技能数量 */
  skill_count: number;
  /** MCP 服务器数量 */
  mcp_count: number;
  /** 命令数量 */
  command_count: number;
}

/**
 * 规则快照接口
 *
 * 表示一个已加载的规则。
 */
export interface RuleSnapshot {
  /** 规则名称 */
  name: string;
  /** 规则来源：'project'（项目级）、'user'（用户级）、'builtin'（内置） */
  source: string;
}

/**
 * 代理（子代理定义）快照接口
 *
 * web_resources 推送的单个代理条目（内置 + 用户级 + 项目级 + 插件的合并视图）。
 */
export interface AgentSnapshot {
  /** 代理名称 */
  name: string;
  /** 使用时机描述 */
  description: string;
  /** 来源：'builtin'（内置）、'user'（用户级）、'plugin'（插件） */
  source: string;
  /** UI 颜色名（可选，AGENT_COLORS 之一） */
  color?: string | null;
  /** 模型覆盖（可选，null 表示继承默认） */
  model?: string | null;
  /** 是否为后台代理 */
  background?: boolean;
}

/**
 * 智能体与任务条目接口
 *
 * web_agent_tasks 推送的单个条目（复用 /agent 指令的双数据源：
 * 前台 agent 工具结果 + 后台 task-notification）。
 */
export interface AgentTaskItem {
  /** 条目 ID（前台 agent 为 tool_use_id；后台任务为 task_id，可传给 /agent 查摘要） */
  id: string;
  /** 展示标题（agent 的 description/name 或任务名） */
  title: string;
  /** 类型：'agent'（智能体）| 'task'（任务） */
  type: 'agent' | 'task';
  /** 状态：completed / failed / 运行中等原始状态 */
  status: string;
  /** 摘要（截断到 160 字） */
  summary: string;
}

/**
 * 文件树节点接口
 *
 * web_file_tree 推送的单个目录条目（单层，前端按需逐层拉取）。
 */
export interface FileTreeNode {
  /** 条目名（不含路径） */
  name: string;
  /** 工作区内相对路径（/ 分隔） */
  path: string;
  /** 条目类型：'dir' | 'file' */
  kind: 'dir' | 'file';
  /** 文件字节大小（仅 file） */
  size?: number;
}

/**
 * 会话内修改文件条目接口
 *
 * web_session_files 推送的单个条目（会话文件区块数据源）。
 * 该列表独立于 Git 与工作区边界：可包含未纳入 Git 追踪、项目目录之外、
 * 无 Git 环境的文件（均可经 web_read_session_file 直接预览）。
 */
export interface SessionFileItem {
  /** 文件绝对路径（读取与安全校验的唯一键） */
  path: string;
  /** 展示路径（统一绝对路径，/ 分隔） */
  display: string;
  /** 修改该文件的工具名（如 edit_file / write_file） */
  tool: string;
}

/**
 * 文件增删行数统计条目接口
 *
 * web_file_stats 推送的单个条目（单轮变更条数据源）。
 * 数值语义为"该文件当前相对 Git HEAD 的差异"；非 Git 环境 / 工作区外 /
 * 二进制文件降级为 null（前端只显示文件名）；文件不存在标记 deleted。
 */
export interface FileStatItem {
  /** 变更工具输入的原始路径串（前端映射键，与请求 paths 一一对应） */
  input: string;
  /** 文件绝对路径（预览请求参数；占位条目为空串） */
  path: string;
  /** 展示路径（统一绝对路径，/ 分隔；占位条目回显原始串） */
  display: string;
  /** 变更状态：'added'（未跟踪新文件）| 'modified' | 'deleted' | null（无法判定） */
  status?: 'added' | 'modified' | 'deleted' | null;
  /** 新增行数（null 表示无法统计） */
  insertions?: number | null;
  /** 删除行数（null 表示无法统计） */
  deletions?: number | null;
}

/**
 * Git 变更文件接口
 *
 * web_git_status 推送的单个变更文件。
 */
export interface GitFileStatus {
  /** 工作区内相对路径 */
  path: string;
  /** 变更状态：'added' | 'modified' | 'deleted' | 'renamed' | 'untracked' | 'unmerged' */
  status: string;
  /** 是否已暂存 */
  staged: boolean;
  /** 重命名原始路径（仅 renamed） */
  orig_path?: string | null;
  /** 新增行数（可选，二进制/未跟踪为 null） */
  insertions?: number | null;
  /** 删除行数（可选） */
  deletions?: number | null;
}

/**
 * Git 状态快照接口
 *
 * web_git_status 推送的整份快照。
 */
export interface GitStatusSnapshot {
  /** 是否为 Git 仓库（false 时前端隐藏区块） */
  is_repo: boolean;
  /** 当前分支（分离 HEAD 时为短提交号） */
  branch?: string | null;
  /** 上游分支名（可选） */
  upstream?: string | null;
  /** 领先上游的提交数（可选） */
  ahead?: number | null;
  /** 落后上游的提交数（可选） */
  behind?: number | null;
  /** 变更文件列表 */
  files?: GitFileStatus[];
}

/**
 * 文件预览载荷接口
 *
 * web_file_content 推送的内容快照（error 非空表示读取失败）。
 */
export interface FileContentPayload {
  /** 工作区内相对路径 */
  path: string;
  /** 视图类型：'content' 文件当前内容（默认）| 'diff' 相对 HEAD 的变更 */
  kind?: 'content' | 'diff';
  /** 文件内容（二进制为空串；diff 视图为 unified diff 文本） */
  content?: string;
  /** 是否为二进制文件 */
  binary?: boolean;
  /** 文件字节大小 */
  size?: number;
  /** 是否因超过上限截断 */
  truncated?: boolean;
  /** 读取失败信息 */
  error?: string;
}

// ---- 前端请求 ----

/**
 * 前端请求类型
 *
 * 前端发送到后端的所有可能请求类型。
 * web_* 类型为 Web 前端专属通道（A/B 通道），与 terminal 共用的
 * submit_line/apply_select_command 等类型隔离。
 */
export type FrontendRequest =
  | { type: 'submit_line'; line: string; treat_as_text?: boolean; session_id?: string }
  | { type: 'stop'; session_id?: string }
  | { type: 'permission_response'; request_id: string; allowed: boolean; session_allow?: boolean; tool_name?: string; session_id?: string }
  | { type: 'question_response'; request_id: string; answer: string; session_id?: string }
  | { type: 'list_sessions' }
  | { type: 'select_command'; command: string; session_id?: string }
  | { type: 'apply_select_command'; command: string; value: string; session_id?: string }
  | { type: 'shutdown' }
  // === Web 前端专属通道（web_* 命名空间）===
  | { type: 'web_new_session'; cwd?: string }
  | { type: 'web_restore_session'; session_id: string; cwd?: string }
  | { type: 'web_delete_sessions'; session_ids?: string[]; delete_all?: boolean; cwd?: string }
  | { type: 'web_set_setting'; setting_key: string; setting_value: string | number | boolean }
  | { type: 'web_request_sessions'; limit?: number; offset?: number }
  | { type: 'web_request_models' }
  | { type: 'web_request_resources'; session_id?: string; cwd?: string }
  | { type: 'web_request_file_tree'; path?: string; session_id?: string; cwd?: string }
  | { type: 'web_request_git_status'; session_id?: string; cwd?: string }
  | { type: 'web_read_file'; path: string; session_id?: string; cwd?: string }
  | { type: 'web_file_diff'; path: string; session_id?: string; cwd?: string }
  | { type: 'web_request_agent_tasks'; session_id?: string }
  | { type: 'web_request_session_files'; session_id?: string }
  | { type: 'web_read_session_file'; path: string; session_id?: string }
  | { type: 'web_query'; command: string; args?: string; request_id: string; session_id?: string }
  | { type: 'web_request_workspaces' }
  | { type: 'web_add_workspace'; path: string }
  | { type: 'web_remove_workspace'; path: string }
  // === agent 向导（terminal + web 共用）===
  | { type: 'agent_wizard_init' }
  | { type: 'agent_generate_request'; prompt: string; model: string; request_id: string; session_id?: string }
  | { type: 'agent_generate_cancel'; request_id: string }
  | { type: 'agent_wizard_submit'; fields: Record<string, unknown>; scope: 'user' | 'project' }
  // === Goal 状态栏操作（GoalBar 的 pause/resume/edit/clear）===
  | { type: 'goal_action'; goal_action: 'pause' | 'resume' | 'edit' | 'clear'; goal_id?: string; revision?: number; objective?: string; session_id?: string };

// ---- 后端事件 ----

/**
 * 后端事件接口
 *
 * 后端通过 WebSocket 发送到前端的所有可能事件类型。
 * 不同类型的事件包含不同的载荷字段。
 */
export interface BackendEvent {
  /** 事件类型标识符 */
  type: string;
  /** 事件消息（可选） */
  message?: string;
  /** 转录项（可选） */
  item?: TranscriptItem;
  /** 转录项列表（可选，用于批量更新） */
  items?: TranscriptItem[];
  /** 状态信息（可选） */
  state?: Record<string, unknown>;
  /** 任务列表快照（可选） */
  tasks?: TaskSnapshot[];
  /** MCP 服务器列表快照（可选） */
  mcp_servers?: McpServerSnapshot[];
  /** 可用命令列表（可选） */
  commands?: string[];
  /** 模态对话框配置（可选） */
  modal?: Record<string, unknown> | null;
  /** 选择选项列表（可选） */
  select_options?: SelectOption[];
  /** 工具名称（可选） */
  tool_name?: string;
  /** 工具输入参数（可选） */
  tool_input?: Record<string, unknown>;
  /** 工具调用唯一标识符（可选） */
  tool_use_id?: string;
  /** 输出内容（可选） */
  output?: string;
  /** 是否为错误（可选） */
  is_error?: boolean;
  /** 工具数量（可选） */
  tool_count?: number;
  /** tool_progress 事件的进度类型（可选，如 'stdout'/'status'/'custom'） */
  progress_type?: string;
  /** 助手的思考/推理过程（可选） */
  reasoning?: string;
  /** assistant_complete 携带：该助手回合后是否跟随工具链（true=中间步骤，false=最终答案） */
  tool_chain_follows?: boolean;
  /** 计划模式状态（可选） */
  plan_mode?: string;
  /** 待办事项列表快照（可选） */
  todo_items?: TodoItemSnapshot[];
  /** 待办事项 Markdown 文本（可选） */
  todo_markdown?: string;
  /** 群体协作者列表快照（可选） */
  swarm_teammates?: SwarmTeammateSnapshot[];
  /** 群体协作通知列表快照（可选） */
  swarm_notifications?: SwarmNotificationSnapshot[];
  /** 指令结果数据（可选） */
  command_result_data?: { message: string; type: 'success' | 'error' | 'info'; request_id?: string };
  // === rewind / 会话回退事件字段 ===
  /** session_rewind 携带的被回退 user 消息（回填输入框用） */
  restored_text?: string;

  // === web_* 推送事件字段 ===
  /** web_restore_started/completed 等会话级事件的归属会话 ID（可选，前端按此路由到会话视图） */
  session_id?: string;
  /** web_sessions 推送的会话列表（可选） */
  web_sessions?: WebSessionItem[];
  /** web_sessions 携带的活跃会话 ID（可选） */
  active_session_id?: string;
  /** web_workspaces 推送的工作区列表（可选，默认目录恒在首位） */
  web_workspaces?: WebWorkspaceItem[];
  /** web_resources 携带的所属工作区目录（可选） */
  cwd?: string;
  /** web_resources 推送的资源快照（可选） */
  web_resources?: {
    skills: SkillSnapshot[];
    agents: AgentSnapshot[];
    plugins: PluginSnapshot[];
    rules: RuleSnapshot[];
    mcp_servers: McpServerSnapshot[];
  };
  /** web_file_tree 推送的目录条目（可选；path 为请求的相对目录，空串为根） */
  web_file_tree?: { path: string; entries: FileTreeNode[]; truncated?: boolean };
  /** web_git_status 推送的 Git 状态快照（可选） */
  web_git_status?: GitStatusSnapshot;
  /** web_file_content 推送的文件预览载荷（可选） */
  web_file_content?: FileContentPayload;
  /** web_agent_tasks 推送的智能体与后台任务列表（可选） */
  web_agent_tasks?: AgentTaskItem[];
  /** web_session_files 推送的会话内修改文件列表（可选，会话文件区块数据源） */
  web_session_files?: SessionFileItem[];
  /** web_file_stats 推送的文件增删行数统计（可选，单轮变更条数据源） */
  web_file_stats?: FileStatItem[];
  /** web_models 推送的模型选项（可选） */
  web_models?: SelectOption[];
  /** web_setting_changed 的键名（可选） */
  setting_key?: string;
  /** web_setting_changed 的值（可选） */
  setting_value?: string | number | boolean;
  /** web_query_result 的结果类型（可选） */
  web_query_kind?: 'text' | 'transcript_replace' | 'download';
  /** web_query_result 的载荷（可选） */
  web_query_payload?: unknown;
  /** web_query_result 关联的请求 ID（可选） */
  web_request_id?: string;
  /** web_query_result 关联的命令名（可选） */
  web_command?: string;
  /** web_restore_completed 等事件的错误信息（非空表示操作失败）（可选） */
  web_error?: string;
  // === agent 向导响应专属字段 ===
  /** 关联的请求 ID（可选，agent_generate_response 携带） */
  request_id?: string;
  /** agent_generate_response 等的错误文本（可选，非空表示请求失败）（与布尔 is_error 区分） */
  error?: string | null;
  /** agent_wizard_init_response 推送的工具列表（可选） */
  tools?: { name: string; description: string }[];
  /** agent_wizard_init_response 推送的模型列表（可选，后端返回 name 字段） */
  models?: { name: string; label: string }[];
  /** agent_generate_response 返回的 LLM 生成草稿（可选） */
  agent?: { identifier: string; when_to_use: string; system_prompt: string };
  /** agent_wizard_result 的成功标志（可选） */
  success?: boolean;
  /** agent_wizard_result 的写入路径（可选，成功时返回） */
  path?: string;
  /** agent_wizard_result 的字段错误映射（可选，失败时返回字段级错误） */
  errors?: Record<string, string>;
  /** ready 事件携带的首次登录标识（可选，无 env_N 且无 working_directory 时为 true） */
  first_login?: boolean;
  /** agent_wizard_submit 关联的表单字段（可选，回声） */
  fields?: Record<string, unknown>;
  /** agent_wizard_submit 关联的作用域（可选，回声） */
  scope?: string;
  /** agent_generate_request 关联的提示词（可选，回声） */
  prompt?: string;
  /** update_available 事件携带的最新版本号（可选） */
  latest_version?: string;
  // === goal_action_result 专属字段 ===
  /** goal_action_result 回执的操作名（可选） */
  goal_action?: string;
  /** goal_action_result 失败时的 {code, message}（可选） */
  goal_error?: { code: string; message: string };
  /** goal_status 携带的结构化轮次生命周期（round/wrapup/limit/disarmed） */
  goal_status?: {
    kind: string;
    round?: number;
    max_rounds?: number;
    phase?: string;
    /** 后端按 ui_language 本地化好的 toast 文案（前端直接展示，不再自行本地化） */
    message?: string;
  };
}

/**
 * Goal 状态接口
 *
 * 后端 goal 域视图（goal/status_payload），随会话级状态推送。
 */
export interface GoalStatus {
  /** goal ID（goal-<uuid>，CAS 标识） */
  id: string;
  /** CAS revision */
  revision: number;
  /** 完成目标文本 */
  objective: string;
  /** 相位：active | paused | blocked | complete */
  phase: 'active' | 'paused' | 'blocked' | 'complete';
  /** 已准入的 goal 轮数 */
  roundsStarted: number;
  /** 轮次上限 */
  maxGoalRounds: number;
  /** 进程内激活状态：armed | disarmed */
  activation: 'armed' | 'disarmed';
  /** 受阻原因（仅 blocked 时存在） */
  blockedReason?: { code: string; message: string };
}

/**
 * Web 会话项接口
 *
 * web_sessions 推送事件中的单个会话条目。
 */
export interface WebSessionItem {
  /** 会话唯一标识符 */
  id: string;
  /** 会话显示标签 */
  label: string;
  /** 会话所属工作区目录（可选，侧边栏按目录分组的依据） */
  cwd?: string;
  /** 创建时间戳（可选） */
  created_at?: number;
  /** 消息数量（可选） */
  message_count?: number;
  /** 轮次数量（可选） */
  turn_count?: number;
  /** 会话摘要（可选） */
  summary?: string;
  /** 自定义会话名称（可选，存在时列表显示用 title） */
  title?: string;
  /** 会话是否正在运行任务（可选） */
  busy?: boolean;
  /** 会话阶段：idle/thinking/tool_executing/awaiting_input（可选） */
  phase?: string;
  /** 是否为活跃会话（可选） */
  active?: boolean;
  /** 后端是否持有该会话的内存运行时（可选，false 时前端点击需重新恢复） */
  in_memory?: boolean;
  /** 会话实时上下文占用 tokens（可选） */
  context_tokens?: number;
  /** 累积输入 tokens（可选，右栏用量展示） */
  input_tokens?: number;
  /** 累积输出 tokens（可选） */
  output_tokens?: number;
  /** 累积缓存读 tokens（可选） */
  cache_read_input_tokens?: number;
  /** 累积缓存创建 tokens（可选） */
  cache_creation_input_tokens?: number;
  /** 最后一次 API 调用的缓存读 tokens（可选，缓存命中率计算） */
  context_cache_read?: number;
  /** 最后一次 API 调用的缓存创建 tokens（可选） */
  context_cache_creation?: number;
  /** 最后一次 API 调用的未缓存输入 tokens（可选） */
  context_input?: number;
  /** 最后一次 API 调用的输出 tokens（可选） */
  context_output?: number;
  /** 会话 goal 视图（可选，null 表示无目标；GoalBar 数据源） */
  goal?: GoalStatus | null;
}

/**
 * Web 工作区（目录空间）项接口
 *
 * web_workspaces 推送事件中的单个工作区条目。
 */
export interface WebWorkspaceItem {
  /** 工作区规范化绝对路径 */
  path: string;
  /** 显示名（目录 basename） */
  name: string;
  /** 是否为默认工作区（settings.working_directory，不可移除） */
  is_default: boolean;
  /** 目录当前是否可用（存在且为文件夹） */
  available: boolean;
}

/**
 * 后端状态快照接口
 *
 * 对应后端 `_state_payload` 返回的字段（src/illusion/ui/protocol.py）。
 * 所有字段可选，因为前端可能收到部分更新或字段尚未到位。
 */
export interface StatusPayload {
  /** 当前模型名 */
  model?: string;
  /** 当前工作目录 */
  cwd?: string;
  /** 认证状态 */
  auth_status?: string;
  /** API base URL */
  base_url?: string;
  /** 权限模式 */
  permission_mode?: string;
  /** UI 语言 */
  ui_language?: string;
  /** 思考强度 */
  effort?: string;
  /** 已连接 MCP 服务器数 */
  mcp_connected?: number;
  /** 失败 MCP 服务器数 */
  mcp_failed?: number;
  /** 输出风格 */
  output_style?: string;
  /** 是否显示思考过程 */
  show_thinking?: boolean;
  /** 当前阶段 */
  phase?: string;
  /** 会话 ID */
  session_id?: string;
  /** 上下文窗口大小 */
  context_window?: number;
  /** 当前上下文已用 tokens（最后一次 API 真实值 + 新增消息估算） */
  context_tokens?: number;
  /** 最后一次 API 调用的缓存命中 tokens */
  context_cache_read?: number;
  /** 最后一次 API 调用的缓存写入 tokens */
  context_cache_creation?: number;
  /** 最后一次 API 调用的非缓存输入 tokens */
  context_input?: number;
  /** 最后一次 API 调用的输出 tokens */
  context_output?: number;
  /** 累积 API input tokens（非缓存） */
  input_tokens?: number;
  /** 累积 API output tokens */
  output_tokens?: number;
  /** 累积缓存命中 tokens */
  cache_read_input_tokens?: number;
  /** 累积缓存写入 tokens */
  cache_creation_input_tokens?: number;
  /** 最大 tokens */
  max_tokens?: number;
  /** 活动 agent 数 */
  agent_count?: number;
  /** 会话 goal 视图（可选，null 表示无目标；GoalBar 数据源） */
  goal?: GoalStatus | null;
}
