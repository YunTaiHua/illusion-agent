/**
 * @fileoverview 选择模态对话框组件
 *
 * 提供通用的选择列表界面，支持：
 * - 键盘上下导航
 * - 当前选项高亮
 * - 选项描述显示
 * - 活跃状态标记
 *
 * @module SelectModal
 */

import React from 'react';
import {Box, Text} from 'ink';

import type {UiLanguage} from '../i18n.js';
import {t} from '../i18n.js';
import {useTerminalSize} from '../hooks/useTerminalSize.js';
import {useTheme} from '../theme/ThemeContext.js';
import {stringWidth, wrapForPrefix} from '../utils/markdown.js';

/**
 * 选择选项类型
 */
export type SelectOption = {
	/** 选项值 */
	value: string;
	/** 显示标签 */
	label: string;
	/** 选项描述（可选） */
	description?: string;
	/** 是否为当前活跃选项（可选） */
	active?: boolean;
};

/** 最大可见选项数 */
const MAX_VISIBLE = 6;

/**
 * 选择模态对话框组件
 *
 * 显示一个可导航的选择列表，用于权限模式选择、语言切换等场景。
 *
 * @param props - 组件属性
 * @param props.title - 对话框标题
 * @param props.options - 选项列表
 * @param props.selectedIndex - 当前选中的索引
 * @param props.language - 当前 UI 语言
 * @returns 返回选择模态对话框的 JSX 元素
 */
export function SelectModal({
	title,
	options,
	selectedIndex,
	language,
}: {
	title: string;
	options: SelectOption[];
	selectedIndex: number;
	language: UiLanguage;
}): React.JSX.Element {
	const theme = useTheme();
	const {columns: terminalWidth} = useTerminalSize();

	const startIndex = Math.max(
		0,
		Math.min(
			selectedIndex - Math.floor(MAX_VISIBLE / 2),
			options.length - MAX_VISIBLE,
		),
	);
	const endIndex = Math.min(startIndex + MAX_VISIBLE, options.length);
	const visible = options.slice(startIndex, endIndex);

	return (
		<Box flexDirection="column" marginTop={1}>
			<Box>
				<Text color={theme.colors.permission}>{theme.icons.pointer} </Text>
				<Text bold>{title}</Text>
			</Box>
			{visible.map((opt, vi) => {
				const i = startIndex + vi;
				const isSelected = i === selectedIndex;
				const isCurrent = opt.active;
				// 前缀（合并为一个字符串，避免 Ink 压缩空白）
				const prefix = isSelected ? `${theme.icons.pointer} ` : '  ';
				const prefixWidth = stringWidth(prefix);
				const continuationPrefix = ' '.repeat(prefixWidth);
				// content = label + activeSuffix + separator + description
				const activeSuffix = isCurrent ? ` (${t(language, 'currentMark')})` : '';
				const sep = opt.description ? ` ${theme.icons.middleDot} ` : '';
				const content = `${opt.label}${activeSuffix}${sep}${opt.description ?? ''}`;
				const wrapped = wrapForPrefix(content, terminalWidth, prefix);
				const splitAt = opt.label.length + activeSuffix.length;
				return (
					<Box key={opt.value} flexDirection="column">
						{wrapped.map((line, li) => (
							<Box key={li}>
								{li === 0 ? (
									<Text>
										<Text color={isSelected ? theme.colors.suggestion : theme.colors.muted} bold={isSelected}>
											{prefix}{line.slice(0, splitAt)}
										</Text>
										{line.length > splitAt ? (
											<Text dimColor>{line.slice(splitAt)}</Text>
										) : null}
									</Text>
								) : (
									<Text dimColor>{continuationPrefix}{line}</Text>
								)}
							</Box>
						))}
					</Box>
				);
			})}
			<Box>
				<Text dimColor>
					<Text color={theme.colors.muted}>↑↓</Text> {t(language, 'permNavHint')}
					<Text> {theme.icons.middleDot} </Text>
					<Text color={theme.colors.muted}>↵</Text> {t(language, 'permSelectHint')}
					<Text> {theme.icons.middleDot} </Text>
					<Text color={theme.colors.muted}>esc</Text> {t(language, 'permCancelHint')}
				</Text>
			</Box>
		</Box>
	);
}
