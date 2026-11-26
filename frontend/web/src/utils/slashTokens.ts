/**
 * @fileoverview 斜杠资源 token 扫描与补全目录
 *
 * web 前端输入框的斜杠资源工具，与 terminal 端 utils/slashCommands.ts
 * 同构（协议层共享资源条目结构）：
 * - buildSlashCatalog 合并 ready 内置命令白名单与工作区资源条目，
 *   同名时保留协议已解析的命令，唯一短名提升为别名候选；
 * - scanPromptTokens 对整份草稿做一次词法扫描，围栏代码块、行内代码、
 *   链接定义、@ 引用与 URL 内部的斜杠不视为资源；
 * - 高亮与激活只认目录精确命中（规范名优先于唯一短名），已提交转录
 *   按 slash_references 快照回放，不依赖当前目录。
 *
 * @module slashTokens
 */

import type { SlashCommandEntry, TranscriptItem } from '../types/protocol';

// ready.commands 是能力清单，不用于在 GUI 中暴露其他运行时内置命令。
const BUILTIN_DESCRIPTIONS = {
  '/compact': 'cmd_compact', '/export': 'cmd_export', '/init': 'cmd_init',
  '/rename': 'cmd_rename', '/goal': 'cmd_goal',
} as const;

type BuiltinName = keyof typeof BUILTIN_DESCRIPTIONS;
export type SlashChoice = SlashCommandEntry | { name: BuiltinName; kind: 'builtin'; descriptionKey: string };

export function buildSlashCatalog(readyNames: readonly string[], entries: readonly SlashCommandEntry[]) {
  const ready = new Set(readyNames.map(name => name.replace(/^\/?/, '/').toLowerCase()));
  const choices: SlashChoice[] = [];
  const builtins = new Set<string>();
  for (const [name, descriptionKey] of Object.entries(BUILTIN_DESCRIPTIONS)) {
    if (ready.has(name)) {
      builtins.add(name);
      choices.push({ name: name as BuiltinName, kind: 'builtin', descriptionKey });
    }
  }
  const exact = new Map<string, SlashCommandEntry>();
  const aliases = new Map<string, SlashCommandEntry | null>();
  for (const raw of entries) {
    if ((raw.kind !== 'skill' && raw.kind !== 'command') || !raw.name) continue;
    const name = raw.name.replace(/^\/?/, '/');
    const key = name.toLowerCase();
    if (key in BUILTIN_DESCRIPTIONS) continue;
    const entry = name === raw.name ? raw : { ...raw, name };
    // 协议已在同名时保留实际会被斜杠调用的命令。
    if (!exact.has(key) || entry.kind === 'command') exact.set(key, entry);
  }
  for (const [key, entry] of exact) {
    choices.push(entry);
    if (!key.includes(':')) continue;
    const suffix = '/' + key.slice(key.lastIndexOf(':') + 1);
    aliases.set(suffix, aliases.has(suffix) || entry.accepts_short_name === false ? null : entry);
  }
  const resources = new Map(exact);
  for (const [key, entry] of aliases) {
    if (entry && !exact.has(key) && !(key in BUILTIN_DESCRIPTIONS)) resources.set(key, entry);
  }
  return { choices, builtins, resources };
}
export type SlashCatalog = ReturnType<typeof buildSlashCatalog>;

/** GUI 既有的「命令 + 参数」行首入口；普通正文一律按 submit_line 作为文本提交。 */
export function builtinForLine(line: string, catalog: SlashCatalog): string | undefined {
  const head = line.trim().split(/\s+/, 1)[0];
  return head && catalog.builtins.has(head) ? head : undefined;
}

export interface TextRange { start: number; end: number }
interface PromptToken extends TextRange { kind: 'slash' | 'mention' }
export interface SlashToken extends TextRange { query: string }

// 对整份草稿只做一次扫描；新增行仍可能处于围栏代码块内部。
const LEFT = /[\s([{'"“‘（【《「『，。！？、；：]/u;
const RIGHT = /[.?!:]*(?=[\s)\]}"'”’）】》」』，。！？、；：,;]|$)/uy;
const NAME = /(?:[\p{L}\p{N}_-]+(?:[.:][\p{L}\p{N}_-]+)*)?/uy;
const LINK_DEFINITION = /^(?: {0,3}>[ \t]?)* {0,3}\[(?:\\.|[^\[\]\\\r\n])+\]:[ \t]*(?:\r?\n[ \t]*)?(?:<(?:\\.|[^<>\\\r\n])*>|[^\s]+)(?:(?:[ \t]+|[ \t]*\r?\n[ \t]*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\((?:\\.|[^()\\])*\)))?/;
const MENTION = /@\[(?:\\.|[^\\\]\r\n])*\]\(illusion-session:[\da-fA-F]{6,64}\)|@"[^"\r\n]*"|@[^\s`"\[\]()]+/y;

function endsTokenAt(text: string, pos: number): boolean {
  RIGHT.lastIndex = pos;
  return RIGHT.test(text);
}

function afterCode(text: string, start: number, marker: string): number {
  const fence = marker.length >= 3 && /^ {0,3}$/.test(text.slice(text.lastIndexOf('\n', start - 1) + 1, start));
  const end = start + marker.length;
  if (fence) {
    const closing = new RegExp(`^ {0,3}${marker[0]}{${marker.length},}[ \\t]*\\r?$`, 'gm');
    closing.lastIndex = end;
    return closing.exec(text) ? closing.lastIndex : text.length;
  }
  if (marker[0] === '~') return end;
  const ticks = /`+/g;
  ticks.lastIndex = end;
  for (let run; (run = ticks.exec(text)); ) if (run[0] === marker) return ticks.lastIndex;
  return text.length; // 用户仍在输入的未闭合行内代码
}

/** 链接目的地可含嵌套括号，引号标题中也可出现 ')' 或斜杠。 */
function afterLink(text: string, pos: number): number {
  let depth = 1;
  let quote = '';
  for (; pos < text.length; pos++) {
    const char = text[pos]!;
    if (char === '\\') { pos++; continue; }
    if (char === '\n' && (text[pos + 1] === '\n' || text.startsWith('\r\n', pos + 1))) break;
    if (quote) {
      if (char === quote) quote = '';
    } else if (char === '<') {
      quote = '>';
    } else if ((char === '"' || char === "'") && /\s/.test(text[pos - 1]!)) {
      quote = char;
    } else if (char === '(') {
      depth++;
    } else if (char === ')' && --depth === 0) {
      return pos + 1;
    }
  }
  return pos;
}

/** 仅返回词法范围；着色与激活还须再做目录精确查找。 */
export function scanPromptTokens(text: string): PromptToken[] {
  // 链接定义包含目的地和可选的引号标题；行内链接目的地用配对扫描跳过，
  // 其普通可见标签仍是正文。
  const markers = new RegExp([LINK_DEFINITION.source, /(?<![\\`])`+|~{3,}|(?:\b[a-z][a-z\d+.-]*:\/\/|\/\/|\bwww\.)[^\s<>`"'，。！？、；]+|\]\(|@|\//.source].join('|'), 'gim');
  const tokens: PromptToken[] = [];
  for (let match; (match = markers.exec(text)); ) {
    const marker = match[0];
    const start = match.index;
    if (marker.startsWith('`') || marker.startsWith('~')) {
      markers.lastIndex = afterCode(text, start, marker);
      continue;
    }
    if (marker === '](') {
      markers.lastIndex = afterLink(text, markers.lastIndex);
      continue;
    }
    if (marker === '@') {
      MENTION.lastIndex = start;
      if (MENTION.exec(text)) {
        if (start === 0 || /\s/.test(text[start - 1]!)) tokens.push({ kind: 'mention', start, end: MENTION.lastIndex });
        markers.lastIndex = MENTION.lastIndex;
      } else if (text[start + 1] === '[') {
        const label = /@\[(?:\\.|[^\\\]\r\n])*(?:\](?:\([^()\r\n]*\))?)?/y;
        label.lastIndex = start;
        if (label.exec(text)) markers.lastIndex = label.lastIndex;
      } else if (text[start + 1] === '"') {
        // 未闭合的引号路径本身可能含空格和斜杠。
        const newline = text.indexOf('\n', start);
        markers.lastIndex = newline < 0 ? text.length : newline;
      }
      continue;
    }
    if (marker !== '/' || (start > 0 && !LEFT.test(text[start - 1]!))) continue;
    NAME.lastIndex = start + 1;
    const name = NAME.exec(text)![0];
    const end = start + 1 + name.length;
    markers.lastIndex = end;
    if (!endsTokenAt(text, end)) continue; // 路径段、查询串、锚点或其他紧邻文本
    tokens.push({ kind: 'slash', start, end });
  }
  return tokens;
}

/** 只给已解析的资源着色（规范名优先于唯一短名）。 */
export function resourceRanges(
  text: string, tokens: ReturnType<typeof scanPromptTokens>, catalog: SlashCatalog,
  recorded?: TranscriptItem['slash_references'],
) {
  const ranges: (TextRange & { kind: 'mention' | SlashCommandEntry['kind'] })[] = [];
  for (const token of tokens) {
    if (token.kind === 'mention') { ranges.push({ ...token, kind: 'mention' }); continue; }
    const raw = text.slice(token.start, token.end);
    const kind = recorded != null ? recorded[raw] : catalog.resources.get(raw.toLowerCase())?.kind;
    if (kind === 'command' || kind === 'skill') ranges.push({ start: token.start, end: token.end, kind });
  }
  return ranges;
}

export function slashAtCaret(text: string, tokens: ReturnType<typeof scanPromptTokens>, caret: number): SlashToken | null {
  for (const token of tokens) {
    if (token.kind !== 'slash' || caret <= token.start) continue;
    // 允许刚输入的命名空间分隔符，此时下一段尚为空。
    const end = caret === token.end + 1 && /[.:]/.test(text[token.end] ?? '') ? caret : token.end;
    if (caret <= end) return { start: token.start, end, query: text.slice(token.start + 1, caret) };
  }
  return null;
}

export function standaloneToken(text: string, token: TextRange | null): boolean {
  return token ? !text.slice(0, token.start).trim() && !text.slice(token.end).trim() : !text.trim();
}

const SECTION_ORDER = { 'BUILT-IN COMMANDS': 0, 'COMMANDS': 1, 'PLUGIN COMMANDS': 2, 'SKILLS': 3, 'PLUGIN SKILLS': 4 } as const;

export function slashSection(entry: SlashChoice): keyof typeof SECTION_ORDER {
  if (entry.kind === 'builtin') return 'BUILT-IN COMMANDS';
  if (entry.kind === 'command') return entry.source === 'plugin' ? 'PLUGIN COMMANDS' : 'COMMANDS';
  return entry.source === 'plugin' ? 'PLUGIN SKILLS' : 'SKILLS';
}

export function filterSlashChoices(catalog: SlashCatalog, query: string, includeBuiltins: boolean): SlashChoice[] {
  const needle = query.toLowerCase();
  const choices = catalog.choices.filter(entry => (includeBuiltins || entry.kind !== 'builtin') && entry.name.toLowerCase().includes(needle));
  const wanted = '/' + needle;
  const preferred = needle ? catalog.resources.get(wanted) ?? choices.find(entry => entry.name.toLowerCase() === wanted) : undefined;
  // 将精确/唯一别名所在整节前移，保证每个分节标题只出现一次。
  const firstSection = preferred && slashSection(preferred);
  const rank = (entry: SlashChoice) => {
    if (entry === preferred) return -2;
    const section = slashSection(entry);
    return section === firstSection ? -1 : SECTION_ORDER[section];
  };
  return choices.sort((a, b) => rank(a) - rank(b));
}

/** 整体替换 token，即使光标位于名称中间；保留草稿两侧文本。 */
export function insertSlashChoice(text: string, range: TextRange, name: string, fromToken: boolean) {
  const before = text.slice(0, range.start);
  const after = text.slice(range.end);
  const lead = !fromToken && before && !LEFT.test(before.slice(-1)) ? ' ' : '';
  const suffix = !after || (!fromToken && !endsTokenAt(text, range.end)) ? ' ' : '';
  const insertion = lead + name + suffix;
  return { value: before + insertion + after, caret: before.length + insertion.length + (/^[ \t]/.test(after) ? 1 : 0) };
}
