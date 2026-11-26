/**
 * @fileoverview 问题导航条组件
 *
 * 在多问题问答模态框顶部展示问题切换标签。每个标签显示问题 header 与已答标记（✓/☐），
 * 当前问题标签高亮。两端有左右箭头，并可选展示"提交"标签。
 *
 * 布局示例：
 *   ←  [✓] Auth method  [ ] Library  [✓] Submit  →
 *
 * @module QuestionNavigationBar
 */

import React from 'react';
import {Box, Text} from 'ink';

import {useTheme} from '../theme/ThemeContext.js';

/**
 * 问题导航条属性
 */
type Props = {
	/** 所有问题的 header（缺失时回退为 Q1/Q2…） */
	headers: string[];
	/** 当前问题索引（等于 headers.length 表示进入复核/提交页） */
	currentQuestionIndex: number;
	/** 已答问题文本集合，用于显示已答标记 */
	answeredHeaders: Set<string>;
	/** 是否隐藏"提交"标签（单问题单选时为 true，答完即直接提交） */
	hideSubmitTab?: boolean;
};

/**
 * 问题导航条组件
 *
 * @param props - 组件属性
 * @returns 导航条 JSX 元素
 */
export function QuestionNavigationBar({
	headers,
	currentQuestionIndex,
	answeredHeaders,
	hideSubmitTab = false,
}: Props): React.JSX.Element {
	const theme = useTheme();
	// 单问题且隐藏提交标签时不显示两端箭头
	const hideArrows = headers.length === 1 && hideSubmitTab;

	return (
		<Box flexDirection="row">
			{/* 左箭头：已在第一题时灰显 */}
			{!hideArrows ? (
				<Text color={currentQuestionIndex === 0 ? theme.colors.muted : undefined}>← </Text>
			) : null}

			{/* 各问题标签 */}
			{headers.map((header, index) => {
				const isSelected = index === currentQuestionIndex;
				const isAnswered = answeredHeaders.has(header);
				const checkbox = isAnswered ? theme.icons.check : '○';
				return (
					<Box key={`${header}-${index}`}>
						{isSelected ? (
							<Text backgroundColor={theme.colors.permission} color={theme.colors.foreground} bold>
								{' '}
								{checkbox} {header}{' '}
							</Text>
						) : (
							<Text>
								{' '}
								{checkbox} {header}{' '}
							</Text>
						)}
					</Box>
				);
			})}

			{/* 提交标签：仅多问题且未隐藏时显示 */}
			{!hideSubmitTab ? (
				<Box>
					{currentQuestionIndex === headers.length ? (
						<Text backgroundColor={theme.colors.permission} color={theme.colors.foreground} bold>
							{' '}
							{theme.icons.check} Submit{' '}
						</Text>
					) : (
						<Text> {theme.icons.check} Submit </Text>
					)}
				</Box>
			) : null}

			{/* 右箭头：已在提交页时灰显 */}
			{!hideArrows ? (
				<Text color={currentQuestionIndex === headers.length ? theme.colors.muted : undefined}> →</Text>
			) : null}
		</Box>
	);
}
