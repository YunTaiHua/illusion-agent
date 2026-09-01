/**
 * @fileoverview 变更工具结果文本的增删行数解析（terminal 渲染辅助）
 *
 * 与 web 端 turnGrouping.parseToolResultStat、后端 illusion.tools.diff_utils
 * 口径一致：优先工具 structured_output（直播精确值），回退结果文本解析
 * （恢复路径）——"Updated {path}\n{diff}" 数 diff 行，"Created {path}\n{预览}"
 * 全量计增行（截断尾行 "... +N lines" 补足）。
 *
 * @module utils/diffStat
 */

/** 变更工具结构化输出（后端 ToolResult.metadata 下发） */
export interface ChangeStatMetadata {
	file_path?: string;
	is_create?: boolean;
	line_count?: number;
	insertions?: number;
	deletions?: number;
}

/** 单次编辑的增删行数 */
export interface DiffStatParts {
	insertions: number;
	deletions: number;
}

/** 从结果文本回算增删行数（无结构化数据时的兜底） */
function statFromText(result: string): DiffStatParts | null {
	const lines = result.split('\n');
	const first = (lines[0] ?? '').trim();
	const body = lines.slice(1);
	if (/^Created\s/.test(first)) {
		let count = body.length > 0 ? body.length : 0;
		const tail = body[body.length - 1]?.match(/\.\.\. \+(\d+) lines$/);
		if (tail) count = body.length - 1 + Number(tail[1]);
		return {insertions: count, deletions: 0};
	}
	if (/^Updated\s/.test(first)) {
		let insertions = 0;
		let deletions = 0;
		let inHunk = false;
		for (const line of body) {
			// 文件头（+++ / ---）只出现在首个 @@ 之前；hunk 内以 --- 开头
			// 的行是"内容以 -- 开头的删除行"，不能误判为文件头
			if (line.startsWith('@@')) {
				inHunk = true;
				continue;
			}
			if (!inHunk && (line.startsWith('+++') || line.startsWith('---'))) continue;
			if (line.startsWith('+')) insertions++;
			else if (line.startsWith('-')) deletions++;
		}
		return {insertions, deletions};
	}
	return null;
}

/**
 * 解析变更工具单次编辑的增删行数（结构化优先，文本回退）
 *
 * @param result - 工具结果文本
 * @param structuredOutput - 工具结构化输出（可选）
 * @returns 增删行数（两端至少一端 > 0），无法解析返回 null
 */
export function diffStatParts(
	result: string,
	structuredOutput?: Record<string, unknown>,
): DiffStatParts | null {
	const meta = structuredOutput as ChangeStatMetadata | undefined;
	let insertions: number | undefined = typeof meta?.insertions === 'number' ? meta.insertions : undefined;
	let deletions: number | undefined = typeof meta?.deletions === 'number' ? meta.deletions : undefined;
	if (insertions === undefined || deletions === undefined) {
		const fallback = statFromText(result);
		if (fallback) {
			if (insertions === undefined) insertions = fallback.insertions;
			if (deletions === undefined) deletions = fallback.deletions;
		}
	}
	if (insertions === undefined || deletions === undefined) return null;
	if (insertions <= 0 && deletions <= 0) return null;
	return {insertions, deletions};
}
