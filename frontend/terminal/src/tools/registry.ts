/**
 * @fileoverview 工具注册表
 *
 * 注册所有工具的渲染器，提供按名称查找的能力。
 * 未注册的工具使用 GenericTool 作为回退。
 *
 * @module tools/registry
 */

import type {Tool, ToolRegistry} from './ToolInterface.js';
import {genericTool, createGenericTool} from './implementations/GenericTool.js';
import {bashTool, powershellTool} from './implementations/BashTool.js';
import {readTool} from './implementations/ReadTool.js';
import {editTool} from './implementations/EditTool.js';
import {writeTool} from './implementations/WriteTool.js';
import {grepTool, globTool} from './implementations/SearchTool.js';
import {agentTool} from './implementations/AgentTool.js';
import {webSearchTool, webFetchTool} from './implementations/WebTool.js';
import {lspTool} from './implementations/LspTool.js';
import {skillTool} from './implementations/SkillTool.js';
import {taskOutputTool, taskStopTool} from './implementations/TaskTool.js';
import {enterPlanModeTool, exitPlanModeTool} from './implementations/PlanTool.js';
import {enterWorktreeTool, exitWorktreeTool} from './implementations/WorktreeTool.js';
import {cronTool} from './implementations/CronTool.js';
import {mcpTool, listMcpResourcesTool, readMcpResourceTool} from './implementations/McpTool.js';
import {getGoalTool, createGoalTool, updateGoalTool} from './implementations/GoalTool.js';

/** 全局工具注册表实例 */
const registry: ToolRegistry = new Map();

/** 是否已初始化 */
let initialized = false;

/**
 * 注册工具到注册表
 */
function register(tool: Tool): void {
	registry.set(tool.name, tool);
}

/**
 * 注册多个工具名指向同一渲染器
 */
function registerAliases(tool: Tool, aliases: string[]): void {
	register(tool);
	for (const alias of aliases) {
		registry.set(alias, tool);
	}
}

/**
 * 初始化注册表，注册所有工具渲染器
 */
function ensureInitialized(): void {
	if (initialized) return;
	initialized = true;

	// Shell 类
	registerAliases(bashTool, ['bash']);
	registerAliases(powershellTool, ['powershell']);

	// 文件读取类
	registerAliases(readTool, ['read_file', 'read', 'fileread']);

	// 文件编辑类
	registerAliases(editTool, ['edit_file', 'edit', 'fileedit']);
	registerAliases(writeTool, ['write_file', 'write', 'filewrite']);

	// 搜索类
	registerAliases(grepTool, ['grep']);
	registerAliases(globTool, ['glob']);

	// 子代理类
	registerAliases(agentTool, ['agent']);

	// Web 类
	registerAliases(webSearchTool, ['web_search']);
	registerAliases(webFetchTool, ['web_fetch']);

	// LSP
	registerAliases(lspTool, ['lsp']);

	// Skill
	registerAliases(skillTool, ['skill']);

	// 任务管理类
	register(taskOutputTool);
	register(taskStopTool);

	// 计划模式类
	register(enterPlanModeTool);
	register(exitPlanModeTool);

	// 工作树类
	register(enterWorktreeTool);
	register(exitWorktreeTool);

	// Cron
	register(cronTool);

	// MCP 动态工具
	registerAliases(mcpTool, ['mcp']);
	register(listMcpResourcesTool);
	register(readMcpResourceTool);

	// 显式注册需要显示名称的通用工具
	register(createGenericTool('ask_user_question', 'AskUserQuestion'));
	register(createGenericTool('sleep', 'Sleep'));
	register(createGenericTool('repl', 'REPL'));
	register(createGenericTool('send_message', 'SendMessage'));
	register(createGenericTool('team_create', 'TeamCreate'));
	register(createGenericTool('team_delete', 'TeamDelete'));
	register(createGenericTool('mcp_auth', 'McpAuth'));

	// Goal 工具（get_goal / create_goal / update_goal）
	register(getGoalTool);
	register(createGoalTool);
	register(updateGoalTool);

	// 渠道媒体工具（当前渠道内发/收文件）
	register(createGenericTool('send_media', 'SendMedia'));
	register(createGenericTool('receive_media', 'ReceiveMedia'));
	// 跨渠道文件传输
	register(createGenericTool('list_channel_sessions', 'ListChannelSessions'));
	register(createGenericTool('send_to_channel', 'SendToChannel'));

	// 专用工具（需要特殊处理）
	register({
		name: 'todo_write',
		displayName: () => 'TodoWrite',
		renderToolUseMessage: (input?: Record<string, unknown>) => {
			if (!input?.todos || !Array.isArray(input.todos)) return '';
			const todos = input.todos as Array<{status?: string}>;
			const total = todos.length;
			const completed = todos.filter((t) => t.status === 'completed').length;
			return `${completed}/${total}`;
		},
		renderToolResultMessage: () => 'Done',
	});
	register({
		name: 'config',
		displayName: () => 'Config',
		renderToolUseMessage: (input?: Record<string, unknown>) => {
			if (!input) return '';
			const action = String(input.action ?? '');
			const key = String(input.key ?? input.setting ?? '');
			if (action === 'get' && key) return `get ${key}`;
			if (action === 'set' && key) return `set ${key}`;
			return action;
		},
		renderToolResultMessage: () => 'Done',
	});
}

/**
 * 根据工具名查找渲染器
 *
 * @param name - 工具名称（匹配后端 tool_name）
 * @returns 工具渲染器，未注册时返回 GenericTool
 */
export function getTool(name: string): Tool {
	ensureInitialized();
	return registry.get(name) ?? genericTool;
}

/**
 * 获取当前活动工具的活动描述
 *
 * @param toolName - 工具名称
 * @param input - 工具输入参数
 * @returns 活动描述文本
 */
export function getActivityDescription(
	toolName: string,
	input?: Record<string, unknown>,
): string {
	const tool = getTool(toolName);
	return tool.getActivityDescription?.(input) ?? toolName;
}
