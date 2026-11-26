/**
 * @fileoverview 输入框组合器控制
 *
 * terminal 前端输入框的草稿状态与斜杠资源选择器管理：
 * - useComposerDraft：挂载的 App 与独立 composer 共享的草稿/光标状态；
 * - useSlashPicker：基于 token 扫描的候选菜单（导航、补全、关闭）；
 * - ComposerController：组装 PromptInput 与 CommandPicker。
 * 所有偏移始终以原始草稿文本为准。
 *
 * @module ComposerController
 */

import React, {useCallback, useEffect, useMemo, useState} from 'react';
import type {Key} from 'ink';

import type {UiLanguage} from '../i18n.js';
import type {SlashCommand, TodoItemSnapshot} from '../types.js';
import {buildCommandCatalog, commandHints, completeSlash, isResource, referenceRanges, scanPromptTokens, slashTokenAt, type CommandCatalog, type CommandHint} from '../utils/slashCommands.js';
import {CommandPicker} from './CommandPicker.js';
import {PromptInput} from './PromptInput.js';

/** 挂载的 App 与独立 composer 共享；偏移始终以原始草稿为准。 */
export function useComposerDraft() {
	const [input, setInput] = useState('');
	const [caret, setCaret] = useState(0);
	const [cursorReset, setCursorReset] = useState(0);
	const replaceDraft = useCallback((value: string, offset: number) => {
		setInput(value);
		setCaret(offset);
		setCursorReset(c => c + 1);
	}, []);
	return {input, setInput, caret, setCaret, cursorReset, replaceDraft};
}

const hintId = (hint: CommandHint) => `${hint.kind}:${hint.name.toLowerCase()}`;

export function useSlashPicker(draft: ReturnType<typeof useComposerDraft>, catalog: CommandCatalog, enabled: boolean) {
	const {input, caret} = draft;
	const tokens = useMemo(() => scanPromptTokens(input), [input]);
	const ranges = useMemo(() => referenceRanges(input, catalog, {tokens}), [input, catalog, tokens]);
	const token = useMemo(() => enabled ? slashTokenAt(input, caret, tokens) : undefined, [enabled, input, caret, tokens]);
	const hints = useMemo(() => commandHints(catalog, token), [catalog, token]);
	const [dismissed, setDismissed] = useState<string>();
	const keyOf = (start: number, query: string) => `${start}:${query}`;
	const tokenKey = token && keyOf(token.start, token.query);
	const open = hints.length > 0 && tokenKey !== dismissed;
	const scope = open ? `${token!.standalone}:${tokenKey}` : '';
	const [selection, setSelection] = useState({scope: '', id: ''});
	const selectedIndex = selection.scope === scope && selection.id
		? Math.max(0, hints.findIndex(hint => hintId(hint) === selection.id)) : 0;
	const selectedId = open ? hintId(hints[selectedIndex]) : '';
	// 对本次渲染的候选求解；旧行消失时也允许落位到新的首行。
	if (selection.scope !== scope || selection.id !== selectedId) setSelection({scope, id: selectedId});
	useEffect(() => {
		setDismissed(previous => previous === tokenKey ? previous : undefined);
	}, [tokenKey]);

	function handleKey(key: Key, submit: (value: string) => void): boolean {
		if (!open || !token) return false;
		if (key.upArrow || key.downArrow) {
			const delta = key.downArrow ? 1 : -1;
			const next = Math.max(0, Math.min(hints.length - 1, selectedIndex + delta));
			setSelection({scope, id: hintId(hints[next])});
		} else if (key.escape) setDismissed(tokenKey);
		else if (key.tab || key.return) {
			const selected = hints[selectedIndex];
			const {value, caret: offset} = completeSlash(input, token, selected.name);
			if (key.return && token.standalone && !isResource(selected)) {
				submit(value.trimEnd());
			} else {
				// 后跟标点时光标停在标点之前；在用户再次编辑或移动光标前，
				// 不在刚补全的名称上重新打开菜单。
				setDismissed(keyOf(token.start, selected.name.slice(1)));
				draft.replaceDraft(value, offset);
			}
		} else return false;
		return true;
	}
	return {hints, selectedIndex, open, ranges, handleKey};
}

export function ComposerController({commands, slashCommands, busy, disabled, language, todoItems, toolName, onSubmit}: {
	commands: string[];
	slashCommands?: SlashCommand[];
	busy: boolean;
	disabled: boolean;
	language: UiLanguage;
	todoItems: TodoItemSnapshot[];
	toolName?: string;
	onSubmit: (value: string) => void;
}): React.JSX.Element | null {
	const draft = useComposerDraft();
	const catalog = useMemo(() => buildCommandCatalog(commands, slashCommands), [commands, slashCommands]);
	const picker = useSlashPicker(draft, catalog, !busy && !disabled);
	const submit = (value: string) => {
		if (!value.trim() || disabled || busy) return;
		onSubmit(value);
		draft.replaceDraft('', 0);
	};
	if (disabled || busy) return null;
	return (
		<>
			{picker.open && <CommandPicker hints={picker.hints} selectedIndex={picker.selectedIndex} />}
			<PromptInput busy={busy} input={draft.input} setInput={draft.setInput} onSubmit={submit}
				toolName={toolName} language={language} todoItems={todoItems} onPickerKey={key => picker.handleKey(key, submit)}
				onCursorChange={draft.setCaret} cursorReset={draft.cursorReset} initialCursorOffset={draft.caret}
				referenceRanges={picker.ranges} />
		</>
	);
}
