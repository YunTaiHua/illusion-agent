"""
斜杠资源发现和 prompt 展开
===========================

本模块提供工作区斜杠资源（技能与命令）的发现、解析和展开功能，
是引擎提交管线与双端补全目录共享的单一实现。

主要功能：
    - resolve_slash_skill: 解析单个行首 ``/name args`` 调用
    - resolve_slash_resources: 返回正文中资源按首现顺序去重的展开结果
    - prepare_slash_submission: 一次查询同时捕获供模型展开的正文
      与供转录标色的引用元数据
    - list_slash_commands: 列出可从斜杠输入激活的技能和命令
    - format_prompt_entry: 格式化真技能的系统提示词条目

展开语义：
    - 命令正文先剥离 YAML frontmatter，再替换资源路径变量与参数占位符
    - 资源路径变量支持 CLAUDE/ZCODE/ILLUSION 三套前缀的 shell 与 Windows 写法
    - ``$ARGUMENTS`` / ``$1`` 等占位符一次性替换，替换值不参与二次扫描
    - 正文扫描跳过代码围栏、行内代码、Markdown 链接、URL 与 @ 引用
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from illusion.skills.registry import SkillRegistry
from illusion.skills.types import SkillDefinition, SlashReferences

MAX_PROMPT_ENTRY_DESC_CHARS = 250
_ARGUMENT_TOKEN = re.compile(r"\$ARGUMENTS|\$(\d+)")


@dataclass(frozen=True)
class SkillCommandMatch:
    """一次斜杠资源命中。"""

    name: str
    kind: str
    args: str
    expanded: str


@dataclass
class SlashSubmission:
    """某次提交的唯一解析结果：正文按规范资源去重，渲染元数据按原始拼写保留。"""

    matches: list[SkillCommandMatch] = field(default_factory=list)
    references: SlashReferences = field(default_factory=dict)


def _truncate_description(description: str) -> str:
    """截断列表描述，避免资源列表占用过多提示词空间。"""
    if len(description) <= MAX_PROMPT_ENTRY_DESC_CHARS:
        return description
    return description[: MAX_PROMPT_ENTRY_DESC_CHARS - 1] + "…"


def format_prompt_entry(skill: SkillDefinition) -> str:
    """格式化真技能的系统提示词条目。"""
    return f"- **{skill.name}**: {_truncate_description(skill.description)}"


def _split_arguments(args: str) -> list[str]:
    """按空白拆分参数并保留引号中的单个参数。"""
    result: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaping = False

    for char in args:
        if escaping:
            current.append(char)
            escaping = False
            continue
        if char == "\\":
            escaping = True
            continue
        if quote:
            if char == quote:
                quote = None
            else:
                current.append(char)
            continue
        if char in {"'", '"'}:
            quote = char
        elif char.isspace():
            if current:
                result.append("".join(current))
                current = []
        else:
            current.append(char)

    if escaping:
        current.append("\\")
    if current:
        result.append("".join(current))
    return result


def _replace_arguments(content: str, args: str) -> tuple[str, bool]:
    """一次性替换参数占位符，替换结果不会再次参与占位符扫描。"""
    positional = _split_arguments(args)
    used = False

    def replace(match: re.Match[str]) -> str:
        nonlocal used
        used = True
        token = match.group(0)
        if token == "$ARGUMENTS":
            return args
        index = int(match.group(1)) - 1
        return positional[index] if 0 <= index < len(positional) else ""

    return _ARGUMENT_TOKEN.sub(replace, content), used


def _strip_frontmatter(content: str) -> str:
    """移除命令正文前的 YAML frontmatter。"""
    normalized = content.removeprefix("\ufeff")
    if not normalized.startswith("---"):
        return content
    lines = normalized.splitlines()
    if not lines or lines[0].strip() != "---":
        return content
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[index + 1 :])
    return content


def _replace_variable(text: str, name: str, value: str | None) -> str:
    """替换一个资源路径变量的常见 shell 和 Windows 写法。"""
    if not value:
        return text
    # 先处理带括号的形式，避免裸变量替换再次触碰替换值。
    text = text.replace(f"${{{name}}}", value)
    text = text.replace(f"%{name}%", value)
    return text.replace(f"${name}", value)


def _expand_skill_content(
    content: str,
    skill_root: str | None,
    args: str,
    *,
    plugin_root: str | None = None,
    substitute_arguments: bool = True,
    append_arguments: bool = False,
) -> str:
    """展开资源路径和参数占位符。"""
    expanded = content
    plugin_names = ("CLAUDE_PLUGIN_ROOT", "ZCODE_PLUGIN_ROOT", "ILLUSION_PLUGIN_ROOT")
    skill_names = ("CLAUDE_SKILL_DIR", "ZCODE_SKILL_DIR", "ILLUSION_SKILL_DIR")

    for variable in plugin_names:
        expanded = _replace_variable(expanded, variable, plugin_root)
    for variable in skill_names:
        expanded = _replace_variable(expanded, variable, skill_root)

    used_arguments = False
    if substitute_arguments:
        # 路径变量先展开，用户参数最后注入，参数中的 $ARGUMENTS 或路径变量
        # 因此不会被二次解释。
        expanded, used_arguments = _replace_arguments(expanded, args)
    if append_arguments and args and not used_arguments:
        expanded = f"{expanded.rstrip()}\n\nUser arguments:\n{args}"
    return expanded


def _resource_directory(skill: SkillDefinition) -> str | None:
    """返回资源文件所在目录。"""
    if skill.skill_root:
        return skill.skill_root
    if skill.path:
        return str(Path(skill.path).parent)
    return None


def _resource_index(registry: SkillRegistry) -> dict[str, SkillDefinition | None]:
    exact: dict[str, SkillDefinition | None] = {}
    leaves: dict[str, SkillDefinition | None] = {}
    for entry in registry.list_entries():
        key = entry.name.casefold()
        leaf = key.rsplit(":", 1)[-1]
        leaves[leaf] = None if leaf in leaves else entry
        if key not in exact or entry.is_command:
            exact[key] = entry
    # 同名时完整名称优先于短名，命令优先于技能。
    return {**leaves, **exact}


def _expand_match(selected: SkillDefinition, args: str) -> SkillCommandMatch:
    expanded_body = _expand_skill_content(
        _strip_frontmatter(selected.content) if selected.is_command else selected.content,
        _resource_directory(selected),
        args,
        plugin_root=selected.plugin_root,
        append_arguments=selected.is_command,
    )
    metadata_lines = [
        f"<command-message>{selected.name}</command-message>",
        f"<command-name>/{selected.name}</command-name>",
    ]
    if args:
        metadata_lines.append(f"<command-args>{args}</command-args>")
    prompt = "\n".join(metadata_lines) + "\n\n" + expanded_body
    return SkillCommandMatch(
        name=selected.name,
        kind=selected.kind,
        args=args,
        expanded=prompt,
    )


# 行首调用沿用「第一个空白前是全名，其余是参数」的约定。正文中的资源
# 则必须是独立 token，不能拿路径的某段或未注册长名的前缀当命中。
_LEADING_RESOURCE = re.compile(r"/([^\s/\\]+)(?=\s|\Z)")
_FENCE_END = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*\r?$", re.MULTILINE)
_TICKS = re.compile(r"(`+)")
_LEFT = re.escape("\"'([{“‘（【《「『，。；：！？、")
_RIGHT = re.escape("\"')]}”’）】》」』，。；：！？、,;")
_LINK_TITLE = r'''(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\((?:\\.|[^()\\])*\))'''
_ANGLE_DEST = r"<(?:\\.|[^<>\\\r\n])*>"
_LINK_PART = re.compile(rf"\\.|{_ANGLE_DEST}|(?<=\s){_LINK_TITLE}|[()]|\r?\n[ \t]*\r?\n")
# 链接定义的目标可在下一行，可带以空白分开的单/双引号或括号标题。
_REFERENCE_DEF = (
    r"^(?: {0,3}>[ \t]?)* {0,3}\[(?:\\.|[^\[\]\\\r\n])+\]:[ \t]*(?:\r?\n[ \t]*)?"
    rf"(?:{_ANGLE_DEST}|[^\s]+)(?:(?:[ \t]+|[ \t]*\r?\n[ \t]*){_LINK_TITLE})?"
)
_INLINE = re.compile(
    r"^ {0,3}(?P<fence>`{3,}|~{3,})|(?<![\\`])(?P<ticks>`+)"
    rf"|{_REFERENCE_DEF}"
    # 跳过 @ 引用（含带空格的草稿）、Markdown 链接目的地和 URL 中的斜杠。
    r'|@\[(?:\\.|[^\\\]\r\n])*(?:\](?=\()|$)|@"[^"\r\n]*(?:"|$)|@[^\s`"\[\]()]+'
    r'|(?P<link>(?<=\])\()'
    r'''|(?:(?<![\w+.\-])[a-zA-Z][a-zA-Z0-9+.\-]*://|//|\bwww\.)[^\s<>`"'，。；！？、]+'''
    rf"|(?<![^\s{_LEFT}])/(?P<name>[\w-]+(?:[.:][\w-]+)*)"
    rf"(?=[.?!:]*(?:[\s{_RIGHT}]|$))",
    re.MULTILINE,
)


def _past_code(line: str, opener: re.Match[str]) -> int:
    """span 要求等长反引号；围栏可以用更长的同类标记闭合。"""
    fence = opener["fence"]
    for end in (_FENCE_END if fence else _TICKS).finditer(line, opener.end()):
        if (fence and end[1].startswith(fence)) or end[1] == opener["ticks"]:
            return end.end()
    return len(line)


def _past_inline_link(line: str, pos: int) -> int:
    """跳过 ](destination "title")，只统计字面值和转义之外的括号。"""
    depth = 1
    for part in _LINK_PART.finditer(line, pos):
        value = part[0]
        if value == "(":
            depth += 1
        elif value == ")":
            depth -= 1
            if not depth:
                return part.end()
        elif value.startswith(("\r", "\n")):  # 空行结束当前 Markdown 段落
            return part.end()
    return len(line)


def _resource_queries(line: str) -> Iterator[tuple[str, str]]:
    head = _LEADING_RESOURCE.match(line)
    if head:
        yield head[1], line[head.end():].strip()
    pos = 0
    while token := _INLINE.search(line, pos):
        pos = token.end()
        if token["fence"] or token["ticks"]:
            pos = _past_code(line, token)
        elif token["link"]:
            pos = _past_inline_link(line, pos)
        elif token["name"] and not (head and token.span() == head.span()):
            yield token["name"], ""


def resolve_slash_skill(
    line: str, cwd: str, *, registry: SkillRegistry | None = None,
) -> SkillCommandMatch | None:
    """解析单个行首 /name args 调用。"""
    line = line.strip()
    head = _LEADING_RESOURCE.match(line)
    if head is None:
        return None
    if registry is None:
        from illusion.skills.loader import load_skill_registry

        registry = load_skill_registry(cwd)
    selected = _resource_index(registry).get(head[1].casefold())
    return _expand_match(selected, line[head.end():].strip()) if selected else None


def prepare_slash_submission(
    line: str, cwd: str, *, reserved_names: Iterable[str] = (),
    registry: SkillRegistry | None = None,
) -> SlashSubmission:
    """一次查询工作区资源，同时捕获供模型展开和供转录标色的结果。"""
    result = SlashSubmission()
    if "/" not in line:
        return result
    reserved = {name.casefold() for name in reserved_names}
    seen: set[str] = set()
    index: dict[str, SkillDefinition | None] | None = None
    for name, args in _resource_queries(line):
        key = name.casefold()
        if key in reserved:
            continue
        if index is None:
            if registry is None:  # 普通文本、URL、代码等不扫描磁盘上的资源目录
                from illusion.skills.loader import load_skill_registry

                registry = load_skill_registry(cwd)
            index = _resource_index(registry)
        selected = index.get(key)
        if selected is None:
            continue
        result.references[f"/{name}"] = "command" if selected.is_command else "skill"
        if selected.name not in seen:
            seen.add(selected.name)
            result.matches.append(_expand_match(selected, args))
    return result


def resolve_slash_resources(
    line: str, cwd: str, *, reserved_names: Iterable[str] = (),
    registry: SkillRegistry | None = None,
) -> list[SkillCommandMatch]:
    """返回按规范名首现顺序去重的资源展开。"""
    return prepare_slash_submission(line, cwd, reserved_names=reserved_names, registry=registry).matches


def list_slash_commands(cwd: str, *, registry: SkillRegistry | None = None) -> list[dict[str, str]]:
    """列出可从斜杠输入激活的技能和命令。"""
    if registry is None:
        from illusion.skills.loader import load_skill_registry

        registry = load_skill_registry(cwd)
    return [
        {
            "name": f"/{entry.name}",
            "kind": entry.kind,
            "source": entry.source,
            "description": entry.description,
        }
        for entry in registry.list_entries()
    ]
