/**
 * @fileoverview Agent 分步创建向导组件
 *
 * 在 /agent create 时触发，串联 SelectModal / MultilineTextInput / 单行输入，
 * 引导用户逐步填写 agent 配置并提交到后端。
 *
 * 步骤：
 * 1. scope（user / project）— SelectModal 单选
 * 2. method（generate / manual）— SelectModal 单选
 * 3a. describe（generate 路径）→ onGenerate → spinner → generated 填充
 * 3b. name + system_prompt（manual 路径）
 * 4. 确认生成结果（generate 路径）
 * 5. description（when_to_use）
 * 6. 默认 model — SelectModal 单选（含 inherit）
 * 7. tools — SelectModal 单选（默认权限 / 仅允许指定工具）
 * 8. effort — SelectModal 单选（含跳过）
 * 9. permission_mode — SelectModal 单选（含跳过）
 * 10. max_turns — 单行数字输入（可跳过）
 * 11. confirm → onSubmit
 *
 * 自管 useInput：SelectModal 步骤处理上下导航/Enter/Esc；
 * 文本步骤仅处理 Esc（取消），其余按键交由 TextInput/MultilineTextInput 内部处理。
 *
 * @module AgentWizard
 */

import React, {useEffect, useMemo, useRef, useState} from 'react';
import {Box, Text, useInput} from 'ink';
import TextInput from 'ink-text-input';

import MultilineTextInput from './MultilineTextInput.js';
import {SelectModal, type SelectOption} from './SelectModal.js';
import {Spinner} from './Spinner.js';
import {useTerminalSize} from '../hooks/useTerminalSize.js';
import {t, UiLanguage} from '../i18n.js';
import {useTheme} from '../theme/ThemeContext.js';
import {wrapToDisplayWidth} from '../utils/markdown.js';

/** 工具项类型 */
type ToolOption = {name: string; description: string};
/** 模型项类型 */
type ModelOption = {name: string; label: string};
/** LLM 生成的 agent 草稿类型 */
type GeneratedAgent = {identifier: string; when_to_use: string; system_prompt: string};
/** 提交结果类型 */
type WizardResult = {success: boolean; path?: string; errors?: Record<string, string>; error?: string};

/** 向导步骤标识 */
type Step =
	| 'scope'
	| 'method'
	| 'describe'
	| 'generating'
	| 'generateFailed'
	| 'generateConfirm'
	| 'name'
	| 'systemPrompt'
	| 'description'
	| 'model'
	| 'tools'
	| 'toolsAllow'
	| 'effort'
	| 'permission'
	| 'maxTurns'
	| 'confirm'
	| 'submitting'
	| 'done'
	| 'failed';

/** 累积的 agent 字段 */
type Fields = {
	scope: 'user' | 'project';
	method: 'generate' | 'manual';
	identifier: string;
	when_to_use: string;
	system_prompt: string;
	model: string;
	/** 工具白名单：null=继承默认权限（所有允许的工具），数组=仅允许指定工具 */
	tools: string[] | null;
	effort?: string;
	permission_mode?: string;
	max_turns?: number;
};

/** 组件属性 */
interface AgentWizardProps {
	/** 当前 UI 语言 */
	language: UiLanguage;
	/** 可选工具列表（来自 agent_wizard_init_response） */
	tools: ToolOption[] | null;
	/** 可选模型列表（来自 agent_wizard_init_response） */
	models: ModelOption[] | null;
	/** LLM 生成的草稿（来自 agent_generate_response） */
	generated: GeneratedAgent | null;
	/** 是否正在生成 */
	generateLoading: boolean;
	/** 生成错误文本 */
	generateError: string | null;
	/** 提交结果（来自 agent_wizard_result） */
	result: WizardResult | null;
	/** 请求初始化（拉取工具/模型列表） */
	onInit: () => void;
	/** 请求 LLM 生成草稿 */
	onGenerate: (prompt: string, model: string) => void;
	/** 提交表单 */
	onSubmit: (fields: Record<string, unknown>, scope: 'user' | 'project') => void;
	/** 清空提交结果（提交前调用，确保旧 result 不干扰新一轮 useEffect 检测） */
	onClearResult: () => void;
	/** 取消/关闭向导 */
	onCancel: () => void;
}

/** effort 选项值列表 */
const EFFORT_VALUES = ['low', 'medium', 'high', 'xhigh', 'max'];
/** permission_mode 选项值列表 */
const PERMISSION_VALUES = ['default', 'plan', 'full_auto'];
/** 可翻页文本视图最大显示行数 */
const MAX_VIEW_LINES = 10;

/**
 * Agent 分步创建向导组件
 *
 * @param props - 组件属性
 * @returns 返回向导的 JSX 元素
 */
export function AgentWizard(props: AgentWizardProps): React.JSX.Element {
	const {
		language, tools, models, generated, generateLoading, generateError, result,
		onInit, onGenerate, onSubmit, onClearResult, onCancel,
	} = props;
	const theme = useTheme();
	const {columns} = useTerminalSize();

	const [step, setStep] = useState<Step>('scope');
	const [fields, setFields] = useState<Fields>({
		scope: 'project',
		method: 'generate',
		identifier: '',
		when_to_use: '',
		system_prompt: '',
		model: 'inherit',
		tools: null,
	});
	const [selectedIndex, setSelectedIndex] = useState(0);
	const [singleValue, setSingleValue] = useState('');
	const [singleError, setSingleError] = useState<string | null>(null);
	const [multilineValue, setMultilineValue] = useState('');
	// 可翻页文本视图的滚动偏移（用于 generateConfirm/generateFailed/confirm 步骤）
	const [viewScroll, setViewScroll] = useState(0);

	// 防止已处理过的 generated/error 在重新进入步骤时被重复消费
	const lastHandledGeneratedRef = useRef<GeneratedAgent | null>(null);
	const lastHandledErrorRef = useRef<string | null>(null);

	// 挂载时请求初始化工具/模型列表
	useEffect(() => {
		onInit();
	}, [onInit]);

	// 收到生成草稿：从 generating/generateFailed 推进到 generateConfirm
	// 注意：ref 赋值必须在 step 检查之后，否则竞态条件下 result 到达时
	// step 还不是 submitting，ref 被标记为已处理，后续 effect 直接跳过，
	// 导致永久卡在 submitting/generating 状态
	useEffect(() => {
		if (!generated) return;
		if (generated === lastHandledGeneratedRef.current) return;
		if (step !== 'generating' && step !== 'generateFailed') return;
		lastHandledGeneratedRef.current = generated;
		setFields((f) => ({
			...f,
			identifier: generated.identifier,
			when_to_use: generated.when_to_use,
			system_prompt: generated.system_prompt,
		}));
		setStep('generateConfirm');
	}, [generated, step]);

	// 收到生成错误：从 generating 推进到 generateFailed
	useEffect(() => {
		if (!generateError) return;
		if (generateError === lastHandledErrorRef.current) return;
		if (step !== 'generating') return;
		lastHandledErrorRef.current = generateError;
		setStep('generateFailed');
	}, [generateError, step]);

	// 收到提交结果：result 到达且 step=submitting 时转换状态
	// submitForm 已在提交前调用 onClearResult 清空旧 result，
	// 因此 result 从 null → 非空必然是新一轮提交的响应，无需 ref 守卫
	useEffect(() => {
		if (!result) return;
		if (step !== 'submitting') return;
		if (result.success) {
			setStep('done');
		} else {
			setStep('failed');
		}
	}, [result, step]);

	// ====== SelectModal 选项构建 ======

	const scopeOptions = useMemo<SelectOption[]>(() => [
		{value: 'project', label: t(language, 'agentWizardScopeProject')},
		{value: 'user', label: t(language, 'agentWizardScopeUser')},
	], [language]);

	const methodOptions = useMemo<SelectOption[]>(() => [
		{value: 'generate', label: t(language, 'agentWizardMethodGenerate')},
		{value: 'manual', label: t(language, 'agentWizardMethodManual')},
	], [language]);

	const modelOptions = useMemo<SelectOption[]>(() => {
		return (models ?? []).map((m) => ({
			value: m.name,
			label: m.label,
			description: m.name,
		}));
	}, [models]);

	const toolsOptions = useMemo<SelectOption[]>(() => [
		{value: 'default', label: t(language, 'agentWizardToolsDefault')},
		{value: 'allow', label: t(language, 'agentWizardToolsAllow')},
	], [language]);

	const effortOptions = useMemo<SelectOption[]>(() => {
		const opts = EFFORT_VALUES.map((v) => ({value: v, label: v}));
		opts.push({value: '__skip__', label: t(language, 'agentWizardSkip')});
		return opts;
	}, [language]);

	const permissionOptions = useMemo<SelectOption[]>(() => {
		const opts = PERMISSION_VALUES.map((v) => ({value: v, label: v}));
		opts.push({value: '__skip__', label: t(language, 'agentWizardSkip')});
		return opts;
	}, [language]);

	/** 当前 select 步骤对应的选项列表 */
	const currentSelectOptions = (): SelectOption[] => {
		switch (step) {
			case 'scope': return scopeOptions;
			case 'method': return methodOptions;
			case 'model': return modelOptions;
			case 'tools': return toolsOptions;
			case 'effort': return effortOptions;
			case 'permission': return permissionOptions;
			default: return [];
		}
	};

	// ====== 提交辅助 ======

	/** 提交描述（generate 路径），触发后端 LLM 生成 */
	const submitDescribe = (v: string): void => {
		const s = v.trim();
		if (!s) return;
		onGenerate(s, 'inherit');
		setStep('generating');
	};

	/** 提交 name（manual 路径） */
	const submitName = (v: string): void => {
		const s = v.trim();
		if (!s) return;
		setFields((f) => ({...f, identifier: s}));
		setMultilineValue('');
		setStep('systemPrompt');
	};

	/** 提交 system_prompt（manual 路径） */
	const submitSystemPrompt = (v: string): void => {
		const s = v.trim();
		if (!s) return;
		setFields((f) => ({...f, system_prompt: s}));
		setSingleValue('');
		setStep('description');
	};

	/** 提交 description（when_to_use） */
	const submitDescription = (v: string): void => {
		const s = v.trim();
		if (!s) return;
		setFields((f) => ({...f, when_to_use: s}));
		setSelectedIndex(0);
		setStep('model');
	};

	/** 提交 max_turns（留空跳过） */
	const submitMaxTurns = (v: string): void => {
		const s = v.trim();
		if (s === '') {
			setStep('confirm');
			setViewScroll(0);
			return;
		}
		if (!/^\d+$/.test(s) || parseInt(s, 10) <= 0) {
			setSingleError(t(language, 'agentWizardMaxTurnsInvalid'));
			return;
		}
		setFields((f) => ({...f, max_turns: parseInt(s, 10)}));
		setStep('confirm');
		setViewScroll(0);
	};

	/** 常见工具名别名 → 标准工具名映射
	 *  处理缩写、同义词等无法通过大小写/分隔符归一化覆盖的情况。
	 *  例如：Read → read_file（缩写与全称不一致） */
	const TOOL_ALIASES: Record<string, string> = {
		'read': 'read_file',
		'file_read': 'read_file',
		'fileread': 'read_file',
		'readfile': 'read_file',
		'write': 'write_file',
		'file_write': 'write_file',
		'filewrite': 'write_file',
	};

	/** 将用户输入的工具名归一化到标准工具名。
	 *  兼容蛇形(snake_case)、驼峰(camelCase)、大写、小写等变体。
	 *  例如：file_read / FileRead / fileread / Read → read_file
	 */
	const normalizeToolName = (raw: string, validNames: Set<string>): string | null => {
		const lower = raw.toLowerCase();
		const compact = lower.replace(/[-_]/g, '');
		// 0. 别名映射（缩写/同义词 → 标准名）
		const aliasTarget = TOOL_ALIASES[lower] ?? TOOL_ALIASES[compact];
		if (aliasTarget && validNames.has(aliasTarget)) return aliasTarget;
		// 1. 直接匹配（大小写不敏感）
		for (const name of validNames) {
			if (name.toLowerCase() === lower) return name;
		}
		// 2. 去除下划线/连字符后匹配（snake_case / kebab-case → 无分隔符）
		for (const name of validNames) {
			if (name.toLowerCase().replace(/[-_]/g, '') === compact) return name;
		}
		return null;
	};

	/** 生成合法工具名示例（包含不同兼容名变体，用于错误提示） */
	const generateToolExamples = (validNames: Set<string>): string => {
		const names = [...validNames];
		const examples: string[] = [];
		// 从可用工具中选取最多 5 个，展示不同命名风格的兼容写法
		for (const name of names.slice(0, 5)) {
			if (name.includes('_')) {
				// snake_case 工具：展示标准名 + 驼峰 + 首字母大写
				const camel = name.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
				const pascal = camel.charAt(0).toUpperCase() + camel.slice(1);
				examples.push(`${name} / ${pascal} / ${camel}`);
			} else {
				// 无分隔符工具：展示标准名 + 首字母大写 + 全大写
				examples.push(`${name} / ${name.charAt(0).toUpperCase() + name.slice(1)} / ${name.toUpperCase()}`);
			}
		}
		return examples.join(', ');
	};

	/** 提交允许的工具名列表（白名单，逗号分隔）
	 *  - 留空视为使用默认预设（Glob, Grep, Read, Bash）
	 *  - 工具名兼容蛇形/驼峰/大写/小写，归一化到标准名
	 *  - 校验：分隔符必须为英文逗号；工具名必须存在
	 */
	const submitToolsAllow = (v: string): void => {
		const s = v.trim();
		// 留空使用默认预设
		if (s === '') {
			const preset = t(language, 'agentWizardToolsDefaultPreset').split(',').map((n) => n.trim()).filter(Boolean);
			setFields((f) => ({...f, tools: preset}));
			setSelectedIndex(0);
			setStep('effort');
			return;
		}
		// 校验分隔符：禁止中文逗号、分号、空格单独分隔
		if (/[,，;；]/.test(s) && /[，；]/.test(s)) {
			setSingleError(t(language, 'agentWizardToolsInvalidSeparator'));
			return;
		}
		// 按英文逗号分割
		const rawNames = s.split(',').map((n) => n.trim()).filter(Boolean);
		if (rawNames.length === 0) {
			setSingleError(t(language, 'agentWizardToolsEmptyInput'));
			return;
		}
		// 归一化并校验每个工具名
		const validNames = new Set((tools ?? []).map((tool) => tool.name));
		const normalized: string[] = [];
		const invalid: string[] = [];
		for (const raw of rawNames) {
			const norm = normalizeToolName(raw, validNames);
			if (norm) {
				if (!normalized.includes(norm)) normalized.push(norm);
			} else {
				invalid.push(raw);
			}
		}
		if (invalid.length > 0) {
			const examples = generateToolExamples(validNames);
			setSingleError(`${t(language, 'agentWizardToolsInvalidNames')}: ${invalid.join(', ')}\n${t(language, 'agentWizardToolsExamples')}: ${examples}`);
			return;
		}
		setFields((f) => ({...f, tools: normalized}));
		setSelectedIndex(0);
		setStep('effort');
	};

	/** 提交完整表单 */
	const submitForm = (): void => {
		// 后端 validate_agent_definition / write_agent_definition 期望字段名为
		// name / description（与 AgentDefinition frontmatter 一致）；
		// 向导内部沿用 identifier / when_to_use 是为了与 agent_generate_response
		// 返回字段保持一致，便于直接填充。提交时映射到后端期望的字段名。
		// 工具权限：tools=null 表示继承默认权限（所有允许的工具），
		// tools 为数组表示仅允许这些工具（白名单模式）
		const payload: Record<string, unknown> = {
			name: fields.identifier,
			description: fields.when_to_use,
			system_prompt: fields.system_prompt,
			model: fields.model || 'inherit',
		};
		if (fields.tools !== null) {
			payload.tools = fields.tools;
		}
		if (fields.effort) payload.effort = fields.effort;
		if (fields.permission_mode) payload.permission_mode = fields.permission_mode;
		if (fields.max_turns != null) payload.max_turns = fields.max_turns;
		// 提交前清空旧 result，确保后端返回的新 result 能被 useEffect 正确检测
		// （旧 result 若残留，ref 守卫会认为"已处理"而跳过新结果）
		onClearResult();
		onSubmit(payload, fields.scope);
		setStep('submitting');
	};

	/** SelectModal 选项被选中时的处理 */
	const handleSelect = (value: string): void => {
		switch (step) {
			case 'scope':
				setFields((f) => ({...f, scope: value as 'user' | 'project'}));
				setSelectedIndex(0);
				setStep('method');
				break;
			case 'method': {
				const m = value as 'generate' | 'manual';
				setFields((f) => ({...f, method: m}));
				setSelectedIndex(0);
				if (m === 'generate') {
					setMultilineValue('');
					setStep('describe');
				} else {
					setSingleValue('');
					setSingleError(null);
					setStep('name');
				}
				break;
			}
			case 'model':
				setFields((f) => ({...f, model: value}));
				setSelectedIndex(0);
				setStep('tools');
				break;
			case 'tools':
			if (value === 'allow') {
				// 进入仅允许模式，预填默认预设值
				setSingleValue(t(language, 'agentWizardToolsDefaultPreset'));
				setSingleError(null);
				setStep('toolsAllow');
			} else {
				// default：继承默认权限（所有允许的工具），tools 置空
				setFields((f) => ({...f, tools: null}));
				setSelectedIndex(0);
				setStep('effort');
			}
			break;
			case 'effort':
				if (value !== '__skip__') {
					setFields((f) => ({...f, effort: value}));
				}
				setSelectedIndex(0);
				setStep('permission');
				break;
			case 'permission':
				if (value !== '__skip__') {
					setFields((f) => ({...f, permission_mode: value}));
				}
				setSelectedIndex(0);
				setSingleValue('');
				setSingleError(null);
				setStep('maxTurns');
				break;
			default:
				break;
		}
	};

	// ====== 键盘输入处理 ======
	useInput((chunk, key) => {
		// done 状态：任意键关闭
		if (step === 'done') {
			onCancel();
			return;
		}
		// generating / submitting：等待后端响应，忽略输入（生成同步进行，不可中途取消 — 简化）
		if (step === 'generating' || step === 'submitting') {
			return;
		}
		// toolsAllow 步骤：Esc 返回工具权限选择，其余交给 TextInput
		if (step === 'toolsAllow') {
			if (key.escape) {
				setSelectedIndex(1); // 选中"仅允许指定工具"
				setStep('tools');
			}
			return;
		}
		// 文本输入步骤：仅处理 Esc（取消），其余交给 TextInput/MultilineTextInput
		// 注意：generate 路径的 description 步骤是预览模式，不在此列
		const isDescriptionPreview = step === 'description' && fields.method === 'generate';
		if (!isDescriptionPreview && (step === 'describe' || step === 'name' || step === 'systemPrompt'
			|| step === 'description' || step === 'maxTurns')) {
			if (key.escape) {
				onCancel();
			}
			return;
		}
		// generate 路径的 description 预览：Enter 接受，Esc 返回 generateConfirm，上下键翻页
		if (isDescriptionPreview) {
			if (key.return) {
				setViewScroll(0);
				setSelectedIndex(0);
				setStep('model');
			} else if (key.escape) {
				setViewScroll(0);
				setStep('generateConfirm');
			} else if (key.upArrow) {
				setViewScroll((s) => Math.max(0, s - 1));
				return;
			} else if (key.downArrow) {
				setViewScroll((s) => s + 1);
				return;
			}
			return;
		}
		// 生成失败：Enter/R 重试，Esc/B 返回方法选择，上下键翻页
		if (step === 'generateFailed') {
			if (key.return || chunk === 'r' || chunk === 'R') {
				setMultilineValue('');
				setViewScroll(0);
				setStep('describe');
			} else if (key.escape || chunk === 'b' || chunk === 'B') {
				setSelectedIndex(0);
				setViewScroll(0);
				setStep('method');
			} else if (key.upArrow) {
				setViewScroll((s) => Math.max(0, s - 1));
				return;
			} else if (key.downArrow) {
				setViewScroll((s) => s + 1);
				return;
			}
			return;
		}
		// 生成结果确认：Enter 接受，Esc 返回 describe 编辑，上下键翻页
		if (step === 'generateConfirm') {
			if (key.return) {
				setSingleValue(fields.when_to_use);
				setSingleError(null);
				setViewScroll(0);
				setStep('description');
			} else if (key.escape) {
				setViewScroll(0);
				setStep('describe');
			} else if (key.upArrow) {
				setViewScroll((s) => Math.max(0, s - 1));
				return;
			} else if (key.downArrow) {
				setViewScroll((s) => s + 1);
				return;
			}
			return;
		}
		// 失败结果：Enter/Esc 返回 confirm 以便重新提交
		if (step === 'failed') {
			if (key.return || key.escape) {
				setStep('confirm');
			}
			return;
		}
		// confirm：Enter 提交，Esc 返回 maxTurns，上下键翻页
		if (step === 'confirm') {
			if (key.return) {
				submitForm();
			} else if (key.escape) {
				setSingleValue(fields.max_turns != null ? String(fields.max_turns) : '');
				setSingleError(null);
				setViewScroll(0);
				setStep('maxTurns');
			} else if (key.upArrow) {
				setViewScroll((s) => Math.max(0, s - 1));
				return;
			} else if (key.downArrow) {
				setViewScroll((s) => s + 1);
				return;
			}
			return;
		}
		// ---- SelectModal 步骤：上下导航 + Enter 选择 + Esc 取消 ----
		const options = currentSelectOptions();
		if (options.length === 0) return;
		if (key.upArrow) {
			setSelectedIndex((i) => Math.max(0, i - 1));
			return;
		}
		if (key.downArrow) {
			setSelectedIndex((i) => Math.min(options.length - 1, i + 1));
			return;
		}
		if (key.escape) {
			onCancel();
			return;
		}
		if (key.return) {
			const opt = options[selectedIndex];
			if (opt) handleSelect(opt.value);
			return;
		}
	});

	// ====== 渲染 ======

	/** 渲染带标签的单行输入 */
	const renderSingleInput = (prompt: string, placeholder: string, submit: (v: string) => void): React.JSX.Element => (
		<Box flexDirection="column" marginTop={1}>
			<Box>
				<Text color={theme.colors.illusion}>{theme.icons.pointer} </Text>
				<Text color={theme.colors.illusionShimmer} bold>{prompt} </Text>
				<TextInput
					value={singleValue}
					onChange={(v) => { setSingleValue(v); setSingleError(null); }}
					placeholder={placeholder}
					focus={true}
					showCursor={true}
					onSubmit={submit}
				/>
			</Box>
			{singleError ? (
				<Box marginTop={1}>
					<Text color={theme.colors.error}>{singleError}</Text>
				</Box>
			) : null}
			<Box marginTop={1}>
				<Text dimColor>
					<Text color={theme.colors.muted}>{t(language, 'questionHintCancel')}</Text>
					<Text> {theme.icons.middleDot} </Text>
					<Text color={theme.colors.muted}>{t(language, 'questionHintSubmit')}</Text>
				</Text>
			</Box>
		</Box>
	);

	/** 渲染带标签的多行输入 */
	const renderMultilineInput = (prompt: string, placeholder: string, submit: (v: string) => void): React.JSX.Element => (
		<Box flexDirection="column" marginTop={1}>
			<Box>
				<Text color={theme.colors.illusion}>{theme.icons.pointer} </Text>
				<Text bold>{prompt}</Text>
			</Box>
			<Box>
			<Text>  </Text>
			<MultilineTextInput
					value={multilineValue}
					onChange={setMultilineValue}
					onSubmit={submit}
					columns={Math.max(20, columns - 2)}
					placeholder={placeholder}
				/>
			</Box>
			<Box marginTop={1}>
				<Text dimColor>
					<Text color={theme.colors.muted}>{t(language, 'questionHintCancel')}</Text>
					<Text> {theme.icons.middleDot} </Text>
					<Text color={theme.colors.muted}>{t(language, 'questionHintSubmit')}</Text>
					<Text> {theme.icons.middleDot} </Text>
					<Text color={theme.colors.muted}>ctrl+j {t(language, 'newline')}</Text>
				</Text>
			</Box>
		</Box>
	);

	/** 渲染 SelectModal 步骤 */
	const renderSelectStep = (title: string, options: SelectOption[]): React.JSX.Element => (
		<SelectModal title={title} options={options} selectedIndex={selectedIndex} language={language} />
	);

	/** 渲染可翻页的长文本（限制 MAX_VIEW_LINES 行，上下键翻页）
	 *  按终端显示宽度折行（CJK 感知），标题和内容均使用默认颜色 */
	const renderPaginatedText = (text: string): React.JSX.Element => {
		const maxWidth = Math.max(20, columns - 2);
		const allLines = wrapToDisplayWidth(text, maxWidth);
		const maxScroll = Math.max(0, allLines.length - MAX_VIEW_LINES);
		const scroll = Math.min(viewScroll, maxScroll);
		const visibleLines = allLines.slice(scroll, scroll + MAX_VIEW_LINES);
		return (
			<Box flexDirection="column">
				{visibleLines.map((line, i) => (
					<Text key={i}>{line}</Text>
				))}
				{allLines.length > MAX_VIEW_LINES ? (
					<Text dimColor>
						{scroll > 0 ? '↑' : ' '} {scroll + 1}-{Math.min(scroll + MAX_VIEW_LINES, allLines.length)}/{allLines.length} {scroll < maxScroll ? '↓' : ' '}
					</Text>
				) : null}
			</Box>
		);
	};

	/** 渲染生成中 */
	const renderGenerating = (): React.JSX.Element => (
		<Box marginTop={1}>
			<Spinner language={language} label={t(language, 'agentWizardGenerating')} />
		</Box>
	);

	/** 渲染生成失败 */
	const renderGenerateFailed = (): React.JSX.Element => {
		const maxWidth = Math.max(20, columns - 2);
		const needScroll = generateError ? wrapToDisplayWidth(generateError, maxWidth).length > MAX_VIEW_LINES : false;
		return (
		<Box flexDirection="column" marginTop={1}>
			<Box>
				<Text color={theme.colors.error}>{theme.icons.error} </Text>
				<Text color={theme.colors.error} bold>{t(language, 'agentWizardGenerateFailed')}</Text>
			</Box>
			{generateError ? renderPaginatedText(generateError) : null}
			<Box marginTop={1}>
				<Text dimColor>
					<Text color={theme.colors.muted}>{t(language, 'agentWizardRetry')}</Text>
					<Text> {theme.icons.middleDot} </Text>
					<Text color={theme.colors.muted}>{t(language, 'agentWizardBack')}</Text>
					{needScroll ? (
						<>
							<Text> {theme.icons.middleDot} </Text>
							<Text color={theme.colors.muted}>↑↓ scroll</Text>
						</>
					) : null}
				</Text>
			</Box>
		</Box>
		);
	};

	/** 渲染生成结果确认 */
	const renderGenerateConfirm = (): React.JSX.Element => {
		// 拼接完整草稿供翻页查看：identifier + when_to_use + system_prompt
		const draftText = [
			`# ${fields.identifier}`,
			'',
			`## ${t(language, 'agentWizardDescriptionLabel')}`,
			fields.when_to_use,
			'',
			`## ${t(language, 'agentWizardSystemPromptLabel')}`,
			fields.system_prompt,
		].join('\n');
		const maxWidth = Math.max(20, columns - 2);
		const needScroll = wrapToDisplayWidth(draftText, maxWidth).length > MAX_VIEW_LINES;
		return (
			<Box flexDirection="column" marginTop={1}>
				<Box>
					<Text>{t(language, 'agentWizardGenerateConfirm')}</Text>
				</Box>
				<Box marginTop={1}>
					{renderPaginatedText(draftText)}
				</Box>
				<Box marginTop={1}>
					<Text dimColor>
						<Text color={theme.colors.muted}>{t(language, 'questionHintSubmit')}</Text>
						<Text> {theme.icons.middleDot} </Text>
						<Text color={theme.colors.muted}>{t(language, 'questionHintCancel')}</Text>
						{needScroll ? (
							<>
								<Text> {theme.icons.middleDot} </Text>
								<Text color={theme.colors.muted}>↑↓ scroll</Text>
							</>
						) : null}
					</Text>
				</Box>
			</Box>
		);
	};

	/** 渲染 description 预览（generate 路径，与 generateConfirm/confirm 风格一致）
	 *  显示当前 when_to_use，Enter 接受，Esc 返回 generateConfirm */
	const renderDescriptionPreview = (): React.JSX.Element => {
		const descText = [
			`## ${t(language, 'agentWizardDescriptionLabel')}`,
			fields.when_to_use || t(language, 'agentWizardDescriptionPlaceholder'),
		].join('\n');
		const maxWidth = Math.max(20, columns - 2);
		const needScroll = wrapToDisplayWidth(descText, maxWidth).length > MAX_VIEW_LINES;
		return (
			<Box flexDirection="column" marginTop={1}>
				<Box>
					<Text>{t(language, 'agentWizardDescriptionPrompt')}</Text>
				</Box>
				<Box marginTop={1}>
					{renderPaginatedText(descText)}
				</Box>
				<Box marginTop={1}>
					<Text dimColor>
						<Text color={theme.colors.muted}>{t(language, 'questionHintSubmit')}</Text>
						<Text> {theme.icons.middleDot} </Text>
						<Text color={theme.colors.muted}>{t(language, 'questionHintCancel')}</Text>
						{needScroll ? (
							<>
								<Text> {theme.icons.middleDot} </Text>
								<Text color={theme.colors.muted}>↑↓ scroll</Text>
							</>
						) : null}
					</Text>
				</Box>
			</Box>
		);
	};

	/** 渲染提交中 */
	const renderSubmitting = (): React.JSX.Element => (
		<Box marginTop={1}>
			<Spinner language={language} label={t(language, 'agentWizardSubmitting')} />
		</Box>
	);

	/** 渲染确认摘要 */
	const renderConfirm = (): React.JSX.Element => {
		// 拼接完整摘要供翻页查看：基本信息 + system_prompt
		const summaryLines = [
			`${t(language, 'agentWizardScopeLabel')}: ${fields.scope}`,
			`${t(language, 'agentWizardNameLabel')}: ${fields.identifier}`,
			`${t(language, 'agentWizardDescriptionLabel')}: ${fields.when_to_use}`,
			`${t(language, 'agentWizardModelLabel')}: ${fields.model}`,
			`${t(language, 'agentWizardToolsLabel')}: ${fields.tools === null ? t(language, 'agentWizardToolsDefault') : fields.tools.join(', ')}`,
		];
		if (fields.effort) summaryLines.push(`${t(language, 'agentWizardEffortLabel')}: ${fields.effort}`);
		if (fields.permission_mode) summaryLines.push(`${t(language, 'agentWizardPermissionLabel')}: ${fields.permission_mode}`);
		if (fields.max_turns != null) summaryLines.push(`${t(language, 'agentWizardMaxTurnsLabel')}: ${fields.max_turns}`);
		summaryLines.push('', `## ${t(language, 'agentWizardSystemPromptLabel')}`, fields.system_prompt);
		const summaryText = summaryLines.join('\n');
		const maxWidth = Math.max(20, columns - 2);
		const needScroll = wrapToDisplayWidth(summaryText, maxWidth).length > MAX_VIEW_LINES;
		return (
			<Box flexDirection="column" marginTop={1}>
				<Box>
					<Text>{t(language, 'agentWizardConfirmTitle')}</Text>
				</Box>
				<Box marginTop={1}>
					{renderPaginatedText(summaryText)}
				</Box>
				<Box marginTop={1}>
					<Text dimColor>
						<Text color={theme.colors.muted}>{t(language, 'questionHintSubmit')}</Text>
						<Text> {theme.icons.middleDot} </Text>
						<Text color={theme.colors.muted}>{t(language, 'questionHintCancel')}</Text>
						{needScroll ? (
							<>
								<Text> {theme.icons.middleDot} </Text>
								<Text color={theme.colors.muted}>↑↓ scroll</Text>
							</>
						) : null}
					</Text>
				</Box>
			</Box>
		);
	};

	/** 渲染成功 */
	const renderDone = (): React.JSX.Element => (
		<Box flexDirection="column" marginTop={1}>
			<Box>
				<Text color={theme.colors.success}>{theme.icons.success} </Text>
				<Text color={theme.colors.success} bold>{t(language, 'agentWizardSuccess')}</Text>
			</Box>
			{result?.path ? (
				<Box>
					<Text dimColor>{result.path}</Text>
				</Box>
			) : null}
			<Box marginTop={1}>
				<Text dimColor>{t(language, 'agentWizardPressAnyKey')}</Text>
			</Box>
		</Box>
	);

	/** 渲染失败 */
	const renderFailed = (): React.JSX.Element => (
		<Box flexDirection="column" marginTop={1}>
			<Box>
				<Text color={theme.colors.error}>{theme.icons.error} </Text>
				<Text color={theme.colors.error} bold>{t(language, 'agentWizardFailed')}</Text>
			</Box>
			{result?.error ? (
				<Box>
					<Text color={theme.colors.error}>{result.error}</Text>
				</Box>
			) : null}
			{result?.errors && Object.keys(result.errors).length > 0 ? (
			<Box flexDirection="column">
				{Object.entries(result.errors).map(([field, msg], i) => (
					<Text key={i} color={theme.colors.error}>- {field}: {msg}</Text>
				))}
			</Box>
		) : null}
			<Box marginTop={1}>
				<Text dimColor>
					<Text color={theme.colors.muted}>{t(language, 'questionHintSubmit')}</Text>
					<Text> {theme.icons.middleDot} </Text>
					<Text color={theme.colors.muted}>{t(language, 'questionHintCancel')}</Text>
				</Text>
			</Box>
		</Box>
	);

	switch (step) {
		case 'scope':
			return renderSelectStep(t(language, 'agentWizardScopeTitle'), scopeOptions);
		case 'method':
			return renderSelectStep(t(language, 'agentWizardMethodTitle'), methodOptions);
		case 'describe':
			return renderMultilineInput(
				t(language, 'agentWizardDescribePrompt'),
				t(language, 'agentWizardDescribePlaceholder'),
				submitDescribe,
			);
		case 'generating':
			return renderGenerating();
		case 'generateFailed':
			return renderGenerateFailed();
		case 'generateConfirm':
			return renderGenerateConfirm();
		case 'name':
			return renderSingleInput(
				t(language, 'agentWizardNamePrompt'),
				t(language, 'agentWizardNamePlaceholder'),
				submitName,
			);
		case 'systemPrompt':
			return renderMultilineInput(
				t(language, 'agentWizardSystemPromptPrompt'),
				'',
				submitSystemPrompt,
			);
		case 'description':
		// generate 路径：预览模式（与 generateConfirm/confirm 风格一致）
		// manual 路径：文本输入模式
		if (fields.method === 'generate') {
			return renderDescriptionPreview();
		}
		return renderSingleInput(
			t(language, 'agentWizardDescriptionPrompt'),
			t(language, 'agentWizardDescriptionPlaceholder'),
			submitDescription,
		);
		case 'model':
			return renderSelectStep(t(language, 'agentWizardModelTitle'), modelOptions);
		case 'tools':
			return renderSelectStep(t(language, 'agentWizardToolsTitle'), toolsOptions);
		case 'toolsAllow':
			return renderSingleInput(
				t(language, 'agentWizardToolsAllowPrompt'),
				t(language, 'agentWizardToolsAllowPlaceholder'),
				submitToolsAllow,
			);
		case 'effort':
			return renderSelectStep(t(language, 'agentWizardEffortTitle'), effortOptions);
		case 'permission':
			return renderSelectStep(t(language, 'agentWizardPermissionTitle'), permissionOptions);
		case 'maxTurns':
			return renderSingleInput(
				t(language, 'agentWizardMaxTurnsPrompt'),
				t(language, 'agentWizardMaxTurnsPlaceholder'),
				submitMaxTurns,
			);
		case 'confirm':
			return renderConfirm();
		case 'submitting':
			return renderSubmitting();
		case 'done':
			return renderDone();
		case 'failed':
			return renderFailed();
		default:
			return <Box><Text>?</Text></Box>;
	}
}
