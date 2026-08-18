/**
 * @fileoverview Markdown 工具模块
 *
 * 提供文本处理相关的工具函数，包括：
 * - ANSI 转义序列处理
 * - 字符串宽度计算
 * - 文本换行
 * - 文本对齐
 *
 * @module markdown
 */

import stripAnsi from 'strip-ansi';
import stringWidth from 'string-width';
import wrapAnsi from 'wrap-ansi';

/** 重新导出常用的文本处理工具 */
export {stripAnsi, stringWidth, wrapAnsi};

/** 最小换行宽度 */
export const MIN_WRAP_WIDTH = 12;

/** 宽度安全余量（防止终端宽度计算偏差导致溢出） */
export const WIDTH_SAFETY_EXTRA = 2;

/**
 * 文本对齐填充
 *
 * 根据指定的对齐方式，在内容周围添加空格以达到目标宽度。
 *
 * @param content - 原始内容
 * @param displayWidth - 内容的显示宽度（考虑多字节字符）
 * @param targetWidth - 目标宽度
 * @param align - 对齐方式：'left'（左对齐）、'center'（居中）、'right'（右对齐）
 * @returns 填充后的字符串
 */
export function padAligned(
	content: string,
	displayWidth: number,
	targetWidth: number,
	align: 'left' | 'center' | 'right' | null | undefined,
): string {
	const padding = Math.max(0, targetWidth - displayWidth);
	if (align === 'center') {
		const leftPad = Math.floor(padding / 2);
		return ' '.repeat(leftPad) + content + ' '.repeat(padding - leftPad);
	}
	if (align === 'right') {
		return ' '.repeat(padding) + content;
	}
	return content + ' '.repeat(padding);
}

/**
 * 文本换行
 *
 * 将文本按指定宽度换行，支持硬换行（在单词内断开）和软换行（在空格处断开）。
 *
 * @param text - 要换行的文本
 * @param width - 每行的最大宽度
 * @param options - 换行选项
 * @param options.hard - 是否启用硬换行（在单词内断开），默认为 false
 * @returns 换行后的字符串数组
 */
export function wrapText(text: string, width: number, options?: {hard?: boolean}): string[] {
	if (width <= 0) return [text];
	const trimmedText = text.trimEnd();
	const wrapped = wrapAnsi(trimmedText, width, {
		hard: options?.hard ?? false,
		trim: false,
	});
	const lines = wrapped.split('\n').filter((line) => line.length > 0);
	return lines.length > 0 ? lines : [''];
}

/**
 * 按显示宽度换行文本
 *
 * 将文本按指定显示宽度换行（在单词内硬断行），返回换行后的行数组。
 * 输入中的空段落保留为空字符串行，便于调用方为每行统一添加前缀。
 *
 * @param text - 要换行的文本
 * @param width - 每行的最大显示宽度
 * @returns 换行后的字符串数组
 */
export function wrapToDisplayWidth(text: string, width: number): string[] {
	if (width <= 0) return [text];
	const sourceLines = text.split('\n');
	const wrapped: string[] = [];
	for (const source of sourceLines) {
		if (source.length === 0) {
			wrapped.push('');
			continue;
		}
		wrapped.push(...wrapText(source, width, {hard: true}));
	}
	return wrapped.length > 0 ? wrapped : [''];
}

/**
 * 按显示宽度截断文本，超出部分用省略号替代
 *
 * @param text - 原始文本
 * @param maxWidth - 最大显示宽度
 * @returns 截断后的文本（如有截断则末尾加 `…`）
 */
export function truncateToDisplayWidth(text: string, maxWidth: number): string {
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

/**
 * 按前缀宽度换行文本
 *
 * 计算可用宽度 = max(MIN_WRAP_WIDTH, terminalWidth - prefixWidth - WIDTH_SAFETY_EXTRA)，
 * 将文本按此宽度换行，续行由调用方自行添加缩进对齐。
 *
 * @param text - 要换行的文本
 * @param terminalWidth - 终端总宽度
 * @param prefix - 前缀字符串（用于计算可用宽度）
 * @returns 换行后的字符串数组
 */
export function wrapForPrefix(text: string, terminalWidth: number, prefix: string): string[] {
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

/**
 * 判断一组文本行是否为统一 diff（unified diff）格式
 *
 * 仅当出现 diff 结构标记时才判定为 diff：
 * - `@@ -x,y +x,y @@` 块头
 * - `--- a/...` 与 `+++ b/...` 文件头成对出现
 *
 * 用于限制 diff 着色范围，避免把普通文本（如无序列表 `- item`、bash 输出、
 * 普通代码块）误渲染为红/绿色。
 *
 * @param lines - 文本行数组
 * @returns 是否为统一 diff 格式
 */
export function isUnifiedDiff(lines: string[]): boolean {
	let hasFrom = false;
	let hasTo = false;
	for (const line of lines) {
		const trimmed = line.trimStart();
		if (/^@@\s+-\d+/.test(trimmed)) {
			return true;
		}
		if (trimmed.startsWith('--- ')) {
			hasFrom = true;
		} else if (trimmed.startsWith('+++ ')) {
			hasTo = true;
		}
		if (hasFrom && hasTo) {
			return true;
		}
	}
	return false;
}
