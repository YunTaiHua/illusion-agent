/**
 * @fileoverview @ 提及补全选择器
 *
 * terminal 前端的 @ 提及候选菜单，与 web 端补全弹窗对称：
 * - 技能（skills）在前、会话（sessions）居中、文件（files）在后分区展示
 * - ↑↓ 导航、Tab/Enter 选中、Esc 关闭（按键由 App 层统一拦截分发）
 *
 * label 做单行截断：Ink 的自动换行在浮动菜单中易引发布局问题，
 * 每行按「前导指针 + label + [kind] 后缀」计算可用宽度并截断
 * （复用工具预览行/任务行的 truncateToDisplayWidth 范式，CJK 宽度安全）。
 *
 * @module MentionPicker
 */

import React from 'react';
import {Box, Text} from 'ink';

import {truncateToDisplayWidth, WIDTH_SAFETY_EXTRA} from '../utils/markdown.js';
import stringWidth from 'string-width';
import {useTheme} from '../theme/ThemeContext.js';
import {useTerminalSize} from '../hooks/useTerminalSize.js';
import type {FileMentionCandidate} from '../types.js';

/** 菜单最大可见行数（超出内部滚动窗口） */
const MAX_VISIBLE = 8;
/** 前导指针宽度（"→ " 或两格空串） */
const POINTER_WIDTH = 2;
/** 最小 label 宽度（极端窄终端下保底） */
const MIN_LABEL_WIDTH = 8;

/**
 * @ 提及补全选择器属性
 */
type MentionPickerProps = {
	/** 候选列表（技能在前、会话居中、文件在后；顺序即导航顺序） */
	candidates: FileMentionCandidate[];
	/** 当前选中索引 */
	selectedIndex: number;
};

/**
 * @ 提及补全选择器
 *
 * 以浮动列表形式展示当前 @ token 的补全候选，技能/会话/文件分区；
 * label 单行截断不换行；无候选时返回 null。
 *
 * @param props - 组件属性
 * @returns 返回选择器的 JSX 元素
 */
export function MentionPicker({candidates, selectedIndex}: MentionPickerProps): React.JSX.Element | null {
	const theme = useTheme();
	const {columns} = useTerminalSize();

	if (candidates.length === 0) {
		return null;
	}

	const safeIndex = Math.max(0, Math.min(selectedIndex, candidates.length - 1));
	const startIndex = Math.max(
		0,
		Math.min(safeIndex - Math.floor(MAX_VISIBLE / 2), candidates.length - MAX_VISIBLE),
	);
	const endIndex = Math.min(startIndex + MAX_VISIBLE, candidates.length);
	const visible = candidates.slice(startIndex, endIndex);

	// 单行截断预算：终端宽度 - 指针 - " [kind]" 后缀 - 安全余量
	const labelWidth = (c: FileMentionCandidate): number => {
		const suffixWidth = stringWidth(` [${c.kind}]`);
		return Math.max(MIN_LABEL_WIDTH, columns - POINTER_WIDTH - suffixWidth - WIDTH_SAFETY_EXTRA);
	};

	return (
		<Box flexDirection="column" marginTop={1}>
			{visible.map((c, vi) => {
				const actualIndex = startIndex + vi;
				const isSelected = actualIndex === safeIndex;
				const label = truncateToDisplayWidth(c.path, labelWidth(c));
				return (
					<Box key={`${c.kind}:${c.sessionId ?? c.path}`}>
						<Text color={isSelected ? theme.colors.suggestion : theme.colors.muted}>
							{isSelected ? `${theme.icons.pointer} ` : '  '}
						</Text>
						{/* 极简形式：截断后的 label + [skill]/[session]/[dir]/[file] 后缀区分，无图标无描述 */}
						<Text color={isSelected ? theme.colors.suggestion : undefined} bold={isSelected} dimColor={!isSelected}>
							{label}
						</Text>
						<Text dimColor> [{c.kind}]</Text>
					</Box>
				);
			})}
			<Box marginTop={0}>
				<Text dimColor>
					<Text color={theme.colors.muted}>↑↓</Text> navigate
					<Text> {theme.icons.middleDot} </Text>
					<Text color={theme.colors.muted}>↵/tab</Text> select
					<Text> {theme.icons.middleDot} </Text>
					<Text color={theme.colors.muted}>esc</Text> dismiss
				</Text>
			</Box>
		</Box>
	);
}
