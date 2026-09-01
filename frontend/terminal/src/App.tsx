/**
 * @fileoverview 应用程序主组件模块
 *
 * 本模块是 IllusionAgent 终端前端的核心入口，负责：
 * 1. 整体应用布局与组件组合
 * 2. 处理用户输入与键盘事件
 * 3. 管理各种模态对话框（权限确认、计划审批、选择列表等）
 * 4. 与后端会话通信
 * 5. 支持国际化语言切换
 *
 * @module App
 */

import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {Box, Text, useApp, useInput} from 'ink';

import {getActivityDescription} from './tools/registry.js';
import {AgentWizard} from './components/AgentWizard.js';
import {CommandPicker} from './components/CommandPicker.js';
import {ConversationView} from './components/ConversationView.js';
import {CustomInputModal} from './components/CustomInputModal.js';
import {MentionPicker} from './components/MentionPicker.js';
import {ModalHost, QuestionModal} from './components/ModalHost.js';
import {PromptInput} from './components/PromptInput.js';
import {SelectModal, type SelectOption} from './components/SelectModal.js';
import {Spinner} from './components/Spinner.js';
import {GoalStatusLine} from './components/GoalStatusLine.js';
import {GoalEditBox} from './components/GoalEditBox.js';
import type {GoalStatus} from './types.js';
import {StatusBar} from './components/StatusBar.js';
import {SwarmPanel} from './components/SwarmPanel.js';
import {TodoPanel} from './components/TodoPanel.js';
import {useBackendSession} from './hooks/useBackendSession.js';
import {normalizeLanguage, t, UiLanguage} from './i18n.js';
import {ThemeProvider, useTheme} from './theme/ThemeContext.js';
import type {FrontendConfig, FileMentionCandidate} from './types.js';
import {fmtTokens} from './utils/fmtTokens.js';
import {detectMentionToken, formatMentionInsertion} from './utils/mention.js';
import {VERSION} from './version.js';

/**
 * 是否使用原始回车提交模式
 * 当环境变量 ILLUSION_FRONTEND_RAW_RETURN 设置为 '1' 时启用
 * 启用后回车键会直接提交输入内容，而不是触发换行
 */
const rawReturnSubmit = process.env.ILLUSION_FRONTEND_RAW_RETURN === '1';

/**
 * 脚本化自动化步骤列表
 * 从环境变量 ILLUSION_FRONTEND_SCRIPT 中解析 JSON 数组
 * 用于自动化测试或演示场景，按顺序自动执行预设的命令
 */
const scriptedSteps = (() => {
	const raw = process.env.ILLUSION_FRONTEND_SCRIPT;
	if (!raw) {
		return [] as string[];
	}
	try {
		const parsed = JSON.parse(raw);
		return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : [];
	} catch {
		return [];
	}
})();

/** @ 提及补全防抖间隔（ms）：连续输入时减少补全请求（与 web 端一致） */
const MENTION_DEBOUNCE_MS = 120;

/**
 * 权限模式选项列表
 * 定义了三种权限模式：默认模式、自动模式、计划模式
 * 用于权限模式选择对话框
 */
const PERMISSION_MODES = (language: UiLanguage): SelectOption[] => [
	{value: 'default', label: 'Default', description: t(language, 'permDefaultDesc')},
	{value: 'full_auto', label: 'Auto', description: t(language, 'permAutoDesc')},
	{value: 'plan', label: 'Plan Mode', description: t(language, 'permPlanDesc')},
	{value: 'yolo', label: 'YOLO', description: t(language, 'permYoloDesc')},
];

/**
 * 选择模态对话框状态类型
 * @property title - 对话框标题
 * @property options - 可选项列表
 * @property onSelect - 选择回调函数
 */
type SelectModalState = {
	title: string;
	options: SelectOption[];
	onSelect: (value: string) => void;
} | null;

/**
 * 应用程序根组件
 *
 * 作为整个应用的入口点，包裹 ThemeProvider 以提供主题上下文。
 * 接收前端配置参数并传递给内部组件。
 *
 * @param props - 组件属性
 * @param props.config - 前端配置对象，包含后端连接等配置信息
 * @returns 返回包含主题提供者和内部应用组件的 JSX 元素
 */
export function App({config}: {config: FrontendConfig}): React.JSX.Element {
	return (
		<ThemeProvider>
			<AppInner config={config} />
		</ThemeProvider>
	);
}

/**
 * 应用程序内部核心组件
 *
 * 负责处理所有应用逻辑，包括：
 * - 用户输入处理与键盘事件监听
 * - 后端会话管理与通信
 * - 各种模态对话框的状态管理（权限确认、计划审批、选择列表等）
 * - 命令解析与执行
 * - 国际化语言支持
 *
 * @param props - 组件属性
 * @param props.config - 前端配置对象
 * @returns 返回完整的应用界面 JSX 元素
 */
function AppInner({config}: {config: FrontendConfig}): React.JSX.Element {
	const {exit} = useApp();
	const theme = useTheme();
	const [input, setInput] = useState('');
	const [modalInput, setModalInput] = useState('');
	const [scriptIndex, setScriptIndex] = useState(0);
	const [pickerIndex, setPickerIndex] = useState(0);
	const [selectModal, setSelectModal] = useState<SelectModalState>(null);
	const [selectIndex, setSelectIndex] = useState(0);
	const [customInputModal, setCustomInputModal] = useState<
		{prompt: string; command: string; prefixValue?: string; numeric?: boolean} | null
	>(null);
	const [cursorReset, setCursorReset] = useState(0);
	/** 停止请求已发送、等待后端确认（终止过程可能有 1-2s 延迟，期间提示符显示旋转动画） */
	const [stopping, setStopping] = useState(false);
	// === @ 提及补全（与 web 端对称：光标处 token 检测 + 防抖请求 + 过期响应丢弃） ===
	/** 光标位置（MultilineTextInput 上报，token 检测依据） */
	const [caret, setCaret] = useState(0);
	/** 补全菜单当前选中候选索引 */
	const [mentionIndex, setMentionIndex] = useState(0);
	/** Esc 关闭后不再弹出；token 变化时重新武装 */
	const [mentionDismissed, setMentionDismissed] = useState(false);
	/** 应用提及后的光标位置（cursorReset 重挂载时定位到插入点之后） */
	const [pendingCaret, setPendingCaret] = useState<number | null>(null);
	const mentionReqIdRef = useRef(0);
	/** 已发出的最新请求 id（ref 形式：仅作响应过期判别，不触发渲染，避免往返期间菜单闪没） */
	const latestMentionReqIdRef = useRef<string | null>(null);
	/** /agent create 触发的分步创建向导是否可见 */
	const [showAgentWizard, setShowAgentWizard] = useState(false);
	/** Ctrl+G 两段式第二段：等待 p/r/e/c 操作键 */
	const [goalKeyMode, setGoalKeyMode] = useState(false);
	/** goal 快捷键编辑弹窗（预填当前 objective） */
	const [goalEditModal, setGoalEditModal] = useState(false);
	const session = useBackendSession(config, () => exit(), (text) => setInput(text));
	const isPermissionModal = session.modal?.kind === 'permission';
	const language = normalizeLanguage(session.status.ui_language);
	// 当前 goal（Ctrl+G 热键与快捷键提示的显示依据；complete 后不可操作）
	const currentGoal = session.status.goal as GoalStatus | null | undefined;
	const hasGoal = !!currentGoal && currentGoal.phase !== 'complete';
	// 上下文窗口占比（用于 idle 提示行末尾展示，保留一位小数）
	const contextWindow = Number(session.status.context_window ?? 0);
	const contextTokens = Number(session.status.context_tokens ?? 0);
	const contextPct = contextWindow > 0 ? Math.min(100, Math.round(contextTokens * 1000 / contextWindow) / 10) : 0;
	// 高危操作（如 rm / git reset --hard）只提供两选项（允许一次 / 拒绝），不可会话级豁免
	const permissionHighRisk = isPermissionModal && session.modal?.high_risk === true;

	/**
	 * 当前正在执行的工具名称
	 *
	 * 用于在加载动画（Spinner）中显示当前正在执行的工具名称。
	 * 优先检查 pendingToolCalls（工具调用刚开始，参数尚未到达），
	 * 然后从静态消息列表中查找最近的工具调用。
	 *
	 * @returns 当前工具名称，如果没有正在执行的工具则返回 undefined
	 */
	const currentToolName = useMemo(() => {
		// 优先检查 pendingToolCalls（工具调用刚开始，参数尚未到达）
		if (session.pendingToolCalls.length > 0) {
			const last = session.pendingToolCalls[session.pendingToolCalls.length - 1];
			return getActivityDescription(last.tool_name, last.tool_input);
		}
		for (let i = session.staticItems.length - 1; i >= 0; i--) {
			const item = session.staticItems[i];
			if (item.role === 'tool') {
				return getActivityDescription(item.tool_name ?? 'tool', item.tool_input);
			}
			if (item.role === 'tool_result' || item.role === 'assistant') {
				break;
			}
		}
		return undefined;
	}, [session.staticItems, session.pendingToolCalls]);

	/**
	 * 命令提示列表
	 *
	 * 根据用户当前输入内容，过滤并排序匹配的命令列表。
	 * 当输入以 '/' 开头时，显示所有以该前缀开头的可用命令。
	 * 特别地，当输入仅为 '/' 时，将 '/language' 命令优先显示。
	 *
	 * @returns 匹配的命令字符串数组
	 */
	const commandHints = useMemo(() => {
		if (!input.startsWith('/')) {
			return [] as string[];
		}
		const value = input.trimEnd();
		if (value === '') {
			return [] as string[];
		}
		const matches = session.commands.filter((cmd) => cmd.startsWith(value));
		if (value === '/') {
			const preferred = ['/language'];
			const boosted = preferred.filter((cmd) => matches.includes(cmd));
			const rest = matches.filter((cmd) => !preferred.includes(cmd));
			return [...boosted, ...rest];
		}
		return matches;
	}, [session.commands, input]);

	const canShowPicker = input.startsWith('/') && commandHints.length > 0;
	const showPicker = canShowPicker && !session.busy && !session.modal && !selectModal;

	useEffect(() => {
		setPickerIndex(0);
	}, [canShowPicker, commandHints.length, input]);

	// === @ 提及补全（与 web 端 PromptInput 同构的检测/防抖/过期丢弃逻辑） ===
	/** 光标处提及 token；斜杠命令选择器、忙碌或模态框期间不触发 */
	const mentionToken = useMemo(
		() => (showPicker || session.busy || session.modal || selectModal
			? null
			: detectMentionToken(input, caret)),
		[input, caret, showPicker, session.busy, session.modal, selectModal],
	);
	const tokenKey = mentionToken ? `${mentionToken.start}:${mentionToken.query}` : null;

	// token 变化时重新武装菜单；变化后防抖拉取候选（request_id 供响应关联）
	useEffect(() => {
		setMentionDismissed(false);
	}, [tokenKey]);
	useEffect(() => {
		if (!tokenKey) return;
		const timer = setTimeout(() => {
			const rid = `m${++mentionReqIdRef.current}`;
			// 仅更新 ref（不触发渲染），请求在途期间保留旧候选继续显示，避免菜单闪没
			latestMentionReqIdRef.current = rid;
			session.sendRequest({
				type: 'web_request_file_mentions',
				query: tokenKey.slice(tokenKey.indexOf(':') + 1),
				request_id: rid,
			});
		}, MENTION_DEBOUNCE_MS);
		return () => clearTimeout(timer);
	}, [tokenKey, session.sendRequest]);
	useEffect(() => {
		if (!mentionToken) setMentionIndex(0);
	}, [mentionToken]);

	// 仅采纳最新请求的响应写入缓存；请求在途时保留旧缓存继续渲染，新响应到达后整批替换。
	// 过期响应（requestId 不符）天然丢弃；过滤统一按当前 tokenQuery 进行，缓存无需保留 query 字段
	const [mentionCache, setMentionCache] = useState<{
		requestId: string;
		candidates: FileMentionCandidate[];
	} | null>(null);
	// 过期响应天然丢弃：仅当响应 requestId 等于最新已发出请求 id 时才采纳写入缓存
	useEffect(() => {
		const r = session.fileMentions;
		if (r && latestMentionReqIdRef.current && r.requestId === latestMentionReqIdRef.current) {
			setMentionCache({ requestId: r.requestId, candidates: r.candidates });
		}
	}, [session.fileMentions]);

	// 候选基于缓存 + 当前 token 查询串实时过滤（新请求在途时旧候选按当前输入过滤，保持菜单稳定且响应式）
	const tokenQuery = mentionToken?.query ?? '';
	const mentionCandidates = useMemo(() => {
		if (!mentionCache) return [];
		const q = tokenQuery.toLowerCase();
		return q ? mentionCache.candidates.filter((c) => c.path.toLowerCase().includes(q)) : mentionCache.candidates;
	}, [mentionCache, tokenQuery]);

	const showMentionMenu =
		mentionToken !== null
		&& !mentionDismissed
		&& mentionCandidates.length > 0
		&& !showPicker
		&& !session.busy
		&& !session.modal
		&& !selectModal;

	// 候选列表变化时钳制选中索引（响应缩窄列表时避免索引越界产生死高亮）
	useEffect(() => {
		setMentionIndex((i) => (mentionCandidates.length === 0 ? 0 : Math.min(i, mentionCandidates.length - 1)));
	}, [mentionCandidates.length]);

	/** 应用选中的提及候选：替换 @token 为插入文本并定位光标到插入点之后 */
	const applyMention = useCallback((candidatePath: string, kind: 'dir' | 'file' | 'skill') => {
		if (!mentionToken) return;
		const insertion = formatMentionInsertion({path: candidatePath, kind});
		const nextCaret = mentionToken.start + insertion.length;
		setInput((prev) => prev.slice(0, mentionToken.start) + insertion + prev.slice(mentionToken.end));
		// 重挂载 MultilineTextInput 并把光标定位到插入点之后（与 web 端 setSelectionRange 对称）
		setPendingCaret(nextCaret);
		setCursorReset((c) => c + 1);
	}, [mentionToken]);

	// 消费 pendingCaret：重挂载（cursorReset 变化）已完成初始化后清理，
	// 避免残留值污染未来其他路径的 cursorReset 递增（正确光标位置）
	useEffect(() => {
		if (pendingCaret === null) return;
		const raf = requestAnimationFrame(() => setPendingCaret(null));
		return () => cancelAnimationFrame(raf);
	}, [cursorReset, pendingCaret]);

	/**
	 * 动态设置终端窗口/tab 标题为当前会话显示名称
	 *
	 * 后端在 app_state 中暴露 session_name（CLI --name 或 /rename 写入的自定义名称）。
	 * 名称为空（--clear / /new / resume 到无标题会话）时回退默认标题 "IllusionAgent"，
	 * 避免残留上一次的自定义标题。通过 ANSI 转义序列 ESC ] 0 ; title BEL 写入，仅在 TTY 生效。
	 */
	const lastTitleRef = useRef<string | null>(null);
	useEffect(() => {
		if (!process.stdout.isTTY) {
			return;
		}
		const name = String(session.status.session_name ?? '').trim();
		const title = name || 'IllusionAgent';
		if (lastTitleRef.current !== title) {
			lastTitleRef.current = title;
			process.stdout.write(`\x1B]0;${title}\x07`);
		}
	}, [session.status.session_name]);

	/**
	 * 处理后端发起的选择请求
	 *
	 * 当后端需要用户从列表中选择时（例如 /resume 恢复会话列表），
	 * 会发送 selectRequest，此效果函数将其转换为前端选择模态对话框。
	 */
	useEffect(() => {
		if (!session.selectRequest) {
			return;
		}
		const req = session.selectRequest;
		if (req.options.length === 0) {
			session.setSelectRequest(null);
			return;
		}
		setSelectIndex(0);
		setSelectModal({
			title: req.title,
			options: req.options.map((o) => ({value: o.value, label: o.label, description: o.description})),
			onSelect: (value) => {
				// max-tokens / context-window custom 分支：弹出数字输入 modal
				if (
					(req.command === 'max-tokens' && value === 'custom') ||
					(req.command === 'context-window' && value === '__custom__')
				) {
					setCustomInputModal({
						prompt:
							req.command === 'max-tokens'
								? t(language, 'maxTokensCustomPrompt')
								: t(language, 'contextWindowCustomPrompt'),
						command: req.command,
					});
					setSelectModal(null);
					session.setSelectRequest(null);
					return;
				}
				// rename / 重命名会话：选中目标会话后弹出自由文本输入框输入新名称
				if (req.command === 'rename_session') {
					setCustomInputModal({
						prompt: t(language, 'renameInputPrompt'),
						command: 'rename',
						prefixValue: value,
						numeric: false,
					});
					setSelectModal(null);
					session.setSelectRequest(null);
					return;
				}
				session.sendRequest({type: 'apply_select_command', command: req.command, value});
				session.setBusy(true);
				setSelectModal(null);
			},
		});
		session.setSelectRequest(null);
	}, [session.selectRequest]);

	// 后端确认终止（busy→false）后清除 stopping 状态，提示符恢复输入态
	const prevBusyForStopRef = useRef(session.busy);
	useEffect(() => {
		if (prevBusyForStopRef.current && !session.busy) {
			setStopping(false);
		}
		prevBusyForStopRef.current = session.busy;
	}, [session.busy]);

	/**
	 * 拦截需要交互式界面的特殊命令
	 *
	 * 检查用户输入是否为需要特殊处理的命令（如权限设置、语言切换等），
	 * 这些命令不会直接发送到后端，而是在前端显示相应的选择界面。
	 *
	 * @param cmd - 用户输入的命令字符串
	 * @returns 如果命令被拦截处理则返回 true，否则返回 false
	 */
	const handleCommand = (cmd: string): boolean => {
		const trimmed = cmd.trim();

		// /permissions → 显示权限模式选择器
		if (trimmed === '/permissions' || trimmed === '/permissions show') {
			const currentMode = String(session.status.permission_mode ?? 'default');
			const options = PERMISSION_MODES(language).map((opt) => ({
				...opt,
				active: opt.value === currentMode,
			}));
			const initialIndex = options.findIndex((o) => o.active);
			setSelectIndex(initialIndex >= 0 ? initialIndex : 0);
			setSelectModal({
				title: t(language, 'permissionMode'),
				options,
				onSelect: (value) => {
					session.sendRequest({type: 'submit_line', line: `/permissions set ${value}`});
					session.setBusy(true);
					setSelectModal(null);
				},
			});
			return true;
		}

		if (trimmed === '/language' || trimmed === '/language show') {
			const current = normalizeLanguage(session.status.ui_language);
			const options: SelectOption[] = [
				{value: 'set zh-CN', label: t(current, 'langZh'), description: '中文界面', active: current === 'zh-CN'},
				{value: 'set en', label: t(current, 'langEn'), description: 'English UI', active: current === 'en'},
			];
			const initialIndex = options.findIndex((o) => o.active);
			setSelectIndex(initialIndex >= 0 ? initialIndex : 0);
			setSelectModal({
				title: t(current, 'language'),
				options,
				onSelect: (value) => {
					session.sendRequest({type: 'submit_line', line: `/language ${value}`});
					session.setBusy(true);
					setSelectModal(null);
				},
			});
			return true;
		}

		// /plan → 切换计划模式
		if (trimmed === '/plan') {
			const currentMode = String(session.status.permission_mode ?? 'default');
			if (currentMode === 'plan') {
				session.sendRequest({type: 'submit_line', line: '/plan off'});
			} else {
				session.sendRequest({type: 'submit_line', line: '/plan on'});
			}
			session.setBusy(true);
			return true;
		}

		// /resume → 从后端请求会话列表（将触发 select_request）
		if (trimmed === '/resume') {
			session.sendRequest({type: 'list_sessions'});
			return true;
		}

		// /model → 显示模型选择下拉菜单
		if (trimmed === '/model' || trimmed === '/model show') {
			session.sendRequest({type: 'select_command', command: 'model'});
			return true;
		}

		// /rewind → 显示消息选择器以选择回溯点
		if (trimmed === '/rewind') {
			session.sendRequest({type: 'select_command', command: 'rewind'});
			return true;
		}

		// /delete → 显示会话选择器以删除会话
		if (trimmed === '/delete') {
			session.sendRequest({type: 'select_command', command: 'delete'});
			return true;
		}

		// /rules → 显示规则选择器
		if (trimmed === '/rules') {
			session.sendRequest({type: 'select_command', command: 'rules'});
			return true;
		}

		// /skills → 显示技能选择器
		if (trimmed === '/skills') {
			session.sendRequest({type: 'select_command', command: 'skills'});
			return true;
		}

		// /agent 无参数 → 前端渲染三分支选择
		// "查看已完成的 agent" 走后端 select_command('agent') 现有管道；
		// "创建新 agent" 直接打开 AgentWizard；
		// "设置 agent 默认模型" 走后端 agent_model 两步选择（内置固化 settings.json）。
		if (trimmed === '/agent') {
			setSelectIndex(0);
			setSelectModal({
				title: t(language, 'agentBranchTitle'),
				options: [
					{value: '__view__', label: t(language, 'agentBranchView'), description: ''},
					{value: '__create__', label: t(language, 'agentBranchCreate'), description: ''},
					{value: '__model__', label: t(language, 'agentBranchModel'), description: ''},
				],
				onSelect: (value) => {
					setSelectModal(null);
					if (value === '__view__') {
						session.sendRequest({type: 'select_command', command: 'agent'});
					} else if (value === '__model__') {
						session.sendRequest({type: 'select_command', command: 'agent_model'});
					} else {
						session.clearAgentWizardState();
						session.sendAgentWizardInit();
						setShowAgentWizard(true);
					}
				},
			});
			return true;
		}

		// /agent create 或 /agent new → 打开分步创建向导
		// 先清空残留向导状态，再请求初始化工具/模型列表
		if (trimmed === '/agent create' || trimmed === '/agent new') {
			session.clearAgentWizardState();
			session.sendAgentWizardInit();
			setShowAgentWizard(true);
			return true;
		}

		// /agent model（无参数）或 /agent model <name>（缺目标模型）→ 后端两步选择
		// /agent model <name> <ref|inherit>（完整形式）走命令注册表
		if (trimmed === '/agent model' || /^\/agent model \S+$/.test(trimmed)) {
			session.sendRequest({type: 'select_command', command: 'agent_model'});
			return true;
		}

		// /effort 无参数时 → 弹出选择框
		if (trimmed === '/effort') {
			session.sendRequest({type: 'select_command', command: 'effort'});
			return true;
		}

		// /max-tokens 无参数时 → 弹出选择框
		if (trimmed === '/max-tokens') {
			session.sendRequest({type: 'select_command', command: 'max-tokens'});
			return true;
		}

		// /memory 无参数时 → 弹出记忆功能开关选择框
		if (trimmed === '/memory') {
			session.sendRequest({type: 'select_command', command: 'memory'});
			return true;
		}

		// /rename 无参数时 → 弹出重命名会话 / 自动标题选择框
		if (trimmed === '/rename') {
			session.sendRequest({type: 'select_command', command: 'rename'});
			return true;
		}

		// /context → 显示上下文管理选择器
		if (trimmed === '/context') {
			session.sendRequest({type: 'select_command', command: 'context'});
			return true;
		}

		// /new → 清空对话窗口并开始新会话
		if (trimmed === '/new' || trimmed === '/clear') {
			session.sendRequest({type: 'submit_line', line: '/new'});
			session.setBusy(true);
			return true;
		}

		// /version → 显示版本信息（前端处理，不发送到后端）
		if (trimmed === '/version') {
			session.setCommandResult({
				text: `IllusionAgent ${VERSION}`,
				type: 'info',
			});
			return true;
		}

		return false;
	};

	/**
	 * 键盘输入事件处理函数
	 *
	 * 处理所有键盘快捷键和交互逻辑，包括：
	 * - Ctrl+C: 退出程序
	 * - Ctrl+X: 停止当前任务
	 * - Ctrl+O: 将指令结果显示到对话中
	 * - ESC: 清除指令结果
	 * - 选择模态对话框的导航和选择
	 * - 权限确认对话框的交互
	 * - 计划审批对话框的交互
	 * - 命令选择器的导航和选择
	 *
	 * @param chunk - 输入的字符内容
	 * @param key - 按键信息对象，包含修饰键状态和特殊键标识
	 */
	useInput((chunk, key) => {
		// Ctrl+C → 退出程序
		if (key.ctrl && chunk === 'c') {
			session.sendRequest({type: 'shutdown'});
			exit();
			return;
		}
		// Ctrl+X → 停止当前任务
		// 空闲但后台任务（agent / bash / powershell）在跑时也可停止
		if (key.ctrl && chunk.toLowerCase() === 'x') {
			// status 为显示状态：running → in_progress
			const hasActiveTasks = session.tasks.some(
				(t) => t.status === 'in_progress' || t.status === 'pending'
			);
			if (session.busy || hasActiveTasks) {
				session.sendRequest({type: 'stop'});
				if (session.busy) {
					session.pushStatic({role: 'system', text: ' '});
					// 停止请求已发出：显示旋转动画直至后端确认（busy→false）
					setStopping(true);
				}
				session.setCommandResult({
					text: t(language, 'taskStopped'),
					type: 'info',
				});
			}
			return;
		}
		// --- AgentWizard 激活时，按键交由向导内部 useInput 处理 ---
		// 此 guard 确保箭头键/Esc/回车等不被 App 重复消费
		if (showAgentWizard) {
			return;
		}
		// --- Ctrl+G → goal 操作模式（两段式第一段；有 goal 且无任何模态时进入） ---
		if (key.ctrl && chunk.toLowerCase() === 'g') {
			if (hasGoal && !session.modal && !selectModal && !customInputModal && !goalEditModal) {
				setGoalKeyMode(true);
			}
			return;
		}
		// --- goal 操作模式（两段式第二段）：Ctrl+P/R/E/D 执行（裸字符不可靠：
		// 中文 IME 拦截且会串入主输入框草稿，Ctrl 组合直达 raw mode），Esc 退出 ---
		if (goalKeyMode) {
			if (key.escape) {
				setGoalKeyMode(false);
				return;
			}
			if (!key.ctrl) {
				return;
			}
			const k = chunk.toLowerCase();
			if (k === 'p') {
				setGoalKeyMode(false);
				sendGoalAction('pause');
			} else if (k === 'r') {
				setGoalKeyMode(false);
				sendGoalAction('resume');
			} else if (k === 'd') {
				setGoalKeyMode(false);
				sendGoalAction('clear');
			} else if (k === 'e') {
				setGoalKeyMode(false);
				setGoalEditModal(true);
			}
			return;
		}
		// Ctrl+O → 将完整结果内容显示在对话中（不发送到 AI）
		if (key.ctrl && chunk.toLowerCase() === 'o' && session.commandResult) {
			session.pushStatic({role: 'system', text: session.commandResult.text});
			session.setCommandResult(null);
			return;
		}

		// ESC → 清除指令结果
		if (key.escape && session.commandResult) {
			session.setCommandResult(null);
			return;
		}

		// --- 自定义输入模态框激活时，字符输入交由其内部 TextInput ---
		if (customInputModal) {
			return;
		}

		// --- 选择模态对话框（权限选择器等） ---
		if (selectModal) {
			if (key.upArrow) {
				setSelectIndex((i) => Math.max(0, i - 1));
				return;
			}
			if (key.downArrow) {
				setSelectIndex((i) => Math.min(selectModal.options.length - 1, i + 1));
				return;
			}
			if (key.return) {
				const selected = selectModal.options[selectIndex];
				if (selected) {
					selectModal.onSelect(selected.value);
				}
				return;
			}
			if (key.escape) {
				setSelectModal(null);
				session.setBusy(false);
				return;
			}
			// 数字键快速选择
			const num = parseInt(chunk, 10);
			if (num >= 1 && num <= selectModal.options.length) {
				const selected = selectModal.options[num - 1];
				if (selected) {
					selectModal.onSelect(selected.value);
				}
				return;
			}
			return;
		}

		// --- 脚本化原始回车提交 ---
		if (rawReturnSubmit && key.return) {
			if (session.modal?.kind === 'question') {
				session.sendRequest({
					type: 'question_response',
					request_id: session.modal.request_id,
					answer: modalInput,
				});
				session.setModal(null);
				setModalInput('');
				return;
			}
			if (!session.modal && !session.busy && input.trim()) {
				onSubmit(input);
				return;
			}
		}

		// --- 权限确认模态对话框（复用问题卡片 QuestionModal 渲染与键盘交互，见渲染区） ---
		if (isPermissionModal) {
			return;
		}

		// --- 问题模态对话框（在忙碌时也会出现） ---
		if (session.modal?.kind === 'question') {
			return;
		}

		// --- 忙碌时忽略输入 ---
		if (session.busy) {
			return;
		}

		// --- @ 提及补全选择器（优先于命令选择器；输入框导航键已被 suppressNavigation 抑制） ---
		if (showMentionMenu) {
			if (key.upArrow) {
				setMentionIndex((i) => Math.max(0, i - 1));
				return;
			}
			if (key.downArrow) {
				setMentionIndex((i) => Math.min(mentionCandidates.length - 1, i + 1));
				return;
			}
			if (key.return || key.tab) {
				const picked = mentionCandidates[mentionIndex];
				if (picked) applyMention(picked.path, picked.kind);
				return;
			}
			if (key.escape) {
				setMentionDismissed(true);
				return;
			}
		}

		// --- 命令选择器 ---
		if (showPicker) {
			if (key.upArrow) {
				setPickerIndex((i) => Math.max(0, i - 1));
				return;
			}
			if (key.downArrow) {
				setPickerIndex((i) => Math.min(commandHints.length - 1, i + 1));
				return;
			}
			if (key.return) {
				const selected = commandHints[pickerIndex];
				if (selected) {
					setInput('');
					setCursorReset((c) => c + 1);
					if (!handleCommand(selected)) {
						onSubmit(selected);
					}
				}
				return;
			}
			if (key.tab) {
				const selected = commandHints[pickerIndex];
				if (selected) {
					setInput(selected + ' ');
					setCursorReset((c) => c + 1);
				}
				return;
			}
			if (key.escape) {
				setInput('');
				setCursorReset((c) => c + 1);
				return;
			}
		}

		// 注意：普通的回车提交由 PromptInput 中的 TextInput 的 onSubmit 处理。
		// 不要在这里重复处理 — 否则会导致重复请求。
	});

	/**
	 * 表单提交处理函数
	 *
	 * 处理用户输入的提交逻辑：
	 * 1. 如果当前有问答模态框，将输入作为回答发送到后端
	 * 2. 如果输入为空、会话忙碌或未就绪，则忽略提交
	 * 3. 如果是交互式命令，交给 handleCommand 处理
	 * 4. 否则将输入作为普通消息发送到后端
	 *
	 * @param value - 用户输入的字符串值
	 */
	// goal 快捷键操作（Ctrl+G 两段式）：CAS ref 从当前会话 goal 状态读取，
	// 经 stdin 即时分发绕过 busy 串行，goal 自动续跑期间也能立即生效
	const sendGoalAction = (action: 'pause' | 'resume' | 'edit' | 'clear', objective?: string): void => {
		const goal = session.status.goal as GoalStatus | null | undefined;
		if (!goal || goal.phase === 'complete') {
			return;
		}
		session.sendRequest({
			type: 'goal_action',
			goal_action: action,
			goal_id: goal.id,
			revision: goal.revision,
			objective,
		});
	};

	const onSubmit = (value: string): void => {
		// 权限确认（复用问题卡片提交）：解析选项答案 → permission_response
		if (session.modal?.kind === 'permission') {
			const answer = value.trim();
			// 优先解析前导序号（"1. 允许" → index 0），再按标签匹配兜底
			let allowed = false;
			let sessionAllow = false;
			const indexMatch = answer.match(/^(\d+)\.\s/);
			if (indexMatch) {
				const idx = parseInt(indexMatch[1]!, 10) - 1;
				const highRisk = session.modal?.high_risk === true;
				if (idx === 0) {
					allowed = true;
				} else if (!highRisk && idx === 1) {
					allowed = true;
					sessionAllow = true;
				}
				// idx = 2（普通）/ 1（高危）→ 拒绝，allowed 保持 false
			} else {
				// 标签匹配兜底（仅当序号解析失败时）
				const sessionLabels = [t(language, 'sessionAllow'), 'Allow for session'];
				const allowLabels = [t(language, 'allow'), 'Allow'];
				if (sessionLabels.some((l) => answer.includes(l))) {
					allowed = true;
					sessionAllow = true;
				} else if (allowLabels.some((l) => answer.includes(l))) {
					allowed = true;
				}
			}
			session.sendRequest({
				type: 'permission_response',
				request_id: session.modal.request_id,
				allowed,
				session_allow: sessionAllow,
				tool_name: String(session.modal?.tool_name ?? ''),
			});
			session.setModal(null);
			setModalInput('');
			return;
		}
		if (session.modal?.kind === 'question') {
			if (typeof session.modal.request_id !== 'string') return;
			session.sendRequest({
				type: 'question_response',
				request_id: session.modal.request_id,
				answer: value,
			});
			session.setModal(null);
			setModalInput('');
			return;
		}
		const trimmed = value.trim();
		if (!trimmed || session.busy || !session.ready) {
			return;
		}
		// 检查是否为交互式命令
		if (handleCommand(trimmed)) {
			setInput('');
			return;
		}
		session.sendRequest({type: 'submit_line', line: trimmed});
		setInput('');
		session.setBusy(true);
	};

	// 指令结果自动消失：3 秒后清除
	useEffect(() => {
		if (!session.commandResult) {
			return;
		}
		const timer = setTimeout(() => {
			session.setCommandResult(null);
		}, 3000);
		return () => clearTimeout(timer);
	}, [session.commandResult]);

	/**
	 * 脚本化自动化执行效果
	 *
	 * 按顺序自动执行 scriptedSteps 中的命令步骤。
	 * 当会话忙碌、存在模态对话框或选择对话框时暂停执行。
	 * 每个步骤之间间隔 200 毫秒。
	 */
	useEffect(() => {
		if (scriptIndex >= scriptedSteps.length) {
			return;
		}
		if (session.busy || session.modal || selectModal || showAgentWizard) {
			return;
		}
		const step = scriptedSteps[scriptIndex];
		const timer = setTimeout(() => {
			onSubmit(step);
			setScriptIndex((index) => index + 1);
		}, 200);
		return () => clearTimeout(timer);
	}, [scriptIndex, session.busy, session.modal, selectModal]);

	return (
		<Box flexDirection="column" height="100%">
			{/* 对话区域 */}
			<Box flexDirection="column" flexGrow={1}>
				<ConversationView
					staticItems={session.staticItems}
					clearCount={session.clearCount}
					assistantBuffer={session.assistantBuffer}
					showWelcome={session.ready}
					showThinking={session.showThinking}
					language={language}
					pendingToolCalls={session.pendingToolCalls}
					commandPickerOpen={showPicker}
				/>
			</Box>

			<Box flexDirection="column" paddingX={1}>
			{/* 权限确认模态框（复用问题卡片样式：沙箱 header + 选项列表；高危无"当前会话允许"） */}
			{isPermissionModal ? (
				<QuestionModal
					modal={{
						kind: 'question',
						request_id: session.modal?.request_id,
						questions: [{
							question: `${t(language, 'allow')} ${String(session.modal?.tool_name ?? 'tool')}?`,
							header: t(language, 'sandbox'),
							options: [
								{label: t(language, 'allow'), description: t(language, 'permAllowDesc')},
								...(permissionHighRisk ? [] : [{label: t(language, 'sessionAllow'), description: t(language, 'permSessionDesc')}]),
								{label: t(language, 'deny'), description: t(language, 'permDenyDesc')},
							],
							multiSelect: false,
							noCustomInput: true,
						}],
					}}
					modalInput={modalInput}
					setModalInput={setModalInput}
					onSubmit={onSubmit}
					language={language}
				/>
			) : null}

			{/* 后端模态框（问答、MCP 认证） */}
			{session.modal && !isPermissionModal ? (
				<ModalHost
					modal={session.modal}
					modalInput={modalInput}
					setModalInput={setModalInput}
					onSubmit={onSubmit}
					language={language}
				/>
			) : null}

			{/* 前端选择模态框（权限选择器等） */}
		{selectModal ? (
			<SelectModal
				title={selectModal.title}
				options={selectModal.options}
				selectedIndex={selectIndex}
				language={language}
			/>
		) : null}

		{/* 自定义数字输入模态框 */}
		{customInputModal ? (
			<CustomInputModal
				prompt={customInputModal.prompt}
				language={language}
				numeric={customInputModal.numeric}
				placeholder={customInputModal.numeric ? undefined : ''}
				onSubmit={(value) => {
					const combined = customInputModal.prefixValue ? `${customInputModal.prefixValue} ${value}` : value;
					session.sendRequest({type: 'apply_select_command', command: customInputModal.command, value: combined});
					session.setBusy(true);
					setCustomInputModal(null);
				}}
				onCancel={() => setCustomInputModal(null)}
			/>
		) : null}

		{/* goal 操作模式提示行（Ctrl+G 两段式第二段；风格同底部快捷键提示） */}
		{goalKeyMode ? (
			<Box marginTop={1}>
				<Text dimColor>{t(language, 'goalKeyModeHint')}</Text>
			</Box>
		) : null}

		{/* 命令选择器 */}
			{showPicker ? (
				<CommandPicker hints={commandHints} selectedIndex={pickerIndex} totalCommands={session.commands.length} />
			) : null}

			{/* @ 提及补全选择器（技能在前、文件在后；按键由 App 层统一拦截） */}
			{!showPicker && showMentionMenu ? (
				<MentionPicker candidates={mentionCandidates} selectedIndex={mentionIndex} />
			) : null}

			{/* 指令结果显示 */}
			{session.commandResult ? (
				<CommandPicker
					mode="result"
					result={session.commandResult.text}
					resultType={session.commandResult.type}
				/>
			) : null}

			{/* 待办面板 — 模态框/命令选择器期间通过 externallyHidden 隐藏而非卸载，避免 hidden 状态丢失 */}
			{session.ready && session.todoItems.length > 0 ? (
				<TodoPanel items={session.todoItems} externallyHidden={!!session.modal || !!selectModal || showPicker} />
			) : null}

			{/* 群体协作面板 */}
			{session.ready && (session.swarmTeammates.length > 0 || session.swarmNotifications.length > 0) ? (
				<SwarmPanel teammates={session.swarmTeammates} notifications={session.swarmNotifications} />
			) : null}

			{/* 状态栏 — 模态框期间隐藏状态栏腾出空间 */}
			{session.ready && !(session.modal && !isPermissionModal) ? (
				<StatusBar status={session.status} tasks={session.tasks} pendingToolCalls={session.pendingToolCalls} busy={session.busy} />
			) : null}

			{/* 输入区域 — 后端就绪前显示加载指示器（后端退出后隐藏） */}
			{!session.ready ? (
				!session.exited ? (
					<Box>
						<Box width={2}>
							<Text color={theme.colors.illusion}>{theme.icons.system}</Text>
						</Box>
						<Text color={theme.colors.illusion}>{t(language, 'connecting')}</Text>
					</Box>
				) : null
			) : showAgentWizard ? (
			<AgentWizard
				language={language}
				tools={session.agentWizardTools}
				models={session.agentWizardModels}
				generated={session.agentGenerated}
				generateLoading={session.agentGenerateLoading}
				generateError={session.agentGenerateError}
				result={session.agentWizardResult}
				onInit={session.sendAgentWizardInit}
				onGenerate={session.sendAgentGenerateRequest}
				onSubmit={session.sendAgentWizardSubmit}
			onClearResult={session.clearAgentWizardResult}
			onCancel={() => {
					setShowAgentWizard(false);
				}}
			/>
		) : session.modal || selectModal || customInputModal ? null : goalEditModal && currentGoal ? (
		// goal 编辑框（Ctrl+G → e）：占据 busy 区替换 Shimmer/Goal 状态行，
		// busy（goal 自动续跑）中也可编辑
		<GoalEditBox
			initialValue={currentGoal.objective}
			language={language}
			onSubmit={(value) => {
				setGoalEditModal(false);
				sendGoalAction('edit', value);
			}}
			onCancel={() => setGoalEditModal(false)}
		/>
	) : session.busy ? (
		<Box marginTop={1}>
			{(() => {
				// Goal 存在且未完成时，以 Goal 状态行替代底部 Shimmer（Spinner）：
				// 相位标签 + 目标 + round 计数；complete/无目标回退原 Spinner
				if (currentGoal && currentGoal.phase !== 'complete') {
					return (
						<GoalStatusLine
							goal={currentGoal}
							language={language}
							sessionId={String(session.status.session_id ?? '')}
						/>
					);
				}
					return (
						<Spinner
							label={session.bgAgentLabel ?? undefined}
							todoItems={session.todoItems}
							language={language}
							toolName={currentToolName}
							sessionId={String(session.status.session_id ?? '')}
						/>
					);
				})()}
			</Box>
		) : (
		<PromptInput
			busy={session.busy}
			stopping={stopping}
			input={input}
			setInput={setInput}
			onSubmit={onSubmit}
			toolName={session.busy ? currentToolName : undefined}
			suppressSubmit={showPicker || goalKeyMode || showMentionMenu}
			inputFocus={!goalKeyMode}
			cursorReset={cursorReset}
			language={language}
			todoItems={session.todoItems}
			onCursorChange={setCaret}
			suppressNavigation={showMentionMenu}
			initialCursorOffset={pendingCaret ?? undefined}
		/>
	)}

			{/* 键盘快捷键提示（仅在后端就绪后显示）；goal 编辑期间替换为编辑操作提示 */}
			{session.ready && goalEditModal ? (
				<Box marginTop={1}>
					<Text dimColor>
						{t(language, 'questionHintCancel')}
						<Text> {theme.icons.middleDot} </Text>
						{t(language, 'questionHintSubmit')}
					</Text>
				</Box>
			) : session.ready && !session.modal && !session.busy && !selectModal && !showAgentWizard ? (
				<Box>
					<Text dimColor>
						<Text color={theme.colors.muted}>ctrl+a</Text> {t(language, 'lineStart')}
						<Text> {theme.icons.middleDot} </Text>
						<Text color={theme.colors.muted}>ctrl+e</Text> {t(language, 'lineEnd')}
						<Text> {theme.icons.middleDot} </Text>
						<Text color={theme.colors.muted}>ctrl+u</Text> {t(language, 'clearInput')}
						<Text> {theme.icons.middleDot} </Text>
						<Text color={theme.colors.muted}>ctrl+j</Text> {t(language, 'newline')}
						<Text> {theme.icons.middleDot} </Text>
						<Text color={theme.colors.muted}>ctrl+c</Text> {t(language, 'exitProgram')}
						<Text> {theme.icons.middleDot} </Text>
						<Text color={theme.colors.muted}>ctrl+x</Text> {t(language, 'stopCurrentTask')}
					{hasGoal ? (
						<>
							<Text> {theme.icons.middleDot} </Text>
							<Text color={theme.colors.muted}>ctrl+g</Text> {t(language, 'goalHotkeyLabel')}
						</>
					) : null}
					{contextWindow > 0 ? (
						<>
							<Text> {theme.icons.middleDot} </Text>
							{t(language, 'contextUsageSummary')
								.replace('{used}', fmtTokens(contextTokens))
								.replace('{window}', fmtTokens(contextWindow))
								.replace('{pct}', contextPct.toFixed(1))}
						</>
					) : null}
				</Text>
			</Box>
		) : session.ready && session.busy && !session.modal && !selectModal && !showAgentWizard ? (
			<Box marginTop={1}>
				<Text dimColor>
					<Text color={theme.colors.muted}>ctrl+c</Text> {t(language, 'exitProgram')}
					<Text> {theme.icons.middleDot} </Text>
					<Text color={theme.colors.muted}>ctrl+x</Text> {t(language, 'stopCurrentTask')}
					{hasGoal ? (
						<>
							<Text> {theme.icons.middleDot} </Text>
							<Text color={theme.colors.muted}>ctrl+g</Text> {t(language, 'goalHotkeyLabel')}
						</>
					) : null}
				</Text>
			</Box>
		) : null}

		</Box>
	</Box>
	);
}
