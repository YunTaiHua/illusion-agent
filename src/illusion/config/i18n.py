"""
国际化消息模块
==============

本模块提供 CLI 输出的国际化（i18n）支持。
根据 settings.json 中的 ui_language 字段返回对应语言的文本。

使用示例：
    >>> from illusion.config.i18n import t
    >>> print(t("mcp_none"))
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

# i18n 消息表
MESSAGES: dict[str, dict[str, str]] = {
    # --- auth ---
    "select_api_format": {"zh-CN": "选择 API 格式:", "en-US": "Select API format:"},
    "custom_format": {"zh-CN": "自定义格式", "en-US": "Custom format"},
    "anthropic_label": {"zh-CN": "Anthropic (Claude API)", "en-US": "Anthropic (Claude API)"},
    "openai_label": {"zh-CN": "OpenAI / 兼容接口", "en-US": "OpenAI / compatible"},
    "copilot_label": {"zh-CN": "GitHub Copilot", "en-US": "GitHub Copilot"},
    "copilot_open_url": {"zh-CN": "请在浏览器中打开以下 URL 完成授权:", "en-US": "Open the following URL in your browser to authorize:"},
    "copilot_enter_code": {"zh-CN": "并输入代码: {code}", "en-US": "and enter code: {code}"},
    "copilot_waiting": {"zh-CN": "等待 GitHub 授权中...", "en-US": "Waiting for GitHub authorization..."},
    "copilot_auth_success": {"zh-CN": "GitHub Copilot 授权成功 (用户: {user})", "en-US": "GitHub Copilot authorized (user: {user})"},
    "copilot_no_subscription": {"zh-CN": "未订阅 GitHub Copilot，请先在 GitHub 上订阅", "en-US": "No GitHub Copilot subscription found, please subscribe on GitHub first"},
    "copilot_not_authenticated": {"zh-CN": "未认证 GitHub Copilot，请先运行 'illusion auth login'", "en-US": "GitHub Copilot not authenticated, run 'illusion auth login' first"},
    "copilot_device_expired": {"zh-CN": "设备码已过期，请重新运行登录", "en-US": "Device code expired, please retry login"},
    "copilot_auth_denied": {"zh-CN": "授权被拒绝", "en-US": "Authorization denied"},
    "codex_label": {"zh-CN": "OpenAI Codex (ChatGPT 订阅)", "en-US": "OpenAI Codex (ChatGPT subscription)"},
    "codex_not_found": {"zh-CN": "未找到 Codex CLI 认证，请先安装 Codex CLI 并运行 'codex auth login'", "en-US": "Codex CLI auth not found, please install Codex CLI and run 'codex auth login' first"},
    "codex_auth_success": {"zh-CN": "Codex 认证读取成功 (用户: {user})", "en-US": "Codex auth loaded successfully (user: {user})"},
    "codex_open_url": {"zh-CN": "请在浏览器中打开以下 URL 完成授权:", "en-US": "Open the following URL in your browser to authorize:"},
    "codex_enter_code": {"zh-CN": "并输入代码: {code}", "en-US": "and enter code: {code}"},
    "codex_waiting": {"zh-CN": "等待 ChatGPT 授权中...", "en-US": "Waiting for ChatGPT authorization..."},
    "codex_oauth_success": {"zh-CN": "Codex OAuth 授权成功 (用户: {user})", "en-US": "Codex OAuth authorized (user: {user})"},
    "codex_device_expired": {"zh-CN": "设备码已过期，请重新运行登录", "en-US": "Device code expired, please retry login"},
    "codex_auth_denied": {"zh-CN": "授权被拒绝", "en-US": "Authorization denied"},
    "codex_no_subscription": {"zh-CN": "未订阅 ChatGPT Plus/Pro，请先在 OpenAI 上订阅", "en-US": "No ChatGPT Plus/Pro subscription found, please subscribe on OpenAI first"},
    "codex_not_authenticated": {"zh-CN": "未认证 Codex，请先运行 'illusion auth login'", "en-US": "Codex not authenticated, run 'illusion auth login' first"},
    "enter_number": {"zh-CN": "输入序号", "en-US": "Enter number"},
    "invalid_selection": {"zh-CN": "无效选择", "en-US": "Invalid selection"},
    "enter_endpoint": {"zh-CN": "输入 API 端点", "en-US": "Enter API endpoint"},
    "select_auth_type": {"zh-CN": "选择认证方式:", "en-US": "Select authentication type:"},
    "auth_type_api_key": {"zh-CN": "标准 x-api-key 认证", "en-US": "Standard x-api-key auth"},
    "auth_type_auth_token": {"zh-CN": "Bearer Token 认证（LongCat 等）", "en-US": "Bearer Token auth (LongCat etc.)"},
    "enter_api_key": {"zh-CN": "输入 API 密钥", "en-US": "Enter API key"},
    "enter_auth_token": {"zh-CN": "输入 Bearer Token", "en-US": "Enter Bearer Token"},
    "enter_model": {"zh-CN": "输入模型名称", "en-US": "Enter model name"},
    "model_required": {"zh-CN": "模型名称不能为空", "en-US": "Model name cannot be empty"},
    "endpoint_required": {"zh-CN": "端点不能为空", "en-US": "Endpoint cannot be empty"},
    "api_key_required": {"zh-CN": "API 密钥不能为空", "en-US": "API key cannot be empty"},
    "env_saved": {"zh-CN": "环境 {env_key} 已保存并激活", "en-US": "Environment {env_key} saved and activated"},
    "no_envs": {"zh-CN": "未配置任何环境，请先运行 'illusion auth login'", "en-US": "No environments configured, run 'illusion auth login' first"},
    "env_status_title": {"zh-CN": "环境认证状态:", "en-US": "Environment credential status:"},
    "col_env": {"zh-CN": "环境", "en-US": "Env"},
    "col_format": {"zh-CN": "格式", "en-US": "Format"},
    "col_model": {"zh-CN": "模型", "en-US": "Model"},
    "col_endpoint": {"zh-CN": "端点", "en-US": "Endpoint"},
    "col_credential": {"zh-CN": "凭据", "en-US": "Credential"},
    "configured": {"zh-CN": "已配置", "en-US": "configured"},
    "missing": {"zh-CN": "未配置", "en-US": "missing"},
    "active_mark": {"zh-CN": "← 当前", "en-US": "<-- active"},
    "select_env_to_logout": {"zh-CN": "选择要清除凭据的环境:", "en-US": "Select environment to clear credentials:"},
    "credential_cleared": {"zh-CN": "已清除环境 {env_key} 的凭据", "en-US": "Credentials cleared for {env_key}"},
    "select_env_to_switch": {"zh-CN": "选择要切换的环境:", "en-US": "Select environment to switch to:"},
    "switched_to": {"zh-CN": "已切换到环境 {env_key}", "en-US": "Switched to environment {env_key}"},
    "env_not_found": {"zh-CN": "环境 {env_key} 不存在", "en-US": "Environment {env_key} not found"},
    "select_language": {"zh-CN": "选择界面语言 | Select interface language:", "en-US": "选择界面语言 | Select interface language:"},
    "language_set": {"zh-CN": "界面语言已设置为: {lang}", "en-US": "Interface language set to: {lang}"},
    "skip_default": {"zh-CN": "回车跳过，使用默认值", "en-US": "Press Enter to skip, use default"},
    "model_added": {"zh-CN": "已向 {env_key} 添加模型 {model_key}: {model_name}", "en-US": "Added {model_key} to {env_key}: {model_name}"},
    "add_another_model": {"zh-CN": "继续添加模型？[y/N]", "en-US": "Add another model? [y/N]"},
    "existing_envs": {"zh-CN": "已有环境:", "en-US": "Existing environments:"},
    "create_new_env": {"zh-CN": "创建新环境", "en-US": "Create new env"},
    "env_not_exist": {"zh-CN": "环境 {env_key} 不存在", "en-US": "Environment {env_key} does not exist"},
    "no_existing_env": {"zh-CN": "暂无环境，请先运行 illusion auth login 创建环境", "en-US": "No environments found. Run illusion auth login first"},
    "no_models": {"zh-CN": "(无模型)", "en-US": "(no models)"},
    # --- 后端事件 ---
    "task_stopped": {"zh-CN": "当前任务已停止。", "en-US": "Current task stopped."},
    "no_active_task": {"zh-CN": "没有正在执行的任务", "en-US": "No active task to stop"},
    "bg_agent_waiting": {"zh-CN": "等待后台代理完成", "en-US": "Waiting for background agent"},
    "bg_agent_resuming": {"zh-CN": "后台代理已完成，继续执行", "en-US": "Background agent completed, resuming"},
    "default_endpoint": {"zh-CN": "默认", "en-US": "default"},
    # --- memory / title 开关 ---
    "memory_usage": {
        "zh-CN": "用法: /memory [on|off|toggle|status|auto on|auto off]",
        "en-US": "Usage: /memory [on|off|toggle|status|auto on|auto off]",
    },
    "memory_enabled": {"zh-CN": "记忆功能已启用", "en-US": "Memory enabled"},
    "memory_disabled": {"zh-CN": "记忆功能已禁用", "en-US": "Memory disabled"},
    "memory_auto_on": {"zh-CN": "后台自动提取已启用", "en-US": "Auto extraction enabled"},
    "memory_auto_off": {"zh-CN": "后台自动提取已禁用", "en-US": "Auto extraction disabled"},
    "memory_show": {
        "zh-CN": "记忆功能: {enabled} | 后台自动提取: {auto}",
        "en-US": "Memory: {enabled} | Auto extract: {auto}",
    },
    "memory_auto_show": {
        "zh-CN": "后台自动提取: {state}",
        "en-US": "Auto extract: {state}",
    },
    "memory_auto_need_mem": {
        "zh-CN": "需先开启记忆功能，后台自动提取才可启用",
        "en-US": "Enable memory first before enabling auto extract",
    },
    # --- mcp ---
    "mcp_none": {"zh-CN": "未配置 MCP 服务器", "en-US": "No MCP servers configured"},
    "mcp_invalid_json": {"zh-CN": "无效 JSON: {exc}", "en-US": "Invalid JSON: {exc}"},
    "mcp_invalid_config": {"zh-CN": "无效的 MCP 服务器配置: {exc}", "en-US": "Invalid MCP server config: {exc}"},
    "mcp_added": {"zh-CN": "已添加 MCP 服务器: {name}", "en-US": "Added MCP server: {name}"},
    "mcp_not_found": {"zh-CN": "未找到 MCP 服务器: {name}", "en-US": "MCP server not found: {name}"},
    "mcp_removed": {"zh-CN": "已移除 MCP 服务器: {name}", "en-US": "Removed MCP server: {name}"},
    # --- plugin ---
    "plugin_none": {"zh-CN": "未安装插件", "en-US": "No plugins installed"},
    "plugin_enabled": {"zh-CN": "启用", "en-US": "enabled"},
    "plugin_disabled": {"zh-CN": "禁用", "en-US": "disabled"},
    "plugin_installed": {"zh-CN": "已安装插件: {name}", "en-US": "Installed plugin: {name}"},
    "plugin_uninstalled": {"zh-CN": "已卸载插件: {name}", "en-US": "Uninstalled plugin: {name}"},
    # --- cron ---
    "cron_already_running": {"zh-CN": "调度器已在运行", "en-US": "Cron scheduler is already running"},
    "cron_started": {"zh-CN": "调度器已启动 (pid={pid})", "en-US": "Cron scheduler started (pid={pid})"},
    "cron_stopped": {"zh-CN": "调度器已停止", "en-US": "Cron scheduler stopped"},
    "cron_not_running": {"zh-CN": "调度器未在运行", "en-US": "Cron scheduler is not running"},
    "cron_serve_started": {"zh-CN": "cron 守护进程已启动 (pid={pid})", "en-US": "Cron daemon started (pid={pid})"},
    "cron_serve_interrupted": {"zh-CN": "收到中断信号，正在停止 cron 守护进程…", "en-US": "Interrupt received, stopping cron daemon…"},
    "cron_state_running": {"zh-CN": "运行中", "en-US": "running"},
    "cron_state_stopped": {"zh-CN": "已停止", "en-US": "stopped"},
    "cron_jobs_none": {"zh-CN": "无启用的 cron 任务", "en-US": "No enabled cron jobs"},
    "cron_recurring": {"zh-CN": "周期", "en-US": "recurring"},
    "cron_oneshot": {"zh-CN": "单次", "en-US": "one-shot"},
    "cron_never": {"zh-CN": "从未", "en-US": "never"},
    "cron_na": {"zh-CN": "无", "en-US": "n/a"},
    "cron_errors": {"zh-CN": "错误: {n}", "en-US": "errors: {n}"},
    "cron_prompt_label": {"zh-CN": "提示词", "en-US": "prompt"},
    "cron_last_label": {"zh-CN": "上次", "en-US": "last"},
    "cron_next_label": {"zh-CN": "下次", "en-US": "next"},
    "cron_job_not_found": {"zh-CN": "未找到定时任务: {name}", "en-US": "Cron job not found: {name}"},
    "cron_enabled": {"zh-CN": "已启用", "en-US": "enabled"},
    "cron_disabled": {"zh-CN": "已禁用", "en-US": "disabled"},
    "cron_job_state": {"zh-CN": "任务 '{name}' {state}", "en-US": "Job '{name}' {state}"},
    "cron_no_prompt": {"zh-CN": "任务无提示词: {name}", "en-US": "Job has no prompt: {name}"},
    "cron_running_job": {"zh-CN": "正在执行任务 '{name}'...", "en-US": "Running job '{name}'..."},
    "cron_finished": {"zh-CN": "完成: {status} (rc={rc})", "en-US": "Finished: {status} (rc={rc})"},
    "cron_output": {"zh-CN": "输出:", "en-US": "Output:"},
    "cron_error": {"zh-CN": "错误:", "en-US": "Error:"},
    "cron_no_history": {"zh-CN": "无执行历史", "en-US": "No execution history"},
    "cron_no_log": {"zh-CN": "未找到调度器日志，请先运行: illusion cron start", "en-US": "No scheduler log found. Start with: illusion cron start"},
    # --- session ---
    "session_not_found_prev": {"zh-CN": "未找到之前的会话", "en-US": "No previous session found"},
    "session_continuing": {"zh-CN": "继续会话: {summary}", "en-US": "Continuing session: {summary}"},
    "session_resume_requires_id": {"zh-CN": "--resume 需要指定会话 ID", "en-US": "--resume requires a session ID"},
    "session_not_found_id": {"zh-CN": "未找到会话: {session_id}", "en-US": "Session not found: {session_id}"},
    "session_no_saved": {"zh-CN": "未找到保存的会话", "en-US": "No saved sessions found"},
    "session_saved_list": {"zh-CN": "已保存的会话:", "en-US": "Saved sessions:"},
    "session_msg_count": {"zh-CN": "{n} 条消息", "en-US": "{n} msgs"},
    "session_enter_id": {"zh-CN": "输入会话序号或 ID", "en-US": "Enter session number or ID"},
    "session_not_found": {"zh-CN": "未找到会话: {id}", "en-US": "Session not found: {id}"},
    "print_requires_prompt": {"zh-CN": "错误: -p/--print 需要提供提示词，例如 -p '你的提示词'", "en-US": "Error: -p/--print requires a prompt, e.g. -p 'your prompt'"},
    "continue_requires_print": {"zh-CN": "--continue/--resume 需要配合 -p 使用", "en-US": "--continue/--resume requires -p"},
    # --- settings ---
    "cwd_invalid": {"zh-CN": "settings.json中配置的working_directory不存在或不是目录: {path}", "en-US": "working_directory in settings.json does not exist or is not a directory: {path}"},
    "no_api_key": {"zh-CN": "未找到 API 密钥。请使用 'illusion auth login' 配置", "en-US": "No API key found. Run 'illusion auth login' to configure"},
    "no_auth": {"zh-CN": "未找到认证信息。请使用 'illusion auth login' 配置", "en-US": "No credentials found. Run 'illusion auth login' to configure"},
    # --- workspace / set command ---
    "set_current_working_directory": {"zh-CN": "当前工作目录：{path}", "en-US": "Current working directory: {path}"},
    "set_no_working_directory": {"zh-CN": "尚未设置工作目录", "en-US": "No working directory set"},
    "set_usage": {"zh-CN": "用法：illusion set [working_directory]，例如 illusion set \"E:\\\\Projects\\\\my-project\"", "en-US": "Usage: illusion set [working_directory], e.g. illusion set \"E:\\\\Projects\\\\my-project\""},
    "set_saved": {"zh-CN": "工作目录已设置为：{path}", "en-US": "Working directory set to: {path}"},
    "set_invalid_path": {"zh-CN": "路径无效：{path}", "en-US": "Invalid path: {path}"},
    "working_dir_prompt": {"zh-CN": "是否设置工作目录？输入目录路径（回车跳过）：", "en-US": "Set working directory? Enter path (Enter to skip): "},
    "working_dir_skipped": {"zh-CN": "未设置工作目录（可稍后使用 'illusion set <path>' 设置）", "en-US": "Working directory not set (use 'illusion set <path>' later)"},
    "working_dir_set_failed": {"zh-CN": "工作目录设置失败：{error}（可稍后使用 'illusion set <path>' 设置）", "en-US": "Working directory setup failed: {error} (use 'illusion set <path>' later)"},
    # --- manager ---
    "unknown_env": {"zh-CN": "未知环境: {env_key}", "en-US": "Unknown environment: {env_key}"},
    "cannot_remove_active_env": {"zh-CN": "不能移除当前活动环境", "en-US": "Cannot remove the active environment"},
    # --- model ---
    "model_active": {"zh-CN": "当前模型：{model}", "en-US": "Active model: {model}"},
    "model_active_detail": {"zh-CN": "  模型：{name}\n  API 格式：{fmt}\n  基础 URL：{url}", "en-US": "  model: {name}\n  api_format: {fmt}\n  base_url: {url}"},
    "model_list_title": {"zh-CN": "模型列表：", "en-US": "Models:"},
    "model_set_to": {"zh-CN": "模型已设置为 {ref}：{name}", "en-US": "Model set to {ref}: {name}"},
    "model_unknown": {"zh-CN": "未知模型：{ref}。使用 /model list 查看可用模型。", "en-US": "Unknown model: {ref}. Use /model list to see available models."},
    "model_usage": {"zh-CN": "用法：/model [show|set MODEL]", "en-US": "Usage: /model [show|set MODEL]"},
    "model_env_model": {"zh-CN": "模型：{name}", "en-US": "model: {name}"},
    "model_api_format": {"zh-CN": "API 格式：{fmt}", "en-US": "api_format: {fmt}"},
    "model_base_url": {"zh-CN": "基础 URL：{url}", "en-US": "base_url: {url}"},
    "model_default_url": {"zh-CN": "（默认）", "en-US": "(default)"},
    # --- compact ---
    "compact_warning_approaching": {"zh-CN": "上下文使用量：~{pct}% — 接近自动压缩阈值", "en-US": "Context usage: ~{pct}% — approaching auto-compact threshold"},
    "compact_compacted": {"zh-CN": "已压缩上下文以释放空间", "en-US": "Context compacted to free up space"},
    "compact_overflow_detected": {"zh-CN": "检测到上下文溢出，尝试响应式压缩…", "en-US": "Context overflow detected, attempting reactive compact..."},
    "compact_reactive_success": {"zh-CN": "响应式压缩成功，正在重试请求…", "en-US": "Reactive compact succeeded, retrying request..."},
    "compact_overflow_failed": {"zh-CN": "上下文溢出且响应式压缩失败：{error}", "en-US": "Context overflow and reactive compact failed: {error}"},
    "compact_network_error": {"zh-CN": "网络错误：{error}。请检查网络连接后重试。", "en-US": "Network error: {error}. Check your internet connection and try again."},
    "compact_api_error": {"zh-CN": "API 错误：{error}", "en-US": "API error: {error}"},
    "compact_result": {"zh-CN": "已压缩对话：{before} → {after} 条消息（节省 ~{saved} tokens）。", "en-US": "Compacted conversation from {before} to {after} messages (saved ~{saved} tokens)."},
    "compact_summary_prefix": {"zh-CN": "本会话从之前超出上下文限制的对话继续。以下摘要涵盖对话的早期部分。", "en-US": "This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation."},
    "compact_recent_preserved": {"zh-CN": "最近的消息已原样保留。", "en-US": "Recent messages are preserved verbatim."},
    "compact_suppress_followup": {"zh-CN": "\n从上次中断处继续对话，不要向用户提问。直接继续 — 不要确认摘要，不要复述进展，不要以「我继续」等开头。像中断从未发生一样继续上次的任务。", "en-US": "\nContinue the conversation from where it left off without asking the user any further questions. Resume directly — do not acknowledge the summary, do not recap what was happening, do not preface with \"I'll continue\" or similar. Pick up the last task as if the break never happened."},
    "compact_conversation_start": {"zh-CN": "（对话开始）", "en-US": "(conversation start)"},
    # --- context usage ---
    "context_usage_title": {"zh-CN": "上下文窗口：{context_window:,} tokens", "en-US": "Context Window: {context_window:,} tokens"},
    "context_input_cached": {"zh-CN": "输入（缓存）：{cached:,} tokens ({cached_pct}%)", "en-US": "Input (Cached): {cached:,} tokens ({cached_pct}%)"},
    "context_input_uncached": {"zh-CN": "输入（未缓存）：{uncached:,} tokens ({uncached_pct}%)", "en-US": "Input (Uncached): {uncached:,} tokens ({uncached_pct}%)"},
    "context_output_line": {"zh-CN": "输出：{output_tokens:,} tokens ({output_pct}%)", "en-US": "Output: {output_tokens:,} tokens ({output_pct}%)"},
    "context_cache_hit_rate": {"zh-CN": "缓存命中率：{hit_rate}%", "en-US": "Cache Hit Rate: {hit_rate}%"},
    "context_used_total": {"zh-CN": "已用上下文：{used:,} tokens ({percentage}%)", "en-US": "Context Used: {used:,} tokens ({percentage}%)"},
    "context_remaining": {"zh-CN": "剩余：{remaining:,} tokens", "en-US": "Remaining: {remaining:,} tokens"},
    "context_cumulative_detail": {"zh-CN": "累积用量：缓存={cache_read:,} 未缓存={input_tokens:,} 输出={output_tokens:,}", "en-US": "Cumulative Usage: cached={cache_read:,} uncached={input_tokens:,} output={output_tokens:,}"},
    "permission_denied_stopped": {"zh-CN": "权限被拒绝，已终止当前操作（{tool}）。", "en-US": "Permission denied, stopped current operation ({tool})."},
    # --- update ---
    "update_checking": {"zh-CN": "正在检查更新...", "en-US": "Checking for updates..."},
    "update_latest": {"zh-CN": "已是最新版本 {version}", "en-US": "Already up to date ({version})"},
    "update_available": {"zh-CN": "发现新版本: {current} → {latest}", "en-US": "Update available: {current} → {latest}"},
    "update_confirm": {"zh-CN": "按回车开始更新，Ctrl+C 取消", "en-US": "Press Enter to update, Ctrl+C to cancel"},
    "update_installing": {"zh-CN": "正在安装...", "en-US": "Installing..."},
    "update_success": {"zh-CN": "更新成功！新版本: {version}", "en-US": "Updated successfully! New version: {version}"},
    "update_failed": {"zh-CN": "更新失败: {error}", "en-US": "Update failed: {error}"},
    "update_network_error": {"zh-CN": "网络连接失败，请检查网络设置", "en-US": "Network error, please check your connection"},
    "update_locked_by_running_process": {"zh-CN": "更新失败：检测到 IllusionAgent 正在运行，请先关闭所有会话后重试", "en-US": "Update failed: IllusionAgent appears to be running. Please close all sessions and retry."},
    # ---- max_tokens 命令反馈 ----
    "max_tokens_show": {"zh-CN": "最大令牌数: {value}", "en-US": "Max tokens: {value}"},
    "max_tokens_set": {"zh-CN": "最大令牌数已设置为 {value}", "en-US": "Max tokens set to {value}"},
    "max_tokens_usage": {"zh-CN": "用法: /max-tokens [show|8k|16k|32k|64k|128k|<数字>]", "en-US": "Usage: /max-tokens [show|8k|16k|32k|64k|128k|<number>]"},
    "update_deps_checking": {"zh-CN": "正在检查依赖更新...", "en-US": "Checking dependency updates..."},
    "update_deps_available": {"zh-CN": "以下依赖可升级:", "en-US": "The following dependencies can be upgraded:"},
    "update_deps_confirm": {"zh-CN": "按回车更新依赖，Ctrl+C 取消", "en-US": "Press Enter to update dependencies, Ctrl+C to cancel"},
    "update_deps_success": {"zh-CN": "依赖更新完成", "en-US": "Dependencies updated successfully"},
    # ---- rename 命令反馈 ----
    "rename_set": {"zh-CN": "会话已重命名为「{title}」", "en-US": "Session renamed to \"{title}\""},
    "rename_cleared": {"zh-CN": "会话名称已清除", "en-US": "Session name cleared"},
    "rename_not_found": {"zh-CN": "未找到会话：{sid}", "en-US": "Session not found: {sid}"},
    "rename_no_args": {"zh-CN": "用法：/rename <名称> 重命名当前会话，或 /rename #N <名称> 重命名指定会话", "en-US": "Usage: /rename <name> to rename current session, or /rename #N <name> for a specific session"},
    "rename_prompt_select": {"zh-CN": "选择要重命名的会话：", "en-US": "Select a session to rename:"},
    "rename_empty_name": {"zh-CN": "名称不能为空", "en-US": "Name cannot be empty"},
    "rename_no_sessions": {"zh-CN": "没有已保存的会话。", "en-US": "No saved sessions found."},
    # 计划审批
    "plan_approval": {"zh-CN": "计划审批", "en-US": "Plan approval"},
    "plan_approve_question": {"zh-CN": "是否批准此计划？", "en-US": "Do you approve this plan?"},
    "plan_approve": {"zh-CN": "批准", "en-US": "Approve"},
    "plan_reject": {"zh-CN": "拒绝", "en-US": "Reject"},
    "plan_start_impl": {"zh-CN": "开始执行", "en-US": "Start implementation"},
    "plan_return_mode": {"zh-CN": "返回计划模式", "en-US": "Return to plan mode"},
    # print 模式 / 渠道端 计划模式通知与审批
    "print_mode_plan_approval_pending": {"zh-CN": "计划已提交，等待审批。请用 illusion -c -p \"批准\" 或 \"拒绝 [反馈]\" 回复", "en-US": "Plan submitted, awaiting approval. Reply with illusion -c -p \"approve\" or \"reject [feedback]\""},
    "print_mode_plan_resuming_approval": {"zh-CN": "检测到待审批计划，正在注入审批结果...", "en-US": "Pending plan approval detected, injecting approval result..."},
    "print_mode_plan_approved": {"zh-CN": "[计划已批准] 开始执行", "en-US": "[Plan approved] Starting implementation"},
    "print_mode_plan_rejected": {"zh-CN": "[计划已拒绝] 返回计划模式", "en-US": "[Plan rejected] Returning to plan mode"},
    "print_mode_permission_approved": {"zh-CN": "权限已批准，正在恢复...", "en-US": "Permission approved, resuming..."},
    "print_mode_permission_denied_resuming": {"zh-CN": "权限已拒绝，正在恢复...", "en-US": "Permission denied, resuming..."},
    "print_mode_permission_resuming": {"zh-CN": "正在恢复权限审批后的操作...", "en-US": "Resuming after permission approval..."},
    "print_mode_pending_answer_hint": {"zh-CN": "等待用户回答...", "en-US": "Waiting for user answer..."},
    "print_mode_pending_plan_hint": {"zh-CN": "等待用户审批计划...", "en-US": "Waiting for user plan approval..."},
    "print_mode_pending_question_exit": {"zh-CN": "问题已提出，请使用 illusion -c -p \"你的回答\" 恢复会话", "en-US": "Question asked. Use illusion -c -p \"your answer\" to resume"},
    "print_mode_pending_plan_exit": {"zh-CN": "计划已保存到 {path}，请使用 illusion -c -p \"批准\" 批准，或 illusion -c -p \"修改意见\" 拒绝并反馈", "en-US": "Plan saved to {path}. Use illusion -c -p \"approve\" to approve, or illusion -c -p \"feedback\" to reject with feedback"},
    "print_mode_pending_permission_exit": {"zh-CN": "权限请求: {tool}，请使用 illusion -c -p \"Y\" 允许一次 / \"N\" 拒绝", "en-US": "Permission request: {tool}. Use illusion -c -p \"Y\" to allow once / \"N\" to deny"},
    "print_mode_sandbox_approved": {"zh-CN": "沙箱权限已批准，正在恢复...", "en-US": "Sandbox permission approved, resuming..."},
    "print_mode_sandbox_denied_resuming": {"zh-CN": "沙箱权限已拒绝，正在恢复...", "en-US": "Sandbox permission denied, resuming..."},
    "print_mode_sandbox_resuming": {"zh-CN": "正在恢复沙箱权限审批后的操作...", "en-US": "Resuming after sandbox permission approval..."},
    "print_mode_pending_sandbox_exit": {"zh-CN": "沙箱权限请求: {tool}，请使用 illusion -c -p \"Y\" 允许 / \"N\" 拒绝", "en-US": "Sandbox permission request: {tool}. Use illusion -c -p \"Y\" to allow / \"N\" to deny"},
    "channel_plan_entered": {"zh-CN": "📋 已进入计划模式，代理正在规划方案", "en-US": "📋 Entered plan mode, agent is planning"},
    "channel_plan_approval_title": {"zh-CN": "📝 计划审批", "en-US": "📝 Plan Approval"},
    "channel_plan_approval_question": {"zh-CN": "是否批准此计划？回复\"批准\"或输入修改意见（视为拒绝+反馈）", "en-US": "Do you approve this plan? Reply \"approve\" or type feedback (treated as reject + feedback)"},
    # --- channel CLI ---
    "channel_select": {"zh-CN": "选择渠道:", "en-US": "Select a channel:"},
    "channel_feishu_label": {"zh-CN": "飞书 / Feishu (Lark)", "en-US": "Feishu / Feishu (Lark)"},
    "channel_none_configured": {"zh-CN": "未配置任何渠道", "en-US": "No channels configured"},
    "channel_login_intro": {"zh-CN": "请先在飞书开放平台创建自建应用并开启「机器人」能力。应用地址: {url}", "en-US": "First create an app on Feishu Open Platform and enable the \"Bot\" capability. App URL: {url}"},
    "channel_select_domain": {"zh-CN": "选择平台:", "en-US": "Select platform:"},
    "channel_feishu_domain": {"zh-CN": "飞书 (open.feishu.cn)", "en-US": "Feishu (open.feishu.cn)"},
    "channel_lark_domain": {"zh-CN": "Lark (open.larksuite.com)", "en-US": "Lark (open.larksuite.com)"},
    "channel_enter_app_id": {"zh-CN": "输入 App ID", "en-US": "Enter App ID"},
    "channel_enter_app_secret": {"zh-CN": "输入 App Secret", "en-US": "Enter App Secret"},
    "channel_require_mention": {"zh-CN": "群组中是否要求 @机器人才响应? (Y/n)", "en-US": "Require @mention in groups to respond? (Y/n)"},
    "channel_allow_bots": {"zh-CN": "是否允许其他机器人消息? (y/N)", "en-US": "Allow other bots' messages? (y/N)"},
    "channel_group_isolation": {"zh-CN": "是否启用群组会话按用户隔离? (Y/n)", "en-US": "Enable per-user session isolation in groups? (Y/n)"},
    "channel_show_reasoning": {"zh-CN": "是否在回复中显示思考过程? (Y/n)", "en-US": "Show thinking process in replies? (Y/n)"},
    "channel_installing_deps": {"zh-CN": "正在安装依赖: {deps}...", "en-US": "Installing dependencies: {deps}..."},
    "channel_deps_installed": {"zh-CN": "依赖安装完成", "en-US": "Dependencies installed"},
    "channel_deps_failed": {"zh-CN": "依赖安装失败: {error}", "en-US": "Failed to install dependencies: {error}"},
    "channel_deps_missing": {"zh-CN": "缺少依赖 {deps}，请先运行 'illusion channel login {channel}'", "en-US": "Missing dependencies {deps}, run 'illusion channel login {channel}' first"},
    "channel_saved": {"zh-CN": "配置已保存到 {path}，{channel} 渠道已启用。下次运行 illusion 时将自动激活。", "en-US": "Config saved to {path}, {channel} channel enabled. Will auto-activate on next illusion run."},
    "channel_enabled": {"zh-CN": "已启用 {channel} 渠道（下次 illusion 启动生效）", "en-US": "Enabled {channel} channel (effective on next illusion start)"},
    "channel_disabled": {"zh-CN": "已禁用 {channel} 渠道", "en-US": "Disabled {channel} channel"},
    "channel_no_creds": {"zh-CN": "未配置凭据，请先运行 'illusion channel login {channel}'", "en-US": "No credentials, run 'illusion channel login {channel}' first"},
    "channel_need_workdir": {"zh-CN": "启用渠道必须指定运行目录，请使用 'illusion channel enable {channel} --working-directory <dir>'", "en-US": "Enabling a channel requires a working directory. Use 'illusion channel enable {channel} --working-directory <dir>'"},
    "channel_ask_workdir": {"zh-CN": "渠道运行目录（渠道 agent 将固定在此目录运行）", "en-US": "Channel working directory (channel agents will run here)"},
    "channel_workdir_required": {"zh-CN": "运行目录不能为空", "en-US": "Working directory is required"},
    "channel_invalid_workdir": {"zh-CN": "运行目录无效: {error}", "en-US": "Invalid working directory: {error}"},
    "channel_logout_done": {"zh-CN": "已清除 {channel} 渠道凭据", "en-US": "Cleared {channel} channel credentials"},
    # --- channel serve ---
    "channel_starting": {"zh-CN": "[渠道] 正在启动 {channel} 渠道...", "en-US": "[Channel] Starting {channel} channel..."},
    "channel_feishu_connected": {"zh-CN": "[渠道] 飞书已连接，正在监听消息 (bot: {bot})", "en-US": "[Channel] Feishu connected, listening (bot: {bot})"},
    "channel_press_exit": {"zh-CN": "[渠道] 按 Ctrl+C 退出", "en-US": "[Channel] Press Ctrl+C to exit"},
    "channel_connected": {"zh-CN": "已连接", "en-US": "connected"},
    "channel_disconnected": {"zh-CN": "未连接", "en-US": "disconnected"},
    # --- 飞书侧（用户在飞书中看到）---
    "feishu_thinking": {"zh-CN": "illusion agent 正在思考中...", "en-US": "illusion agent is thinking..."},
    "streaming_thinking": {"zh-CN": "💭 **思考中...**", "en-US": "💭 **Thinking...**"},
    "feishu_cmd_help": {"zh-CN": "可用命令: /help /clear /new /stop /sessions /resume /detach /model", "en-US": "Commands: /help /clear /new /stop /sessions /resume /detach /model"},
    "feishu_cmd_cleared": {"zh-CN": "会话已清空，开启新会话。", "en-US": "Session cleared, starting new session."},
    # --- 通用渠道命令文案（不限渠道）---
    "cmd_new": {"zh-CN": "已开启新的会话。", "en-US": "New session started."},
    "cmd_stop_no_task": {"zh-CN": "当前没有正在执行的任务。", "en-US": "No running task to stop."},
    "cmd_stop_done": {"zh-CN": "已中断当前任务。", "en-US": "Task interrupted."},
    "feishu_cmd_sessions_title": {"zh-CN": "本地未完成会话:", "en-US": "Local unfinished sessions:"},
    "feishu_cmd_no_sessions": {"zh-CN": "没有本地会话可恢复。", "en-US": "No local sessions to resume."},
    "feishu_cmd_resumed": {"zh-CN": "已恢复本地会话（{n} 条历史），继续吧。", "en-US": "Resumed local session ({n} messages), continue."},
    "feishu_cmd_detached": {"zh-CN": "当前飞书会话已保存为本地 session（id: {id}），可在终端用 --resume 继续。", "en-US": "Feishu session saved as local session (id: {id}), resume in terminal with --resume."},
    "feishu_cmd_model_usage": {"zh-CN": "用法: /model [show | set 名称]", "en-US": "Usage: /model [show | set NAME]"},
    "feishu_cmd_model_show": {"zh-CN": "当前飞书会话模型: {model}", "en-US": "Current Feishu session model: {model}"},
    "feishu_cmd_model_set": {"zh-CN": "飞书会话模型已切换为 {model}", "en-US": "Feishu session model set to {model}"},
    # --- 微信渠道 ---
    "channel_weixin_label": {"zh-CN": "微信 / WeChat", "en-US": "WeChat"},
    "weixin_qr_fetching": {"zh-CN": "正在获取二维码...", "en-US": "Fetching QR code..."},
    "weixin_qr_browser_opened": {"zh-CN": "浏览器已打开: {url}\n请用微信扫描浏览器中的二维码", "en-US": "Browser opened: {url}\nScan the QR code with WeChat"},
    "weixin_qr_waiting": {"zh-CN": "等待扫码... (Ctrl+C 取消)", "en-US": "Waiting for scan... (Ctrl+C to cancel)"},
    "weixin_qr_scanned": {"zh-CN": "→ 已扫码，请在手机上确认", "en-US": "→ Scanned, please confirm on phone"},
    "weixin_qr_redirect": {"zh-CN": "→ 重定向到最优服务器...", "en-US": "→ Redirecting to optimal server..."},
    "weixin_qr_expired": {"zh-CN": "二维码已过期，正在刷新...", "en-US": "QR code expired, refreshing..."},
    "weixin_qr_timeout": {"zh-CN": "扫码超时，请重新运行 illusion channel login", "en-US": "QR scan timed out, run 'illusion channel login' again"},
    "weixin_login_success": {"zh-CN": "微信登录成功", "en-US": "WeChat login successful"},
    "weixin_session_expired": {"zh-CN": "微信会话已过期，请重新运行 'illusion channel login'", "en-US": "WeChat session expired, run 'illusion channel login' again"},
    "channel_starting_weixin": {"zh-CN": "[渠道] 正在启动微信渠道...", "en-US": "[Channel] Starting WeChat channel..."},
    # QQ 渠道
    "channel_qq_label": {"zh-CN": "QQ", "en-US": "QQ"},
    "channel_starting_qq": {"zh-CN": "[渠道] 正在启动 QQ 渠道...", "en-US": "[Channel] Starting QQ channel..."},
    "qq_enter_app_id": {"zh-CN": "输入 App ID", "en-US": "Enter App ID"},
    "qq_enter_client_secret": {"zh-CN": "输入 Client Secret", "en-US": "Enter Client Secret"},
    "qq_login_intro": {
        "zh-CN": "请先访问 https://q.qq.com 注册机器人应用，获取 App ID 和 Client Secret",
        "en-US": "Please visit https://q.qq.com to register a bot app and obtain App ID and Client Secret",
    },
    # 守护进程已在运行（拒绝重复启动）
    "channel_daemon_already_running": {
        "zh-CN": "[channel] 守护进程已在运行 (PID={pid})，拒绝重复启动。 若确信无进程在运行，请删除 {pid_file} 后重试。",
        "en-US": "[channel] Daemon is already running (PID={pid}). If no process is running, delete {pid_file} and retry.",
    },
    # 收到中断信号正在关闭
    "channel_interrupted_closing": {
        "zh-CN": "收到中断信号，正在关闭...",
        "en-US": "Interrupt received, shutting down...",
    },
    # 渠道状态标题
    "channel_status_title": {
        "zh-CN": "渠道状态：",
        "en-US": "Channel status:",
    },
    # --- web 端设置弹窗和 terminal 认证提示 ---
    "terminal_auth_hint": {
        "zh-CN": "请运行 'illusion auth login' 配置 API 环境",
        "en-US": "Run 'illusion auth login' to configure API environment",
    },
    # --- terminal_io 终端交互回调 ---
    "terminal_permission_request": {
        "zh-CN": "\n⚠️ 权限请求: {tool_name}",
        "en-US": "\n⚠️ Permission request: {tool_name}",
    },
    "terminal_permission_reason": {
        "zh-CN": "   原因: {reason}",
        "en-US": "   Reason: {reason}",
    },
    "terminal_permission_prompt": {
        "zh-CN": "   允许执行? [y/N] ",
        "en-US": "   Allow execution? [y/N] ",
    },
    "terminal_ask_user_question": {
        "zh-CN": "\n❓ {question}",
        "en-US": "\n❓ {question}",
    },
    "print_mode_question_asked": {
        "zh-CN": "📋 已提出问题，请使用 illusion -c -p \"<答案>\" 回答后继续",
        "en-US": "📋 Question asked, answer with illusion -c -p \"<answer>\" to continue",
    },
    "print_mode_resuming_answer": {
        "zh-CN": "✅ 检测到待回答问题，正在注入答案并继续执行...",
        "en-US": "✅ Pending question detected, injecting answer and continuing...",
    },
    "print_mode_permission_denied": {
        "zh-CN": "⏸️ 权限拒绝: {tool_name} ({reason})。print 模式不支持交互式权限确认，如需允许请使用 --permission-mode full_auto",
        "en-US": "⏸️ Permission denied: {tool_name} ({reason}). Print mode does not support interactive permission confirmation, use --permission-mode full_auto to allow",
    },
    "print_mode_multi_question_format": {
        "zh-CN": "📋 多问题请用 JSON 格式回答（key 为方括号内的 header）：\n{example}\n（multiSelect 用数组，如 {{\"header\": [\"选项A\", \"选项B\"]}}）",
        "en-US": "📋 For multiple questions, answer in JSON format (key = header in brackets):\n{example}\n(multiSelect uses array, e.g. {{\"header\": [\"optionA\", \"optionB\"]}})",
    },
    # print 模式事件前缀（text 格式输出到 stderr，区分不同事件类型）
    "print_mode_prefix_reasoning": {
        "zh-CN": "[思考过程]",
        "en-US": "[Thinking]",
    },
    "print_mode_prefix_tool_call": {
        "zh-CN": "[工具调用]",
        "en-US": "[Tool Call]",
    },
    "print_mode_prefix_tool_result": {
        "zh-CN": "[工具结果]",
        "en-US": "[Tool Result]",
    },
    "print_mode_prefix_assistant": {
        "zh-CN": "[最终回复]",
        "en-US": "[Assistant]",
    },
    # print 模式 max_turns 耗尽提示
    "print_mode_max_turns_stopped": {
        "zh-CN": "已达到最大轮次 ({max_turns})，停止执行",
        "en-US": "Stopped after {max_turns} turns (max_turns)",
    },
    # goal 轮次生命周期提示（print 模式 / web toast 共用文案）
    "goal_status_round": {
        "zh-CN": "目标轮次 {round}/{max}",
        "en-US": "Goal round {round}/{max}",
    },
    "goal_status_wrapup_complete": {
        "zh-CN": "目标完成 — 正在写收尾消息",
        "en-US": "Goal complete — writing closing message",
    },
    "goal_status_wrapup_blocked": {
        "zh-CN": "目标受阻 — 正在写收尾消息",
        "en-US": "Goal blocked — writing closing message",
    },
    "goal_status_limit": {
        "zh-CN": "已达到轮次上限（{max}），目标自动暂停",
        "en-US": "Goal round limit reached ({max}); goal auto-paused",
    },
    "goal_status_disarmed": {
        "zh-CN": "单轮达到最大轮次，目标已解除武装；要求继续可恢复",
        "en-US": "Goal round hit max turns; goal disarmed. Ask to resume to continue",
    },
    # goal 快捷键操作回执（terminal Ctrl+G 两段式；command_result 显示）
    # pause 不打断当前轮：跑完自然停在边界，故文案区分"已暂停"与"续跑已停止"
    "goal_action_paused": {
        "zh-CN": "目标已暂停：当前轮完成后停止，不再自动续跑",
        "en-US": "Goal paused: stops after the current round finishes; no further auto rounds",
    },
    "goal_action_resumed": {
        "zh-CN": "目标已恢复：从停止点继续自动轮次",
        "en-US": "Goal resumed: continuing autonomous rounds from where it stopped",
    },
    "goal_action_edited": {
        "zh-CN": "目标已更新：当前轮完成后按新目标续跑",
        "en-US": "Goal updated: next round continues with the new objective",
    },
    "goal_action_cleared": {
        "zh-CN": "目标已清除：当前轮完成后停止",
        "en-US": "Goal cleared: stops after the current round finishes",
    },
    "goal_action_failed": {
        "zh-CN": "目标操作失败：{message}",
        "en-US": "Goal action failed: {message}",
    },
    # 会话摘要缺失时的回退文本
    "session_summary_fallback": {
        "zh-CN": "(无摘要)",
        "en-US": "(no summary)",
    },
    # ask_user_question header 格式（zh-CN 用全角括号，en-US 用半角括号）
    "question_header_format": {
        "zh-CN": "【{header}】",
        "en-US": "[{header}]",
    },
    "web_settings_title": {
        "zh-CN": "设置",
        "en-US": "Settings",
    },
    "web_onboarding_subtitle": {
        "zh-CN": "配置 API 环境以开始使用",
        "en-US": "Configure API environment to get started",
    },
    "web_env_add": {
        "zh-CN": "新增环境",
        "en-US": "Add Environment",
    },
    "web_env_delete": {
        "zh-CN": "删除",
        "en-US": "Delete",
    },
    "web_env_activate": {
        "zh-CN": "设为当前",
        "en-US": "Activate",
    },
    "web_env_active": {
        "zh-CN": "当前",
        "en-US": "Active",
    },
    "web_env_api_format": {
        "zh-CN": "API 格式",
        "en-US": "API Format",
    },
    "web_env_base_url": {
        "zh-CN": "Base URL",
        "en-US": "Base URL",
    },
    "web_env_api_key": {
        "zh-CN": "API Key",
        "en-US": "API Key",
    },
    "web_env_model": {
        "zh-CN": "模型",
        "en-US": "Model",
    },
    "web_env_add_model": {
        "zh-CN": "新增模型",
        "en-US": "Add Model",
    },
    "web_env_oauth_login_github": {
        "zh-CN": "使用 GitHub 登录",
        "en-US": "Login with GitHub",
    },
    "web_env_oauth_login_openai": {
        "zh-CN": "使用 OpenAI 登录",
        "en-US": "Login with OpenAI",
    },
    "web_env_oauth_waiting": {
        "zh-CN": "请在弹出的浏览器窗口中完成授权...",
        "en-US": "Please complete authorization in the browser window...",
    },
    "web_env_oauth_success": {
        "zh-CN": "OAuth 认证成功",
        "en-US": "OAuth authentication successful",
    },
    "web_env_oauth_failed": {
        "zh-CN": "OAuth 认证失败",
        "en-US": "OAuth authentication failed",
    },
    "web_ui_language": {
        "zh-CN": "界面语言",
        "en-US": "UI Language",
    },
    "web_settings_save": {
        "zh-CN": "保存",
        "en-US": "Save",
    },
    "web_settings_cancel": {
        "zh-CN": "取消",
        "en-US": "Cancel",
    },
    "web_settings_close": {
        "zh-CN": "关闭",
        "en-US": "Close",
    },
    "settings_tooltip": {
        "zh-CN": "设置",
        "en-US": "Settings",
    },
    "unknown_oauth_provider": {
        "zh-CN": "未知的 OAuth 提供商: {provider}",
        "en-US": "Unknown OAuth provider: {provider}",
    },
    "device_code_required": {
        "zh-CN": "device_code 为必填项",
        "en-US": "device_code is required",
    },
}

# --- 命令描述翻译 ---
COMMAND_DESCRIPTIONS_ZH: dict[str, str] = {
    "help": "显示可用命令及用法说明",
    "exit": "退出 IllusionAgent",
    "clear": "清空当前对话并开启新会话",
    "new": "开启新对话并重置任务 ID",
    "version": "显示已安装版本",
    "context": "显示上下文使用量或管理上下文窗口",
    "compact": "压缩较早对话历史",
    "memory": "查看和管理项目记忆",
    "hooks": "显示已配置 hooks",
    "resume": "恢复最近保存的会话",
    "export": "导出当前转录",
    "share": "创建可分享的转录快照",
    "copy": "复制最新回复或指定文本",
    "rewind": "移除最新对话轮次",
    "init": "初始化项目 IllusionAgent 文件",
    "login": "查看认证状态或保存 API Key",
    "logout": "清除已保存 API Key",
    "skills": "列出或显示可用技能",
    "config": "显示或更新配置",
    "max-tokens": "显示或更新最大输出令牌数",
    "sandbox": "显示沙箱状态或管理排除命令",
    "mcp": "显示 MCP 状态",
    "plugin": "管理插件",
    "reload-plugins": "重新加载当前工作区插件发现结果",
    "permissions": "显示或更新权限模式",
    "thinking": "显示或更新思考模式",
    "effort": "显示或更新推理强度",
    "turns": "显示或更新最大 agent 轮数",
    "continue": "在中断后继续上一轮工具循环",
    "model": "显示或更新默认模型",
    "language": "显示或更新界面语言",
    "output-style": "显示或更新输出风格",
    "doctor": "显示环境诊断信息",
    "privacy-settings": "显示本地隐私与存储设置",
    "delete": "清理选定的会话",
    "rules": "查看选定的规则",
    "update": "检查并更新 IllusionAgent",
    "agent": "查看已完成 agent 摘要或创建新 agent",
    "goal": "设置或查看长任务的完成目标",
}

# --- 斜杠命令输出翻译 ---

# 命令消息精确匹配表（英文 -> 中文）
_COMMAND_EXACT: dict[str, str] = {
    # 通用
    "Available commands:": "可用命令：",
    "(empty)": "（空）",
    "(no output)": "（无输出）",
    "(no directories)": "（无目录）",
    "(no matching files)": "（无匹配文件）",
    "(no diff)": "（无差异）",
    "(working tree clean)": "（工作区干净）",
    # 会话
    "Conversation cleared.": "对话已清空。",
    "Started a new conversation session.": "已开启新对话。",
    "No saved sessions found for this project.": "当前项目未找到已保存会话。",
    "Nothing to copy.": "没有可复制的内容。",
    "Deleted current session:": "已删除当前会话：",
    # Goal
    "No goal is currently set.": "当前未设置目标。",
    "Goal cleared.": "目标已清除。",
    "Goal paused.": "目标已暂停。",
    "Goal resumed. Continuing autonomous rounds…": "目标已恢复。继续自动轮次…",
    "Goal objective updated.": "目标内容已更新。",
    "Goal set. Starting autonomous rounds…": "目标已设置。开始自动轮次…",
    # 记忆与 hooks
    "No memory files.": "没有记忆文件。",
    "No hooks configured.": "未配置 hooks。",
    # 插件与技能
    "No plugins discovered.": "未发现插件。",
    "No skills available.": "没有可用技能。",
    # 项目初始化
    "Project already initialized for IllusionAgent.": "项目已完成 IllusionAgent 初始化。",
    "## Files created": "## 已创建文件",
    "## Files updated": "## 已更新文件",
    "## Project analysis": "## 项目分析",
    "## Next steps": "## 下一步建议",
    "- Review `CLAUDE.md` for project configuration": "- 查看 `CLAUDE.md` 了解项目配置",
    "- Review `ILLUSION.md` for project-specific guidance": "- 查看 `ILLUSION.md` 了解项目特定指导",
    "- Run `/memory` to manage project memories": "- 运行 `/memory` 管理项目记忆",
    "- Run `/skills` to view available skills": "- 运行 `/skills` 查看可用技能",
    "- Adjust `CLAUDE.md` as needed": "- 根据需要调整 `CLAUDE.md`",
    # 认证
    "Stored API key in ~/.illusion/settings.json": "API Key 已保存到 ~/.illusion/settings.json",
    "Cleared stored API key.": "已清除已保存 API Key。",
    # 计划审批
    "Plan approved. Starting implementation.": "计划已批准，开始实施。",
    "User rejected the plan.": "用户拒绝了该计划。",
    # 模型
    "Usage: /model [show|set MODEL]": "用法：/model [show|set MODEL]",
    "Model set to": "模型已切换为",
    # 语言
    "Available UI languages: zh-CN, en": "可用界面语言：zh-CN, en",
    "Usage: /language [show|list|set zh-CN|set en]": "用法：/language [show|list|set zh-CN|set en]",
    # 输出风格
    "Usage: /output-style [show|list|set NAME]": "用法：/output-style [show|list|set NAME]",
    # 诊断与隐私
    "Doctor summary:": "诊断摘要：",
    "Privacy settings:": "隐私设置：",
    # Git
    "Nothing to continue (no pending tool results).": "没有待继续的内容（无待处理工具结果）。",
    "Continuing pending tool loop...": "正在继续待处理的工具循环…",
    # MCP
    "HTTP/WS MCP auth supports bearer or header modes.": "HTTP/WS MCP 认证支持 bearer 或 header 模式。",
    "stdio MCP auth supports bearer or env modes.": "stdio MCP 认证支持 bearer 或 env 模式。",
    "No MCP servers configured.": "未配置 MCP 服务器。",
    # 上下文窗口
    "Error: context window must be positive": "错误：上下文窗口必须为正数",
    "Error: invalid number": "错误：无效的数字",
    "Usage: /context [usage|show|window|set N]": "用法：/context [usage|show|window|set N]",
    # 用法提示
    "Usage: /compact [PRESERVE_RECENT]": "用法：/compact [保留近期消息数]",
    "Usage: /memory add TITLE :: CONTENT": "用法：/memory add 标题 :: 内容",
    "Usage: /memory [list|show NAME|add TITLE :: CONTENT|remove NAME]": "用法：/memory [list|show 名称|add 标题 :: 内容|remove 名称]",
    "Usage: /rewind [TURNS] [both|conversation]": "用法：/rewind [轮数] [both|conversation]",
    "Usage: /config [show|set KEY VALUE]": "用法：/config [show|set 键 值]",
    "Usage: /thinking [show|on|off|toggle]": "用法：/thinking [show|on|off|toggle]",
    "Usage: /effort [show|low|medium|high|xhigh|max]": "用法：/effort [show|low|medium|high|xhigh|max]",
    "Usage: /turns [show|COUNT]": "用法：/turns [数量]",
    "Usage: /continue [COUNT]": "用法：/continue [数量]",
    "Usage: /permissions [show|set MODE]": "用法：/permissions [show|set 模式]",
    "Usage: /plugin [list|enable NAME|disable NAME|install PATH|uninstall NAME]":
        "用法：/plugin [list|enable 名称|disable 名称|install 路径|uninstall 名称]",
    # 删除与规则
    "Saved sessions:": "已保存会话：",
    "Use /resume <session_id> to restore a specific session.": "使用 /resume <会话ID> 恢复指定会话。",
    # 登录
    "Usage: /login API_KEY": "用法：/login API_KEY",
    # 用法提示（registry.usage 追加到 message 时翻译）
    "Usage: /resume [session_id|#N]": "用法：/resume [session_id|#N]",
    "Usage: /skills [name|number]": "用法：/skills [名称|序号]",
    "Usage: /max-tokens [show|set N]": "用法：/max-tokens [show|set N]",
    "Usage: /delete [session_id|#N|all]": "用法：/delete [session_id|#N|all]",
    # Doctor
    "- backend host: available": "- 后端宿主：可用",
    "- network: enabled only for API endpoint and explicit web/MCP calls": "- 网络：仅用于 API 端点和显式 web/MCP 调用",
    "- storage: local files under ~/.illusion and project .illusion": "- 存储：本地文件位于 ~/.illusion 和项目 .illusion",
    # 沙箱
    "Sandbox status: enabled": "沙箱状态：已启用",
    "  Enabled platforms: all": "  限制平台：无（全部平台）",
    "  Excluded commands: none": "  排除命令：无",
}

# 命令消息正则替换表（pattern, replacement）
# replacement 可以是字符串（含 \1 等反向引用）或 lambda(match) -> str
_COMMAND_SUBSTITUTIONS: list[tuple[str, str | Callable[[re.Match[str]], str]]] = [
    # 版本
    (r"^IllusionAgent (.+)$", r"IllusionAgent 版本 \1"),
    # 上下文窗口
    (r"^Context window: (\d[\d,]*) tokens$", r"上下文窗口：\1 tokens"),
    (r"^Context window set to (\d[\d,]*) tokens$", r"上下文窗口已设置为 \1 tokens"),
    (r"^Context Window: (\d[\d,]*) tokens$", r"上下文窗口：\1 tokens"),
    (r"^Estimated Used: ~(\d[\d,]*) tokens \((\d+)%\)$", r"预估已用：~\1 tokens（\2%）"),
    (r"^Remaining: ~(\d[\d,]*) tokens$", r"剩余：~\1 tokens"),
    (r"^  System Prompt: ~(\d[\d,]*) tokens \((\d+)%\)$", r"  System Prompt: ~\1 tokens（\2%）"),
    (r"^  System Prompt: ~ tokens$", r"  System Prompt: ~ tokens"),
    (r"^  Messages: ~(\d[\d,]*) tokens \((\d+)%\)$", r"  Messages: ~\1 tokens（\2%）"),
    (r"^  Estimated Used: ~(\d[\d,]*) tokens \((\d+)%\)$", r"  预估已用：~\1 tokens（\2%）"),
    (r"^  Remaining: ~(\d[\d,]*) tokens$", r"  剩余：~\1 tokens"),
    (r"^  Cumulative API Usage: input=(\d[\d,]*) output=(\d[\d,]*)$", r"  累积 API 用量：input=\1 output=\2"),
    (r"^  Note: System Prompt includes skills/hooks/rules/memory/channels and other system-level overhead$", r"  注: System Prompt 包含 skills/hooks/rules/memory/channels 等系统级开销"),
    # 模型
    (r"^Model: (.+)$", r"模型：\1"),
    (r"^Model set to (.+)\. Restart session to use it\.$", r"模型已设置为 \1。重启会话后生效。"),
    (r"^Model set to (.+)\.$", r"模型已设置为 \1。"),
    (r"^Unknown model: (.+)$", r"未知模型：\1"),
    # 语言
    (r"^UI language: (.+)$", r"界面语言：\1"),
    (r"^UI language set to (.+)$", r"界面语言已设置为 \1"),
    # 输出风格
    (r"^Output style: (.+)$", r"输出风格：\1"),
    (r"^Output style set to (.+)$", r"输出风格已设置为 \1"),
    (r"^Unknown output style: (.+)$", r"未知输出风格：\1"),
    # 思考模式
    (r"^Thinking mode: (on|off)$", r"思考模式：\1"),
    (r"^Thinking mode (enabled|disabled)\.$",
     lambda m: f"思考模式{'已开启' if m.group(1) == 'enabled' else '已关闭'}。"),
    # 推理强度
    (r"^Reasoning effort: (.+)$", r"推理强度：\1"),
    (r"^Reasoning effort set to (.+)\.$", r"推理强度已设置为 \1。"),
    # 推理轮数
    (r"^Passes: (.+)$", r"推理轮数：\1"),
    (r"^Pass count set to (.+)\.$", r"推理轮数已设置为 \1。"),
    # 最大轮数
    (r"^Max turns set to (.+)\.$", r"最大轮数已设置为 \1。"),
    # 权限
    (r"^Permission mode set to (.+)$", r"权限模式已设置为 \1"),
    (r"^Mode: (.+)$", r"模式：\1"),
    # 会话
    (r"^Session not found: (.+)$", r"未找到会话：\1"),
    (r"^Restored (\d+) messages from session (.+)$", r"已从会话 \2 恢复 \1 条消息"),
    (r"^Restored (\d+) messages from the latest session\.$", r"已从最近会话恢复 \1 条消息。"),
    (r"^Exported transcript to (.+)$", r"已导出转录到 \1"),
    (r"^Created shareable transcript snapshot at (.+)$", r"已创建可分享的转录快照：\1"),
    (r"^Copied (\d+) characters to the clipboard\.$", r"已复制 \1 个字符到剪贴板。"),
    (r"^Clipboard unavailable\. Saved copied text to (.+)$", r"剪贴板不可用，已保存到 \1"),
    (r"^Rewound (\d+) turn\(s\); removed (\d+) message\(s\)\.$", r"已回退 \1 轮，移除 \2 条消息。"),
    (r"^Reverted (\d+) file\(s\)\.$", r"已恢复 \1 个文件。"),
    (r"^Nothing to rewind\.$", r"没有需要回退的内容。"),
    # 任务
    (r"^Started task (.+)$", r"已启动任务 \1"),
    (r"^Stopped task (.+)$", r"已停止任务 \1"),
    (r"^No task found with ID: (.+)$", r"未找到任务 ID：\1"),
    (r"^Updated task (.+) description$", r"已更新任务 \1 的描述"),
    (r"^Updated task (.+) progress to (\d+)%$", r"已更新任务 \1 的进度为 \2%"),
    (r"^Updated task (.+) note$", r"已更新任务 \1 的备注"),
    (r"^Deleted (\d+) session file\(s\)\.$", r"已删除 \1 个会话文件。"),
    (r"^Deleted session: (.+)$", r"已删除会话：\1"),
    (r"^Deleted current session: (.+)$", r"已删除当前会话：\1"),
    # Agent
    (r"^No agent found with ID: (.+)$", r"未找到 agent ID：\1"),
    # 插件
    (r"^Enabled plugin '(.+)'\. Restart session to reload\.$", r"已启用插件「\1」，重启会话后生效。"),
    (r"^Disabled plugin '(.+)'\. Restart session to reload\.$", r"已禁用插件「\1」，重启会话后生效。"),
    (r"^Installed plugin to (.+)$", r"已安装插件到 \1"),
    (r"^Uninstalled plugin '(.+)'$", r"已卸载插件「\1」"),
    (r"^Plugin '(.+)' not found$", r"未找到插件「\1」"),
    # 配置
    (r"^Unknown config key: (.+)$", r"未知配置项：\1"),
    (r"^Updated (.+)$", r"已更新 \1"),
    # 记忆
    (r"^Memory entry not found: (.+)$", r"未找到记忆条目：\1"),
    (r"^Added memory entry (.+)$", r"已添加记忆条目 \1"),
    (r"^Removed memory entry (.+)$", r"已移除记忆条目 \1"),
    # MCP
    (r"^Unknown MCP server: (.+)$", r"未知 MCP 服务器：\1"),
    (r"^Server (.+) does not support auth updates$", r"服务器 \1 不支持认证更新"),
    (r"^Saved MCP auth for (.+)\. Restart session to reconnect\.$", r"已保存 \1 的 MCP 认证，重启会话后重新连接。"),
    # Issue 与 PR 评论
    (r"^No issue context\. File path: (.+)$", r"无 issue 上下文。文件路径：\1"),
    (r"^Saved issue context to (.+)$", r"已保存 issue 上下文到 \1"),
    (r"^No PR comments context\. File path: (.+)$", r"无 PR 评论上下文。文件路径：\1"),
    (r"^Added PR comment to (.+)$", r"已添加 PR 评论到 \1"),
    # 初始化
    (r"^Initialized project files:$", r"已初始化项目文件："),
    (r"^\*\*Illusion Agent project initialization complete\.\*\*$", r"✨ **Illusion Agent 项目初始化完成**"),
    (r"^- \*\*Languages\*\*: (.+)$", r"- **检测到语言**: \1"),
    (r"^- \*\*Frameworks\*\*: (.+)$", r"- **检测到框架**: \1"),
    (r"^- \*\*Package Manager\*\*: (.+)$", r"- **包管理器**: \1"),
    (r"^- \*\*Build\*\*: `(.+)`$", r"- **构建命令**: `\1`"),
    (r"^- \*\*Test\*\*: `(.+)`$", r"- **测试命令**: `\1`"),
    (r"^- \*\*Lint\*\*: `(.+)`$", r"- **代码检查**: `\1`"),
    (r"^- \*\*Format\*\*: `(.+)`$", r"- **格式化工具**: `\1`"),
    (r"^- \*\*CI/CD\*\*: (.+)$", r"- **CI/CD**: \1"),
    # 技能
    (r"^Skill not found: (.+)$", r"未找到技能：\1"),
    # 规则
    (r"^All rules are disabled$", r"所有规则已被禁用"),
    (r"^No rules found in (.+)$", r"在 \1 中未找到规则"),
    (r"^Rule not found: (.+)\. Use /rules to list available rules\.$", r"未找到规则：\1。使用 /rules 查看可用规则。"),
    # 状态行（多行消息的逐行翻译）
    (r"^Session stats:$", r"会话统计："),
    (r"^Messages: (\d+)$", r"消息数：\1"),
    (r"^Usage: input=(\d+) output=(\d+)$", r"用量：输入=\1 输出=\2"),
    (r"^Effort: (.+)$", r"推理强度：\1"),
    (r"^Actual usage: input=(\d+) output=(\d+)$", r"实际用量：输入=\1 输出=\2"),
    (r"^Estimated conversation tokens: (\d+)$", r"预估对话 token：\1"),
    (r"^Input tokens: (\d+)$", r"输入 token：\1"),
    (r"^Output tokens: (\d+)$", r"输出 token：\1"),
    (r"^Total tokens: (\d+)$", r"总计 token：\1"),
    (r"^Estimated cost: (.+)$", r"预估费用：\1"),
    (r"^Max turns \(engine\): (.+)$", r"最大轮数（引擎）：\1"),
    (r"^Max turns \(config\): (.+)$", r"最大轮数（配置）：\1"),
    (r"^Memory directory: (.+)$", r"记忆目录：\1"),
    (r"^Entrypoint: (.+)$", r"入口文件：\1"),
    (r"^Compacted conversation from (\d+) messages to (\d+)\.$", r"已压缩对话：\1 条 → \2 条。"),
    (r"^Compacted conversation from (\d+) to (\d+) messages \(saved ~(\d[\d,]*) tokens\)\.$", r"已压缩对话：\1 → \2 条消息（节省 ~\3 tokens）。"),
    (r"^Current branch: (.+)$", r"当前分支：\1"),
    (r"^Feedback log: (.+)$", r"反馈日志：\1"),
    (r"^Auth status:$", r"认证状态："),
    (r"^Reloaded plugins:$", r"已重新加载插件："),
    (r"^Available skills:$", r"可用技能："),
    (r"^User skills directory: (.+)$", r"用户技能目录：\1"),
    (r"^Project skills directory: (.+)$", r"项目技能目录：\1"),
    (r"^Usage: /skills <name|number>  — view a specific skill$", r"用法：/skills <名称|序号>  — 查看指定技能"),
    (r"^Skill not found: (.+)\. Use /skills to list available skills\.$", r"未找到技能：\1。使用 /skills 查看可用技能。"),
    (r"^Rules directory: (.+)$", r"规则目录：\1"),
    # 前缀行（doctor, privacy-settings, login, stats, permissions 等）
    (r"^- backend host: available$", r"- 后端宿主：可用"),
    (r"^- network: enabled only for API endpoint and explicit web/MCP calls$", r"- 网络：仅用于 API 端点和显式 web/MCP 调用"),
    (r"^- storage: local files under ~\/\.illusion and project \.illusion$", r"- 存储：本地文件位于 ~/.illusion 和项目 .illusion"),
    (r"^- messages: (\d+)$", r"- 消息数：\1"),
    (r"^- estimated_tokens: (\d+)$", r"- 预估 token：\1"),
    (r"^- tools: (\d+)$", r"- 工具数：\1"),
    (r"^- memory_files: (\d+)$", r"- 记忆文件：\1"),
    (r"^- background_tasks: (\d+)$", r"- 后台任务：\1"),
    (r"^- output_style: (.+)$", r"- 输出风格：\1"),
    (r"^- cwd: (.+)$", r"- 工作目录：\1"),
    (r"^- sessions: (\d+)$", r"- 会话数：\1"),
    (r"^- utilities: (.+)$", r"- 工具集：\1"),
    (r"^- auth_status: (.+)$", r"- 认证状态：\1"),
    (r"^- base_url: (.+)$", r"- 基础 URL：\1"),
    (r"^- model: (.+)$", r"- 模型：\1"),
    (r"^- api_key: (.+)$", r"- API Key：\1"),
    (r"^Allowed tools: (.+)$", r"允许的工具：\1"),
    (r"^Denied tools: (.+)$", r"拒绝的工具：\1"),
    (r"^- permission_mode: (.+)$", r"- 权限模式：\1"),
    (r"^- ui_language: (.+)$", r"- 界面语言：\1"),
    (r"^- memory_dir: (.+)$", r"- 记忆目录：\1"),
    (r"^- plugin_count: (\d+)$", r"- 插件数：\1"),
    (r"^- mcp_configured: (yes|no)$",
     lambda m: f"- MCP 已配置：{'是' if m.group(1) == 'yes' else '否'}"),
    (r"^- user_config_dir: (.+)$", r"- 用户配置目录：\1"),
    (r"^- project_config_dir: (.+)$", r"- 项目配置目录：\1"),
    (r"^- session_dir: (.+)$", r"- 会话目录：\1"),
    (r"^- api_base_url: (.+)$", r"- API 基础 URL：\1"),
    # 沙箱
    (r"^Sandbox status: (.+)$", r"沙箱状态：\1"),
    (r"^  Enabled platforms: (.+)$", r"  限制平台：\1"),
    (r"^  Excluded commands \((\d+)\):$", r"  排除命令（\1）："),
    (r"^  Allow write: (.+)$", r"  允许写入：\1"),
    (r"^  Deny write: (.+)$", r"  拒绝写入：\1"),
    (r"^  Deny read: (.+)$", r"  拒绝读取：\1"),
    (r"^  Allowed domains: (.+)$", r"  允许域名：\1"),
    (r"^  Denied domains: (.+)$", r"  拒绝域名：\1"),
    (r"^Added excluded command: (.+)$", r"已添加排除命令：\1"),
    (r"^Current excluded list: (.+)$", r"当前排除列表：\1"),
    (r"^Removed excluded command: (.+)$", r"已移除排除命令：\1"),
    (r"^Command pattern '(.+)' is already in the excluded list$", r"命令模式「\1」已在排除列表中"),
    (r"^Command pattern '(.+)' is not in the excluded list$", r"命令模式「\1」不在排除列表中"),
    (r"^Sandbox restriction: '(.+)' is blocked by sandbox configuration\.$", r"沙箱限制：「\1」被沙箱配置阻止。"),
    (r"^Tool: (.+)$", r"工具：\1"),
    (r"^Do you want to allow this operation\?$", r"是否允许此操作？"),
    (r"^Sandbox denied: (.+)$", r"沙箱已拒绝：\1"),
]


def _get_lang() -> str:
    """获取当前 ui_language，避免循环导入"""
    from illusion.config.settings import load_settings
    settings = load_settings()
    return settings.ui_language or "zh-CN"


def t(key: str, **kwargs: Any) -> str:
    """根据 ui_language 返回对应语言的文本

    Args:
        key: 消息键名
        **kwargs: 格式化参数

    Returns:
        str: 对应语言的文本，未找到时返回 key 本身
    """
    lang = _get_lang()
    msg = MESSAGES.get(key, {}).get(lang, MESSAGES.get(key, {}).get("en-US", key))
    if kwargs:
        return msg.format(**kwargs)
    return msg


def _is_zh(locale: str) -> bool:
    return locale.lower().startswith("zh")


def _translate_single_line(line: str) -> str:
    """翻译单行命令消息（英文 -> 当前语言）"""
    # 对带缩进的行（如 help_text 中的 "              Usage: /xxx"），
    # 剥离前导空白后进行精确匹配，匹配后恢复前导缩进
    stripped = line.lstrip()
    leading_ws = line[: len(line) - len(stripped)]
    if stripped in _COMMAND_EXACT:
        return leading_ws + _COMMAND_EXACT[stripped]
    translated = line
    for pattern_str, replacement in _COMMAND_SUBSTITUTIONS:
        pattern = re.compile(pattern_str)
        if callable(replacement):
            translated = pattern.sub(replacement, translated)
        else:
            translated = pattern.sub(replacement, translated)
    return translated


def translate_command_message(message: str, *, locale: str) -> str:
    """翻译命令处理器输出的消息

    对于中文 locale，将英文输出翻译为中文；其他语言原样返回。
    支持多行消息：按行分割，逐行翻译，重新拼接。

    Args:
        message: 命令处理器的英文输出
        locale: 当前 UI 语言

    Returns:
        str: 翻译后的消息
    """
    if not message or not _is_zh(locale):
        return message
    lines = message.split("\n")
    translated_lines = [_translate_single_line(line) for line in lines]
    return "\n".join(translated_lines)
