"""
/agent 命令处理器
=================

查看已完成任务的摘要，引导用户创建新 agent，或设置 agent 的默认模型。

路由规则：
    - 无参数 / list：提示用法（前端可通过 select_command('agent') 列出可选项）
    - create / new：提示创建向导由前端驱动（agent_wizard_init/submit）
    - model：设置 agent 的默认模型
        - /agent model：列出各 agent 当前生效的模型
        - /agent model <name>：显示该 agent 当前模型与用法
        - /agent model <name> <env_N.model_M|inherit>：写入模型
          * 内置 agent → settings.json 的 agent_models（仅模型可改）
          * 用户/项目级 agent → 直接改写其 .md frontmatter
    - <id>：双数据源查询
        - 前台 agent：<id> 为 tool_use_id，从 engine.messages 提取对应 tool_result
        - 后台任务（agent / bash / powershell 等）：<id> 为 task_id，从 transcript 的
          task-notification 提取 <result>

主要组件：
    - agent_handler: 处理 /agent 命令，返回 CommandResult

使用示例：
    >>> result = await agent_handler("", context)
    >>> result.message
    'Use /agent <id> to view a completed task's summary, or /agent create to create a new agent.'
"""

from __future__ import annotations

from illusion.commands.types import CommandContext, CommandResult
from illusion.engine.messages import TextBlock, ToolResultBlock
from illusion.tasks.types import TASK_NOTIFICATION_RE

_MODEL_USAGE = (
    "Usage: /agent model <name> <env_N.model_M|inherit>"
)


async def agent_handler(args: str, context: CommandContext) -> CommandResult:
    """处理 /agent 命令。

    Args:
        args: 命令参数（空 / list / create / new / model ... / <id>）
        context: 命令上下文

    Returns:
        CommandResult: 摘要消息或引导提示
    """
    tokens = args.strip().split()
    if not tokens or tokens[0] == "list":
        return CommandResult(
            message=(
                "Use /agent <id> to view a completed task's summary, "
                "or /agent create to create a new agent."
            )
        )
    if tokens[0] == "model":
        return _handle_agent_model(tokens[1:], context)
    if tokens[0] in ("create", "new"):
        return CommandResult(
            message="Agent creation wizard triggered via UI. Use the agent_wizard_init request to begin."
        )

    query_id = tokens[0]

    # 1. 前台 agent：从 transcript 找 tool_use_id 匹配的 tool_result
    #    跳过 "launched in background/as subprocess" 启动通知（非摘要，
    #    与 select_command('agent') 的前台过滤逻辑保持一致）
    for msg in context.engine.messages:
        if msg.role != "user":
            continue
        for block in msg.content:
            if isinstance(block, ToolResultBlock) and block.tool_use_id == query_id:
                text = block.text_content
                if text and ("launched in background" in text or "launched as subprocess" in text):
                    continue  # 启动通知非摘要，跳过继续查找 task-notification
                if text:
                    return CommandResult(message=text)
                return CommandResult(message=f"Agent tool result '{query_id}' is empty.")

    # 2. 后台任务（agent / bash / powershell 等）：从 transcript 的 task-notification 提取 <result>
    #    task-notification 是后端在后台任务完成时注入的 user 消息 TextBlock，
    #    天然随会话同步，避免 manager._tasks 进程级单例跨会话不同步问题。
    for msg in context.engine.messages:
        if msg.role != "user":
            continue
        for block in msg.content:
            if not isinstance(block, TextBlock):
                continue
            match = TASK_NOTIFICATION_RE.search(block.text)
            if not match:
                continue
            task_id = match.group("task_id").strip()
            if task_id != query_id:
                continue
            status = match.group("status").strip()
            if status != "completed":
                return CommandResult(message=f"Agent '{query_id}' is not completed (status: {status}).")
            result_text = match.group("result").strip()
            # 若 <result> 为空，从 tasks 目录的 .log 文件提取实际输出
            if not result_text:
                try:
                    from illusion.config.paths import get_tasks_dir
                    log_file = get_tasks_dir() / f"{query_id}.log"
                    if log_file.exists():
                        content = log_file.read_text(encoding="utf-8", errors="replace")
                        result_text = content[-12000:] if len(content) > 12000 else content
                except OSError:
                    pass
            return CommandResult(message=result_text or f"Agent '{query_id}' has no captured output.")

    # 3. 找不到
    return CommandResult(message=f"No task found with id: {query_id}")


def _handle_agent_model(args: list[str], context: CommandContext) -> CommandResult:
    """处理 /agent model 子命令：查看或设置 agent 的默认模型。

    内置 agent 的模型固化到 settings.json（agent_models），且仅允许改模型；
    用户/项目级 agent 直接改写其 .md frontmatter。模型值必须为
    ``env_N.model_M`` 引用或 ``inherit``（裸模型名拒绝），避免跨 env
    调用时的 404 model_not_found。
    """
    from illusion.config.settings import load_settings, save_settings
    from illusion.coordinator.agent_definitions import get_managed_agent_definitions

    agents = get_managed_agent_definitions(context.cwd)
    settings = load_settings()

    if not args:
        lines = [_MODEL_USAGE, "", "Current agent models:"]
        for agent in agents:
            override = str(settings.agent_models.get(agent.name, "") or "").strip()
            candidate = override or (agent.model or "inherit")
            env_key, model_name, ref = settings.resolve_agent_model_spec(candidate)
            display = ref or model_name or "inherit"
            origin = " (settings)" if override else ""
            lines.append(f"  {agent.name}: {display}{origin}")
        return CommandResult(message="\n".join(lines))

    name = args[0]
    agent = next((a for a in agents if a.name == name), None)
    if agent is None:
        return CommandResult(
            message=(
                f"Agent '{name}' not found. Available agents: "
                + ", ".join(a.name for a in agents)
            )
        )

    override = str(settings.agent_models.get(agent.name, "") or "").strip()
    current = override or (agent.model or "inherit")
    if len(args) < 2:
        env_key, _mn, ref = settings.resolve_agent_model_spec(current)
        display = ref or current
        origin = " (settings.json)" if override else (
            " (.md)" if agent.model else ""
        )
        return CommandResult(
            message=(
                f"Agent '{name}' current model: {display}{origin}\n" + _MODEL_USAGE
            )
        )

    raw_ref = args[1].strip()
    if raw_ref.lower() == "inherit":
        target_ref = "inherit"
    else:
        env_key, _mn, resolved = settings.resolve_agent_model_spec(raw_ref)
        if not resolved or not env_key:
            return CommandResult(
                message=(
                    f"Unknown model '{raw_ref}'. Configure it in settings first, "
                    "then reference it as env_N.model_M."
                )
            )
        target_ref = resolved

    if agent.source == "builtin":
        # 内置 agent：仅固化模型到 settings.json，其余配置不可改
        if target_ref == "inherit":
            settings.agent_models.pop(agent.name, None)
        else:
            settings.agent_models[agent.name] = target_ref
        save_settings(settings)
        return CommandResult(
            message=(
                f"Built-in agent '{name}' default model set to {target_ref} "
                "(saved to settings.json)."
            )
        )

    # 用户/项目级 agent：直接改写 .md frontmatter（无需固化 settings）
    from illusion.services.agent_creator import update_agent_definition_file

    try:
        path = update_agent_definition_file(agent, {"model": target_ref})
    except (ValueError, OSError) as exc:
        return CommandResult(message=f"Failed to update agent: {exc}")
    return CommandResult(
        message=f"Agent '{name}' default model set to {target_ref} ({path})."
    )
