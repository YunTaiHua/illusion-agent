/**
 * @fileoverview 文件编辑工具渲染实现
 *
 * 显示结构化统一 diff，带框线和着色。
 * 结果截断时尾部追加未展示行数/hunk 数提示（与 Bash 工具风格一致）。
 *
 * @module tools/implementations/EditTool
 */

import type {Tool} from '../ToolInterface.js';

/**
 * 内容行数上限：多渲染 1 行尾部提示后总输出恰好 ≤ 15 行，
 * 低于下层 ToolResultBlock 的 15 行二次截断阈值（> 15 才截断），
 * 保证自带的截断提示不被吞掉（否则会被二次截断为固定“… +1 lines”）。
 */
const MAX_SHOWN_LINES = 14;

interface DiffHunk {
	old_start: number;
	old_lines: number;
	new_start: number;
	new_lines: number;
	lines: string[];
}

export const editTool: Tool = {
	name: 'edit_file',

	displayName(input?: Record<string, unknown>): string {
		if (!input?.old_string || String(input.old_string).trim() === '') {
			return 'Create';
		}
		return 'Update';
	},

	renderToolUseMessage(input?: Record<string, unknown>): string {
		if (!input) return '';
		return String(input.path ?? input.file_path ?? '');
	},

	renderToolResultMessage(
		result: string,
		_input?: Record<string, unknown>,
		_isBrief?: boolean,
		structuredOutput?: Record<string, unknown>,
	): string {
		if (structuredOutput?.hunks && Array.isArray(structuredOutput.hunks)) {
			// 从结构化 diff 提取文本行（hunk 头 + diff 行共用内容行预算）；
			// 超出预算的行整体计入未展示提示，尾部提示不占内容预算
			const hunks = structuredOutput.hunks as DiffHunk[];
			// 行数口径与下层 ToolResultBlock 一致：空白行不参与统计/展示
			const totalDiffLines = hunks.reduce(
				(n, h) => n + h.lines.filter((l) => l.trim() !== '').length,
				0,
			);
			const lines: string[] = [];
			let renderedDiffLines = 0;
			let shownHunks = 0;
			outer:
			for (const hunk of hunks) {
				if (lines.length >= MAX_SHOWN_LINES) break;
				lines.push(`@@ -${hunk.old_start},${hunk.old_lines} +${hunk.new_start},${hunk.new_lines} @@`);
				shownHunks++;
				for (const line of hunk.lines) {
					if (line.trim() === '') continue;
					if (lines.length >= MAX_SHOWN_LINES) break outer;
					lines.push(line);
					renderedDiffLines++;
				}
			}
			const hiddenLines = totalDiffLines - renderedDiffLines;
			const hiddenHunks = hunks.length - shownHunks;
			if (hiddenLines > 0) {
				lines.push(hiddenHunks > 0
					? `… +${hiddenLines} lines in ${hiddenHunks} more hunks`
					: `… +${hiddenLines} lines`);
			}
			return lines.join('\n');
		}

		const lines = result.split('\n').filter((l) => l.trim() !== '');
		if (lines.length === 0) {
			return '(No diff)';
		}
		if (lines.length > MAX_SHOWN_LINES) {
			return [...lines.slice(0, MAX_SHOWN_LINES), `… +${lines.length - MAX_SHOWN_LINES} lines`].join('\n');
		}
		return lines.join('\n');
	},

	getActivityDescription(input?: Record<string, unknown>): string | null {
		if (!input) return 'Editing file';
		const path = String(input.path ?? input.file_path ?? '');
		return path ? `Editing ${path}` : 'Editing file';
	},
};
