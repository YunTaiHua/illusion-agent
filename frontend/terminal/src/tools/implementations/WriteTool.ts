/**
 * @fileoverview 文件写入工具渲染实现
 *
 * 新建时显示行数和代码预览，更新时显示 diff。
 *
 * @module tools/implementations/WriteTool
 */

import type {Tool} from '../ToolInterface.js';

/**
 * 解析创建场景的真实写入行数
 *
 * 优先级：工具入参 content（权威，直播/恢复均可用）→ structured_output
 * 的 line_count → 结果文本回算。文本回算须排除 "Created {path}" 首行，
 * 且截断预览的尾行 "... +N lines" 需补足剩余行数——直接按总行数会
 * 恒等于预览上限（表现为固定 "Wrote 12 lines"）。
 */
function resolveCreateLineCount(
	result: string,
	input?: Record<string, unknown>,
	metadata?: Record<string, unknown>,
): number {
	if (typeof input?.content === 'string') {
		return countLines(input.content);
	}
	if (typeof metadata?.line_count === 'number') {
		return metadata.line_count;
	}
	const body = result.split('\n').slice(1);
	const tail = body[body.length - 1]?.match(/\.\.\. \+(\d+) lines$/);
	if (tail) {
		return body.length - 1 + Number(tail[1]);
	}
	return body.filter((l) => l.trim() !== '').length;
}

function countLines(text: string): number {
	if (!text) return 0;
	return text.split('\n').reduce((n, line, i, arr) => (
		n + (line !== '' || i < arr.length - 1 ? 1 : 0)
	), 0);
}

export const writeTool: Tool = {
	name: 'write_file',

	displayName(): string {
		return 'Write';
	},

	renderToolUseMessage(input?: Record<string, unknown>): string {
		if (!input) return '';
		return String(input.path ?? input.file_path ?? '');
	},

	renderToolResultMessage(
		result: string,
		input?: Record<string, unknown>,
		_isBrief?: boolean,
		structuredOutput?: Record<string, unknown>,
	): string {
		const metadata = structuredOutput as Record<string, unknown> | undefined;
		const isCreate = metadata?.is_create !== false && !/^Updated\s/.test(result.split('\n')[0] ?? '');
		const filePath = metadata?.file_path ?? input?.path ?? input?.file_path ?? '';

		if (isCreate) {
			return `Wrote ${resolveCreateLineCount(result, input, metadata)} lines to ${filePath}`;
		}

		// 更新：显示 diff 行
		const lines = result.split('\n').filter((l) => l.trim() !== '');
		if (lines.length === 0) {
			return '(No diff)';
		}
		return lines.slice(0, 15).join('\n');
	},

	getActivityDescription(input?: Record<string, unknown>): string | null {
		if (!input) return 'Writing file';
		const path = String(input.path ?? input.file_path ?? '');
		return path ? `Writing ${path}` : 'Writing file';
	},
};
