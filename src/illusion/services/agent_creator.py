"""
代理定义服务
============

提供代理定义的校验、文件写入与 LLM 辅助生成能力，是 /agent 创建向导功能的核心后端服务。

核心设计：
    - 支持用户手动输入或 LLM 从自然语言描述生成代理定义
    - 代理定义存储为 frontmatter markdown 文件
    - 自动避免标识符重复

主要组件：
    - AGENT_CREATION_SYSTEM_PROMPT: LLM 生成代理定义使用的系统提示词
    - GeneratedAgent: LLM 生成的代理定义数据模型
    - validate_agent_definition: 校验用户输入的代理字段
    - write_agent_definition: 将代理定义写入 frontmatter markdown 文件
    - generate_agent_from_description: 通过 LLM 从自然语言生成代理定义
    - list_available_models: 返回可用模型列表
    - list_available_tools: 返回可用工具列表

使用示例：
    >>> errors = validate_agent_definition({"name": "test", "system_prompt": "..."}, cwd)
    >>> path = write_agent_definition(fields, scope, cwd)
    >>> agent = await generate_agent_from_description("帮助代码审查", "inherit", [], engine)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from illusion.api.client import ApiMessageRequest
from illusion.config.paths import get_config_dir, get_project_config_dir
from illusion.coordinator.agent_definitions import AgentDefinition, get_all_agent_definitions
from illusion.engine.messages import ConversationMessage

if TYPE_CHECKING:
    from illusion.engine.query_engine import QueryEngine
    from illusion.state import AppStateStore
    from illusion.tools.base import ToolRegistry

logger = logging.getLogger(__name__)

# AgentTool 的注册名（用于提示词中的工具引用）
_AGENT_TOOL_NAME = "agent"

AGENT_CREATION_SYSTEM_PROMPT = f"""You are an elite AI agent architect specializing in crafting high-performance agent configurations. Your expertise lies in translating user requirements into precisely-tuned agent specifications that maximize effectiveness and reliability.

**Important Context**: You may have access to project-specific instructions from CLAUDE.md files and other context that may include coding standards, project structure, and custom requirements. Consider this context when creating agents to ensure they align with the project's established patterns and practices.

When a user describes what they want an agent to do, you will:

1. **Extract Core Intent**: Identify the fundamental purpose, key responsibilities, and success criteria for the agent. Look for both explicit requirements and implicit needs. Consider any project-specific context from CLAUDE.md files. For agents that are meant to review code, you should assume that the user is asking to review recently written code and not the whole codebase, unless the user has explicitly instructed you otherwise.

2. **Design Expert Persona**: Create a compelling expert identity that embodies deep domain knowledge relevant to the task. The persona should inspire confidence and guide the agent's decision-making approach.

3. **Architect Comprehensive Instructions**: Develop a system prompt that:
   - Establishes clear behavioral boundaries and operational parameters
   - Provides specific methodologies and best practices for task execution
   - Anticipates edge cases and provides guidance for handling them
   - Incorporates any specific requirements or preferences mentioned by the user
   - Defines output format expectations when relevant
   - Aligns with project-specific coding standards and patterns from CLAUDE.md

4. **Optimize for Performance**: Include:
   - Decision-making frameworks appropriate to the domain
   - Quality control mechanisms and self-verification steps
   - Efficient workflow patterns
   - Clear escalation or fallback strategies

5. **Create Identifier**: Design a concise, descriptive identifier that:
   - Uses lowercase letters, numbers, and hyphens only
   - Is typically 2-4 words joined by hyphens
   - Clearly indicates the agent's primary function
   - Is memorable and easy to type
   - Avoids generic terms like "helper" or "assistant"

6. **Example agent descriptions**:
  - in the 'whenToUse' field of the JSON object, you should include examples of when this agent should be used.
  - examples should be of the form:
    - <example>
      Context: The user is creating a test-runner agent that should be called after a logical chunk of code is written.
      user: "Please write a function that checks if a number is prime"
      assistant: "Here is the relevant function: "
      <function call omitted for brevity only for this example>
      <commentary>
      Since a significant piece of code was written, use the {_AGENT_TOOL_NAME} tool to launch the test-runner agent to run the tests.
      </commentary>
      assistant: "Now let me use the test-runner agent to run the tests"
    </example>
    - <example>
      Context: User is creating an agent to respond to the word "hello" with a friendly joke.
      user: "Hello"
      assistant: "I'm going to use the {_AGENT_TOOL_NAME} tool to launch the greeting-responder agent to respond with a friendly goodbye"
      <commentary>
      Since the user is greeting, use the greeting-responder agent to respond with a friendly goodbye.
      </commentary>
    </example>
  - If the user mentioned or implied that the agent should be used proactively, you should include examples of this.
- NOTE: Ensure that in the examples, you are making the assistant use the {_AGENT_TOOL_NAME} tool and not simply respond directly to the task.

Your output must be a valid JSON object with exactly these fields:
{{
  "identifier": "A unique, descriptive identifier using lowercase letters, numbers, and hyphens (e.g., 'test-runner', 'api-docs-writer', 'code-formatter')",
  "whenToUse": "A precise, actionable description starting with 'Use this agent when...' that clearly defines the triggering conditions and use cases. Ensure you include examples as described above.",
  "systemPrompt": "The complete system prompt that will govern the agent's behavior, written in second person ('You are...', 'You will...') and structured for maximum clarity and effectiveness"
}}

Key principles for your system prompts:
- Be specific rather than generic - avoid vague instructions
- Include concrete examples when they would clarify behavior
- Balance comprehensiveness with clarity - every instruction should add value
- Ensure the agent has enough context to handle variations of the core task
- Make the agent proactive in seeking clarification when needed
- Build in quality assurance and self-correction mechanisms

Remember: The agents you create should be autonomous experts capable of handling their designated tasks with minimal additional guidance. Your system prompts are their complete operational manual.
"""


@dataclass
class GeneratedAgent:
    """LLM 生成的代理定义。

    属性:
        identifier: 代理标识符（小写字母、数字、连字符）
        when_to_use: 使用时机描述
        system_prompt: 系统提示词
    """

    identifier: str
    when_to_use: str
    system_prompt: str


def _get_agents_dir(scope: str, cwd: str | Path) -> Path:
    """根据 scope 返回 agents 目录路径。

    Args:
        scope: 作用域，``"user"`` 返回用户级目录，``"project"`` 返回项目级目录
        cwd: 当前工作目录（仅 ``"project"`` scope 使用）

    Returns:
        Path: agents 目录路径

    Raises:
        ValueError: 不支持的 scope
    """
    if scope == "user":
        return get_config_dir() / "agents"
    if scope == "project":
        return get_project_config_dir(cwd) / "agents"
    raise ValueError(f"Unsupported scope: {scope!r}")


def validate_agent_definition(
    fields: dict[str, Any],
    cwd: str | None = None,
) -> dict[str, str]:
    """校验代理定义字段，返回错误字典。

    空字典表示校验通过。检查项：
        - ``name`` 非空且不与现有代理冲突（冲突检测按 cwd 加载：
          内置 + 用户级 + 插件 + 该工作区的项目级定义）
        - ``description`` 非空
        - ``system_prompt`` 非空
        - ``model`` 为空 / ``inherit`` / 合法的 ``env_N.model_M`` 引用；
          裸模型名等其他值直接报错

    Args:
        fields: 代理定义字段
        cwd: 目标工作目录（项目级创建所在工作区，缺省当前目录）

    Returns:
        dict[str, str]: 字段名到错误信息的映射
    """
    errors: dict[str, str] = {}

    name = str(fields.get("name", "")).strip()
    if not name:
        errors["name"] = "代理名称不能为空"
    else:
        for existing in get_all_agent_definitions(cwd=cwd):
            if existing.name == name:
                errors["name"] = f"代理名称 '{name}' 已存在"
                break

    if not str(fields.get("description", "")).strip():
        errors["description"] = "代理描述不能为空"

    if not str(fields.get("system_prompt", "")).strip():
        errors["system_prompt"] = "系统提示词不能为空"

    model = fields.get("model")
    if model is not None:
        model_str = str(model).strip() if isinstance(model, str) else ""
        if not model_str:
            errors["model"] = "模型必须是非空字符串或 'inherit'"
        elif model_str.lower() != "inherit":
            # 模型必须是 env 引用：裸模型名配当前 env 的 provider 直发会
            # 404 model_not_found，创建阶段直接拦截
            from illusion.config.settings import load_settings

            settings = load_settings()
            env_key, _model_name, ref = settings.resolve_agent_model_spec(model_str)
            if not ref or not env_key:
                errors["model"] = (
                    f"未知模型 '{model_str}'：请使用 settings 中已配置的 "
                    "env_N.model_M 模型引用，或使用 'inherit'"
                )

    return errors


def write_agent_definition(
    fields: dict[str, Any],
    scope: str = "user",
    cwd: str | Path = ".",
) -> Path:
    """将代理定义写入 frontmatter markdown 文件。

    frontmatter 字段与 ``AgentDefinition`` 一致（name/description/model 等），
    markdown body 作为 ``system_prompt``。

    Args:
        fields: 代理定义字段
        scope: 写入作用域，``"user"`` 或 ``"project"``
        cwd: 当前工作目录（仅 ``"project"`` scope 使用）

    Returns:
        Path: 写入的文件路径

    Raises:
        ValueError: 代理名称为空
    """
    agents_dir = _get_agents_dir(scope, cwd)
    agents_dir.mkdir(parents=True, exist_ok=True)

    name = str(fields.get("name", "")).strip()
    if not name:
        raise ValueError("代理名称不能为空")

    # 文件名安全化：仅保留字母、数字、点、下划线、连字符
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "-", name).strip("-") or "agent"
    file_path = agents_dir / f"{safe_name}.md"

    # 构建 frontmatter（字段顺序与现有 agent 定义文件一致）
    fm_lines: list[str] = ["---"]
    fm_lines.append(f"name: {name}")

    description = str(fields.get("description", "")).strip()
    if description:
        fm_lines.append(f"description: {description}")

    model = fields.get("model")
    if model:
        fm_lines.append(f"model: {model}")

    tools = fields.get("tools")
    if tools:
        if isinstance(tools, list):
            fm_lines.append("tools: [" + ", ".join(str(t) for t in tools) + "]")
        else:
            fm_lines.append(f"tools: {tools}")

    disallowed_tools = fields.get("disallowed_tools") or fields.get("disallowedTools")
    if disallowed_tools:
        if isinstance(disallowed_tools, list):
            fm_lines.append(
                "disallowedTools: [" + ", ".join(str(t) for t in disallowed_tools) + "]"
            )
        else:
            fm_lines.append(f"disallowedTools: {disallowed_tools}")

    effort = fields.get("effort")
    if effort:
        fm_lines.append(f"effort: {effort}")

    permission_mode = fields.get("permission_mode") or fields.get("permissionMode")
    if permission_mode:
        fm_lines.append(f"permissionMode: {permission_mode}")

    color = fields.get("color")
    if color:
        fm_lines.append(f"color: {color}")

    fm_lines.append("---")

    body = str(fields.get("system_prompt", "")).strip()
    content = "\n".join(fm_lines) + "\n" + body + "\n"

    file_path.write_text(content, encoding="utf-8")
    return file_path


# 更新操作支持的外科手术式改写键：updates 键 → frontmatter 键（含别名）。
# 未列出的 frontmatter 字段（skills/mcpServers/hooks/color 等）原样保留。
_FM_UPDATE_KEYS: dict[str, tuple[str, ...]] = {
    "model": ("model",),
    "description": ("description", "when_to_use", "whenToUse"),
    "tools": ("tools",),
    "effort": ("effort",),
    "permission_mode": ("permission_mode", "permissionMode"),
    "max_turns": ("max_turns", "maxTurns"),
}


def _format_fm_value(key: str, value: Any) -> str:
    """将 updates 值格式化为单行 frontmatter 文本。"""
    if isinstance(value, list):
        return f"{key}: [" + ", ".join(str(v) for v in value) + "]"
    return f"{key}: {value}"


def _agent_md_path(agent: AgentDefinition) -> Path:
    """定位用户/项目级 agent 的 .md 文件。

    Raises:
        ValueError: agent 不是文件来源（内置/插件）或路径信息缺失
    """
    if agent.source == "builtin":
        raise ValueError(f"内置代理 '{agent.name}' 不存在对应的定义文件")
    if not agent.base_dir or not agent.filename:
        raise ValueError(f"代理 '{agent.name}' 缺少定义文件路径信息")
    return Path(agent.base_dir) / f"{agent.filename}.md"


def update_agent_definition_file(
    agent: AgentDefinition,
    updates: dict[str, Any],
) -> Path:
    """外科手术式更新用户/项目级 agent 的 .md frontmatter。

    仅替换 ``updates`` 中出现的受管键（model/description/tools/effort/
    permission_mode/max_turns，支持 camelCase 别名定位），其余 frontmatter
    字段与 markdown body（system_prompt，可通过 ``updates["system_prompt"]``
    显式替换）原样保留。

    Args:
        agent: 现有代理定义（来自 get_all_agent_definitions 等）
        updates: 待更新字段；值为 None 时删除对应 frontmatter 键

    Returns:
        Path: 更新后的文件路径

    Raises:
        ValueError: agent 非文件来源、路径信息缺失或文件不存在
    """
    file_path = _agent_md_path(agent)
    if not file_path.exists():
        raise ValueError(f"代理定义文件不存在: {file_path}")

    content = file_path.read_text(encoding="utf-8")

    # 拆分 frontmatter 与 body（无 frontmatter 的文件视为空 fm + 全 body）
    if content.startswith("---"):
        lines = content.split("\n")
        try:
            fm_end = lines.index("---", 1)
        except ValueError as exc:
            raise ValueError(f"代理定义文件 frontmatter 格式错误: {file_path}") from exc
        fm_lines = lines[1:fm_end]
        body_lines = lines[fm_end + 1 :]
    else:
        fm_lines = []
        body_lines = content.split("\n")

    # system_prompt 更新：直接替换 body
    system_prompt = updates.get("system_prompt")
    if system_prompt is not None:
        body_lines = str(system_prompt).strip().split("\n")

    # 受管键定位：key → fm_lines 索引（匹配任意别名，含大小写变体）
    def _key_of(line: str) -> str | None:
        if not line.strip() or ":" not in line:
            return None
        if line.lstrip() != line:
            return None  # 缩进行（块标量/列表延续）不算顶层键
        key_part = line.split(":", 1)[0].strip()
        return key_part or None

    def _matches(key: str | None, aliases: tuple[str, ...]) -> bool:
        if key is None:
            return False
        normalized = key.lower().replace("_", "")
        return normalized in {a.lower().replace("_", "") for a in aliases}

    def _span(start: int) -> int:
        """受管键条目的行跨度：键行 + 后续缩进/延续行（块标量、列表项）。"""
        end = start + 1
        while end < len(fm_lines):
            line = fm_lines[end]
            if not line.strip():
                break
            if line[:1] in (" ", "\t") or line.lstrip().startswith("- "):
                end += 1
                continue
            break
        return end

    for updates_key, aliases in _FM_UPDATE_KEYS.items():
        if updates_key not in updates:
            continue
        value = updates[updates_key]
        fm_key = aliases[0]
        new_lines: list[str] = [] if value is None else [_format_fm_value(fm_key, value)]

        index = next(
            (i for i, line in enumerate(fm_lines) if _matches(_key_of(line), aliases)),
            None,
        )
        if index is not None:
            fm_lines[index : _span(index)] = new_lines
        elif new_lines:
            # 键不存在：追加到 frontmatter 末尾（保持 name/description 靠前的
            # 既有顺序，新键附加以避免插入位置的启发式判断）
            fm_lines.extend(new_lines)

    file_path.write_text(
        "---\n" + "\n".join(fm_lines) + "\n---\n" + "\n".join(body_lines) + "\n",
        encoding="utf-8",
    )
    return file_path


def delete_agent_definition_file(agent: AgentDefinition) -> Path:
    """删除用户/项目级 agent 的 .md 定义文件。

    Args:
        agent: 现有代理定义

    Returns:
        Path: 被删除的文件路径

    Raises:
        ValueError: agent 非用户创建来源（内置/插件不可删除）、路径信息
            缺失或文件不存在
    """
    if agent.source != "user":
        raise ValueError(f"代理 '{agent.name}' 不是用户创建的，无法删除")
    file_path = _agent_md_path(agent)
    if not file_path.exists():
        raise ValueError(f"代理定义文件不存在: {file_path}")
    file_path.unlink()
    return file_path


def _extract_json(text: str) -> str:
    """从可能包含 markdown 代码块的文本中提取 JSON 字符串。

    Args:
        text: 原始文本

    Returns:
        str: 提取出的 JSON 字符串
    """
    text = text.strip()
    # 尝试提取 ```json ... ``` 代码块
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # 否则提取第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


async def generate_agent_from_description(
    user_prompt: str,
    model: str,
    existing_identifiers: list[str],
    engine: QueryEngine,
    abort_signal: Any = None,
) -> GeneratedAgent:
    """通过 LLM 从自然语言描述生成代理定义。

    使用 ``AGENT_CREATION_SYSTEM_PROMPT`` 作为系统提示词，将用户描述发送
    给 LLM，解析返回的 JSON 为 ``GeneratedAgent``。

    Args:
        user_prompt: 用户的自然语言描述
        model: 使用的模型名称
        existing_identifiers: 已存在的代理标识符列表（用于去重提示）
        engine: 查询引擎（用于访问 api_client 和 max_tokens）
        abort_signal: 中止信号（保留参数，当前未使用）

    Returns:
        GeneratedAgent: 生成的代理定义

    Raises:
        ValueError: LLM 返回的内容不是有效 JSON 或缺少必需字段
    """
    del abort_signal  # 预留

    # 构造用户消息：附加已存在标识符以避免重复
    if existing_identifiers:
        user_content = (
            f"Existing agent identifiers (avoid duplicates): "
            f"{', '.join(existing_identifiers)}\n\n"
            f"User request: {user_prompt}"
        )
    else:
        user_content = user_prompt

    messages = [ConversationMessage.from_user_text(user_content)]

    # inherit 不是真实模型名，需替换为 engine 当前默认模型；
    # 指定其他 env 的模型（env_N.model_M 引用）时，按该 env 的端点/凭据
    # 独立构建 client——复用主 client 调另一 provider 的模型必然 404
    # model_not_found（与 memory/extract.py 的处理对齐）。
    # 注意：任何分支发送给 API 的都必须是裸模型名，"env_N.model_M"
    # 引用串本身不是合法模型名（否则报 model_invalid 404）。
    from illusion.config.settings import load_settings

    settings = load_settings()
    requested = str(model).strip() if model else ""
    actual_model = engine.model
    api_client = engine.api_client
    if requested and requested.lower() != "inherit":
        env_key, model_name, ref = settings.resolve_agent_model_spec(requested)
        if ref and env_key and model_name:
            actual_model = model_name
            if env_key != settings._active_env_key:
                try:
                    from illusion.api.factory import build_api_client_for_env

                    api_client = build_api_client_for_env(settings, env_key)
                except (ValueError, RuntimeError) as exc:
                    logger.warning("Failed to build API client for env %s: %s", env_key, exc)
                    # 原子回退：client 失败则 model 同步回退当前模型
                    actual_model = engine.model
                    api_client = engine.api_client
        else:
            # 不可解析的值（裸模型名等）不直发 provider，回退当前模型
            logger.warning(
                "Agent generation model %r does not match any configured model; "
                "falling back to %s", requested, engine.model,
            )

    request = ApiMessageRequest(
        model=actual_model,
        messages=messages,
        system_prompt=AGENT_CREATION_SYSTEM_PROMPT,
        max_tokens=engine.max_tokens,
        tools=[],
        effort=None,
    )

    chunks: list[str] = []
    async for event in api_client.stream_message(request):  # type: ignore[attr-defined]
        text = getattr(event, "text", None)
        if text:
            chunks.append(text)

    raw_text = "".join(chunks).strip()
    json_text = _extract_json(raw_text)

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"LLM 返回的内容不是有效的 JSON: {exc}\n内容: {raw_text}"
        ) from exc

    identifier = str(data.get("identifier", "")).strip()
    when_to_use = str(data.get("whenToUse", "")).strip()
    system_prompt_text = str(data.get("systemPrompt", "")).strip()

    if not identifier or not when_to_use or not system_prompt_text:
        raise ValueError(
            f"LLM 返回的 JSON 缺少必需字段 (identifier/whenToUse/systemPrompt): {raw_text}"
        )

    return GeneratedAgent(
        identifier=identifier,
        when_to_use=when_to_use,
        system_prompt=system_prompt_text,
    )


def list_available_models(
    app_state: AppStateStore | None = None,
) -> list[dict[str, Any]]:
    """返回可用模型列表（``env_N.model_M`` 引用形式）。

    列表首项为 ``inherit``（继承默认模型）；其余项 ``name`` 为模型引用
    （写入 agent frontmatter 的值），``label`` 为模型名，``supports_images``
    取自该模型在 settings 中声明的媒体能力（供前端展示多模态徽标）。

    Args:
        app_state: 应用状态（保留参数，当前未使用）

    Returns:
        list[dict[str, Any]]: 模型信息列表（name/label/supports_images）
    """
    del app_state  # 预留
    models: list[dict[str, Any]] = [
        {"name": "inherit", "label": "继承默认模型", "supports_images": True}
    ]
    try:
        from illusion.config.settings import load_settings

        settings = load_settings()
        for env_key, env in settings.list_envs().items():
            for model_key, model_config in env.list_model_configs().items():
                ref = f"{env_key}.{model_key}"
                models.append(
                    {
                        "name": ref,
                        "label": model_config.name,
                        "supports_images": model_config.media_capabilities.supports_images,
                    }
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("列出可用模型失败: %s", exc)

    return models


def list_available_tools(
    tool_registry: ToolRegistry | None = None,
) -> list[dict[str, str]]:
    """返回可用工具列表。

    Args:
        tool_registry: 工具注册表，为 None 时返回空列表

    Returns:
        list[dict[str, str]]: 工具信息列表，每项包含 ``name`` 和 ``description``
    """
    if tool_registry is None:
        return []

    tools: list[dict[str, str]] = []
    for tool in tool_registry.list_tools():
        tools.append(
            {
                "name": tool.name,
                "description": tool.description,
            }
        )
    return tools
