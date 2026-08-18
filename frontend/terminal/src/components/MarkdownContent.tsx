/**
 * @fileoverview Markdown 内容渲染组件
 *
 * 将 Markdown 文本渲染为终端可显示的 Ink 组件。
 * 支持的 Markdown 特性包括：
 * - 标题（h1-h6）
 * - 粗体、斜体、删除线
 * - 行内代码和代码块
 * - 链接和图片
 * - 列表（有序和无序）
 * - 引用块
 * - 表格
 * - 水平线
 * - HTML 标签（kbd、sub、sup）
 * - 上标语法（^text^）
 *
 * @module MarkdownContent
 */

import {lexer, Lexer} from 'marked';
import React, {type ReactNode, useMemo} from 'react';
import {Box, Text} from 'ink';
import type {Token, Tokens} from 'marked';
import {MarkdownTable} from './MarkdownTable.js';
import type {ThemeConfig} from '../theme/ThemeContext.js';
import {useTheme} from '../theme/ThemeContext.js';
import {useTerminalSize} from '../hooks/useTerminalSize.js';
import {stringWidth, padAligned, wrapText, isUnifiedDiff} from '../utils/markdown.js';

/** 行内代码颜色 */
const INLINE_CODE_COLOR = '#b1b9f9';

/**
 * HTML 标签颜色映射
 * 定义特定 HTML 标签的渲染颜色
 */
const HTML_TAG_COLORS: Record<string, string | undefined> = {
	kbd: '#56d4dd',
	sub: undefined,
	sup: undefined,
};

/** HTML 标签匹配正则表达式 */
const HTML_TAG_RE = /^<(\/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>/;

/**
 * 预处理 ^上标^ 语法：在 lexer 之前转换为 <sup> 标签
 * 正则要求 ^ 前面不是字母/数字（避免 x^2 等数学表达式被误匹配），
 * 且 ^ 后第一个字符不能是空白或 ^（避免 ^2 + y^ 等误匹配）
 */
const _originalLex = Lexer.prototype.lex;
Lexer.prototype.lex = function (src: string) {
	src = src.replace(/(?<![a-zA-Z0-9])\^([^\s^][^\^]*?)\^(?!\^)/g, '<sup>$1</sup>');
	return _originalLex.call(this, src);
};

/**
 * Markdown 渲染样式类型
 */
type MarkdownRenderStyle = {
	/** 文本颜色 */
	color?: string;
	/** 是否斜体 */
	italic?: boolean;
};

/**
 * 命名颜色 RGB 值映射
 * 将颜色名称映射到 RGB 值数组
 */
const NAMED_COLORS: Record<string, [number, number, number]> = {
	black: [0, 0, 0], red: [205, 0, 0], green: [0, 205, 0], yellow: [205, 205, 0],
	blue: [0, 0, 238], magenta: [205, 0, 205], cyan: [0, 205, 205], white: [229, 229, 229],
	gray: [128, 128, 128], grey: [128, 128, 128],
};

function colorToAnsi(color: string): string {
	if (color.startsWith('#')) {
		const r = parseInt(color.slice(1, 3), 16);
		const g = parseInt(color.slice(3, 5), 16);
		const b = parseInt(color.slice(5, 7), 16);
		return `38;2;${r};${g};${b}`;
	}
	const rgb = NAMED_COLORS[color.toLowerCase()];
	if (rgb) return `38;2;${rgb[0]};${rgb[1]};${rgb[2]}`;
	return '39';
}

function renderInline(
	tokens: Token[] | undefined,
	theme: ThemeConfig,
	prefix: string,
	style?: MarkdownRenderStyle,
): ReactNode[] {
	if (!tokens || tokens.length === 0) return [];
	const result: ReactNode[] = [];

	for (let i = 0; i < tokens.length; i++) {
		const t = tokens[i];
		const k = `${prefix}-${i}`;

		switch (t.type) {
			case 'strong': {
				const st = t as Tokens.Strong;
				result.push(
					<Text key={k} bold color={style?.color} italic={style?.italic}>{renderInline(st.tokens, theme, k, style)}</Text>,
				);
				break;
			}
			case 'em': {
				const et = t as Tokens.Em;
				result.push(
					<Text key={k} italic color={style?.color}>{renderInline(et.tokens, theme, k, style)}</Text>,
				);
				break;
			}
			case 'codespan': {
				const ct = t as Tokens.Codespan;
				result.push(
					<Text key={k} color={style?.color ?? INLINE_CODE_COLOR} italic={style?.italic}>{ct.text}</Text>,
				);
				break;
			}
			case 'link': {
				const lt = t as Tokens.Link;
				result.push(
					<Text key={k} color={style?.color ?? theme.colors.info} underline italic={style?.italic}>
						{renderInline(lt.tokens, theme, k, style)}
					</Text>,
				);
				break;
			}
			case 'text': {
				const tt = t as Tokens.Text;
				if (tt.tokens && tt.tokens.length > 0) {
					result.push(...renderInline(tt.tokens, theme, k, style));
				} else {
					result.push(<Text key={k} color={style?.color} italic={style?.italic}>{tt.raw ?? tt.text}</Text>);
				}
				break;
			}
			case 'escape': {
				result.push(<Text key={k} color={style?.color} italic={style?.italic}>{t.text}</Text>);
				break;
			}
			case 'br': {
				result.push(<Text key={k} color={style?.color} italic={style?.italic}>{'\n'}</Text>);
				break;
			}
			case 'del': {
				const dt = t as Tokens.Del;
				result.push(
					<Text key={k} strikethrough color={style?.color} italic={style?.italic}>{renderInline(dt.tokens, theme, k, style)}</Text>,
				);
				break;
			}
			case 'html': {
				const raw = t.raw ?? (t as {text?: string}).text ?? '';
				// 自闭合标签（<hr>、<br>）或无法识别的标签：跳过
				if (/^<(hr|br|img)\b/i.test(raw)) break;
				const m = raw.match(HTML_TAG_RE);
				if (!m) break;
				const isClosing = !!m[1];
				if (isClosing) break; // 闭合标签，跳过
				const tagName = m[2].toLowerCase();
				// 开始标签：向后找到闭合标签，收集中间文本并施加样式
				let innerText = '';
				let found = false;
				let j = i + 1;
				for (; j < tokens.length; j++) {
					const nt = tokens[j];
					if (nt.type === 'html') {
						const nRaw = nt.raw ?? (nt as {text?: string}).text ?? '';
						const cm = nRaw.match(HTML_TAG_RE);
						if (cm && cm[1] && cm[2].toLowerCase() === tagName) {
							found = true;
							break;
						}
					}
					innerText += (nt as {text?: string}).text ?? (nt as {raw?: string}).raw ?? '';
				}
				if (found && innerText) {
					const tagColor = HTML_TAG_COLORS[tagName];
					result.push(
						<Text key={k} color={tagColor ?? style?.color} bold={!!tagColor} italic={style?.italic}>
							{innerText}
						</Text>,
					);
					i = j; // 跳到闭合标签位置，循环 i++ 会移到下一个
				}
				break;
			}
			default: {
				const raw = (t as {raw?: string}).raw ?? (t as {text?: string}).text ?? '';
				result.push(<Text key={k} color={style?.color} italic={style?.italic}>{raw}</Text>);
				break;
			}
		}
	}

	return result;
}

function renderItemContent(item: Tokens.ListItem, theme: ThemeConfig, prefix: string, style?: MarkdownRenderStyle): ReactNode {
	if (!item.tokens || item.tokens.length === 0) {
		return <Text color={style?.color} italic={style?.italic}>{item.text}</Text>;
	}

	const parts: ReactNode[] = [];
	for (let i = 0; i < item.tokens.length; i++) {
		const t = item.tokens[i];
		const k = `${prefix}-${i}`;

		if (t.type === 'text') {
			const tt = t as Tokens.Text;
			if (tt.tokens && tt.tokens.length > 0) {
				parts.push(...renderInline(tt.tokens, theme, k, style));
			} else {
				parts.push(<Text key={k} color={style?.color} italic={style?.italic}>{tt.text}</Text>);
			}
		} else if (t.type === 'paragraph') {
			const pt = t as Tokens.Paragraph;
			parts.push(...renderInline(pt.tokens, theme, k, style));
		} else if (t.type === 'list') {
			// 嵌套列表：递归渲染，有序显示序号，无序显示 - 符号
			const nestedList = t as Tokens.List;
			for (let ni = 0; ni < nestedList.items.length; ni++) {
				const nestedItem = nestedList.items[ni];
				const marker = nestedList.ordered ? `${ni + 1}. ` : '- ';
				const nestedContent = renderItemContent(nestedItem, theme, `${k}-${ni}`, style);
				parts.push(
					<Text key={`${k}-${ni}`} color={style?.color} italic={style?.italic}>{'\n'}{'  '}<Text color={theme.colors.muted}>{marker}</Text>{nestedContent}</Text>,
				);
			}
		} else {
			const raw = (t as {raw?: string}).raw ?? (t as {text?: string}).text ?? '';
			parts.push(<Text key={k} color={style?.color} italic={style?.italic}>{raw}</Text>);
		}
	}
	return <>{parts}</>;
}

function tokensToElements(
	tokens: Token[],
	theme: ThemeConfig,
	terminalWidth: number,
	style?: MarkdownRenderStyle,
): ReactNode[] {
	const elements: ReactNode[] = [];
	let ki = 0;

	for (const token of tokens) {
		switch (token.type) {
			case 'table': {
				elements.push(
					<MarkdownTable key={`t-${ki++}`} token={token as Tokens.Table} forceWidth={terminalWidth} />,
				);
				break;
			}

			case 'code': {
				const ct = token as Tokens.Code;
				const codeLines = ct.text.split('\n');
				if (codeLines.length > 0 && codeLines[codeLines.length - 1] === '') {
					codeLines.pop();
				}
				if (codeLines.length === 0) break;

				const numWidth = String(codeLines.length).length;

				// Border width: based on content, capped at terminal width
				let maxContentWidth = 0;
				for (const line of codeLines) {
					const w = stringWidth(line || ' ');
					if (w > maxContentWidth) maxContentWidth = w;
				}
				if (ct.lang) {
					const lw = stringWidth(`${ct.lang}: ${codeLines.length} lines`);
					if (lw > maxContentWidth) maxContentWidth = lw;
				}
				maxContentWidth = Math.max(maxContentWidth, 1);

				// │ numStr │ code │ = numWidth + codeWidth + 7
				const borderWidth = Math.min(numWidth + maxContentWidth + 7, terminalWidth - 4);
				const codeWidth = borderWidth - numWidth - 7;
				const lineDash = '─'.repeat(Math.max(borderWidth - 2, 0));

				const innerLines: string[] = [];

				// Language label + line count inside the border
				if (ct.lang) {
					const labelText = `${ct.lang}: ${codeLines.length} lines`;
					const labelW = stringWidth(labelText);
					const labelPad = ' '.repeat(Math.max(borderWidth - 3 - labelW, 0));
					const gold = `\x1b[1m\x1b[${colorToAnsi(theme.colors.illusion)}m`;
					const rst = '\x1b[39m\x1b[22m';
					innerLines.push(`│ ${gold}${labelText}${rst}${labelPad}│`);
				}

				// Code lines: fully closed borders, wrap long lines only
				// 仅当代码块为统一 diff 时才按 + / - / @@ 着色，避免把普通代码/列表误染
				const isDiff = isUnifiedDiff(codeLines);
				if (codeWidth > 0) {
					for (let li = 0; li < codeLines.length; li++) {
						const line = codeLines[li] || ' ';
						const lineNum = numWidth > 1
							? String(li + 1).padStart(numWidth, '0')
							: String(li + 1);

						let color = theme.colors.subtle;
						if (isDiff) {
							const trimmed = line.trimStart();
							if (trimmed.startsWith('+') && !trimmed.startsWith('+++')) {
								color = theme.colors.success;
							} else if (trimmed.startsWith('-') && !trimmed.startsWith('---')) {
								color = theme.colors.error;
							} else if (trimmed.startsWith('@@')) {
								color = theme.colors.info;
							}
						}

						const wrapped = wrapText(line, codeWidth, {hard: true});
						for (let wi = 0; wi < wrapped.length; wi++) {
							const segment = wrapped[wi]!;
							const numStr = wi === 0 ? lineNum : ' '.repeat(numWidth);
							const padded = padAligned(segment, stringWidth(segment), codeWidth, 'left');
							const colored = `\x1b[${colorToAnsi(color)}m${padded}\x1b[39m`;
							innerLines.push(`│ ${numStr} │ ${colored} │`);
						}
					}
				}

				const allLines = [`╭${lineDash}╮`, ...innerLines, `╰${lineDash}╯`];
				elements.push(
					<Text key={`t-${ki++}`} color={style?.color} italic={style?.italic}>{allLines.join('\n')}</Text>,
				);
				break;
			}

			case 'heading': {
				const ht = token as Tokens.Heading;
				const headingColor = ht.depth === 1
					? theme.colors.highlight
					: ht.depth === 2
						? theme.colors.info
						: theme.colors.illusionShimmer;
				const headingStyle: MarkdownRenderStyle = {...style, color: headingColor};
				const content = renderInline(ht.tokens, theme, `h-${ki}`, headingStyle);

				if (ht.depth === 1) {
					elements.push(
						<Text key={`t-${ki++}`} bold underline color={headingColor} italic={style?.italic}>
							{content}
						</Text>,
					);
				} else if (ht.depth === 2) {
					elements.push(
						<Text key={`t-${ki++}`} bold color={headingColor} italic={style?.italic}>
							{content}
						</Text>,
					);
				} else {
					elements.push(
						<Text key={`t-${ki++}`} bold color={headingColor} italic={style?.italic}>
							{content}
						</Text>,
					);
				}
				break;
			}

			case 'list': {
				const lt = token as Tokens.List;
				for (let li = 0; li < lt.items.length; li++) {
					const item = lt.items[li];
					const content = renderItemContent(item, theme, `l-${ki}-${li}`, style);
					// 有序列表显示序号，无序列表显示 - 符号
					const marker = lt.ordered ? `${li + 1}. ` : '- ';
					elements.push(
						<Text key={`t-${ki++}`} color={style?.color} italic={style?.italic}>
							<Text color={theme.colors.muted}>{marker}</Text>
							{content}
						</Text>,
					);
				}
				break;
			}

			case 'hr': {
				elements.push(
					<Text key={`t-${ki++}`} color={theme.colors.muted} italic={style?.italic}>{'─'.repeat(40)}</Text>,
				);
				break;
			}

			case 'blockquote': {
				const bt = token as Tokens.Blockquote;
				for (const inner of bt.tokens ?? []) {
					if (inner.type === 'paragraph') {
						const pt = inner as Tokens.Paragraph;
						const content = renderInline(pt.tokens, theme, `bq-${ki}`, style);
						elements.push(
							<Text key={`t-${ki++}`} italic color={theme.colors.muted}>
								{content}
							</Text>,
						);
					} else if (inner.type === 'text') {
						const tt = inner as Tokens.Text;
						const content = renderInline(tt.tokens, theme, `bq-${ki}`, style);
						elements.push(
							<Text key={`t-${ki++}`} italic color={theme.colors.muted}>
								{content}
							</Text>,
						);
					}
				}
				break;
			}

			case 'paragraph': {
				const pt = token as Tokens.Paragraph;
				elements.push(
					<Text key={`t-${ki++}`} color={style?.color} italic={style?.italic}>
						{renderInline(pt.tokens, theme, `p-${ki}`, style)}
					</Text>,
				);
				break;
			}

			case 'text': {
				const tt = token as Tokens.Text;
				if (tt.tokens && tt.tokens.length > 0) {
					elements.push(
						<Text key={`t-${ki++}`} color={style?.color} italic={style?.italic}>
							{renderInline(tt.tokens, theme, `tx-${ki}`, style)}
						</Text>,
					);
				} else {
					const raw = tt.raw ?? tt.text ?? '';
					raw.replace(/\n+$/, '').split('\n').forEach((line) => {
						elements.push(<Text key={`t-${ki++}`} color={style?.color} italic={style?.italic}>{line}</Text>);
					});
				}
				break;
			}

			case 'html': {
				const ht = token as Tokens.HTML;
				const raw = ht.raw ?? ht.text ?? '';
				const lines = raw.replace(/\n+$/, '').split('\n');
				let inDetails = false;
				let inSummary = false;
				let summaryText = '';
				let detailLines: string[] = [];
				const summaryTagRe = /<summary\b[^>]*>([\s\S]*?)<\/summary>/i;
				const stripTags = (s: string) => s.replace(/<[^>]+>/g, '');
				for (const line of lines) {
					if (/^<details\b/i.test(line)) { inDetails = true; continue; }
					if (/^<\/details>/i.test(line)) {
						if (summaryText) {
							elements.push(
								<Text key={`t-${ki++}`} bold color={theme.colors.info}>{summaryText}</Text>,
							);
						}
						for (const dl of detailLines) {
							elements.push(
								<Text key={`t-${ki++}`} color={style?.color}>{'  '}{dl}</Text>,
							);
						}
						inDetails = false; summaryText = ''; detailLines = [];
						continue;
					}
					if (/<summary\b/i.test(line)) {
						const sm = line.match(summaryTagRe);
						if (sm) { summaryText = stripTags(sm[1]).trim(); }
						inSummary = true;
						if (!/<\/summary>/i.test(line)) continue;
					}
					if (/<\/summary>/i.test(line)) { inSummary = false; continue; }
					if (inSummary) { summaryText += stripTags(line).trim(); continue; }
					if (/^<hr\b/i.test(line)) {
						elements.push(<Text key={`t-${ki++}`} color={theme.colors.muted}>{'─'.repeat(40)}</Text>);
						continue;
					}
					const stripped = stripTags(line).trim();
					if (!stripped) continue;
					if (inDetails) { detailLines.push(stripped); continue; }
					elements.push(<Text key={`t-${ki++}`} color={style?.color}>{stripped}</Text>);
				}
				break;
			}

			default: {
				const raw = (token as {raw?: string}).raw;
				if (raw) {
					raw.replace(/\n+$/, '').split('\n').forEach((line) => {
						elements.push(<Text key={`t-${ki++}`} color={style?.color} italic={style?.italic}>{line}</Text>);
					});
				}
				break;
			}
		}
	}

	return elements;
}

export function renderInlineMarkdown(text: string, theme: ThemeConfig, keyPrefix: string, style?: MarkdownRenderStyle): ReactNode[] {
	if (!text || !text.trim()) return [<Text key={`${keyPrefix}-empty`} color={style?.color} italic={style?.italic}>{text}</Text>];
	try {
		const tokens = lexer(text);
		for (const token of tokens) {
			if (token.type === 'paragraph') {
				const pt = token as Tokens.Paragraph;
				const rendered = renderInline(pt.tokens, theme, keyPrefix, style);
				if (rendered.length > 0) return rendered;
			} else if (token.type === 'text') {
				const tt = token as Tokens.Text;
				if (tt.tokens && tt.tokens.length > 0) {
					const rendered = renderInline(tt.tokens, theme, keyPrefix, style);
					if (rendered.length > 0) return rendered;
				}
			} else if (token.type === 'list') {
				// 处理列表项：提取第一个列表项的内容（有序显示 1.，无序显示 -）
				const lt = token as Tokens.List;
				const inlineMarker = lt.ordered ? '1. ' : '- ';
				if (lt.items.length > 0) {
					const item = lt.items[0];
					if (item.tokens && item.tokens.length > 0) {
						for (const itemToken of item.tokens) {
							if (itemToken.type === 'text') {
								const tt = itemToken as Tokens.Text;
								if (tt.tokens && tt.tokens.length > 0) {
									return [<Text key={`${keyPrefix}-list`} color={style?.color} italic={style?.italic}>{inlineMarker}</Text>, ...renderInline(tt.tokens, theme, `${keyPrefix}-list`, style)];
								}
							} else if (itemToken.type === 'paragraph') {
								const pt = itemToken as Tokens.Paragraph;
								return [<Text key={`${keyPrefix}-list`} color={style?.color} italic={style?.italic}>{inlineMarker}</Text>, ...renderInline(pt.tokens, theme, `${keyPrefix}-list`, style)];
							}
						}
					}
					// fallback: 用 raw 文本
					return [<Text key={`${keyPrefix}-list`} color={style?.color} italic={style?.italic}>{inlineMarker}{item.text}</Text>];
				}
			}
		}
	} catch {
		// fall through to raw text
	}
	return [<Text key={`${keyPrefix}-raw`} color={style?.color} italic={style?.italic}>{text}</Text>];
}

export function MarkdownContent({
	text,
	style,
	availableWidth,
}: {
	text: string;
	style?: MarkdownRenderStyle;
	availableWidth?: number;
}): React.JSX.Element {
	const theme = useTheme();
	const {columns: terminalWidth} = useTerminalSize();
	const contentWidth = Math.max(20, Math.min(availableWidth ?? terminalWidth, terminalWidth));
	const elements = useMemo(() => {
		if (!text.trim()) return [];
		try {
			const tokens = lexer(text);
			return tokensToElements(tokens, theme, contentWidth, style);
		} catch {
			return text.split('\n').map((line, i) => <Text key={`f-${i}`} color={style?.color} italic={style?.italic}>{line}</Text>);
		}
	}, [text, theme, contentWidth, style]);

	return (
		<Box flexDirection="column" width={contentWidth}>
			{elements}
		</Box>
	);
}
