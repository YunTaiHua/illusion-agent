"""
文件写入工具
===========

本模块提供写入完整文件内容到本地文件系统的功能。

主要组件：
    - FileWriteTool: 写入完整文件内容的工具

使用示例：
    >>> from illusion.tools import FileWriteTool
    >>> tool = FileWriteTool()
"""

from __future__ import annotations

import asyncio
import os
from difflib import unified_diff
from typing import Any

from pydantic import BaseModel, Field, model_validator

from illusion.config.paths import resolve_relative_path
from illusion.tools.base import BaseTool, ToolExecutionContext, ToolResult
from illusion.tools.diff_utils import change_metadata
from illusion.utils.atomic_write import atomic_write_text
from illusion.utils.file_state_cache import FileState, FileStateCache


class FileWriteToolInput(BaseModel):
    """文件写入参数。

    属性：
        file_path: 要写入的文件路径
        content: 完整的文件内容
        create_directories: 是否创建父目录

    兼容旧参数名：path 也可传入，会自动映射。
    """

    file_path: str = Field(description="Path of the file to write")
    content: str = Field(description="Full file contents")
    create_directories: bool = Field(default=True)

    @model_validator(mode="before")
    @classmethod
    def _normalize_fields(cls, values: dict[str, Any]) -> dict[str, Any]:
        """将旧参数名映射到新参数名，确保向后兼容。"""
        if "path" in values and "file_path" not in values:
            values["file_path"] = values.pop("path")
        return values


class FileWriteTool(BaseTool[FileWriteToolInput]):
    """写入完整的文件内容。

    用于创建新文件或完全重写现有文件。
    """

    name = "write_file"
    description = """Writes a file to the local filesystem.

Usage:
- This tool will overwrite the existing file if there is one at the provided path.
- If this is an existing file, you MUST use the Read tool first to read the file's contents. This tool will fail if you did not read the file first.
- Prefer the Edit tool for modifying existing files — it only sends the diff. Only use this tool to create new files or for complete rewrites.
- NEVER create documentation files (*.md) or README files unless explicitly requested by the User.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked.
"""
    input_model = FileWriteToolInput

    async def execute(
        self,
        arguments: FileWriteToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """执行文件写入操作，对新文件返回内容预览，对已有文件返回差异信息。

        Args:
            arguments: 文件写入参数
            context: 工具执行上下文

        Returns:
            ToolResult: 包含写入结果和差异/预览文本的执行结果
        """
        # 解析文件路径（拒绝路径穿越攻击）
        try:
            path = resolve_relative_path(context.cwd, arguments.file_path)
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)

        # 获取文件状态缓存
        cache: FileStateCache | None = context.metadata.get("file_state_cache")

        # 对于已有文件，执行读后写强制检查（基于缓存）
        abs_path = str(path)
        if await asyncio.to_thread(path.exists) and cache is not None:
            cached = cache.get(abs_path)
            if cached is None:
                return ToolResult(
                    output=(
                        f"You must read the file at {path} using the Read tool "
                        "before you can write to it. This tool will fail if you attempt "
                        "a write without reading the file first."
                    ),
                    is_error=True,
                )

            # mtime 过期检测
            try:
                current_mtime = await asyncio.to_thread(os.path.getmtime, path)
                if current_mtime > cached.timestamp:
                    # 对于完整读取，进行内容比较回退
                    if cached.offset is None and cached.limit is None:
                        current_content = await asyncio.to_thread(
                            path.read_text, encoding="utf-8"
                        )
                        if current_content != cached.content:
                            return ToolResult(
                                output=(
                                    f"File {path} has been modified since last read. "
                                    "Please read it again before writing."
                                ),
                                is_error=True,
                            )
                    else:
                        return ToolResult(
                            output=(
                                f"File {path} has been modified since last read. "
                                "Please read it again before writing."
                            ),
                            is_error=True,
                        )
            except OSError:
                pass

        # 如果需要，创建父目录
        if arguments.create_directories:
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)

        # 判断是创建还是更新
        is_update = await asyncio.to_thread(path.exists)

        # 对于已有文件，读取原始内容以生成diff
        original = ""
        if is_update:
            original = await asyncio.to_thread(path.read_text, encoding="utf-8")

        # 写入文件内容
        await asyncio.to_thread(atomic_write_text, path, arguments.content)

        # 更新缓存
        if cache is not None:
            try:
                mtime = await asyncio.to_thread(os.path.getmtime, path)
                cache.set(abs_path, FileState(
                    content=arguments.content,
                    timestamp=mtime,
                    offset=None,  # 标记为非 Read 来源
                    limit=None,
                ))
            except OSError:
                pass

        # 生成差异或预览
        if is_update:
            diff_text = _generate_diff(str(path), original, arguments.content)
            return ToolResult(
                output=f"Updated {path}\n{diff_text}",
                metadata=change_metadata(
                    str(path), is_create=False, diff_text=diff_text, content=arguments.content
                ),
            )
        else:
            preview = _generate_create_preview(str(path), arguments.content)
            return ToolResult(
                output=f"Created {path}\n{preview}",
                metadata=change_metadata(str(path), is_create=True, content=arguments.content),
            )


def _generate_diff(file_path: str, original: str, updated: str, context_lines: int = 3) -> str:
    """生成统一差异格式的文本

    Args:
        file_path: 文件路径
        original: 原始内容
        updated: 更新后内容
        context_lines: 上下文行数

    Returns:
        str: 差异文本
    """
    original_lines = original.splitlines(keepends=True)
    updated_lines = updated.splitlines(keepends=True)
    diff_lines = list(unified_diff(
        original_lines,
        updated_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        n=context_lines,
    ))
    if not diff_lines:
        return ""
    return "".join(diff_lines).rstrip()


def _generate_create_preview(file_path: str, content: str, max_lines: int = 10) -> str:
    """生成新文件创建的内容预览

    Args:
        file_path: 文件路径
        content: 文件内容
        max_lines: 最大预览行数

    Returns:
        str: 预览文本
    """
    lines = content.splitlines()
    total = len(lines)
    if total <= max_lines:
        return content
    preview_lines = lines[:max_lines]
    remaining = total - max_lines
    return "\n".join(preview_lines) + f"\n... +{remaining} lines"
