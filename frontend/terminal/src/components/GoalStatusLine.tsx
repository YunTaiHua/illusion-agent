/**
 * Goal 状态行组件
 *
 * 当会话存在未完成 goal 时替代底部 Shimmer（Spinner），两行结构：
 *   第一行：spinner 帧 + 相位标签 + SESSION ID
 *   （沿用 Spinner 的自然追加位置）
 *   第二行：目标文本单行截断
 * 轮次进度（round N/M）由 StatusBar 的 GoalIndicator 呈现，本组件不重复显示。
 * 动画节奏与 Spinner 一致（220ms 帧轮换 / 800ms 呼吸点）。
 *
 * @module GoalStatusLine
 */

import React, {useEffect, useState} from 'react';
import {Box, Text} from 'ink';

import type {UiLanguage} from '../i18n.js';
import {t} from '../i18n.js';
import {useTheme} from '../theme/ThemeContext.js';
import type {GoalStatus} from '../types.js';

/** 目标文本截断长度（单行显示，过长以 … 截断） */
const OBJECTIVE_MAX_CHARS = 80;

/**
 * Goal 状态行组件
 *
 * @param props - 组件属性
 * @param props.goal - 当前 goal 快照（complete 时调用方应回退 Spinner）
 * @param props.language - 当前 UI 语言（可选）
 * @param props.sessionId - 会话 ID（可选）
 * @returns 返回状态行的 JSX 元素
 */
export function GoalStatusLine({goal, language, sessionId}: {goal: GoalStatus; language?: UiLanguage; sessionId?: string}): React.JSX.Element {
	const theme = useTheme();
	const frames = theme.icons.spinner;
	const [frame, setFrame] = useState(0);
	const [dotCount, setDotCount] = useState(0);

	// 涟漪图标轮换（与 Spinner 同节奏）
	useEffect(() => {
		const timer = setInterval(() => {
			setFrame((f) => (f + 1) % frames.length);
		}, 220);
		return () => clearInterval(timer);
	}, [frames.length]);

	// 省略号呼吸动画：· → ·· → ··· → (空) → ·
	useEffect(() => {
		const timer = setInterval(() => {
			setDotCount((d) => (d + 1) % 4);
		}, 800);
		return () => clearInterval(timer);
	}, []);

	// 相位标签
	const phaseLabel = goal.phase === 'active'
		? (language ? t(language, 'goalPhaseActive') : 'Ongoing Goal')
		: goal.phase === 'paused'
			? (language ? t(language, 'goalPhasePaused') : 'Paused Goal')
			: (language ? t(language, 'goalPhaseBlocked') : 'Blocked Goal');

	// 目标单行截断
	const objective = goal.objective.length > OBJECTIVE_MAX_CHARS
		? goal.objective.slice(0, OBJECTIVE_MAX_CHARS - 1) + '…'
		: goal.objective;

	const dots = dotCount > 0 ? '·'.repeat(dotCount) : '';

	return (
		<Box flexDirection="column">
			<Box>
				<Box width={2}>
					<Text color={theme.colors.illusionShimmer}>{frames[frame]}</Text>
				</Box>
				<Text color={theme.colors.illusionShimmer}>{phaseLabel}</Text>
				<Box width={5}>
					<Text color={theme.colors.illusionShimmer}> {dots}</Text>
				</Box>
				{sessionId ? <Text color={theme.colors.muted} dimColor>(SESSION ID = {sessionId})</Text> : null}
			</Box>
			<Box marginTop={0}>
				<Box width={2} />
				<Text color={theme.colors.illusionShimmer}>{objective}</Text>
			</Box>
		</Box>
	);
}
