/**
 * @fileoverview @ 提及补全工具
 *
 * terminal 前端的 @ 提及（@file / @skill）token 检测与插入文本格式化，
 * 与 web 端 PromptInput.detectMentionToken / formatMentionInsertion 同构：
 * '@' 须位于行首或空白后（邮箱不触发）；@"..." 引号形式允许空格，
 * 闭合引号后提及结束。
 *
 * @module mention
 */

/**
 * @ 提及 token（光标处正在输入的提及片段）
 */
export type MentionToken = {
	/** '@' 字符在文本中的下标 */
	start: number;
	/** 光标位置（token 结束边界） */
	end: number;
	/** 查询串：@ 之后、光标之前的路径片段（引号形式为引号内内容） */
	query: string;
	/** 是否以 @" 开启的引号形式（路径含空格时使用） */
	quoted: boolean;
};

/**
 * 提及候选的最小形状（文件/目录/技能共用）
 *
 * 与 types.ts 的 FileMentionCandidate 结构一致，这里用宽松类型
 * 便于纯函数单测。
 */
export type MentionCandidateLike = {
	path: string;
	kind: 'dir' | 'file' | 'skill';
};

/**
 * 检测光标处是否处于 @ 提及输入中。
 *
 * 规则：'@' 必须位于输入开头或空白字符之后（邮箱等文本不触发）；
 * 未加引号时 token 内不允许空格；@" 开启的引号形式允许空格，
 * 出现闭合引号即视为提及结束。
 *
 * @param text - 输入框全文
 * @param pos - 光标位置
 * @returns 提及 token；不在提及上下文返回 null
 */
export function detectMentionToken(text: string, pos: number): MentionToken | null {
	const before = text.slice(0, pos);
	const at = before.lastIndexOf('@');
	if (at === -1) return null;
	if (at > 0 && !/\s/.test(before[at - 1] ?? '')) return null;
	const rest = before.slice(at + 1);
	if (rest.startsWith('"')) {
		// 引号已闭合或跨行：提及结束
		if (rest.indexOf('"', 1) !== -1 || rest.includes('\n')) return null;
		return {start: at, end: pos, query: rest.slice(1), quoted: true};
	}
	// 未引号形式不允许空格
	if (/\s/.test(rest)) return null;
	return {start: at, end: pos, query: rest, quoted: false};
}

/**
 * 格式化提及插入文本：
 * 名称含空格或引号时用 @"..." 形式；目录保留尾部 / 继续下钻，
 * 文件与技能追加空格闭合 token。
 *
 * @param candidate - 选中的候选
 * @returns 插入到输入框的完整文本
 */
export function formatMentionInsertion(candidate: MentionCandidateLike): string {
	const needsQuote = /[\s"]/.test(candidate.path);
	if (candidate.kind === 'dir') return needsQuote ? `@"${candidate.path}/` : `@${candidate.path}/`;
	return needsQuote ? `@"${candidate.path}" ` : `@${candidate.path} `;
}
