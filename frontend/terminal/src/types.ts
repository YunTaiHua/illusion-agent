/**
 * @fileoverview 类型定义模块
 *
 * 本模块定义了终端前端使用的所有 TypeScript 类型，包括：
 * - 前端配置类型
 * - 会话转录项类型
 * - 工具调用相关类型
 * - 后端事件类型
 * - 各种快照类型（任务、MCP 服务器、桥接会话等）
 *
 * @module types
 */

/**
 * 待处理的工具调用信息
 *
 * 表示一个已经开始但尚未收到参数的工具调用。
 */
export type PendingToolCall = {
	/** 工具名称 */
	tool_name: string;
	/** 工具调用的唯一标识符 */
	tool_use_id: string;
	/** 工具输入参数（可选，可能尚未到达） */
	tool_input?: Record<string, unknown>;
	/** 流式进度消息列表（message 为内容，type 为进度类型：thinking/text/tool/status） */
	progressMessages?: Array<{message: string; type?: string}>;
};

/**
 * 前端配置类型
 *
 * 从环境变量 ILLUSION_FRONTEND_CONFIG 中解析的配置对象。
 */
export type FrontendConfig = {
	/** 后端启动命令及其参数 */
	backend_command: string[];
	/** 初始提示词（可选），用于在会话开始时自动发送 */
	initial_prompt?: string | null;
};

/**
 * 会话转录项类型
 *
 * 表示对话中的一个消息项，可以是用户消息、助手回复、工具调用等。
 */
export type TranscriptItem = {
	/**
	 * 消息角色类型：
	 * - 'system': 系统消息
	 * - 'user': 用户消息
	 * - 'assistant': 助手回复
	 * - 'assistant_streaming': 助手流式回复（正在进行中）
	 * - 'tool': 工具调用
	 * - 'tool_result': 工具执行结果
	 * - 'log': 日志消息
	 * - 'plan': 计划内容
	 */
	role: 'system' | 'user' | 'assistant' | 'assistant_streaming' | 'tool' | 'tool_result' | 'log' | 'plan';
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
	/** 所属 assistant message ID，用于并行工具分组 */
	message_id?: string;
	/** 命令产物标记：命令选择器产生的转录（如 /context set 512000），非真实用户输入 */
	is_command?: boolean;
	/** 结构化输出数据（仅在 role 为 'tool_result' 时存在） */
	structured_output?: Record<string, unknown>;
	/** 输出类型：text/diff/search_results/file_list/error */
	output_type?: string;
	/** 工具特定元数据 */
	tool_metadata?: Record<string, unknown>;
};

/**
 * 任务快照类型
 *
 * 表示后台任务的当前状态快照。
 */
export type TaskSnapshot = {
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
};

/**
 * MCP 服务器快照类型
 *
 * 表示 MCP（Model Context Protocol）服务器的当前状态快照。
 */
export type McpServerSnapshot = {
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
};

/**
 * 选择选项载荷类型
 *
 * 用于选择模态对话框中的单个选项。
 */
export type SelectOptionPayload = {
	/** 选项值（提交到后端的值） */
	value: string;
	/** 选项显示标签 */
	label: string;
	/** 选项描述（可选） */
	description?: string;
	/** 是否为当前活跃选项（可选） */
	active?: boolean;
};

/**
 * 选择请求载荷类型
 *
 * 后端发送到前端的选择请求，用于显示选择模态对话框。
 */
export type SelectRequestPayload = {
	/** 对话框标题 */
	title: string;
	/** 关联的命令名称 */
	command: string;
	/** 可选项列表 */
	options: SelectOptionPayload[];
};

/**
 * 待办事项快照类型
 *
 * 表示单个待办事项的当前状态。
 */
export type TodoItemSnapshot = {
	/** 待办事项内容 */
	content: string;
	/** 待办事项状态：'pending'（待处理）、'in_progress'（进行中）、'completed'（已完成） */
	status: 'pending' | 'in_progress' | 'completed';
	/** 当前活动形式描述（如 "正在编写代码"） */
	activeForm: string;
};

/**
 * Goal 快照类型
 *
 * 后端 goal 域视图（state_snapshot.state.goal），GoalStatusLine 的数据源。
 */
export type GoalStatus = {
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
	blockedReason?: {code: string; message: string};
};

/**
 * 群体协作者快照类型
 *
 * 表示群体协作模式中的一个协作者（teammate）的当前状态。
 */
export type SwarmTeammateSnapshot = {
	/** 协作者名称 */
	name: string;
	/** 协作者状态：'running'（运行中）、'idle'（空闲）、'done'（已完成）、'error'（错误） */
	status: 'running' | 'idle' | 'done' | 'error';
	/** 运行时长（秒，可选） */
	duration?: number;
	/** 当前任务描述（可选） */
	task?: string;
};

/**
 * 群体协作通知快照类型
 *
 * 表示群体协作模式中的一个通知消息。
 */
export type SwarmNotificationSnapshot = {
	/** 发送者名称 */
	from: string;
	/** 通知消息内容 */
	message: string;
	/** 通知时间戳（Unix 时间戳，毫秒） */
	timestamp: number;
};

/**
 * 后端事件类型
 *
 * 后端通过 WebSocket 发送到前端的所有可能事件类型。
 * 不同类型的事件包含不同的载荷字段。
 */
export type BackendEvent = {
	/** 事件类型标识符 */
	type: string;
	/** 事件消息（可选） */
	message?: string | null;
	/** 转录项（可选） */
	item?: TranscriptItem | null;
	/** 状态信息（可选） */
	state?: Record<string, unknown> | null;
	/** 任务列表快照（可选） */
	tasks?: TaskSnapshot[] | null;
	/** MCP 服务器列表快照（可选） */
	mcp_servers?: McpServerSnapshot[] | null;
	/** 可用命令列表（可选） */
	commands?: string[] | null;
	/** 模态对话框配置（可选） */
	modal?: Record<string, unknown> | null;
	/** 选择选项列表（可选） */
	select_options?: SelectOptionPayload[] | null;
	/** 工具名称（可选） */
	tool_name?: string | null;
	/** 工具输入参数（可选） */
	tool_input?: Record<string, unknown> | null;
	/** 工具调用唯一标识符（可选） */
	tool_use_id?: string | null;
	/** 输出内容（可选） */
	output?: string | null;
	/** 是否为错误（可选） */
	is_error?: boolean | null;
	/** 结构化输出数据（可选，用于 tool_completed 事件） */
	structured_output?: Record<string, unknown> | null;
	/** 输出类型（可选） */
	output_type?: string | null;
	/** 工具特定元数据（可选） */
	tool_metadata?: Record<string, unknown> | null;
	/** 进度消息类型（可选，用于 tool_progress 事件） */
	progress_type?: string | null;
	/** 回退位置索引（可选，用于 session_rewind 事件） */
	rewind_to_index?: number | null;
	/** rewind 被回退的 user 消息（可选，前端回填输入框） */
	restored_text?: string;
	// 新事件载荷
	/** 待办事项列表快照（可选） */
	todo_items?: TodoItemSnapshot[] | null;
	/** 待办事项 Markdown 文本（可选） */
	todo_markdown?: string | null;
	/** 计划模式状态（可选） */
	plan_mode?: string | null;
	/** 群体协作者列表快照（可选） */
	swarm_teammates?: SwarmTeammateSnapshot[] | null;
	/** 群体协作通知列表快照（可选） */
	swarm_notifications?: SwarmNotificationSnapshot[] | null;
	/** 助手的思考/推理过程（可选） */
	reasoning?: string | null;
	/** assistant_complete 携带：该助手回合后是否跟随工具链（true=中间步骤，false=最终答案） */
	tool_chain_follows?: boolean | null;
	/** 指令结果数据（可选） */
	command_result_data?: {
		/** 结果消息 */
		message: string;
		/** 结果类型：'success'（成功）、'error'（错误）、'info'（信息） */
		type: 'success' | 'error' | 'info';
	} | null;
	// ---- goal 快捷键操作回执（goal_action_result 携带；成功与否见 success 字段） ----
	/** 回执的操作名（pause/resume/edit/clear） */
	goal_action?: string | null;
	/** 失败原因（code + message） */
	goal_error?: {code: string; message: string} | null;
	/** 转录项列表（可选，用于批量更新） */
	items?: TranscriptItem[] | null;
	// ---- agent 向导相关字段 ----
	/** 关联请求 ID（可选） */
	request_id?: string | null;
	/** 错误文本（可选，agent_generate_response / agent_wizard_result 携带，与布尔 is_error 区分） */
	error?: string | null;
	/** 工具列表（可选，agent_wizard_init_response 携带） */
	tools?: {name: string; description: string}[] | null;
	/** 模型列表（可选，agent_wizard_init_response 携带） */
	models?: {name: string; label: string}[] | null;
	/** 生成的 agent 信息（可选，agent_generate_response 携带） */
	agent?: {identifier: string; when_to_use: string; system_prompt: string} | null;
	/** 向导提交是否成功（可选，agent_wizard_result 携带） */
	success?: boolean | null;
	/** 写入文件路径（可选，agent_wizard_result 携带） */
	path?: string | null;
	/** 校验错误字典（可选，agent_wizard_result 携带，字段名→错误信息） */
	errors?: Record<string, string> | null;
	/** 最新可用版本号（可选，update_available 事件携带） */
	latest_version?: string | null;
};

/**
 * 后端状态载荷类型
 *
 * 表示后端 `_state_payload` 推送的状态快照，包含模型、权限、上下文窗口、
 * token 计量等字段。前端通过 `Record<string, unknown>` 宽松接收，
 * 此接口用于文档化与类型安全（token 计量相关字段为 Token 计量优化新增）。
 */
export type StatusPayload = {
	/** 当前模型名称 */
	model?: string;
	/** 权限模式 */
	permission_mode?: string;
	/** 上下文窗口大小（token 数） */
	context_window?: number;
	/** 当前预估已用上下文 token 数（最后一次 API 真实值 + 新增消息估算） */
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
	/** UI 语言 */
	ui_language?: string;
	/** 会话 ID */
	session_id?: string;
	/** MCP 已连接数 */
	mcp_connected?: number;
	/** 活动代理数 */
	agent_count?: number;
	/** 会话 goal 快照（GoalStatusLine 数据源；null 表示无目标） */
	goal?: GoalStatus | null;
	/** 其他未知字段索引签名 */
	[key: string]: unknown;
};
