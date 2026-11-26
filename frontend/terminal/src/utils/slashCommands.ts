/**
 * @fileoverview 斜杠资源目录与 token 扫描
 *
 * terminal 前端的行首命令与行内斜杠资源工具，与 web 端
 * utils/slashTokens.ts 同构（纯文本无图标）：
 * - buildCommandCatalog 合并内置命令与工作区资源，完整名优先，
 *   可唯一解析的短名作为别名补全；
 * - scanPromptTokens 在原文 UTF-16 偏移上扫描斜杠 token，代码围栏、
 *   行内代码、Markdown 链接、URL 与 @ 引用内部不视为资源；
 * - 高亮须再做目录精确查找（referenceRanges），普通文本永不误染；
 *   ANSI 着色不进入编辑器偏移与请求文本。
 *
 * @module slashCommands
 */

import type {SlashCommand, TranscriptItem} from '../types.js';

/** 无元数据时沿用旧的字符串菜单，即行首命令菜单。 */
export type CommandHint = SlashCommand | {name: string; kind: 'builtin' | 'legacy'};
export function isResource(entry: CommandHint): entry is SlashCommand {
	return entry.kind === 'skill' || entry.kind === 'command';
}

export function buildCommandCatalog(commands: readonly string[], resources?: readonly SlashCommand[]) {
	const entries = new Map<string, CommandHint>();
	for (const name of commands) {
		entries.set(name.toLowerCase(), {name, kind: resources ? 'builtin' : 'legacy'});
	}
	for (const resource of resources ?? []) {
		entries.set(resource.name.toLowerCase(), resource);
	}
	// 短名必须能解析到唯一目标；完整名（含内置命令）始终优先。
	const aliases = new Map<string, SlashCommand | null>();
	for (const [name, entry] of entries) {
		if (!isResource(entry) || !name.includes(':')) continue;
		const short = '/' + name.slice(name.lastIndexOf(':') + 1);
		aliases.set(short, aliases.has(short) || entry.accepts_short_name === false ? null : entry);
	}
	return {entries, aliases};
}
export type CommandCatalog = ReturnType<typeof buildCommandCatalog>;

export type TextRange = {start: number; end: number};
type PromptToken = TextRange & {kind: 'slash' | 'mention'};
export type SlashToken = TextRange & {query: string; standalone: boolean};

// 在原始 UTF-16 偏移上工作；先扫描受保护构造，再跳过其内部。
const LEFT = /[\s([{'"“‘（【《「『，。！？、：；]/u;
const RIGHT = /^[.?!:]*(?:[\s)\]}"'”’）】》」』，。！？、：；,;]|$)/u;
const NAME = /(?:[\p{L}\p{N}_-]+(?:[.:][\p{L}\p{N}_-]+)*)?/uy;
const LINK_DEFINITION = /^(?: {0,3}>[ \t]?)* {0,3}\[(?:\\.|[^\[\]\\\r\n])+\]:[ \t]*(?:\r?\n[ \t]*)?(?:<(?:\\.|[^<>\\\r\n])*>|[^\s]+)(?:(?:[ \t]+|[ \t]*\r?\n[ \t]*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\((?:\\.|[^()\\])*\)))?/;
const ITEMS = [
	LINK_DEFINITION.source,
	String.raw`^ {0,3}(?<fence>\x60{3,}|~{3,})|(?<![\\\x60])(?<ticks>\x60+)`,
	String.raw`@\[(?:\\.|[^\\\]\r\n])*(?:\]\([^()\r\n]*\)|$)|@"[^"\r\n]*(?:"|$)|@[^\s"\x60\[\]()]+`,
	String.raw`\]\(|(?:[a-z][a-z\d+.\-]*://|//|\bwww\.)[^\s<>"'\x60，。；！？、]+`,
	'/',
].join('|');

function pastCode(text: string, offset: number, marker: string, fence: boolean): number {
	const closer = fence ? /^ {0,3}(`{3,}|~{3,})[ \t]*\r?$/gm : /`+/g;
	closer.lastIndex = offset;
	let match;
	while ((match = closer.exec(text))) {
		if (fence ? match[1].startsWith(marker) : match[0] === marker) return closer.lastIndex;
	}
	return text.length; // 同时保护用户仍在输入的未闭合代码
}

function pastLink(text: string, offset: number): number {
	let depth = 1;
	let quote = '';
	for (let i = offset; i < text.length; i++) {
		const ch = text[i];
		if (ch === '\n' && (text[i + 1] === '\n' || text.startsWith('\r\n', i + 1))) return i;
		if (ch === '\\') i++;
		else if (quote) { if (ch === quote) quote = ''; }
		else if (ch === '<') quote = '>';
		else if ((ch === '"' || ch === "'") && /\s/.test(text[i - 1])) quote = ch;
		else if (ch === '(') depth++;
		else if (ch === ')' && --depth === 0) return i + 1;
	}
	return text.length;
}

export function scanPromptTokens(text: string): PromptToken[] {
	const tokens: PromptToken[] = [];
	const items = new RegExp(ITEMS, 'gimu');
	let match;
	while ((match = items.exec(text))) {
		const start = match.index;
		const word = match[0];
		const {fence, ticks} = match.groups!;
		if (fence || ticks) items.lastIndex = pastCode(text, items.lastIndex, fence || ticks, !!fence);
		else if (word === '](') items.lastIndex = pastLink(text, items.lastIndex);
		else if (word.startsWith('@')) {
			if (start === 0 || /\s/.test(text[start - 1])) tokens.push({kind: 'mention', start, end: items.lastIndex});
		} else if (word === '/' && (start === 0 || LEFT.test(text[start - 1]))) {
			NAME.lastIndex = items.lastIndex;
			const end = items.lastIndex + NAME.exec(text)![0].length;
			if (RIGHT.test(text.slice(end))) tokens.push({kind: 'slash', start, end});
		}
	}
	return tokens;
}

export function slashTokenAt(text: string, caret: number, tokens: PromptToken[]): SlashToken | undefined {
	const token = tokens.find(t => t.kind === 'slash' && t.start < caret && (caret <= t.end
		|| (caret === t.end + 1 && text[t.end] === ':')));
	if (!token) return undefined;
	const end = Math.max(caret, token.end);
	return {start: token.start, end, query: text.slice(token.start + 1, caret), standalone: token.start === 0 && !text.slice(end).trim()};
}

const CATEGORY_ORDER = {
	'built-in command': 0,
	command: 1,
	'plugin command': 2,
	skill: 3,
	'plugin skill': 4,
} as const;

export function slashCategory(entry: CommandHint): keyof typeof CATEGORY_ORDER {
	if (entry.kind === 'builtin') return 'built-in command';
	if (!isResource(entry)) return 'command'; // 旧式同名条目只展示名称
	return entry.source === 'plugin' ? `plugin ${entry.kind}` : entry.kind;
}

export function commandHints(catalog: CommandCatalog, token?: SlashToken): CommandHint[] {
	if (!token) return [];
	const query = token.query.toLowerCase();
	const hints = [...catalog.entries.values()].filter(entry => {
		if (!token.standalone && !isResource(entry)) return false;
		const name = entry.name.slice(1).toLowerCase();
		return name.startsWith(query) || name.includes(':' + query);
	});
	// 完整名 > 可解析短名 > 前缀。空查询时 /language 固定置顶。
	const wanted = '/' + query;
	const preferred = query ? catalog.entries.get(wanted) ?? catalog.aliases.get(wanted) : catalog.entries.get('/language');
	const preferredCategory = preferred && slashCategory(preferred);
	const rank = (entry: CommandHint) => {
		if (entry === preferred) return -2;
		const category = slashCategory(entry);
		return category === preferredCategory ? -1 : CATEGORY_ORDER[category];
	};
	return hints.sort((a, b) => rank(a) - rank(b));
}

export function completeSlash(text: string, token: SlashToken, name: string) {
	const suffix = text.slice(token.end);
	const value = text.slice(0, token.start) + name + (suffix || ' ');
	const caret = token.start + name.length + (!suffix || /^[ \t]/.test(suffix) ? 1 : 0);
	return {value, caret};
}

/** 输入框与实时/重放的用户消息绘制同一批原文范围。 */
export function referenceRanges(text: string, catalog: CommandCatalog, {
	tokens = scanPromptTokens(text), snapshot,
}: {tokens?: PromptToken[]; snapshot?: TranscriptItem['slash_references']} = {}): TextRange[] {
	return tokens.filter(token => {
		if (token.kind === 'mention') return true;
		const raw = text.slice(token.start, token.end);
		if (snapshot != null) return Object.hasOwn(snapshot, raw);
		const name = raw.toLowerCase();
		const entry = catalog.entries.get(name) ?? catalog.aliases.get(name);
		return entry && isResource(entry);
	});
}

/** 在量测提示词后再应用样式；ANSI 字节不进入编辑器偏移与请求文本。 */
export function paintReferences(part: string, offset: number, ranges: TextRange[], color: (s: string) => string): string {
	let result = '';
	let pos = 0;
	for (const {start, end} of ranges) {
		if (end <= offset) continue;
		if (start >= offset + part.length) break;
		const from = Math.max(0, start - offset);
		const to = Math.min(part.length, end - offset);
		result += part.slice(pos, from) + color(part.slice(from, to));
		pos = to;
	}
	return result + part.slice(pos);
}
