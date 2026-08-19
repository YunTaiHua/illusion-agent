/**
 * @fileoverview 文件读取工具渲染实现
 *
 * 显示文件路径和读取结果（行数、图片大小、PDF 等）。
 *
 * @module tools/implementations/ReadTool
 */

import type {Tool} from '../ToolInterface.js';

function formatSize(bytes: number): string {
	if (bytes < 1024) return `${bytes}B`;
	if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
	return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

export const readTool: Tool = {
	name: 'read_file',

	displayName(): string {
		return 'Read';
	},

	renderToolUseMessage(input?: Record<string, unknown>): string {
		if (!input) return '';
		const path = String(input.path ?? input.file_path ?? '');
		const offset = input.offset;
		const limit = input.limit;
		let suffix = '';
		if (offset !== undefined && limit !== undefined) {
			suffix = `:${offset}-${Number(offset) + Number(limit)}`;
		}
		return `${path}${suffix}`;
	},

	renderToolResultMessage(
		result: string,
		_input?: Record<string, unknown>,
		_isBrief?: boolean,
		structuredOutput?: Record<string, unknown>,
	): string {
		const metadata = structuredOutput as Record<string, unknown> | undefined;
		const outputType = metadata?.output_type ?? 'text';

		if (outputType === 'image') {
			const size = metadata?.file_size ? ` (${formatSize(Number(metadata.file_size))})` : '';
			return `Read image${size}`;
		}

		if (outputType === 'pdf') {
			const size = metadata?.file_size ? ` (${formatSize(Number(metadata.file_size))})` : '';
			return `Read PDF${size}`;
		}

		if (result.includes('Unchanged since last read')) {
			return 'Unchanged since last read';
		}

		const lineCount = metadata?.line_count ?? result.split('\n').length;
		return `Read ${lineCount} line(s)`;
	},


	getActivityDescription(input?: Record<string, unknown>): string | null {
		if (!input) return 'Reading file';
		const path = String(input.path ?? input.file_path ?? '');
		return path ? `Reading ${path}` : 'Reading file';
	},
};
