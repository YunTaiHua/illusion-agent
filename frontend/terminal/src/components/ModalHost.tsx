/**
 * @fileoverview 模态对话框宿主组件
 *
 * 管理和渲染各种类型的模态对话框，包括：
 * - 问答模态框（支持单选、多选、自定义输入、多问题并行、预览分栏）
 * - 权限确认模态框
 * - MCP 认证模态框
 *
 * 问答模态框的交互设计参考自 Claude Code 的 AskUserQuestionPermissionRequest：
 * 多问题通过顶部导航条切换，单选选中即生效，多选 Space/数字键切换，
 * 无单独"确认"按钮，底部辅助行全部 i18n。
 *
 * @module ModalHost
 */

import React, {useEffect, useMemo, useState} from 'react';
import {Box, Text, useInput} from 'ink';
import TextInput from 'ink-text-input';

import type {UiLanguage} from '../i18n.js';
import {t} from '../i18n.js';
import {useQuestionState} from '../hooks/useQuestionState.js';
import type {QuestionState} from '../hooks/useQuestionState.js';
import {useTerminalSize} from '../hooks/useTerminalSize.js';
import {MIN_WRAP_WIDTH, stringWidth, WIDTH_SAFETY_EXTRA, wrapText} from '../utils/markdown.js';
import {useTheme} from '../theme/ThemeContext.js';
import {QuestionNavigationBar} from './QuestionNavigationBar.js';
import {QuestionPreviewBox} from './QuestionPreviewBox.js';

/**
 * 问题选项类型
 */
type QuestionOption = {
	/** 选项标签 */
	label: string;
	/** 选项描述（可选） */
	description?: string;
	/** 选项预览（可选，Markdown） */
	preview?: string;
};

/**
 * 问题项类型
 */
type QuestionItem = {
	/** 问题文本 */
	question: string;
	/** 问题标题（可选） */
	header?: string;
	/** 选项列表（可选） */
	options?: QuestionOption[];
	/** 是否多选（可选） */
	multiSelect?: boolean;
	/** 禁用手动输入（可选） */
	noCustomInput?: boolean;
};

/**
 * 选项条目类型：普通选项或工具自动追加的"其他"选项
 */
type OptionEntry =
	| {type: 'option'; label: string; description?: string; preview?: string}
	| {type: 'other'; label: string; description?: undefined; preview?: undefined};

/**
 * 辅助行片段类型，用于动态拼接底部键位提示
 */
type HintFragment = {key: string; label: string};

/**
 * 问答模态框组件
 *
 * 完整支持后端下发的 1-4 个问题：单选、多选、"其他"自定义输入、
 * 多问题切换导航、复核页与最终提交，以及单选 preview 左右分栏预览。
 * 单选选中即生效（单问题直接提交，多问题自动前进）；
 * 多选 Space/数字键切换、Enter 前进/提交，无单独"确认"按钮。
 *
 * @param props - 组件属性
 * @param props.modal - 模态对话框配置
 * @param props.modalInput - 当前输入内容（无选项时的自由文本输入）
 * @param props.setModalInput - 设置输入内容的回调
 * @param props.onSubmit - 提交回调
 * @param props.language - 当前 UI 语言
 * @returns 返回问答模态框的 JSX 元素
 */
export function QuestionModal({
	modal,
	modalInput,
	setModalInput,
	onSubmit,
	language,
}: {
	modal: Record<string, unknown>;
	modalInput: string;
	setModalInput: (value: string) => void;
	onSubmit: (value: string) => void;
	language: UiLanguage;
}): React.JSX.Element {
	const theme = useTheme();
	const {columns: terminalWidth} = useTerminalSize();
	const state = useQuestionState();

	/** 自由文本输入的多行缓冲（无选项场景） */
	const [extraLines, setExtraLines] = useState<string[]>([]);
	/** 当前问题内的选项聚焦索引 */
	const [optionIndex, setOptionIndex] = useState(0);
	/** "其他"选项是否处于输入状态（聚焦在输入框上） */
	const [isOtherFocused, setIsOtherFocused] = useState(false);
	/** "其他"选项的输入内容 */
	const [otherInput, setOtherInput] = useState('');
	/** 当前问题多选的已选索引集合 */
	const [selectedIndices, setSelectedIndices] = useState<Set<number>>(new Set());
	/** 预览模式下的分页起始行 */
	const [previewStartLine, setPreviewStartLine] = useState(0);

	/** 解析后端下发的问题列表 */
	const questions: QuestionItem[] = useMemo(() => {
		const raw = modal.questions;
		if (!Array.isArray(raw)) return [];
		return raw as QuestionItem[];
	}, [modal.questions]);

	const {currentQuestionIndex} = state;
	const currentQuestion = questions[currentQuestionIndex] ?? null;
	const isSubmitView = currentQuestionIndex >= questions.length && questions.length > 0;

	/** 当前问题的选项构建（过滤 LLM 自带的"其他"，工具统一追加） */
	const allOptions = useMemo<OptionEntry[]>(() => {
		if (isSubmitView || !currentQuestion) return [];
		const opts = currentQuestion.options ?? [];
		const hasOptions = opts.length > 0;
		if (!hasOptions) return [];
		// 过滤掉 LLM 返回的"其他"选项，保留工具自动添加的
		const filtered = opts.filter((opt) => {
			const lbl = opt.label.toLowerCase();
			return !(lbl === 'other' || lbl === '其他' || lbl.startsWith('other') || lbl.startsWith('其他'));
		});
		const result: OptionEntry[] = filtered.map((opt) => ({
			type: 'option',
			label: opt.label,
			description: opt.description,
			preview: opt.preview,
		}));
		// 指定 noCustomInput 时不追加"其他"选项（如沙箱权限对话框）
		if (currentQuestion.noCustomInput === true) return result;
		// 始终添加工具自动的"其他"选项（单选和多选均适用）
		result.push({type: 'other', label: t(language, 'questionOther'), description: undefined, preview: undefined});
		return result;
	}, [currentQuestion, isSubmitView, language]);

	const hasOptions = allOptions.length > 0;
	const isMultiSelect = currentQuestion?.multiSelect === true && hasOptions;
	/** 单选问题且任一选项带 preview 时启用左右分栏预览 */
	const hasPreview = !isMultiSelect && allOptions.some((o) => o.type === 'option' && !!o.preview);
	const otherIdx = allOptions.findIndex((o) => o.type === 'other');

	// 切换问题时重置或恢复当前问题的局部交互状态
	useEffect(() => {
		const qs = currentQuestion ? state.questionStates[currentQuestion.question] : undefined;
		const persisted = qs?.selectedValue;

		// 恢复单选 optionIndex（string 型 selectedValue）
		if (typeof persisted === 'string') {
			const idx = allOptions.findIndex((o) => o.type === 'option' && o.label === persisted);
			setOptionIndex(idx >= 0 ? idx : 0);
		} else {
			setOptionIndex(0);
		}

		// 恢复"其他"输入文本
		setOtherInput(qs?.textInputValue ?? '');
		setIsOtherFocused(Boolean(qs?.textInputValue));

		// 恢复自由文本多行缓冲
		setExtraLines(qs?.extraLines ?? []);

		setPreviewStartLine(0);

		// 恢复多选 selectedIndices（从 questionStates 回读）
		if (Array.isArray(persisted)) {
			const restored = new Set<number>();
			persisted.forEach((label) => {
				const idx = allOptions.findIndex((o) => o.type === 'option' && o.label === label);
				if (idx >= 0) restored.add(idx);
			});
			setSelectedIndices(restored);
		} else {
			setSelectedIndices(new Set());
		}

		// 恢复自由文本单行输入（无选项场景）
		if (qs?.freeTextInput !== undefined && setModalInput) {
			setModalInput(qs.freeTextInput);
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [currentQuestionIndex, allOptions.length]);

	/**
	 * 提交单个问题的答案。
	 * - 单问题单选：直接以 "N. label" 提交（兼容 plan 审批等字符串契约）
	 * - 单问题多选：直接以 JSON {header:[labels]} 提交
	 * - 多问题：写入 answers，由复核页统一提交
	 */
	const commitAnswer = (questionText: string, value: string | string[], header: string, shouldAdvance: boolean = true): void => {
		const isSingleQuestion = questions.length === 1;
		// 单问题直接提交（保持字符串契约）
		if (isSingleQuestion) {
			if (Array.isArray(value)) {
				onSubmit(JSON.stringify({[header]: value}));
			} else {
				onSubmit(value);
			}
			return;
		}
		// 多问题：记录答案，按需前进
		const stored = Array.isArray(value) ? value.join(', ') : value;
		state.setAnswer(questionText, stored, shouldAdvance);
	};

	/**
	 * 提交多选答案（收集当前问题所有选中项，含"其他"输入）。
	 */
	const commitMultiSelect = (shouldAdvance: boolean = true): void => {
		if (!currentQuestion) return;
		const labels = allOptions
			.filter((opt, i) => opt.type === 'option' && selectedIndices.has(i))
			.map((opt) => opt.label);
		// "其他"选项的输入内容
		if (otherIdx >= 0 && selectedIndices.has(otherIdx) && otherInput.trim()) {
			labels.push(otherInput.trim());
		}
		if (labels.length === 0) return;
		const header = currentQuestion.header ?? 'answer';
		// 同步持久化到 questionStates（便于切回时不丢失）
		state.updateQuestionState(currentQuestion.question, {selectedValue: labels}, true);
		commitAnswer(currentQuestion.question, labels, header, shouldAdvance);
	};

	/** 更新自由文本多行缓冲并持久化到 questionStates */
	const updateExtraLines = (next: string[] | ((prev: string[]) => string[])): void => {
		setExtraLines(next);
		if (currentQuestion) {
			// 计算最终值用于持久化
			const resolved = typeof next === 'function' ? next(extraLines) : next;
			state.updateQuestionState(
				currentQuestion.question,
				{extraLines: resolved},
				false,
			);
		}
	};

	/** 渲染"其他"选项行（预览模式与普通模式共用） */
	const renderOtherOption = (index: number, isCurrent: boolean, opts?: {rowLayout?: boolean}) => {
		const isActive = isOtherFocused;
		const pointer = isCurrent ? `${theme.icons.pointer} ` : '  ';
		const checkbox = (!opts?.rowLayout && isMultiSelect) ? `[${selectedIndices.has(index) ? theme.icons.check : ' '}]` : '';
		const labelText = `${pointer}${checkbox}${index + 1}. ${optLabel(index)}`;
		return (
			<Box key={index} flexDirection="column">
				<Text
					color={isCurrent || (isMultiSelect && selectedIndices.has(index)) ? theme.colors.illusionShimmer : theme.colors.illusion}
					bold={isCurrent && (opts?.rowLayout || !isMultiSelect)}
				>
					{labelText}
				</Text>
				{isActive ? (
					<Box paddingLeft={2}>
						<TextInput
						value={otherInput}
						onChange={(v) => {
							setOtherInput(v);
							// 即时持久化到 questionStates（便于切回时不丢失）
							if (currentQuestion) {
								state.updateQuestionState(
									currentQuestion.question,
									{textInputValue: v},
									isMultiSelect,
								);
							}
						}}
						placeholder={t(language, 'questionOtherPlaceholder')}
						focus={true}
						showCursor={true}
						onSubmit={(v) => {
							if (!opts?.rowLayout && isMultiSelect) {
								setIsOtherFocused(false);
								return;
							}
							const allLines = [...extraLines, v].filter(Boolean);
							updateExtraLines([]);
							setIsOtherFocused(false);
							if (allLines.length > 0 && currentQuestion) {
								commitAnswer(currentQuestion.question, allLines.join('\n'), currentQuestion.header ?? 'answer');
							}
						}}
					/>
					</Box>
				) : otherInput ? (
					<Box paddingLeft={2}>
						<Text>{otherInput}</Text>
					</Box>
				) : null}
			</Box>
		);
	};

	/** 获取选项标签 */
	const optLabel = (index: number): string => allOptions[index]?.label ?? '';

	useInput((_chunk, key) => {
		// ---- 复核/提交页 ----
		if (isSubmitView) {
			// 复核页：Enter 提交全部答案，Esc 取消，左右/Tab 导航回问题
			if (key.escape) {
				onSubmit('');
				return;
			}
			if (key.return) {
				const result: Record<string, string | string[]> = {};
				for (const q of questions) {
					const ans = state.answers[q.question];
					if (ans === undefined) continue;
					const header = q.header ?? 'answer';
					// 多选答案以逗号分隔存储，还原为数组
					const qState = state.questionStates[q.question]?.selectedValue;
					if (Array.isArray(qState)) {
						result[header] = qState;
					} else {
						result[header] = ans;
					}
				}
				onSubmit(JSON.stringify(result));
				return;
			}
			// 回到上一题
			if (key.leftArrow || (key.tab && key.shift) || key.upArrow) {
				state.prevQuestion();
				return;
			}
			return;
		}

		if (!currentQuestion) return;

		// ---- 预览分页：ctrl+左右方向键 ----
		if (hasPreview && key.ctrl && (key.leftArrow || key.rightArrow)) {
			const previewContent = allOptions[optionIndex]?.type === 'option' ? (allOptions[optionIndex]?.preview ?? '') : '';
			const totalPreviewLines = previewContent.split('\n').length;
			const maxPreviewLines = 10;
			const totalPages = Math.max(1, Math.ceil(totalPreviewLines / maxPreviewLines));
			if (key.leftArrow) {
				setPreviewStartLine((s) => Math.max(0, s - maxPreviewLines));
			} else {
				setPreviewStartLine((s) => Math.min((totalPages - 1) * maxPreviewLines, s + maxPreviewLines));
			}
			return;
		}

		// ---- "其他"选项输入模式：仅拦截特殊键，字符交给 TextInput ----
		if (isOtherFocused) {
			if (key.escape) {
				setIsOtherFocused(false);
				setOtherInput('');
				// 多选模式下取消选中"其他"
				if (isMultiSelect && otherIdx >= 0) {
					setSelectedIndices((prev) => {
						const next = new Set(prev);
						next.delete(otherIdx);
						return next;
					});
				}
				return;
			}
			// 上下箭头：退出输入模式并导航选项
			if (key.upArrow) {
				setIsOtherFocused(false);
				setOptionIndex((i) => Math.max(0, i - 1));
				return;
			}
			if (key.downArrow) {
				setIsOtherFocused(false);
				setOptionIndex((i) => Math.min(allOptions.length - 1, i + 1));
				return;
			}
			// Shift+Enter 换行
			if (key.shift && key.return) {
				updateExtraLines((lines) => [...lines, otherInput]);
				setOtherInput('');
				return;
			}
			// Enter 与字符输入交给 TextInput.onSubmit 处理
			return;
		}

		// ---- 多问题间导航（左右箭头 / Tab / Shift+Tab）----
		// 切换 tab 等同于一次回车：先提交当前问题（不自动前进），再前进/后退
		// 文本输入模式或"其他"聚焦时不导航
		const wantsPrev = key.leftArrow || (key.tab && key.shift);
		const wantsNext = key.rightArrow || (key.tab && !key.shift);
		if (wantsPrev && currentQuestionIndex > 0) {
			// 先提交当前问题（不自动前进）：选项题提交选中项，自由文本提交当前输入
			if (currentQuestion) {
				if (hasOptions) {
					if (isMultiSelect) {
						commitMultiSelect(false);
					} else {
						const selected = allOptions[optionIndex];
						if (selected && selected.type !== 'other') {
							selectSingle(optionIndex, selected.label, false);
						}
					}
				} else if (modalInput.trim()) {
					// 自由文本：仅在有内容时提交，避免空答案覆盖
					handleTextSubmit(modalInput, false);
				}
			}
			state.prevQuestion();
			return;
		}
		if (wantsNext && currentQuestionIndex < questions.length) {
			// 先提交当前问题（不自动前进）：选项题提交选中项，自由文本提交当前输入
			if (currentQuestion) {
				if (hasOptions) {
					if (isMultiSelect) {
						commitMultiSelect(false);
					} else {
						const selected = allOptions[optionIndex];
						if (selected && selected.type !== 'other') {
							selectSingle(optionIndex, selected.label, false);
						}
					}
				} else if (modalInput.trim()) {
					// 自由文本：仅在有内容时提交，避免空答案覆盖
					handleTextSubmit(modalInput, false);
				}
			}
			state.nextQuestion();
			return;
		}

		// ---- 无选项的自由文本输入 ----
		if (!hasOptions) {
			if (key.shift && key.return) {
				updateExtraLines((lines) => [...lines, modalInput]);
				setModalInput('');
			}
			return;
		}

		// ---- 选项导航 ----
		if (key.upArrow) {
			setOptionIndex((i) => Math.max(0, i - 1));
			setPreviewStartLine(0);
			return;
		}
		if (key.downArrow) {
			setOptionIndex((i) => Math.min(allOptions.length - 1, i + 1));
			setPreviewStartLine(0);
			return;
		}
		if (key.escape) {
			onSubmit('');
			return;
		}

		// ---- 多选模式：Space/数字键切换，Enter 前进/提交 ----
		if (isMultiSelect) {
			if (key.return) {
				commitMultiSelect();
				return;
			}
			if (_chunk === ' ') {
				toggleSelection(optionIndex);
				return;
			}
			const num = parseInt(_chunk, 10);
			if (num >= 1 && num <= allOptions.length) {
				toggleSelection(num - 1);
				return;
			}
			return;
		}

		// ---- 单选模式：Enter/数字键选中 ----
		if (key.return) {
			const selected = allOptions[optionIndex];
			if (!selected) return;
			if (selected.type === 'other') {
				// 进入"其他"输入模式
				setIsOtherFocused(true);
				return;
			}
			selectSingle(optionIndex, selected.label);
			return;
		}
		const num = parseInt(_chunk, 10);
		if (num >= 1 && num <= allOptions.length) {
			const target = allOptions[num - 1];
			if (!target) return;
			if (target.type === 'other') {
				setOptionIndex(num - 1);
				setIsOtherFocused(true);
				return;
			}
			selectSingle(num - 1, target.label);
			return;
		}
	});

	/**
	 * 单选选中某选项（含 preview 模式下的选中）。
	 */
	const selectSingle = (index: number, label: string, shouldAdvance: boolean = true): void => {
		if (!currentQuestion) return;
		const header = currentQuestion.header ?? 'answer';
		// 持久化选中值与"其他"输入文本（便于切回时回显）
		const updates: Partial<QuestionState> = {selectedValue: label};
		if (isOtherFocused) {
			updates.textInputValue = otherInput;
		}
		state.updateQuestionState(currentQuestion.question, updates, false);
		commitAnswer(currentQuestion.question, `${index + 1}. ${label}`, header, shouldAdvance);
	};

	/**
	 * 多选切换某选项的选中状态（持久化到 questionStates）。
	 */
	const toggleSelection = (index: number): void => {
		const target = allOptions[index];
		if (!target) return;
		setSelectedIndices((prev) => {
			const next = new Set(prev);
			if (target.type === 'other') {
				if (next.has(index)) {
					next.delete(index);
					setIsOtherFocused(false);
					setOtherInput('');
				} else {
					next.add(index);
					setIsOtherFocused(true);
					setOptionIndex(index);
				}
			} else if (next.has(index)) {
				next.delete(index);
			} else {
				next.add(index);
			}
			// 即时持久化（含"其他"输入文本，便于切回时回显）
			if (currentQuestion) {
				const labels = allOptions
					.filter((opt, i) => opt.type === 'option' && next.has(i))
					.map((opt) => opt.label);
				const updates: Partial<QuestionState> = {selectedValue: labels};
				// 若"其他"被选中，同步保存其输入文本
				if (otherIdx >= 0 && next.has(otherIdx)) {
					updates.textInputValue = otherInput;
				}
				state.updateQuestionState(currentQuestion.question, updates, true);
			}
			return next;
		});
	};

	/** 自由文本输入的提交处理 */
	const handleTextSubmit = (value: string, shouldAdvance: boolean = true): void => {
		if (hasOptions || !currentQuestion) return;
		const allLines = [...extraLines, value];
		updateExtraLines([]);
		const header = currentQuestion.header ?? 'answer';
		commitAnswer(currentQuestion.question, allLines.join('\n'), header, shouldAdvance);
	};

	const toolName = modal.tool_name ? String(modal.tool_name) : null;

	// ============ 复核/提交页 ============
	if (isSubmitView) {
		const allAnswered = questions.every((q) => q.question && state.answers[q.question] !== undefined);
		return (
			<Box flexDirection="column" marginTop={1}>
				{/* 话题分割线：把提问与上方对话内容明显隔开 */}
				<Text>{'─'.repeat(60)}</Text>
				<Text> </Text>
				<QuestionNavigationBar
					headers={questions.map((q, i) => q.header ?? `Q${i + 1}`)}
					currentQuestionIndex={currentQuestionIndex}
					answeredHeaders={new Set(questions.filter((q) => q.question && state.answers[q.question] !== undefined).map((q) => q.header ?? q.question))}
				/>
				<Box marginTop={1}>
					<Text color={theme.colors.suggestion} bold>{t(language, 'questionReviewTitle')}</Text>
				</Box>
				{!allAnswered ? (
					<Box marginBottom={1}>
						<Text color={theme.colors.warning}>⚠ {t(language, 'questionNotAllAnswered')}</Text>
					</Box>
				) : null}
				<Box flexDirection="column" marginBottom={1}>
					{questions.map((q) => {
						const ans = state.answers[q.question];
						return (
							<Box key={q.question} flexDirection="column" marginLeft={1}>
								<Text>{theme.icons.bullet} {q.question}</Text>
								<Box marginLeft={2}>
									<Text color={theme.colors.success}>{theme.icons.arrow} {ans ?? t(language, 'questionNoAnswer')}</Text>
								</Box>
							</Box>
						);
					})}
				</Box>
				<Box flexDirection="column">
					<Text dimColor>{t(language, 'questionReadyToSubmit')}</Text>
					<Box marginTop={1}>
						<Text dimColor>
							<Text color={theme.colors.suggestion} bold>{t(language, 'questionSubmit')}</Text>
							<Text> {theme.icons.middleDot} </Text>
							<Text color={theme.colors.muted}>←/Tab</Text> {t(language, 'questionHintSwitchTab')}
							<Text> {theme.icons.middleDot} </Text>
							<Text color={theme.colors.muted}>Esc</Text> {t(language, 'questionHintCancel')}
						</Text>
					</Box>
					<Box marginTop={1}>
						<Text color={theme.colors.suggestion} bold>{theme.icons.pointer} {t(language, 'questionSubmit')} ({t(language, 'questionHintSubmit')})</Text>
					</Box>
				</Box>
			</Box>
		);
	}

	if (!currentQuestion) {
		return <Box marginTop={1}><Text dimColor>...</Text></Box>;
	}

	// 当前问题的辅助行片段构建（底部键位提示）
	const hintText = isMultiSelect ? t(language, 'questionSelectAll') : t(language, 'questionSelectOne');

	// 构建底部辅助行片段（全部 i18n，按当前模式动态拼接）
	const hintFragments: HintFragment[] = [];
	if (hasOptions) {
		hintFragments.push({key: 'enter', label: `${t(language, 'questionHintSelect')}`});
		hintFragments.push({key: 'nav', label: t(language, 'questionHintNavigate')});
		if (isMultiSelect) {
			hintFragments.push({key: 'toggle', label: t(language, 'questionHintToggle')});
			hintFragments.push({key: 'submit', label: t(language, 'questionHintSubmit')});
		} else {
			hintFragments.push({key: 'quick', label: t(language, 'questionHintQuickSelect')});
		}
		if (questions.length > 1) {
			hintFragments.push({key: 'tab', label: t(language, 'questionHintSwitchTab')});
		}
		hintFragments.push({key: 'cancel', label: t(language, 'questionHintCancel')});
	} else {
		// 无选项自由输入
		hintFragments.push({key: 'submit', label: t(language, 'questionHintSubmit')});
		if (questions.length > 1) {
			hintFragments.push({key: 'tab', label: t(language, 'questionHintSwitchTab')});
		}
		hintFragments.push({key: 'cancel', label: t(language, 'questionHintCancel')});
	}

	// 预览模式下聚焦选项的 preview 内容
	const focusedPreview = hasPreview
		? (allOptions[optionIndex]?.type === 'option' ? allOptions[optionIndex]?.preview : undefined) ?? null
		: null;

	// 标题换行计算：header 和 question 同一行
	// 两阶段拆分：首行按首行宽度（含 header）拆分，续行按续行宽度（仅 pointer）拆分，避免续行提前换行
	const pointerPart = `${theme.icons.pointer} `;
	const headerChipPart = currentQuestion.header ? ` ${currentQuestion.header} ` : '';
	const separatorPart = currentQuestion.header ? ' ' : '';
	const firstLinePrefix = pointerPart + headerChipPart + separatorPart;
	const continuationIndent = ' '.repeat(stringWidth(pointerPart));
	const firstLineWidth = Math.max(MIN_WRAP_WIDTH, terminalWidth - stringWidth(firstLinePrefix) - WIDTH_SAFETY_EXTRA);
	const continuationWidth = Math.max(MIN_WRAP_WIDTH, terminalWidth - stringWidth(pointerPart) - WIDTH_SAFETY_EXTRA);
	const firstBreak = wrapText(currentQuestion.question, firstLineWidth, {hard: true});
	const firstLine = firstBreak[0] ?? '';
	const remainingText = firstBreak.slice(1).join('');
	const continuationLines = remainingText ? wrapText(remainingText, continuationWidth, {hard: true}) : [];
	const wrappedQuestion = [firstLine, ...continuationLines];

	return (
		<Box flexDirection="column" marginTop={1}>
			{/* 话题分割线：把提问与上方对话内容明显隔开，避免用户忽略提问 */}
			<Text>{'─'.repeat(60)}</Text>
<Text> </Text>
			{/* 导航条（仅多问题时显示，置于题目上方） */}
			{questions.length > 1 ? (
				<QuestionNavigationBar
					headers={questions.map((q, i) => q.header ?? `Q${i + 1}`)}
					currentQuestionIndex={currentQuestionIndex}
					answeredHeaders={new Set(questions.filter((q) => q.question && state.answers[q.question] !== undefined).map((q) => q.header ?? q.question))}
					hideSubmitTab={questions.length === 1 && !isMultiSelect}
				/>
			) : null}
			{/* 标题行：header chip + 问题文本（首行计算 header 宽度，续行只缩进 pointer 宽度） */}
			{wrappedQuestion.map((line, i) => (
				<Box key={i}>
					{i === 0 ? (
						<Text>
							<Text color={theme.colors.illusion}>{theme.icons.pointer} </Text>
							{currentQuestion.header ? (
								<>
									<Text backgroundColor={theme.colors.illusionShimmer} color={theme.colors.foreground} bold>{' '}{currentQuestion.header}{' '}</Text>
									<Text> </Text>
								</>
							) : null}
							<Text bold>{line}</Text>
						</Text>
					) : (
						<Text bold>{continuationIndent}{line}</Text>
					)}
				</Box>
			))}
			{toolName ? (
				<Box>
					<Text dimColor>{`  ${theme.icons.resultPrefix} `}</Text>
					<Text dimColor>{t(language, 'toolLabel')}</Text>
					<Text color={theme.colors.info}>{toolName}</Text>
				</Box>
			) : null}
			{hasOptions ? (
				<Box flexDirection="column" marginTop={1}>
					<Text>{hintText}</Text>
					{hasPreview ? (
						// ===== 预览分栏：左选项列表 + 右预览框 =====
						<Box flexDirection="row" gap={2}>
							<Box flexDirection="column" width={Math.min(30, Math.floor(terminalWidth * 0.35))}>
								{allOptions.map((opt, i) => {
									const isCurrent = i === optionIndex;
									if (opt.type === 'other') return renderOtherOption(i, isCurrent, {rowLayout: true});
									return (
										<Box key={i} flexDirection="row">
											<Text color={isCurrent ? theme.colors.illusionShimmer : theme.colors.illusion} bold={isCurrent}>
												{`${isCurrent ? `${theme.icons.pointer} ` : '  '}${i + 1}. ${opt.label}`}
											</Text>
										</Box>
									);
								})}
							</Box>
							<Box flexDirection="column" flexGrow={1}>
								<QuestionPreviewBox
									content={focusedPreview ?? ''}
									maxWidth={terminalWidth - 36}
									startLine={previewStartLine}
								/>
							</Box>
						</Box>
					) : (
						// ===== 普通选项列表 =====
						allOptions.map((opt, i) => {
							const isCurrent = i === optionIndex;
							const isSelected = isMultiSelect ? selectedIndices.has(i) : false;
							if (opt.type === 'other') return renderOtherOption(i, isCurrent);
							// label 单行合并，description 独占下一行缩进
							const pointer = isCurrent ? `${theme.icons.pointer} ` : '  ';
							const checkbox = isMultiSelect ? `[${isSelected ? theme.icons.check : ' '}]` : '';
							const previewHint = isCurrent && !isMultiSelect && opt.preview ? ` ${theme.icons.middleDot}preview` : '';
							const labelText = `${pointer}${checkbox}${i + 1}. ${opt.label}${previewHint}`;
							return (
								<Box key={i} flexDirection="column">
									<Text
										color={isCurrent || (isMultiSelect && isSelected) ? theme.colors.illusionShimmer : theme.colors.illusion}
										bold={isCurrent && !isMultiSelect}
									>
										{labelText}
									</Text>
									{opt.description ? (
										<Box paddingLeft={2}>
											<Text>{opt.description}</Text>
										</Box>
									) : null}
								</Box>
							);
						})
					)}
				</Box>
			) : null}

			{/* 无选项时的自由文本输入 */}
			{!hasOptions ? (
				<>
					{extraLines.length > 0 ? (
						<Box flexDirection="column" marginTop={1}>
							{extraLines.map((line, i) => (
								<Box key={i}>
									<Text dimColor>{`  ${theme.icons.resultPrefix} `}</Text>
									<Text dimColor>{line}</Text>
								</Box>
							))}
						</Box>
					) : null}
					<Box marginTop={1}>
					<Text color={theme.colors.illusion}>{theme.icons.pointer} </Text>
					<TextInput
						value={modalInput}
						onChange={(v) => {
							setModalInput(v);
							// 持久化自由文本输入到 questionStates（便于切回时不丢失）
							if (currentQuestion) {
								state.updateQuestionState(
									currentQuestion.question,
									{freeTextInput: v},
									false,
								);
							}
						}}
						onSubmit={handleTextSubmit}
					/>
				</Box>
				</>
			) : null}

			{/* 底部辅助行：全部 i18n 片段动态拼接 */}
			<Box marginTop={1}>
				<Text dimColor>
					{hintFragments.map((frag, i) => (
						<React.Fragment key={frag.key}>
							{i > 0 ? <Text> {theme.icons.middleDot} </Text> : null}
							{frag.label}
						</React.Fragment>
					))}
				</Text>
			</Box>
		</Box>
	);
}

/**
 * MCP 认证模态框组件
 *
 * 显示 MCP 服务器认证请求，提示用户输入认证信息。
 *
 * @param props - 组件属性
 * @param props.modal - 模态对话框配置
 * @param props.modalInput - 当前输入内容
 * @param props.setModalInput - 设置输入内容的回调
 * @param props.onSubmit - 提交回调
 * @param props.language - 当前 UI 语言
 * @returns 返回 MCP 认证模态框的 JSX 元素
 */
function McpAuthModal({
	modal,
	modalInput,
	setModalInput,
	onSubmit,
	language,
}: {
	modal: Record<string, unknown>;
	modalInput: string;
	setModalInput: (value: string) => void;
	onSubmit: (value: string) => void;
	language: UiLanguage;
}): React.JSX.Element {
	const theme = useTheme();
	const prompt = String(modal.prompt ?? t(language, 'mcpAuthPrompt'));

	return (
		<Box flexDirection="column" marginTop={1}>
			<Box>
				<Text color={theme.colors.warning}>{theme.icons.pointer} </Text>
				<Text bold>{t(language, 'mcpAuthTitle')}</Text>
			</Box>
			<Box>
				<Text dimColor>{`  ${theme.icons.resultPrefix} `}</Text>
				<Text dimColor>{prompt}</Text>
			</Box>
			<Box marginTop={1}>
				<Text color={theme.colors.illusion}>{theme.icons.pointer} </Text>
				<TextInput value={modalInput} onChange={setModalInput} onSubmit={onSubmit} />
			</Box>
		</Box>
	);
}

/**
 * 模态对话框宿主组件
 *
 * 根据模态对话框的类型渲染对应的模态框组件。
 * 支持的类型包括：permission（权限确认）、question（问答）、mcp_auth（MCP 认证）。
 *
 * @param props - 组件属性
 * @param props.modal - 模态对话框配置（null 表示无模态框）
 * @param props.modalInput - 当前输入内容
 * @param props.setModalInput - 设置输入内容的回调
 * @param props.onSubmit - 提交回调
 * @param props.language - 当前 UI 语言
 * @returns 返回对应的模态框组件，如果没有模态框则返回 null
 */
export function ModalHost({
	modal,
	modalInput,
	setModalInput,
	onSubmit,
	language,
}: {
	modal: Record<string, unknown> | null;
	modalInput: string;
	setModalInput: (value: string) => void;
	onSubmit: (value: string) => void;
	language: UiLanguage;
}): React.JSX.Element | null {
	if (!modal) {
		return null;
	}

	if (modal.kind === 'question') {
		return (
			<QuestionModal
				modal={modal}
				modalInput={modalInput}
				setModalInput={setModalInput}
				onSubmit={onSubmit}
				language={language}
			/>
		);
	}

	if (modal.kind === 'mcp_auth') {
		return (
			<McpAuthModal
				modal={modal}
				modalInput={modalInput}
				setModalInput={setModalInput}
				onSubmit={onSubmit}
				language={language}
			/>
		);
	}

	return null;
}
