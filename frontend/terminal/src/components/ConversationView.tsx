/**
 * @fileoverview 对话视图组件
 *
 * 显示完整的对话历史，包括：
 * - 用户消息
 * - 助手回复（支持 Markdown 渲染和思考过程显示）
 * - 工具调用及其结果
 * - 系统消息
 * - 计划内容
 * - 流式回复尾部
 * - 待处理工具调用的闪烁指示器
 *
 * @module ConversationView
 */

import React, {useMemo} from 'react';
import {Box, Static, Text} from 'ink';

import {useTerminalSize} from '../hooks/useTerminalSize.js';
import type {UiLanguage} from '../i18n.js';
import {t} from '../i18n.js';
import type {PendingToolCall} from '../types.js';
import type {ThemeConfig} from '../theme/ThemeContext.js';
import {useTheme} from '../theme/ThemeContext.js';
import type {TranscriptItem} from '../types.js';
import {stringWidth, wrapText, isUnifiedDiff} from '../utils/markdown.js';
import {renderAssistantText, stripThinkTags, extractThinkContent, hasThinkTags, stripToolCallArtifacts, mergeReasoning} from '../utils/thinking.js';
import {MarkdownContent} from './MarkdownContent.js';
import {WelcomeBanner} from './WelcomeBanner.js';
import {useBlink} from '../hooks/useBlink.js';
import {getTool} from '../tools/registry.js';

/** 流式尾部最大显示行数 */
const STREAMING_TAIL_LINES = 10;
/** 最小换行宽度 */
const MIN_WRAP_WIDTH = 12;
/** 宽度安全余量 */
const WIDTH_SAFETY_EXTRA = 2;

/**
 * 对话视图组件
 *
 * 使用 Ink 的 Static 组件高效渲染大量历史消息。
 * 支持消息分组（工具调用与结果配对）、自动换行和截断。
 *
 * @param props - 组件属性
 * @param props.staticItems - 静态转录项列表
 * @param props.clearCount - 清空计数器（用于触发重新渲染）
 * @param props.assistantBuffer - 助手流式回复缓冲区
 * @param props.showWelcome - 是否显示欢迎横幅
 * @param props.showThinking - 是否显示思考过程
 * @param props.language - 当前 UI 语言
 * @param props.pendingToolCalls - 待处理的工具调用列表
 * @returns 返回对话视图的 JSX 元素
 */
export function ConversationView({
	staticItems,
	clearCount,
	assistantBuffer,
	showWelcome,
	showThinking,
	language,
	pendingToolCalls,
}: {
	staticItems: TranscriptItem[];
	clearCount: number;
	assistantBuffer: string;
	showWelcome: boolean;
	showThinking: boolean;
	language: UiLanguage;
	pendingToolCalls?: PendingToolCall[];
	commandPickerOpen?: boolean;
}): React.JSX.Element {
	const theme = useTheme();
	const {columns: terminalWidth} = useTerminalSize();
	const filtered = useMemo(() => staticItems.filter((item) => {
		if (!isEmptyItem(item)) {
			if (item.role === 'user' && item.text.startsWith('/')) {
				return false;
			}
			return true;
		}
		return false;
	}), [staticItems]);
	const grouped = useMemo(() => groupToolItems(filtered), [filtered]);
	const displayItems = useMemo<DisplayEntry[]>(() => {
		const entries: GroupEntry[] = showWelcome
			? [{type: 'welcome', role: 'welcome'}, ...grouped]
			: grouped;
		return entries.map((entry, index) => ({
			key: `s-${index}`,
			entry,
			prevRole: index > 0 ? entries[index - 1]?.role : undefined,
		}));
	}, [grouped, showWelcome]);
	const displayedBuffer = assistantBuffer; // 已在 useBackendSession 中处理
	const isSuppressedByStatic = useMemo(() => {
		if (!displayedBuffer) return false;
		const lastAssistant = [...grouped].reverse().find((entry) => entry.role === 'assistant');
		if (!lastAssistant) return false;
		const item = lastAssistant.type === 'single' ? lastAssistant.item : null;
		if (!item) return false;
		const staticDisplayText = renderAssistantText(item.text, showThinking, item.reasoning);
		return isTextSubsetOrEqual(staticDisplayText, displayedBuffer);
	}, [grouped, displayedBuffer, showThinking]);

	return (
		<>
			<Static key={clearCount} items={displayItems}>
				{(display) => {
					const {entry, prevRole, key} = display;
					if (entry.type === 'welcome') {
						return <WelcomeBanner key={key} language={language} />;
					}
					if (entry.type === 'tool_group') {
						return <ToolGroupRow key={key} toolItem={entry.toolItem} resultItem={entry.resultItem} theme={theme} prevRole={prevRole} terminalWidth={terminalWidth} />;
					}
					return <MessageRow key={key} item={entry.item} theme={theme} language={language} prevRole={prevRole} showThinking={showThinking} terminalWidth={terminalWidth} />;
				}}
			</Static>

			{displayedBuffer && !isSuppressedByStatic ? renderStreamingTail(displayedBuffer, grouped, theme, terminalWidth) : null}

			{/* 待处理工具调用指示器 — ● 闪烁表示工具正在执行中 */}
			{pendingToolCalls && pendingToolCalls.length > 0 ? (
				<Box marginTop={displayedBuffer || isSuppressedByStatic ? 0 : 1} flexDirection="column">
					{pendingToolCalls.map((pc) => (
						<BlinkingToolIndicator
							key={pc.tool_use_id}
							pending={pc}
							theme={theme}
							terminalWidth={terminalWidth}
						/>
					))}
				</Box>
			) : null}
		</>
	);
}

function BlinkingToolIndicator({
	pending,
	theme,
	terminalWidth,
}: {
	pending: PendingToolCall;
	theme: ThemeConfig;
	terminalWidth: number;
}): React.JSX.Element {
	const isVisible = useBlink(true);

	const tool = getTool(pending.tool_name);
	const displayName = tool.displayName(pending.tool_input) || pending.tool_name;
	const summary = pending.tool_input
		? tool.renderToolUseMessage(pending.tool_input)
		: '';
	const content = summary ? `${displayName}(${summary})` : displayName;
	const prefix = `${theme.icons.tool} `;
	const continuationPrefix = ' '.repeat(stringWidth(prefix));
	const wrapped = wrapForPrefix(content, terminalWidth, prefix);

	return (
		<Box flexDirection="column">
			{wrapped.map((line, i) => (
				<Box key={i}>
					{i === 0 ? (
						<Text>
							<Text color={theme.colors.info}>
								{isVisible ? theme.icons.tool : ' '}
								{' '}
							</Text>
							<Text bold>{line}</Text>
						</Text>
					) : (
						<Text bold>{continuationPrefix}{line}</Text>
					)}
				</Box>
			))}
			{/* 流式进度消息：混合显示 thinking/text/tool，取最后 3 行，包裹在 ⎿ 前缀中 */}
			{pending.progressMessages && pending.progressMessages.length > 0 ? (
				<AgentProgressLines messages={pending.progressMessages} theme={theme} terminalWidth={terminalWidth} />
			) : null}
		</Box>
	);
}

/**
 * Agent 工具流式进度渲染：混合显示 thinking/text/tool 消息，取最后 3 行。
 * 首行使用 ⎿ 前缀，续行对齐缩进。统一灰调，与最终回复视觉一致。
 * 每行按显示宽度截断为 1 视觉行（与 renderStreamingTail 一致），确保总共恰好 3 行，
 * 避免长行触发终端自动换行导致视觉行数膨胀。
 */
function AgentProgressLines({
	messages,
	theme,
	terminalWidth,
}: {
	messages: Array<{message: string; type?: string}>;
	theme: ThemeConfig;
	terminalWidth: number;
}): React.JSX.Element | null {
	// 合并所有消息的行，保留每行类型
	const allLines: Array<{text: string; type?: string}> = [];
	for (const msg of messages) {
		const lines = msg.message.split('\n').filter(l => l.trim() !== '');
		for (const line of lines) {
			allLines.push({text: line, type: msg.type});
		}
	}
	if (allLines.length === 0) return null;

	// 取最后 3 个逻辑行，每行按显示宽度截断为 1 视觉行，确保总共恰好 3 行。
	// 与 renderStreamingTail 的截断策略一致，避免长行触发终端自动换行导致视觉行数膨胀。
	const tail = allLines.slice(-3);
	const prefix = `  ${theme.icons.resultPrefix} `;
	const continuationPrefix = ' '.repeat(stringWidth(prefix));
	const maxWidth = Math.max(MIN_WRAP_WIDTH, terminalWidth - stringWidth(prefix) - WIDTH_SAFETY_EXTRA);

	return (
		<Box flexDirection="column">
			{tail.map((entry, i) => {
				const truncated = truncateToDisplayWidth(entry.text, maxWidth);
				return (
					<Box key={i}>
						<Text dimColor>{i === 0 ? prefix : continuationPrefix}</Text>
						<Text dimColor>{truncated}</Text>
					</Box>
				);
			})}
		</Box>
	);
}

function isEmptyItem(item: TranscriptItem): boolean {
	if (item.role === 'assistant' && (!item.text || item.text.trim() === '') && (!item.reasoning || item.reasoning.trim() === '')) {
		return true;
	}
	if (item.role === 'assistant_streaming' && (!item.text || item.text.trim() === '')) {
		return true;
	}
	if (item.role === 'tool' && (!item.text || item.text.trim() === '') && !item.tool_name) {
		return true;
	}
	return false;
}

type GroupEntry =
	| {type: 'single'; item: TranscriptItem; role: string}
	| {type: 'tool_group'; toolItem: TranscriptItem; resultItem: TranscriptItem | null; role: string}
	| {type: 'welcome'; role: string};

type DisplayEntry = {
	key: string;
	entry: GroupEntry;
	prevRole?: string;
};

function groupToolItems(items: TranscriptItem[]): GroupEntry[] {
	const usedResults = new Set<number>();
	const matchedResult = new Map<number, TranscriptItem>();
	// 第一轮：匹配 tool 与 tool_result
	for (let i = 0; i < items.length; i++) {
		const item = items[i];
		if (item.role !== 'tool') continue;
		if (item.tool_use_id) {
			for (let j = i + 1; j < items.length; j++) {
				if (items[j].role === 'tool_result' && items[j].tool_use_id === item.tool_use_id && !usedResults.has(j)) {
					matchedResult.set(i, items[j]);
					usedResults.add(j);
					break;
				}
			}
		}
		if (!matchedResult.has(i)) {
			for (let j = i + 1; j < items.length; j++) {
				if (items[j].role === 'tool_result' && items[j].tool_name === item.tool_name && !usedResults.has(j)) {
					matchedResult.set(i, items[j]);
					usedResults.add(j);
					break;
				}
			}
		}
	}
	// 第二轮：检测 replay 模式（tool 与 result 不相邻）并重排序
	// replay 时所有 tool 在前、所有 result 在后，需要将 result 移到对应 tool 后面
	// 正常流程中 tool 和 result 已相邻，无需重排
	const hasReplayPattern = items.some((item, i) => {
		if (item.role !== 'tool' || !matchedResult.has(i)) return false;
		// 检查下一个 item 是否是对应的 result
		const next = items[i + 1];
		return !next || next.role !== 'tool_result' || next.tool_use_id !== item.tool_use_id;
	});
	if (hasReplayPattern) {
		// 重排序：每个 tool 后面紧跟其 result
		const reordered: TranscriptItem[] = [];
		const unmatchedResults: TranscriptItem[] = [];
		for (let i = 0; i < items.length; i++) {
			const item = items[i];
			if (item.role === 'tool') {
				reordered.push(item);
				const res = matchedResult.get(i);
				if (res) reordered.push(res);
			} else if (item.role === 'tool_result' && usedResults.has(i)) {
				unmatchedResults.push(item); // 已在对应 tool 后面渲染，跳过原位
			} else {
				reordered.push(item);
			}
		}
		return groupToolItemsOrdered(reordered);
	}
	return groupToolItemsOrdered(items);
}

function groupToolItemsOrdered(items: TranscriptItem[]): GroupEntry[] {
	const result: GroupEntry[] = [];
	const usedResults = new Set<number>();
	const resultToTool = new Map<number, number>();
	let i = 0;
	while (i < items.length) {
		const item = items[i];
		if (item.role === 'tool') {
			let resultItem: TranscriptItem | null = null;
			if (item.tool_use_id) {
				for (let j = i + 1; j < items.length; j++) {
					if (items[j].role === 'tool_result' && items[j].tool_use_id === item.tool_use_id && !usedResults.has(j)) {
						resultItem = items[j];
						usedResults.add(j);
						resultToTool.set(j, i);
						break;
					}
				}
			}
			if (!resultItem) {
				for (let j = i + 1; j < items.length; j++) {
					if (items[j].role === 'tool_result' && items[j].tool_name === item.tool_name && !usedResults.has(j)) {
						resultItem = items[j];
						usedResults.add(j);
						resultToTool.set(j, i);
						break;
					}
				}
			}
			result.push({type: 'tool_group', toolItem: item, resultItem, role: 'tool'});
			i += 1;
			continue;
		}
		if (item.role === 'tool_result' && usedResults.has(i)) {
			const toolIdx = resultToTool.get(i)!;
			let hasConcurrentTool = false;
			for (let k = toolIdx + 1; k < i; k++) {
				if (items[k].role === 'tool') {
					hasConcurrentTool = true;
					break;
				}
			}
			if (!hasConcurrentTool) {
				result.push({type: 'single', item, role: 'tool_result'});
			}
			i += 1;
			continue;
		}
		result.push({type: 'single', item, role: item.role});
		i += 1;
	}
	return result;
}

function ToolGroupRow({
	toolItem,
	resultItem,
	theme,
	prevRole,
	terminalWidth,
}: {
	toolItem: TranscriptItem;
	resultItem: TranscriptItem | null;
	theme: ThemeConfig;
	prevRole?: string;
	terminalWidth: number;
}): React.JSX.Element {
	const toolName = toolItem.tool_name ?? 'tool';
	const tool = getTool(toolName);
	const displayName = tool.displayName(toolItem.tool_input) || toolName;
	const summary = tool.renderToolUseMessage(toolItem.tool_input);
	const needsGap = prevRole !== undefined && prevRole !== 'tool' && prevRole !== 'tool_result';
	const prefix = `${theme.icons.tool} `;
	const continuationPrefix = ' '.repeat(stringWidth(prefix));
	const content = summary ? `${displayName}(${summary})` : displayName;
	const wrapped = wrapForPrefix(content, terminalWidth, prefix);

	return (
		<Box flexDirection="column" marginTop={needsGap ? 1 : 0}>
			{wrapped.map((line, i) => (
				<Box key={i}>
					{i === 0 ? (
						<Text>
							<Text color={theme.colors.info}>{prefix}</Text>
							<Text bold>{line}</Text>
						</Text>
					) : (
						<Text>{continuationPrefix}{line}</Text>
					)}
				</Box>
			))}
		</Box>
	);
}

/**
 * 将多行文本按行渲染，每行独立 Box 以正确处理换行和截断
 */
function ToolResultBlock({
	item,
	theme,
	terminalWidth,
}: {
	item: TranscriptItem;
	theme: ThemeConfig;
	terminalWidth: number;
}): React.JSX.Element {
	const MAX_RESULT_LINES = 15;

	// 使用工具专用渲染器获取摘要文本，回退到 item.text
	const tool = getTool(item.tool_name ?? 'tool');
	const rendered = tool.renderToolResultMessage(item.text, item.tool_input, false, item.structured_output);
	const displayText = rendered || item.text;

	const lines = displayText.split('\n').filter((l) => l.trim() !== '');
	const truncated = lines.length > MAX_RESULT_LINES;
	const display = truncated
		? [...lines.slice(0, MAX_RESULT_LINES), `… +${lines.length - MAX_RESULT_LINES} lines`]
		: lines;

	if (display.length === 0) {
		return (
			<Box>
				<Text dimColor>{`  ${theme.icons.resultPrefix} `}</Text>
				<Text color={theme.colors.success}>{theme.icons.check}</Text>
			</Box>
		);
	}

	const isError = item.is_error;
	const icon = isError ? theme.icons.cross : theme.icons.check;
	const iconColor = isError ? theme.colors.error : theme.colors.success;
	const firstPrefix = `  ${theme.icons.resultPrefix} ${icon} `;
	const firstPrefixText = `  ${theme.icons.resultPrefix} `;
	const continuationPrefix = '      ';
	const firstWidth = Math.max(MIN_WRAP_WIDTH, terminalWidth - stringWidth(firstPrefix) - WIDTH_SAFETY_EXTRA);
	const continuationWidth = Math.max(MIN_WRAP_WIDTH, terminalWidth - stringWidth(continuationPrefix) - WIDTH_SAFETY_EXTRA);
	// 仅当结果为统一 diff 时才按 + / - / @@ 着色，避免把无序列表等普通文本误染
	const isDiff = isUnifiedDiff(lines);

	return (
		<Box flexDirection="column">
			{display.map((line, i) => {
				let lineColor: string | undefined = undefined;
				let lineDim = !isError;
				if (isDiff) {
					const trimmedLine = line.trimStart();
					if (trimmedLine.startsWith('+') && !trimmedLine.startsWith('+++')) {
						lineColor = theme.colors.success;
						lineDim = false;
					} else if (trimmedLine.startsWith('-') && !trimmedLine.startsWith('---')) {
						lineColor = theme.colors.error;
						lineDim = false;
					} else if (trimmedLine.startsWith('@@')) {
						lineColor = theme.colors.info;
						lineDim = false;
					}
				}
				const width = i === 0 ? firstWidth : continuationWidth;
				const displayLine = truncateToDisplayWidth(line, width);
				const showLeadingIcon = i === 0;

				return (
					<Box key={i}>
						<Text dimColor>{showLeadingIcon ? firstPrefixText : continuationPrefix}</Text>
						{showLeadingIcon ? (
							<Text color={iconColor}>{icon} </Text>
						) : null}
						<Text color={isError ? theme.colors.error : lineColor} dimColor={isError ? false : lineDim}>
							{displayLine}
						</Text>
					</Box>
				);
			})}
		</Box>
	);
}

function MessageRow({
	item,
	theme,
	language,
	prevRole,
	showThinking = true,
	terminalWidth,
}: {
	item: TranscriptItem;
	theme: ThemeConfig;
	language: UiLanguage;
	prevRole?: string;
	showThinking?: boolean;
	terminalWidth: number;
}): React.JSX.Element {
	switch (item.role) {
		case 'user': {
			const needsDivider = prevRole !== 'user';
			const prefix = `${theme.icons.pointer} `;
			const continuationPrefix = ' '.repeat(stringWidth(prefix));
			const wrapped = wrapForPrefix(item.text, terminalWidth, prefix);
			return (
				<Box flexDirection="column" marginTop={needsDivider ? 1 : 0}>
					{needsDivider ? (
						<Box marginBottom={0}>
							<Text color={theme.colors.text}>{' '}{'─'.repeat(60)}</Text>
						</Box>
					) : null}
					{wrapped.map((line, i) => (
						<Box key={i}>
							{i === 0 ? (
								<Text>
									<Text color={theme.colors.illusion}>{theme.icons.pointer}</Text>
									<Text bold>{' '}{line}</Text>
								</Text>
							) : (
								<Text bold>{continuationPrefix}{line}</Text>
							)}
						</Box>
					))}
				</Box>
			);
		}

		case 'assistant': {
				const sanitized = stripToolCallArtifacts(item.text);
				const hasTags = hasThinkTags(sanitized);
				let cleanText = sanitized;
				let thinkFromTags = '';
				if (hasTags) {
					thinkFromTags = extractThinkContent(sanitized);
					cleanText = stripThinkTags(sanitized);
				}
				const reasoning = showThinking ? mergeReasoning(item.reasoning, thinkFromTags) : '';
				return (
					<Box flexDirection="column">
						{reasoning ? renderReasoningBlock(reasoning, theme, t(language, 'reasoning'), terminalWidth) : null}
						{renderAssistantBlock(cleanText, theme, terminalWidth, t(language, 'assistantReply'))}
					</Box>
				);
			}

		case 'assistant_streaming': {
				const isFirst = prevRole !== 'assistant_streaming';
				if (isFirst) {
					return (
						<Box marginTop={1}>
							<Text color={theme.colors.illusion}>{theme.icons.assistant}</Text>
							<Box marginLeft={1} flexGrow={1}>
								<Text>{item.text}</Text>
							</Box>
						</Box>
					);
				}
				return (
					<Box marginLeft={2}>
						<Text>{item.text}</Text>
					</Box>
				);
			}

		case 'tool_result': {
			return <ToolResultBlock item={item} theme={theme} terminalWidth={terminalWidth} />;
		}

		case 'system': {
			if (!item.text.trim()) {
				return null;
			}
			const sysLines = item.text.split('\n');
			const firstLine = sysLines[0];
			const restLines = sysLines.slice(1);
			return (
				<Box marginTop={1} flexDirection="column">
					<Text>
						<Text color={theme.colors.warning} italic>{theme.icons.system}</Text>
						<Text color={theme.colors.muted} italic>{' '}{firstLine}</Text>
					</Text>
					{restLines.map((line, idx) => (
						<Box key={idx} marginLeft={2}>
							<Text color={theme.colors.muted} italic>{line}</Text>
						</Box>
					))}
				</Box>
			);
		}

		case 'plan':
			return (
				<Box marginTop={1} flexDirection="column" borderStyle="round" borderColor={theme.colors.info} paddingX={1}>
					<Box marginBottom={0}>
						<Text bold color={theme.colors.info}>{t(language, 'planReview')}</Text>
					</Box>
					<Box flexDirection="column">
						<MarkdownContent text={item.text} availableWidth={Math.max(40, terminalWidth - 6)} />
					</Box>
				</Box>
			);

		case 'log':
			return (
				<Box>
					<Text dimColor>{item.text}</Text>
				</Box>
			);

		default:
			return (
				<Box>
					<Text>{item.text}</Text>
				</Box>
			);
	}
}

function renderAssistantBlock(text: string, theme: ThemeConfig, terminalWidth: number, label: string): React.JSX.Element | null {
	if (!text) return null;

	return (
		<Box marginTop={1} flexDirection="column">
			<Box>
				<Text color={theme.colors.illusion}>{theme.icons.assistant}</Text>
				<Box marginLeft={1} flexGrow={1}>
					<Text>{'(' + label + ')'}</Text>
				</Box>
			</Box>
			<Box marginLeft={2} flexDirection="column">
				<MarkdownContent text={text} availableWidth={Math.max(MIN_WRAP_WIDTH, terminalWidth - 2 - WIDTH_SAFETY_EXTRA)} />
			</Box>
		</Box>
	);
}


function renderReasoningBlock(text: string, theme: ThemeConfig, label: string, terminalWidth: number): React.JSX.Element | null {
	if (!text.trim()) return null;

	return (
		<Box marginTop={1} flexDirection="column">
			<Box>
				<Text color={theme.colors.muted}>● ({label})</Text>
			</Box>
			<Box marginLeft={2} flexDirection="column">
				<MarkdownContent
					text={text}
					style={{color: theme.colors.muted}}
					availableWidth={Math.max(MIN_WRAP_WIDTH, terminalWidth - 2 - WIDTH_SAFETY_EXTRA)}
				/>
			</Box>
		</Box>
	);
}

function renderStreamingTail(
	text: string,
	grouped: GroupEntry[],
	theme: ThemeConfig,
	terminalWidth: number,
): React.JSX.Element {
	// 过滤空行以防止显示没有文本的金色 ●
	const allLines = text.split('\n');
	const lines = allLines.filter(l => l.trim() !== '');
	if (lines.length === 0) return <Box />;

	const hasOverflow = lines.length > STREAMING_TAIL_LINES;
	const tailCount = hasOverflow ? STREAMING_TAIL_LINES - 1 : STREAMING_TAIL_LINES;
	const tailLines = lines.slice(-tailCount);

	const lastStaticRole = grouped.length > 0 ? grouped[grouped.length - 1].role : undefined;
	const showIcon = lastStaticRole !== 'assistant' && lastStaticRole !== 'assistant_streaming';

	return (
		<Box marginTop={1} flexDirection="column">
			{lines.length > STREAMING_TAIL_LINES ? (
				<Box marginLeft={2}>
					<Text dimColor>… {lines.length - STREAMING_TAIL_LINES} lines above</Text>
				</Box>
			) : null}
			{tailLines.map((line, i) => {
				const isFirst = i === 0 && showIcon;
				const prefixWidth = isFirst ? stringWidth(`${theme.icons.assistant} `) : 2;
				const maxWidth = Math.max(MIN_WRAP_WIDTH, terminalWidth - prefixWidth - WIDTH_SAFETY_EXTRA);
				const truncated = truncateToDisplayWidth(line, maxWidth);
				return (
					<Box key={i} marginLeft={isFirst ? 0 : 2}>
						{isFirst ? (
							<>
								<Text color={theme.colors.illusion}>{theme.icons.assistant}</Text>
								<Box marginLeft={1} flexGrow={1}>
									<Text>{truncated}</Text>
								</Box>
							</>
						) : (
							<Text>{truncated}</Text>
						)}
					</Box>
				);
			})}
		</Box>
	);
}

function wrapForPrefix(text: string, terminalWidth: number, prefix: string): string[] {
	const availableWidth = Math.max(MIN_WRAP_WIDTH, terminalWidth - stringWidth(prefix) - WIDTH_SAFETY_EXTRA);
	const sourceLines = text.split('\n');
	const wrapped: string[] = [];
	for (const source of sourceLines) {
		const segments = wrapText(source, availableWidth, {hard: true});
		if (segments.length === 0) {
			wrapped.push('');
			continue;
		}
		wrapped.push(...segments);
	}
	return wrapped.length > 0 ? wrapped : [''];
}

function truncateToDisplayWidth(text: string, maxWidth: number): string {
	// 将 tab 替换为空格，因为 string-width 将 tab 算作1字符宽度，
	// 但终端中 tab 会展开为多个空格，导致宽度计算偏小、截断不足
	const expanded = text.replace(/\t/g, '        ');
	if (stringWidth(expanded) <= maxWidth) {
		return expanded;
	}
	let result = '';
	let width = 0;
	for (const ch of expanded) {
		const charWidth = stringWidth(ch);
		if (width + charWidth > Math.max(1, maxWidth - 1)) {
			break;
		}
		result += ch;
		width += charWidth;
	}
	return result + '…';
}

function normalizeTextForCompare(raw: string): string {
	return raw.replace(/\s+/g, ' ').trim();
}

function isTextSubsetOrEqual(a: string, b: string): boolean {
	const normA = normalizeTextForCompare(a);
	const normB = normalizeTextForCompare(b);
	if (!normA || !normB) return false;
	return normA === normB || normA.includes(normB) || normB.includes(normA);
}

