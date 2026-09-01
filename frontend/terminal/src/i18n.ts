/**
 * @fileoverview 国际化（i18n）模块
 *
 * 本模块提供终端前端的多语言支持功能，目前支持：
 * - 简体中文（zh-CN）
 * - 英文（en）
 *
 * 所有用户可见的文本都应通过 t() 函数获取，以确保语言切换功能正常工作。
 *
 * @module i18n
 */

/**
 * 支持的 UI 语言类型
 * - 'zh-CN': 简体中文
 * - 'en': 英文
 */
export type UiLanguage = 'zh-CN' | 'en';

/**
 * 语言字典类型
 * 键为文本标识符，值为对应语言的翻译文本
 */
type Dict = Record<string, string>;

/**
 * 简体中文字典
 * 包含所有 UI 文本的中文翻译
 */
const ZH: Dict = {
	connecting: '正在抵达云端...',
	updateAvailable: '发现新版本 v{version}',
	exitProgram: '退出',
	stopCurrentTask: '停止',
	permissionMode: '权限模式',
	permDefaultDesc: '写入或执行前询问',
	permAutoDesc: '自动允许所有工具',
	permPlanDesc: '阻止所有写入操作',
	permYoloDesc: '完全绕过限制（危险）',
	language: '语言',
	langZh: '简体中文',
	langEn: 'English',
	newline: '换行',
	allow: '允许',
	sessionAllow: '本次会话允许',
	deny: '拒绝',
	sandbox: '沙箱',
	// 权限弹窗底部键位提示
	permNavHint: '导航',
	permSelectHint: '选择',
	permCancelHint: '取消',
	// 权限确认选项描述
	permAllowDesc: '批准此次工具执行',
	permSessionDesc: '仅本次会话内自动允许',
	permDenyDesc: '拒绝此次工具执行',
	// 当前活跃选项标记
	currentMark: '当前',
	// MCP 认证模态框
	mcpAuthTitle: 'MCP 认证',
	mcpAuthPrompt: '提供认证信息',
	// 问答模态框工具行
	toolLabel: '工具：',
	spinnerVerbs: '思考,分析,推理,生成,处理,计算,检索,整合,优化,验证,解析,构建',
	spinnerToolAction: '正在着手',
	// Goal 相位标签
	goalPhaseActive: '进行中的目标',
	goalPhasePaused: '已暂停的目标',
	goalPhaseBlocked: '受阻的目标',
	// Goal 快捷键提示（Ctrl+G 两段式；第二段 Ctrl 组合，裸字符会被 IME/输入框拦截）
	goalHotkeyLabel: '目标',
	goalKeyModeHint: 'ctrl+p 暂停 · ctrl+r 恢复 · ctrl+e 编辑 · ctrl+d 清除 · esc 取消',
	goalEditPrompt: '编辑目标',
	longTextHint: '幻想与实用，于此交融…',
	clearInput: '删行',
	lineStart: '行首',
	lineEnd: '行尾',
	taskStopped: '当前任务已停止。',
	stoppingTask: '正在停止任务…',
	reasoning: '思考过程',
	assistantReply: '助手回复',
	planReview: '计划审批',
	approve: '批准',
	reject: '拒绝',
	// ---- ask_user_question 问答模态框文案 ----
	questionOther: '其他',
	questionOtherPlaceholder: '请输入...',
	questionSelectOne: '选择一项',
	questionSelectAll: '选择所有适用项',
	questionSubmit: '提交',
	questionReviewTitle: '确认你的选择',
	questionNotAllAnswered: '还有问题未作答',
	questionReadyToSubmit: '准备好提交了吗？',
	questionNoAnswer: '（未作答）',
	// ---- 底部辅助行片段（每个片段单独 i18n，动态拼接）----
	questionHintSelect: '选择',
	questionHintNavigate: '上下导航',
	questionHintSwitchTab: 'Tab/方向键切换问题',
	questionHintToggle: 'Space 切换',
	questionHintSubmit: 'Enter 提交',
	questionHintCancel: 'Esc 取消',
	questionHintQuickSelect: '数字键快捷选择',
	// ---- max-tokens / 自定义输入模态框 ----
	maxTokensCustomPrompt: '输入自定义最大令牌数:',
	maxTokensInvalid: '请输入有效的正整数',
	contextWindowCustomPrompt: '输入自定义上下文窗口大小:',
	// ---- rename / 自定义输入模态框 ----
	renameInputPrompt: '输入新的会话名称:',
	inputValueEmpty: '输入不能为空',
	// ---- 后端退出兜底提示 ----
	backend_exit_hint: '后端启动失败。请运行 \'illusion auth login\' 配置 API 环境，或检查 settings.json 配置。',
	// ---- Ctrl+x 行上下文占比摘要（{used}/{window}/{pct} 为占位符）----
	contextUsageSummary: '上下文 {used} / {window} ({pct}%)',
	// ---- agent 创建向导（Task 12）----
	agentWizardScopeTitle: '选择写入范围',
	agentWizardScopeUser: '用户级（所有项目共享）',
	agentWizardScopeProject: '项目级（仅当前项目）',
	agentWizardMethodTitle: '选择创建方法',
	agentWizardMethodGenerate: 'LLM 生成（推荐）',
	agentWizardMethodManual: '手动配置',
	agentWizardDescribePrompt: '用自然语言描述你想要的 agent',
	agentWizardDescribePlaceholder: '例如：一个专门做代码审查的助手...',
	agentWizardGenerating: '正在生成 agent...',
	agentWizardSubmitting: '正在提交...',
	agentWizardGenerateFailed: '生成失败',
	agentWizardRetry: '重试',
	agentWizardBack: '返回',
	agentWizardGenerateConfirm: '确认生成结果（Enter 接受 / Esc 返回编辑）',
	agentWizardNamePrompt: '输入 agent 名称（identifier）',
	agentWizardNamePlaceholder: 'code-reviewer',
	agentWizardSystemPromptPrompt: '输入 system_prompt',
	agentWizardDescriptionPrompt: '输入 description（when_to_use）',
	agentWizardDescriptionPlaceholder: '在审查代码时使用',
	agentWizardModelTitle: '选择默认模型',
	agentWizardToolsTitle: '选择工具权限',
	agentWizardToolsDefault: '继承默认权限（所有允许的工具）',
	agentWizardToolsAllow: '仅允许指定工具',
	agentWizardToolsAllowPrompt: '输入允许的工具名（逗号分隔）',
	agentWizardToolsAllowPlaceholder: 'glob, grep, read_file, bash',
	agentWizardToolsInvalidNames: '以下工具名无效',
	agentWizardToolsInvalidSeparator: '分隔符错误，请用英文逗号分隔',
	agentWizardToolsEmptyInput: '未输入任何工具名',
	agentWizardToolsDefaultPreset: 'glob, grep, read_file, bash',
	agentWizardToolsExamples: '合法实例',
	agentWizardEffortTitle: '选择 effort',
	agentWizardPermissionTitle: '选择 permission_mode',
	agentWizardMaxTurnsPrompt: '输入 max_turns（留空跳过）',
	agentWizardMaxTurnsPlaceholder: '20',
	agentWizardMaxTurnsInvalid: '请输入有效的正整数',
	agentWizardConfirmTitle: '确认创建',
	agentWizardSkip: '跳过',
	agentWizardDone: '完成',
	agentWizardSuccess: '创建成功',
	agentWizardFailed: '创建失败',
	agentWizardPressAnyKey: '按任意键关闭',
	agentWizardScopeLabel: '范围',
	agentWizardNameLabel: '名称',
	agentWizardDescriptionLabel: '描述',
	agentWizardModelLabel: '模型',
	agentWizardToolsLabel: '工具',
	agentWizardEffortLabel: 'effort',
	agentWizardPermissionLabel: '权限',
	agentWizardMaxTurnsLabel: 'max_turns',
	agentWizardSystemPromptLabel: 'system_prompt',
	agentWizardReviewHint: '提交后将在上述路径创建文件，请打开文件审查完整内容',
	agentBranchTitle: 'Agent 操作',
	agentBranchView: '查看已完成的 agent',
	agentBranchCreate: '创建新 agent',
	agentBranchModel: '设置子智能体默认模型',
};

/**
 * 英文字典
 * 包含所有 UI 文本的英文翻译
 */
const EN: Dict = {
	connecting: 'Ascending to the cloud...',
	updateAvailable: 'New version v{version} available',
	exitProgram: 'exit',
	stopCurrentTask: 'stop',
	permissionMode: 'Permission Mode',
	permDefaultDesc: 'Ask before write/execute operations',
	permAutoDesc: 'Allow all tools automatically',
	permPlanDesc: 'Block all write operations',
	permYoloDesc: 'Bypass all restrictions (dangerous)',
	language: 'Language',
	langZh: '简体中文',
	langEn: 'English',
	newline: 'newline',
	allow: 'Allow',
	sessionAllow: 'Allow for session',
	deny: 'Deny',
	sandbox: 'Sandbox',
	// 权限弹窗底部键位提示
	permNavHint: 'navigate',
	permSelectHint: 'select',
	permCancelHint: 'cancel',
	// 权限确认选项描述
	permAllowDesc: 'Approve this tool execution',
	permSessionDesc: 'Allow this tool for the current session only',
	permDenyDesc: 'Reject this tool execution',
	// 当前活跃选项标记
	currentMark: 'current',
	// MCP 认证模态框
	mcpAuthTitle: 'MCP Authentication',
	mcpAuthPrompt: 'Provide auth details',
	// 问答模态框工具行
	toolLabel: 'Tool: ',
	spinnerVerbs: 'Thinking,Processing,Analyzing,Reasoning,Generating,Computing,Refining,Synthesizing,Optimizing,Validating,Parsing,Building',
	spinnerToolAction: 'Wielding tool',
	// Goal phase labels
	goalPhaseActive: 'Ongoing Goal',
	goalPhasePaused: 'Paused Goal',
	goalPhaseBlocked: 'Blocked Goal',
	// Goal hotkey hints (Ctrl+G two-step; second step uses Ctrl combos — bare keys are swallowed by IME/input)
	goalHotkeyLabel: 'goal',
	goalKeyModeHint: 'ctrl+p pause · ctrl+r resume · ctrl+e edit · ctrl+d clear · esc cancel',
	goalEditPrompt: 'Edit goal',
	longTextHint: 'Where Fantasy Meets Functionality...',
	clearInput: 'delete line',
	lineStart: 'start',
	lineEnd: 'end',
	taskStopped: 'Current task stopped.',
	stoppingTask: 'Stopping task…',
	reasoning: 'Thinking',
	assistantReply: 'Response',
	planReview: 'Plan Review',
	approve: 'Approve',
	reject: 'Reject',
	// ---- ask_user_question question modal strings ----
	questionOther: 'Other',
	questionOtherPlaceholder: 'Type something...',
	questionSelectOne: 'Select one',
	questionSelectAll: 'Select all that apply',
	questionSubmit: 'Submit',
	questionReviewTitle: 'Review your answers',
	questionNotAllAnswered: 'You have not answered all questions',
	questionReadyToSubmit: 'Ready to submit your answers?',
	questionNoAnswer: '(No answer)',
	// ---- bottom hint line fragments (each fragment is i18n'd, composed dynamically) ----
	questionHintSelect: 'select',
	questionHintNavigate: '↑/↓ to navigate',
	questionHintSwitchTab: 'Tab/Arrows to switch questions',
	questionHintToggle: 'Space to toggle',
	questionHintSubmit: 'Enter to submit',
	questionHintCancel: 'Esc to cancel',
	questionHintQuickSelect: '1-N quick select',
	// ---- max-tokens / custom input modal ----
	maxTokensCustomPrompt: 'Enter custom max tokens:',
	maxTokensInvalid: 'Please enter a valid positive integer',
	contextWindowCustomPrompt: 'Enter custom context window size:',
	// ---- rename / custom input modal ----
	renameInputPrompt: 'Enter a new session name:',
	inputValueEmpty: 'Input cannot be empty',
	// ---- backend exit fallback hint ----
	backend_exit_hint: 'Backend startup failed. Run \'illusion auth login\' to configure API environment, or check settings.json.',
	// ---- Ctrl+x line context usage summary ({used}/{window}/{pct} are placeholders) ----
	contextUsageSummary: '{used} / {window} ({pct}%)',
	// ---- agent creation wizard (Task 12) ----
	agentWizardScopeTitle: 'Select write scope',
	agentWizardScopeUser: 'User-level (shared across all projects)',
	agentWizardScopeProject: 'Project-level (current project only)',
	agentWizardMethodTitle: 'Select creation method',
	agentWizardMethodGenerate: 'Generate with LLM (recommended)',
	agentWizardMethodManual: 'Manual configuration',
	agentWizardDescribePrompt: 'Describe the agent you want in natural language',
	agentWizardDescribePlaceholder: 'e.g. an assistant specialized in code review...',
	agentWizardGenerating: 'Generating agent...',
	agentWizardSubmitting: 'Submitting...',
	agentWizardGenerateFailed: 'Generation failed',
	agentWizardRetry: 'Retry',
	agentWizardBack: 'Back',
	agentWizardGenerateConfirm: 'Confirm generated result (Enter to accept / Esc to edit)',
	agentWizardNamePrompt: 'Enter agent name (identifier)',
	agentWizardNamePlaceholder: 'code-reviewer',
	agentWizardSystemPromptPrompt: 'Enter system_prompt',
	agentWizardDescriptionPrompt: 'Enter description (when_to_use)',
	agentWizardDescriptionPlaceholder: 'Use when reviewing code',
	agentWizardModelTitle: 'Select default model',
	agentWizardToolsTitle: 'Select tool permissions',
	agentWizardToolsDefault: 'Inherit default (all allowed tools)',
	agentWizardToolsAllow: 'Allow specific tools only',
	agentWizardToolsAllowPrompt: 'Enter allowed tool names (comma-separated)',
	agentWizardToolsAllowPlaceholder: 'glob, grep, read_file, bash',
	agentWizardToolsInvalidNames: 'Invalid tool names',
	agentWizardToolsInvalidSeparator: 'Invalid separator, use English comma',
	agentWizardToolsEmptyInput: 'No tool names entered',
	agentWizardToolsDefaultPreset: 'glob, grep, read_file, bash',
	agentWizardToolsExamples: 'Valid examples',
	agentWizardEffortTitle: 'Select effort',
	agentWizardPermissionTitle: 'Select permission_mode',
	agentWizardMaxTurnsPrompt: 'Enter max_turns (empty to skip)',
	agentWizardMaxTurnsPlaceholder: '20',
	agentWizardMaxTurnsInvalid: 'Please enter a valid positive integer',
	agentWizardConfirmTitle: 'Confirm creation',
	agentWizardSkip: 'Skip',
	agentWizardDone: 'Done',
	agentWizardSuccess: 'Created successfully',
	agentWizardFailed: 'Creation failed',
	agentWizardPressAnyKey: 'Press any key to close',
	agentWizardScopeLabel: 'Scope',
	agentWizardNameLabel: 'Name',
	agentWizardDescriptionLabel: 'Description',
	agentWizardModelLabel: 'Model',
	agentWizardToolsLabel: 'Tools',
	agentWizardEffortLabel: 'effort',
	agentWizardPermissionLabel: 'permission',
	agentWizardMaxTurnsLabel: 'max_turns',
	agentWizardSystemPromptLabel: 'system_prompt',
	agentWizardReviewHint: 'After submitting, a file will be created at the path above for your review',
	agentBranchTitle: 'Agent Actions',
	agentBranchView: 'View completed agents',
	agentBranchCreate: 'Create a new agent',
	agentBranchModel: 'Set subagent default model',
};

/**
 * 所有语言字典的集合
 * 按语言代码索引对应的翻译字典
 */
const ALL: Record<UiLanguage, Dict> = {
	'zh-CN': ZH,
	en: EN,
};

/**
 * 标准化语言代码
 *
 * 将输入的语言值标准化为有效的 UiLanguage 类型。
 * 如果输入为 'en' 则返回 'en'，否则默认返回 'zh-CN'。
 *
 * @param raw - 原始语言值（可能为任意类型）
 * @returns 标准化后的语言代码
 */
export function normalizeLanguage(raw: unknown): UiLanguage {
	return raw === 'en' ? 'en' : 'zh-CN';
}

/**
 * 获取国际化文本
 *
 * 根据当前语言和文本键获取对应的翻译文本。
 * 如果指定语言中不存在该键，则回退到中文文本。
 *
 * @param lang - 当前 UI 语言
 * @param key - 文本标识符（必须是中文字典中已定义的键）
 * @returns 对应语言的翻译文本
 */
export function t(lang: UiLanguage, key: keyof typeof ZH): string {
	return ALL[lang][key] ?? ZH[key];
}
