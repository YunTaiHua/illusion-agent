"""
斜杠命令共享工具函数
====================

提供多个命令模块共用的工具函数。
"""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING, Any

import pyperclip

from illusion.config.paths import get_data_dir
from illusion.engine.messages import ConversationMessage

if TYPE_CHECKING:
    from illusion.config.settings import Settings


def run_git_command(cwd: str, *args: str) -> tuple[bool, str]:
    """执行 git 命令并返回结果

    Args:
        cwd: 工作目录
        args: git子命令和参数

    Returns:
        tuple[bool, str]: (是否成功, 输出内容)
    """
    try:
        run_kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            **run_kwargs,
        )
    except FileNotFoundError:
        return False, "git is not installed."
    output = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0:
        return False, output or f"git {' '.join(args)} failed"
    return True, output


def copy_to_clipboard(text: str) -> tuple[bool, str]:
    """复制文本到剪贴板

    尝试多种复制方式: pyperclip, pbcopy, wl-copy, xclip, xsel

    Args:
        text: 要复制的文本

    Returns:
        tuple[bool, str]: (是否成功, 目标位置)
    """
    try:
        pyperclip.copy(text)
        return True, "clipboard"
    except (pyperclip.PyperclipException, OSError):
        clip_kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            clip_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        for command in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard"]):
            try:
                subprocess.run(command, input=text, text=True, check=True, capture_output=True, **clip_kwargs)
                return True, "clipboard"
            except (subprocess.SubprocessError, OSError):
                pass
    fallback = get_data_dir() / "last_copy.txt"
    fallback.write_text(text, encoding="utf-8")
    return False, str(fallback)


def last_message_text(messages: list[ConversationMessage]) -> str:
    """获取最后一条有内容的用户消息

    Args:
        messages: 消息列表

    Returns:
        str: 消息文本，空字符串若无
    """
    for message in reversed(messages):
        if message.text.strip():
            return message.text.strip()
    return ""


def rewind_turns(messages: list[ConversationMessage], turns: int) -> list[ConversationMessage]:
    """回退指定数量的对话回合

    回退到上一个非空的 user 消息（命令从不进入 engine.messages，
    以 / 开头的消息即为真实用户输入，属于可回退轮次）。
    当 pop 到的 user 消息包含 tool_result 时，继续 pop 前面的 assistant 消息
    （含 tool_use），以保持回合完整性，避免产生孤立的 tool_use。

    Args:
        messages: 消息列表
        turns: 回退回合数

    Returns:
        list[ConversationMessage]: 回退后的消息列表
    """
    from illusion.engine.messages import ToolResultBlock, ToolUseBlock

    updated = list(messages)
    for _ in range(max(0, turns)):
        if not updated:
            break
        while updated:
            popped = updated.pop()
            if popped.role == "user" and popped.text.strip():
                # 如果此 user 消息包含 tool_result，说明它是工具调用回合的一部分
                # 需要继续 pop 前面的 assistant 消息（含 tool_use），保持回合完整性
                # 注：命令不进 engine.messages，真实 / 前缀消息也属于可回退轮次
                has_tool_result = any(isinstance(b, ToolResultBlock) for b in popped.content)
                if has_tool_result and updated:
                    prev = updated[-1]
                    if prev.role == "assistant" and any(isinstance(b, ToolUseBlock) for b in prev.content):
                        updated.pop()
                break
    return updated


def coerce_setting_value(settings: Settings, key: str, raw: str) -> Any:
    """将字符串值强制转换为设置字段的正确类型

    Args:
        settings: 设置对象
        key: 字段名
        raw: 原始字符串值

    Returns:
        转换后的值

    Raises:
        KeyError: 字段不存在
        ValueError: 值无效
    """
    from typing import Literal, get_args

    field = settings.model_fields.get(key)
    if field is None:
        raise KeyError(key)
    annotation = field.annotation
    if annotation is bool:
        lowered = raw.lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"Invalid boolean value for {key}: {raw}")
    if annotation is int:
        return int(raw)
    if annotation is str:
        return raw
    if getattr(annotation, "__origin__", None) is Literal:
        allowed = get_args(annotation)
        if raw not in allowed:
            raise ValueError(f"Invalid value for {key}: {raw}")
        return raw
    return raw
