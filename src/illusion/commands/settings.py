"""
设置与配置斜杠命令
==================

/config, /language, /privacy-settings, /doctor,
/thinking, /effort, /max-tokens, /turns, /permissions
"""

from __future__ import annotations

from illusion.commands.helpers import coerce_setting_value
from illusion.commands.types import CommandContext, CommandResult
from illusion.config.paths import (
    get_config_dir,
    get_project_config_dir,
)
from illusion.config.settings import Settings, load_settings, save_settings
from illusion.permissions import PermissionChecker, PermissionMode
from illusion.prompts import build_runtime_system_prompt

_MODE_LABELS = {"default": "Default", "plan": "Plan Mode", "full_auto": "Auto", "yolo": "YOLO"}


async def config_handler(args: str, context: CommandContext) -> CommandResult:
    """显示或更新配置"""
    del context
    settings = load_settings()
    tokens = args.split(maxsplit=2)
    if not tokens or tokens[0] == "show":
        return CommandResult(message=settings.model_dump_json(indent=2))
    if tokens[0] == "set" and len(tokens) == 3:
        key, value = tokens[1], tokens[2]
        if key not in Settings.model_fields:
            return CommandResult(message=f"Unknown config key: {key}")
        try:
            coerced = coerce_setting_value(settings, key, value)
        except ValueError as exc:
            return CommandResult(message=str(exc))
        setattr(settings, key, coerced)
        save_settings(settings)
        return CommandResult(message=f"Updated {key}")
    return CommandResult(message="Usage: /config [show|set KEY VALUE]")


async def language_handler(args: str, context: CommandContext) -> CommandResult:
    """显示或更新 UI 语言"""
    settings = load_settings()
    current = (
        str(context.app_state.get().ui_language)
        if context.app_state is not None
        else settings.ui_language
    )
    tokens = args.split()
    if not tokens or tokens[0] == "show":
        return CommandResult(message=f"UI language: {current}")
    if tokens[0] == "list":
        return CommandResult(message="Available UI languages: zh-CN, en")
    if tokens[0] == "set" and len(tokens) == 2:
        value = tokens[1]
        if value not in {"zh-CN", "en"}:
            return CommandResult(message="Usage: /language [show|list|set zh-CN|set en]")
        settings.ui_language = value
        save_settings(settings)
        if context.app_state is not None:
            context.app_state.set(ui_language=value)
        return CommandResult(message=f"UI language set to {value}")
    return CommandResult(message="Usage: /language [show|list|set zh-CN|set en]")


async def privacy_settings_handler(_: str, context: CommandContext) -> CommandResult:
    """显示隐私和存储设置"""
    from illusion.services.session_storage import get_project_session_dir_no_create
    settings = load_settings()
    session_dir = get_project_session_dir_no_create(context.cwd)
    lines = [
        "Privacy settings:",
        f"- user_config_dir: {get_config_dir()}",
        f"- project_config_dir: {get_project_config_dir(context.cwd)}",
        f"- session_dir: {session_dir}",
        f"- api_base_url: {settings.base_url or '(default Anthropic-compatible endpoint)'}",
        "- network: enabled only for API endpoint and explicit web/MCP calls",
        "- storage: local files under ~/.illusion and project .illusion",
    ]
    return CommandResult(message="\n".join(lines))


async def doctor_handler(_: str, context: CommandContext) -> CommandResult:
    """显示环境诊断信息"""
    from illusion.memory import get_memory_dir_for_cwd
    settings = load_settings()
    memory_dir = get_memory_dir_for_cwd(context.cwd)
    state = context.app_state.get() if context.app_state is not None else None
    lines = [
        "Doctor summary:",
        f"- cwd: {context.cwd}",
        f"- model: {settings.model}",
        f"- permission_mode: {state.permission_mode if state is not None else settings.permission.mode}",
        f"- ui_language: {state.ui_language if state is not None else settings.ui_language}",
        f"- effort: {state.effort if state is not None else settings.effort}",
        f"- memory_dir: {memory_dir}",
        f"- plugin_count: {max(len(context.plugin_summary.splitlines()) - 1, 0) if context.plugin_summary else 0}",
        f"- mcp_configured: {'yes' if context.mcp_summary and 'No MCP' not in context.mcp_summary else 'no'}",
    ]
    return CommandResult(message="\n".join(lines))


async def thinking_handler(args: str, context: CommandContext) -> CommandResult:
    """显示或更新 thinking 模式"""
    settings = load_settings()
    current = (
        context.app_state.get().show_thinking
        if context.app_state is not None
        else settings.show_thinking
    )
    action = args.strip() or "toggle"
    if action == "show":
        return CommandResult(message=f"Thinking mode: {'on' if current else 'off'}")
    enabled = {"on": True, "off": False, "toggle": not current}.get(action)
    if enabled is None:
        return CommandResult(message="Usage: /thinking [show|on|off|toggle]")
    settings.show_thinking = enabled
    save_settings(settings)
    if context.app_state is not None:
        context.app_state.set(show_thinking=enabled)
    return CommandResult(message=f"Thinking mode {'enabled' if enabled else 'disabled'}.")


async def effort_handler(args: str, context: CommandContext) -> CommandResult:
    """显示或更新推理努力级别"""
    settings = load_settings()
    current = context.app_state.get().effort if context.app_state is not None else settings.effort
    value = args.strip() or "show"
    if value == "show":
        return CommandResult(message=f"Reasoning effort: {current}")
    if value not in {"low", "medium", "high", "xhigh", "max"}:
        return CommandResult(message="Usage: /effort [show|low|medium|high|xhigh|max]")
    try:
        from illusion.api.effort import EffortMapper
        effort_level = EffortMapper.normalize(value)
    except ValueError:
        return CommandResult(message="Usage: /effort [show|low|medium|high|xhigh|max]")
    settings.effort = value
    save_settings(settings)
    if context.engine is not None:
        context.engine.effort = effort_level
        context.engine.set_system_prompt(build_runtime_system_prompt(settings, cwd=context.cwd, channel_hint=context.channel_hint))
    if context.app_state is not None:
        context.app_state.set(effort=value)
    return CommandResult(message=f"Reasoning effort set to {value}.")


async def max_tokens_handler(args: str, context: CommandContext) -> CommandResult:
    """显示或更新最大输出令牌数"""
    from illusion.config.i18n import t

    settings = load_settings()
    current = context.app_state.get().max_tokens if context.app_state is not None else settings.max_tokens
    value = args.strip() or "show"
    if value == "show":
        return CommandResult(message=t("max_tokens_show", value=current))
    # 预设档位
    preset = {
        "8k": 8192,
        "16k": 16384,
        "32k": 32768,
        "64k": 65536,
        "128k": 131072,
    }
    if value in preset:
        tokens = preset[value]
    elif value.isdigit():
        tokens = int(value)
    else:
        return CommandResult(message=t("max_tokens_usage"))
    settings.max_tokens = tokens
    save_settings(settings)
    if context.engine is not None:
        context.engine.max_tokens = tokens
    if context.app_state is not None:
        context.app_state.set(max_tokens=tokens)
    return CommandResult(message=t("max_tokens_set", value=tokens))


async def turns_handler(args: str, context: CommandContext) -> CommandResult:
    """显示或更新最大 agentic 轮次"""
    settings = load_settings()
    tokens = args.split()
    if not tokens or tokens[0] == "show":
        engine_turns = context.engine.max_turns if context.engine is not None else settings.max_turns
        return CommandResult(
            message=(
                f"Max turns (engine): {engine_turns}\n"
                f"Max turns (config): {settings.max_turns}\n"
                "Usage: /turns [show|COUNT]"
            )
        )
    if tokens[0] == "set" and len(tokens) == 2:
        raw = tokens[1]
    elif len(tokens) == 1:
        raw = tokens[0]
    else:
        return CommandResult(message="Usage: /turns [show|COUNT]")
    try:
        turns = int(raw)
    except ValueError:
        return CommandResult(message="Usage: /turns [show|COUNT]")
    turns = max(1, min(turns, 512))
    settings.max_turns = turns
    save_settings(settings)
    if context.engine is not None:
        context.engine.set_max_turns(turns)
    return CommandResult(message=f"Max turns set to {turns}.")


def _apply_permission_checker(context: CommandContext, settings: Settings) -> None:
    """将权限配置变更热生效：重建 PermissionChecker 并注入引擎。

    沿用现有模式切换逻辑：权限规则（含 auto_review/review_model）可能被
    其他会话的检查器持有旧快照，统一按新配置重建并注入。
    """
    checker = PermissionChecker(settings.permission)
    checker.sync_sandbox_restrictions(
        settings.sandbox, working_directory=settings.working_directory
    )
    if context.engine is not None:
        context.engine.set_permission_checker(checker)
    if context.app_state is not None:
        context.app_state.set(permission_mode=settings.permission.mode.value)


async def permissions_handler(args: str, context: CommandContext) -> CommandResult:
    """显示或更新权限模式（含 LLM 自动审核开关与审核模型）"""
    from illusion.config.i18n import t

    settings = load_settings()
    tokens = args.split()
    if not tokens or tokens[0] == "show":
        permission = settings.permission
        label = _MODE_LABELS.get(permission.mode.value, permission.mode.value)
        auto_state = "on" if permission.auto_review else "off"
        review_model = permission.review_model or "(inherit)"
        return CommandResult(
            message=(
                f"Mode: {label}\n"
                f"LLM auto-review: {auto_state}\n"
                f"Review model: {review_model}\n"
                f"Allowed tools: {permission.allowed_tools}\n"
                f"Denied tools: {permission.denied_tools}"
            )
        )
    if tokens[0] == "set" and len(tokens) == 2:
        settings.permission.mode = PermissionMode(tokens[1])
        save_settings(settings)
        _apply_permission_checker(context, settings)
        label = _MODE_LABELS.get(tokens[1], tokens[1])
        return CommandResult(message=f"Permission mode set to {label}")
    if tokens[0] == "auto":
        # /permissions auto on|off|toggle|status（类似 /memory auto 流程）
        action = tokens[1] if len(tokens) == 2 else "status"
        if action == "status":
            return CommandResult(
                message=t(
                    "permission_auto_show",
                    state=("on" if settings.permission.auto_review else "off"),
                )
            )
        enabled = {"on": True, "off": False, "toggle": not settings.permission.auto_review}.get(action)
        if enabled is None:
            return CommandResult(message=t("permission_auto_usage"))
        settings.permission.auto_review = enabled
        save_settings(settings)
        # 不重建检查器：maybe_auto_review 实时读磁盘开关，save 即生效。
        # 重建会连带破坏计划模式状态/会话级沙箱允许/粘滞拒绝缓存
        return CommandResult(
            message=t("permission_auto_on" if enabled else "permission_auto_off")
        )
    if tokens[0] == "model":
        # /permissions model [show|set REF|set inherit]
        action = tokens[1] if len(tokens) >= 2 else "show"
        if action == "show":
            return CommandResult(
                message=t(
                    "permission_review_model_show",
                    model=settings.permission.review_model or "(inherit)",
                )
            )
        if action == "set" and len(tokens) == 3:
            value = tokens[2]
            if value in {"inherit", ""}:
                settings.permission.review_model = None
                save_settings(settings)
                # 不重建检查器（同 auto 子命令理由）：review_permission 实时
                # 读磁盘 review_model，save 即生效
                return CommandResult(message=t("permission_review_model_inherit"))
            # 设置时即校验引用有效性：坏 ref 若落盘只会在首次审批时懒失败
            # 并静默回退会话模型，用户无从得知
            env_key, model_name = settings.resolve_model_ref_with_env(value)
            if not (env_key and model_name):
                return CommandResult(
                    message=t("permission_review_model_invalid", ref=value)
                )
            settings.permission.review_model = value
            save_settings(settings)
            return CommandResult(
                message=t("permission_review_model_set", model=value)
            )
    return CommandResult(message=t("permission_auto_usage"))
