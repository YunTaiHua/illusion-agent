/**
 * @fileoverview 提示输入组件
 *
 * 终端前端的用户输入组件，包含：
 * - 加载动画（忙碌时显示）
 * - 多行文本输入框
 * - 输入内容清理（移除回车符）
 * - 占位符提示
 *
 * @module PromptInput
 */

import React from 'react';
import {Box} from 'ink';

import type {UiLanguage} from '../i18n.js';
import {t} from '../i18n.js';
import {useTheme} from '../theme/ThemeContext.js';
import {useTerminalSize} from '../hooks/useTerminalSize.js';
import {Spinner} from './Spinner.js';
import MultilineTextInput from './MultilineTextInput.js';
import type {TodoItemSnapshot} from '../types.js';

/** 空操作函数，用于禁用提交 */
function noop(): void {}

/**
 * 提示输入组件
 *
 * 终端前端的用户输入组件。
 *
 * @param props - 组件属性
 * @param props.busy - 是否忙碌
 * @param props.stopping - 停止请求已发送、等待后端终止确认（显示"正在停止"旋转动画）
 * @param props.input - 当前输入内容
 * @param props.setInput - 设置输入内容的回调
 * @param props.onSubmit - 提交回调
 * @param props.toolName - 当前工具名称（可选，用于加载动画显示）
 * @param props.suppressSubmit - 是否禁用提交（可选，用于命令选择器打开时）
 * @param props.inputFocus - 输入框是否聚焦（可选，默认 true；goal 操作模式时失焦防串键）
 * @param props.cursorReset - 光标重置计数器（可选，用于重置光标位置）
 * @param props.language - 当前 UI 语言
 * @param props.todoItems - 待办事项列表（可选，用于加载动画显示）
 * @returns 返回提示输入的 JSX 元素
 */
export function PromptInput({
	busy,
	stopping,
	input,
	setInput,
	onSubmit,
	toolName,
	suppressSubmit,
	inputFocus = true,
	cursorReset,
	language,
	todoItems,
	onCursorChange,
	suppressNavigation,
	initialCursorOffset,
}: {
	busy: boolean;
	stopping?: boolean;
	input: string;
	setInput: (value: string) => void;
	onSubmit: (value: string) => void;
	toolName?: string;
	suppressSubmit?: boolean;
	inputFocus?: boolean;
	cursorReset?: number;
	language: UiLanguage;
	todoItems?: TodoItemSnapshot[];
	/** 光标位置变化回调（@ 提及 token 检测依赖光标位置） */
	onCursorChange?: (offset: number) => void;
	/** 为 true 时忽略 ↑↓/Enter/Tab（补全菜单打开时按键交由菜单导航） */
	suppressNavigation?: boolean;
	/** 重挂载时光标初始位置（@ 提及插入后定位到插入点之后） */
	initialCursorOffset?: number;
}): React.JSX.Element {
	const theme = useTheme();
	const {columns} = useTerminalSize();

	// 四边圆角框：边框2列 + padding 2列 + 光标预留1列 + 安全余量1列 = 6列
	const inputColumns = Math.max(10, columns - 6);

	return (
		<Box flexDirection="column" marginTop={1}>
			{busy || stopping ? (
				<Box marginBottom={1}>
					{stopping ? (
						// 停止请求已发出、等待后端确认（终止可能延迟 1-2s）：旋转动画反馈
						<Spinner label={t(language, 'stoppingTask')} language={language} />
					) : (
						<Spinner todoItems={todoItems} language={language} toolName={toolName} />
					)}
				</Box>
			) : null}
			<Box borderStyle="round" borderColor={theme.colors.promptBorder} paddingLeft={1} paddingRight={1}>
			<MultilineTextInput
				key={cursorReset ?? 0}
				value={input}
				onChange={setInput}
				onSubmit={suppressSubmit ? noop : onSubmit}
				placeholder={t(language, 'longTextHint')}
				focus={!busy && inputFocus}
				columns={inputColumns}
				onCursorChange={onCursorChange}
				suppressNavigation={suppressNavigation}
				initialCursorOffset={initialCursorOffset}
			/>
		</Box>
		</Box>
	);
}
