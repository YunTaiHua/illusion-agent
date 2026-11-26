/**
 * @fileoverview 多行文本输入组件
 *
 * 基于 Cursor/MeasuredText 模型，提供：
 * - display-line 感知的光标导航（上下箭头在 wrap 行间移动）
 * - 粘贴缓冲（多字符一次性插入，不吞换行/空格）
 * - Ctrl+U 逐逻辑行删除（连续5次清空全部）
 * - Ctrl+A/E 行首/行尾
 * - 视口滚动（最大可见行数限制）
 * - 占位符显示
 *
 * @module MultilineTextInput
 */

import React, {useState, useEffect, useLayoutEffect, useCallback, useRef} from 'react';
import {Text, useInput, type Key} from 'ink';
import chalk from 'chalk';
import {Cursor} from '../utils/Cursor.js';
import {paintReferences, type TextRange} from '../utils/slashCommands.js';
import {useTheme} from '../theme/ThemeContext.js';

/** 粘贴超时（ms） */
const PASTE_TIMEOUT_MS = 100;
/** Ctrl+U 连续删除达到此值时清空全部 */
const CTRL_U_CLEAR_THRESHOLD = 5;
/** 默认最大可见行数 */
const DEFAULT_MAX_VISIBLE_LINES = 10;

/**
 * 多行文本输入组件
 *
 * 基于 Cursor/MeasuredText 模型，提供 display-line 感知的光标导航、
 * 粘贴缓冲、Ctrl+U 逐行删除、视口滚动。
 */
export default function MultilineTextInput({
	value: originalValue,
	placeholder = '',
	focus = true,
	showCursor = true,
	columns,
	maxVisibleLines = DEFAULT_MAX_VISIBLE_LINES,
	onChange,
	onSubmit,
	onCursorChange,
	onPickerKey,
	initialCursorOffset,
	referenceRanges,
}: {
	value: string;
	placeholder?: string;
	focus?: boolean;
	showCursor?: boolean;
	columns: number;
	maxVisibleLines?: number;
	onChange: (value: string) => void;
	onSubmit?: (value: string) => void;
	/** 光标位置变化回调（@ 提及 token 检测依赖光标位置） */
	onCursorChange?: (offset: number) => void;
	/** 选择器已消费该按键（含 Enter）时为真。 */
	onPickerKey?: (key: Key) => boolean;
	/** 挂载时光标初始位置（缺省为文本末尾；@ 提及插入后定位到插入点之后） */
	initialCursorOffset?: number;
	referenceRanges?: TextRange[];
}): React.JSX.Element {
	const theme = useTheme();
	// === 状态 ===
	const [cursorOffset, setCursorOffset] = useState(initialCursorOffset ?? (originalValue || '').length);
	const [preservedColumn, setPreservedColumn] = useState<number | null>(null);
	const [isPasting, setIsPasting] = useState(false);

	// 粘贴缓冲（用 ref 避免闭包陷阱）
	const pasteChunksRef = useRef<string[]>([]);
	const pasteTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

	// Ctrl+U 连续计数
	const ctrlUCountRef = useRef(0);

	// 外部 value 变化时，钳位光标位置
	useEffect(() => {
		setCursorOffset(prev => {
			if (!focus || !showCursor) return prev;
			const maxOffset = (originalValue || '').length;
			if (prev > maxOffset) return maxOffset;
			return prev;
		});
	}, [originalValue, focus, showCursor]);

	// 光标位置变化上报（App 层据此检测 @ 提及 token）
	useLayoutEffect(() => {
		onCursorChange?.(cursorOffset);
	}, [cursorOffset, onCursorChange]);

	// === 辅助函数 ===

	/** 应用光标变更 */
	const applyCursor = useCallback((next: Cursor) => {
		if (next.text !== originalValue) {
			onChange(next.text);
		}
		setCursorOffset(next.offset);
	}, [originalValue, onChange]);

	/** 清除 preservedColumn */
	const clearPreservedColumn = useCallback(() => setPreservedColumn(null), []);

	/** 重置 Ctrl+U 计数 */
	const resetCtrlUCount = useCallback(() => {
		ctrlUCountRef.current = 0;
	}, []);

	// === 粘贴处理 ===

	/** 清理粘贴文本（保留换行和空格，只统一换行符和Tab） */
	const cleanPastedText = useCallback((rawText: string): string => {
		return rawText
			.replace(/\r\n/g, '\n')
			.replace(/\r/g, '\n')
			.replace(/\t/g, '    ');
	}, []);

	/** 处理粘贴缓冲完成：一次性插入所有缓存的 chunk */
	const flushPasteBuffer = useCallback(() => {
		const rawText = pasteChunksRef.current.join('');
		pasteChunksRef.current = [];
		setIsPasting(false);

		if (rawText.length === 0) return;

		const cleaned = cleanPastedText(rawText);
		const cursor = Cursor.fromText(originalValue, columns, cursorOffset);
		applyCursor(cursor.insert(cleaned));
	}, [originalValue, cursorOffset, columns, cleanPastedText, applyCursor]);

	/** 检测是否为粘贴输入（多字符且非特殊键） */
	const isPasteInput = useCallback((input: string, key: {
		upArrow?: boolean;
		downArrow?: boolean;
		leftArrow?: boolean;
		rightArrow?: boolean;
		return?: boolean;
		escape?: boolean;
		tab?: boolean;
		ctrl?: boolean;
		meta?: boolean;
	}): boolean => {
		if (key.ctrl || key.meta) return false;
		if (key.upArrow || key.downArrow || key.leftArrow || key.rightArrow) return false;
		if (key.return || key.escape || key.tab) return false;
		// 多字符输入且不是特殊键 = 粘贴
		return input.length > 1;
	}, []);

	/** 将输入加入粘贴缓冲 */
	const addToPasteBuffer = useCallback((input: string) => {
		setIsPasting(true);
		pasteChunksRef.current.push(input);
		if (pasteTimeoutRef.current) {
			clearTimeout(pasteTimeoutRef.current);
		}
		pasteTimeoutRef.current = setTimeout(() => {
			flushPasteBuffer();
		}, PASTE_TIMEOUT_MS);
	}, [flushPasteBuffer]);

	// 组件卸载时清理定时器
	useEffect(() => {
		return () => {
			if (pasteTimeoutRef.current) {
				clearTimeout(pasteTimeoutRef.current);
			}
		};
	}, []);

	// === 键盘处理 ===

	const handleKeyDown = useCallback((input: string, key: Key) => {
		// 粘贴检测：优先处理
		if (isPasteInput(input, key)) {
			addToPasteBuffer(input);
			resetCtrlUCount();
			return;
		}

		// 粘贴期间不处理其他按键（防止 Enter 提交）
		if (isPasting && key.return) {
			return;
		}

		const cursor = Cursor.fromText(originalValue, columns, cursorOffset);

		// Ctrl 组合键
		if (key.ctrl) {
			if (input === 'u') {
				// Ctrl+U: 删除当前显示行（display line）内容，跨行时删 \n 继续
				// 注意：此处不调用 resetCtrlUCount()，计数需要跨多次 Ctrl+U 累积
				ctrlUCountRef.current++;

				// 达到阈值：直接清空全部
				if (ctrlUCountRef.current >= CTRL_U_CLEAR_THRESHOLD) {
					onChange('');
					setCursorOffset(0);
					ctrlUCountRef.current = 0;
					return;
				}

				// 删除当前显示行内容
				const next = cursor.deleteToDisplayLineStart();
				applyCursor(next);
				return;
			}
			if (input === 'a') {
				resetCtrlUCount();
				applyCursor(cursor.startOfLine());
				clearPreservedColumn();
				return;
			}
			if (input === 'e') {
				resetCtrlUCount();
				applyCursor(cursor.endOfLine());
				clearPreservedColumn();
				return;
			}
			// 其他 Ctrl 组合键不处理，让 App 层处理
			return;
		}

		// 聚焦的编辑器拥有导航与 Enter；选择器选中即消费该按键。
		if (onPickerKey?.(key) || key.tab) return;

		// \n (Ctrl+J) 插入换行
		if (input === '\n') {
			resetCtrlUCount();
			applyCursor(cursor.insert('\n'));
			clearPreservedColumn();
			return;
		}

		// Enter (\r) 提交
		if (key.return) {
			resetCtrlUCount();
			onSubmit?.(originalValue);
			return;
		}

		// 上箭头
		if (key.upArrow) {
			if (!showCursor) return;
			resetCtrlUCount();
			// 首次按上下箭头时保存列位置，后续保持该列
			const pos = cursor.getPosition();
			const targetCol = preservedColumn ?? pos.column;
			if (preservedColumn === null) {
				setPreservedColumn(pos.column);
			}
			const next = cursor.up(targetCol);
			if (!next.equals(cursor)) {
				applyCursor(next);
			}
			return;
		}

		// 下箭头
		if (key.downArrow) {
			if (!showCursor) return;
			resetCtrlUCount();
			const pos = cursor.getPosition();
			const targetCol = preservedColumn ?? pos.column;
			if (preservedColumn === null) {
				setPreservedColumn(pos.column);
			}
			const next = cursor.down(targetCol);
			if (!next.equals(cursor)) {
				applyCursor(next);
			}
			return;
		}

		// 左右箭头
		if (key.leftArrow) {
			resetCtrlUCount();
			applyCursor(cursor.left());
			clearPreservedColumn();
			return;
		}
		if (key.rightArrow) {
			resetCtrlUCount();
			applyCursor(cursor.right());
			clearPreservedColumn();
			return;
		}

		// 退格/删除（统一处理：Windows Terminal 的 Backspace 发送 \x7f 被解析为 key.delete）
		if (key.backspace || key.delete) {
			resetCtrlUCount();
			applyCursor(cursor.backspace());
			clearPreservedColumn();
			return;
		}

		// 普通字符输入
		if (input.length > 0) {
			resetCtrlUCount();
			applyCursor(cursor.insert(input));
			clearPreservedColumn();
		}
	}, [originalValue, cursorOffset, columns, showCursor, isPasting,
		preservedColumn, onChange, onSubmit, applyCursor, onPickerKey,
		isPasteInput, addToPasteBuffer, resetCtrlUCount,
		clearPreservedColumn]);

	// Ink 在 passive effect 中订阅。只保留一个监听器并向其提供最后一次
	// 提交的草稿/光标，包括粘贴后的第一个按键。
	const latestKeyDown = useRef(handleKeyDown);
	useLayoutEffect(() => { latestKeyDown.current = handleKeyDown; }, [handleKeyDown]);
	const dispatchKey = useCallback((chunk: string, key: Key) => latestKeyDown.current(chunk, key), []);
	useInput(dispatchKey, {isActive: focus});

	// === 渲染 ===
	// 空 placeholder 处理
	if (originalValue.length === 0 && placeholder) {
		const renderedPlaceholder = showCursor && focus
			? chalk.inverse(placeholder[0] ?? ' ') + chalk.grey(placeholder.slice(1))
			: chalk.grey(placeholder);
		return <Text>{renderedPlaceholder}</Text>;
	}

	// 空文本无 placeholder
	if (originalValue.length === 0) {
		return <Text>{showCursor && focus ? chalk.inverse(' ') : ' '}</Text>;
	}

	// 构建 Cursor 并渲染带视口的文本
	const cursor = Cursor.fromText(originalValue, columns, cursorOffset);
	const startLine = cursor.getViewportStartLine(maxVisibleLines);
	const color = chalk.hex(theme.colors.suggestion);
	const renderedText = cursor.render(' ', startLine, maxVisibleLines, referenceRanges?.length
		? (part, offset) => paintReferences(part, offset, referenceRanges, color)
		: undefined);

	return <Text>{renderedText}</Text>;
}
