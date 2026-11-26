/**
 * @fileoverview 不可变光标模型
 *
 * 封装 {measuredText, offset}，所有操作返回新 Cursor 实例。
 * 提供 display-line 感知的导航（up/down）、编辑（insert/backspace）、渲染。
 *
 * @module Cursor
 */

import chalk from 'chalk';
import stringWidth from 'string-width';
import {MeasuredText} from './MeasuredText.js';

interface Position {
	line: number;
	column: number;
}

/**
 * 不可变光标模型
 *
 * 封装 {measuredText, offset}，所有操作返回新 Cursor 实例。
 */
export class Cursor {
	readonly offset: number;
	readonly measuredText: MeasuredText;

	constructor(measuredText: MeasuredText, offset: number = 0) {
		this.measuredText = measuredText;
		this.offset = Math.max(0, Math.min(measuredText.text.length, offset));
	}

	/**
	 * 工厂方法：从原始文本创建 Cursor
	 * columns 应为实际换行宽度（已扣除边框和光标预留）
	 */
	static fromText(text: string, columns: number, offset: number = 0): Cursor {
		return new Cursor(new MeasuredText(text, columns), offset);
	}

	get text(): string {
		return this.measuredText.text;
	}

	get columns(): number {
		return this.measuredText.columns;
	}

	getPosition(): Position {
		return this.measuredText.getPositionFromOffset(this.offset);
	}

	equals(other: Cursor): boolean {
		return this.offset === other.offset && this.measuredText === other.measuredText;
	}

	isAtStart(): boolean {
		return this.offset === 0;
	}

	isAtEnd(): boolean {
		return this.offset >= this.text.length;
	}

	// === 导航 ===

	left(): Cursor {
		if (this.offset === 0) return this;
		return new Cursor(this.measuredText, this.offset - 1);
	}

	right(): Cursor {
		if (this.offset >= this.text.length) return this;
		return new Cursor(this.measuredText, this.offset + 1);
	}

	/**
	 * 上移：先尝试 display line，到顶则尝试 logical line
	 * @param targetColumn - 目标列位置（用于保持列位置），默认使用当前列
	 */
	up(targetColumn?: number): Cursor {
		const pos = this.getPosition();
		if (pos.line > 0) {
			// display line 上移
			const prevLineText = this.measuredText.getWrappedText()[pos.line - 1] ?? '';
			const prevWidth = stringWidth(prevLineText);
			const desired = targetColumn ?? pos.column;
			const newCol = Math.min(desired, prevWidth);
			const newOffset = this.measuredText.getOffsetFromPosition({line: pos.line - 1, column: newCol});
			return new Cursor(this.measuredText, newOffset);
		}
		// display line 已在顶部，尝试 logical line 上移
		const logicalStart = this.findLogicalLineStart();
		if (logicalStart > 0) {
			// 移到上一逻辑行的末尾
			return new Cursor(this.measuredText, logicalStart - 1);
		}
		return this;
	}

	/**
	 * 下移：先尝试 display line，到底则尝试 logical line
	 * @param targetColumn - 目标列位置（用于保持列位置），默认使用当前列
	 */
	down(targetColumn?: number): Cursor {
		const pos = this.getPosition();
		if (pos.line < this.measuredText.lineCount - 1) {
			// display line 下移
			const nextLineText = this.measuredText.getWrappedText()[pos.line + 1] ?? '';
			const nextWidth = stringWidth(nextLineText);
			const desired = targetColumn ?? pos.column;
			const newCol = Math.min(desired, nextWidth);
			const newOffset = this.measuredText.getOffsetFromPosition({line: pos.line + 1, column: newCol});
			return new Cursor(this.measuredText, newOffset);
		}
		// display line 已在底部，尝试 logical line 下移
		const logicalEnd = this.findLogicalLineEnd();
		if (logicalEnd < this.text.length) {
			return new Cursor(this.measuredText, logicalEnd);
		}
		return this;
	}

	startOfLine(): Cursor {
		const pos = this.getPosition();
		const newOffset = this.measuredText.getOffsetFromPosition({line: pos.line, column: 0});
		return new Cursor(this.measuredText, newOffset);
	}

	endOfLine(): Cursor {
		const pos = this.getPosition();
		const lineLength = this.measuredText.getLineLength(pos.line);
		const newOffset = this.measuredText.getOffsetFromPosition({line: pos.line, column: lineLength});
		return new Cursor(this.measuredText, newOffset);
	}

	// === Logical line 辅助 ===

	/**
	 * 查找当前逻辑行起始 offset
	 */
	private findLogicalLineStart(fromOffset: number = this.offset): number {
		const prevNewline = this.text.lastIndexOf('\n', fromOffset - 1);
		return prevNewline === -1 ? 0 : prevNewline + 1;
	}

	/**
	 * 查找当前逻辑行结束 offset（不含 \n）
	 */
	private findLogicalLineEnd(fromOffset: number = this.offset): number {
		const nextNewline = this.text.indexOf('\n', fromOffset);
		return nextNewline === -1 ? this.text.length : nextNewline;
	}

	// === 编辑 ===

	/**
	 * 在光标处插入文本，返回新 Cursor
	 */
	insert(insertString: string): Cursor {
		const normalized = insertString.normalize('NFC');
		const newText = this.text.slice(0, this.offset) + normalized + this.text.slice(this.offset);
		return Cursor.fromText(newText, this.columns, this.offset + normalized.length);
	}

	/**
	 * 删除光标前一个字符
	 */
	backspace(): Cursor {
		if (this.isAtStart()) return this;
		const newText = this.text.slice(0, this.offset - 1) + this.text.slice(this.offset);
		return Cursor.fromText(newText, this.columns, this.offset - 1);
	}

	/**
	 * 删除光标处一个字符
	 */
	del(): Cursor {
		if (this.isAtEnd()) return this;
		const newText = this.text.slice(0, this.offset) + this.text.slice(this.offset + 1);
		return Cursor.fromText(newText, this.columns, this.offset);
	}

	/**
	 * 删除光标到当前逻辑行行首的内容
	 *
	 * 特殊处理：当光标紧跟在 \n 之后（逻辑行首），删除该 \n，
	 * 使连续 Ctrl+U 能跨行逐行删除。
	 */
	deleteToLogicalLineStart(): Cursor {
		// 光标紧跟在 \n 之后：删除该 \n（连接到上一行）
		if (this.offset > 0 && this.text[this.offset - 1] === '\n') {
			const newText = this.text.slice(0, this.offset - 1) + this.text.slice(this.offset);
			return Cursor.fromText(newText, this.columns, this.offset - 1);
		}
		const logicalStart = this.findLogicalLineStart();
		if (this.offset === logicalStart) return this;
		const newText = this.text.slice(0, logicalStart) + this.text.slice(this.offset);
		return Cursor.fromText(newText, this.columns, logicalStart);
	}

	/**
	 * 删除光标到当前显示行（display line）行首的内容
	 *
	 * display line 是 wrap 后的视觉行，不是逻辑行。
	 * 连续调用时，到达 display line 行首后删除前一个字符，跨行继续。
	 */
	deleteToDisplayLineStart(): Cursor {
		// 光标紧跟在 \n 之后：删除该 \n
		if (this.offset > 0 && this.text[this.offset - 1] === '\n') {
			const newText = this.text.slice(0, this.offset - 1) + this.text.slice(this.offset);
			return Cursor.fromText(newText, this.columns, this.offset - 1);
		}
		const displayStart = this.startOfLine();
		if (displayStart.offset === this.offset) {
			// 已在 display line 行首，删除前一个字符（与上一行连接）
			return this.backspace();
		}
		const newText = this.text.slice(0, displayStart.offset) + this.text.slice(this.offset);
		return Cursor.fromText(newText, this.columns, displayStart.offset);
	}

	// === 视口与渲染 ===

	/**
	 * 计算视口起始行（确保光标在可见区域内）
	 */
	getViewportStartLine(maxVisibleLines?: number): number {
		if (maxVisibleLines === undefined || maxVisibleLines <= 0) return 0;
		const {line} = this.getPosition();
		const allLines = this.measuredText.getWrappedText();
		if (allLines.length <= maxVisibleLines) return 0;

		const half = Math.floor(maxVisibleLines / 2);
		let startLine = Math.max(0, line - half);
		const endLine = Math.min(allLines.length, startLine + maxVisibleLines);
		if (endLine - startLine < maxVisibleLines) {
			startLine = Math.max(0, endLine - maxVisibleLines);
		}
		return startLine;
	}

	/**
	 * 渲染带光标高亮的文本
	 *
	 * @param cursorChar - 光标字符（通常为 ' '）
	 * @param startLine - 视口起始行
	 * @param maxVisibleLines - 最大可见行数
	 * @returns 渲染后的字符串（含 ANSI 颜色码，行间用 \n 连接）
	 */
	render(cursorChar: string, startLine = 0, maxVisibleLines?: number, paint?: (part: string, offset: number) => string): string {
		const {line: cursorLine} = this.getPosition();
		const lines = this.measuredText.getWrappedLines();
		const endLine = maxVisibleLines && maxVisibleLines > 0 ? startLine + maxVisibleLines : lines.length;
		return lines.slice(startLine, endLine).map((line, i) => {
			const text = line.precededByNewline ? line.text : line.text.trimStart();
			const start = line.startOffset + line.text.length - text.length;
			const style = (part: string, relative = 0) => paint ? paint(part, start + relative) : part;
			if (cursorLine !== startLine + i) return style(text.trimEnd()) || ' ';

			const at = Math.max(0, Math.min(this.offset - start, text.length));
			const point = text.codePointAt(at);
			const char = point === undefined ? '' : String.fromCodePoint(point);
			return style(text.slice(0, at)) + chalk.inverse(char ? style(char, at) : cursorChar)
				+ style(text.slice(at + char.length).trimEnd(), at + char.length);
		}).join('\n');
	}
}
