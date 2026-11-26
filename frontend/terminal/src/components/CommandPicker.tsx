/**
 * @fileoverview 命令选择器组件
 *
 * 提供命令提示列表和指令执行结果的显示功能。
 * 支持两种模式：
 * - hints: 显示命令提示列表，支持键盘导航和选择
 * - result: 显示指令执行结果
 *
 * @module CommandPicker
 */

import React from 'react';
import {Box, Text} from 'ink';

import {useTheme} from '../theme/ThemeContext.js';
import {slashCategory, type CommandHint} from '../utils/slashCommands.js';

/**
 * 最大可见行数
 * 控制命令提示列表或结果最多显示的行数
 */
const MAX_VISIBLE = 6;

/**
 * 命令选择器模式类型
 * - 'hints': 命令提示模式
 * - 'result': 结果显示模式
 */
type CommandPickerMode = 'hints' | 'result';

/**
 * 命令选择器组件属性
 */
type CommandPickerProps = {
	/** 命令提示列表 */
	hints?: CommandHint[];
	/** 当前选中的索引 */
	selectedIndex?: number;
	/** 结果模式：显示指令执行结果 */
	mode?: CommandPickerMode;
	/** 结果文本内容 */
	result?: string;
	/** 结果类型：'success'（成功）、'error'（错误）、'info'（信息） */
	resultType?: 'success' | 'error' | 'info';
};

/**
 * 命令选择器组件
 *
 * 根据模式显示命令提示列表或指令执行结果。
 * 在提示模式下，支持键盘上下导航、Tab 补全、Enter 选择和 Esc 关闭。
 *
 * @param props - 组件属性
 * @returns 返回命令选择器的 JSX 元素，如果没有内容可显示则返回 null
 */
export function CommandPicker({
	hints,
	selectedIndex,
	mode = 'hints',
	result,
}: CommandPickerProps): React.JSX.Element | null {
	const theme = useTheme();

	// 结果模式：显示指令执行结果
	if (mode === 'result' && result) {
		const lines = result.split('\n');
		const displayLines = lines.slice(0, MAX_VISIBLE);

		return (
			<Box flexDirection="column" marginTop={1} borderStyle="round" borderColor={theme.colors.illusion} paddingX={1}>
				{displayLines.map((line, i) => (
					<Text key={i} color={theme.colors.illusion}>{line}</Text>
				))}
				{lines.length > MAX_VISIBLE ? (
					<Text color={theme.colors.illusion} dimColor>
						{'  '}... +{lines.length - MAX_VISIBLE} more lines
					</Text>
				) : null}
				<Text dimColor>
					<Text color={theme.colors.muted}>esc</Text> dismiss
					<Text> {theme.icons.middleDot} </Text>
					<Text color={theme.colors.muted}>ctrl+o</Text> show full
				</Text>
			</Box>
		);
	}

	// 提示模式：显示命令提示列表
	if (!hints || hints.length === 0) {
		return null;
	}

	const safeSelectedIndex = selectedIndex ?? 0;
	const startIndex = Math.max(
		0,
		Math.min(
			safeSelectedIndex - Math.floor(MAX_VISIBLE / 2),
			hints.length - MAX_VISIBLE,
		),
	);
	const endIndex = Math.min(startIndex + MAX_VISIBLE, hints.length);
	const visible = hints.slice(startIndex, endIndex);

	return (
		<Box flexDirection="column" marginTop={1}>
			{visible.map((hint, vi) => {
				const isSelected = startIndex + vi === safeSelectedIndex;
				return (
					<Box key={hint.name}>
						<Text wrap="truncate-end" color={isSelected ? theme.colors.suggestion : undefined} bold={isSelected} dimColor={!isSelected}>
							{isSelected ? `${theme.icons.pointer} ` : '  '}{hint.name}
						</Text>
						<Box flexShrink={0}>
							<Text dimColor> [{slashCategory(hint)}]</Text>
						</Box>
					</Box>
				);
			})}
		</Box>
	);
}
