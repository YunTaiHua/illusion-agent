/**
 * @fileoverview 工具渲染接口定义
 *
 * 定义每个工具类型需要实现的渲染方法，取代通用的 summarizeInput + ToolResultBlock。
 *
 * @module tools/ToolInterface
 */

import type React from 'react';

/**
 * 工具渲染接口
 *
 * 每个工具类型实现此接口，提供定制的显示名称、参数渲染、结果渲染等。
 */
export interface Tool {
	/** 工具名，匹配后端 tool_name */
	readonly name: string;

	/**
	 * 用户可见显示名称
	 * @param input - 工具输入参数
	 * @returns 显示名称，如 "Bash", "Read", "Edit"
	 */
	displayName(input?: Record<string, unknown>): string;

	/**
	 * 渲染括号中的参数
	 * 如 Bash(git status), Read(src/foo.ts)
	 * @param input - 工具输入参数
	 * @returns 参数文本
	 */
	renderToolUseMessage(input?: Record<string, unknown>): string;

	/**
	 * 渲染工具结果摘要
	 * @param result - 原始结果文本
	 * @param input - 工具输入参数
	 * @param isBrief - 是否为简要模式
	 * @param structuredOutput - 结构化输出数据
	 * @returns 摘要文本字符串
	 */
	renderToolResultMessage(
		result: string,
		input?: Record<string, unknown>,
		isBrief?: boolean,
		structuredOutput?: Record<string, unknown>,
	): string;

	/**
	 * 活动描述，用于 Spinner
	 * @param input - 工具输入参数
	 * @returns 描述文本或 null
	 */
	getActivityDescription?(input?: Record<string, unknown>): string | null;
}

/**
 * 工具注册表类型
 */
export type ToolRegistry = Map<string, Tool>;
