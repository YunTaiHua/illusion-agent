"""
文件读取工具
===========

本模块提供读取本地文件系统文件的功能，支持文本文件和图片文件。

主要组件：
    - FileReadTool: 读取文本文件和图片文件的工具

使用示例：
    >>> from illusion.tools import FileReadTool
    >>> tool = FileReadTool()
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from illusion.config.paths import resolve_relative_path
from illusion.tools.base import BaseTool, ToolExecutionContext, ToolResult
from illusion.utils.file_state_cache import FileState, FileStateCache

# 图片文件扩展名集合
_IMAGE_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",
})

# 图片文件大小限制（字节）
_IMAGE_SIZE_LIMIT: int = 20 * 1024 * 1024  # 20 MB


def _is_image_file(path: Path) -> bool:
    """检测文件是否为图片文件。"""
    return path.suffix.lower() in _IMAGE_EXTENSIONS


def _get_media_type(path: Path) -> str:
    """获取文件的 MIME 类型。"""
    media_type, _ = mimetypes.guess_type(str(path))
    if media_type:
        return media_type
    fallback = {
        ".svg": "image/svg+xml",
    }
    return fallback.get(path.suffix.lower(), "application/octet-stream")


class FileReadToolInput(BaseModel):
    """文件读取参数。

    属性：
        file_path: 要读取的文件路径
        offset: 起始行号（从 0 开始）
        limit: 返回的行数限制

    兼容旧参数名：path 也可传入，会自动映射。
    """

    file_path: str = Field(description="Path of the file to read")
    offset: int = Field(default=0, ge=0, description="Zero-based starting line")
    limit: int = Field(default=2000, ge=1, le=2000, description="Number of lines to return")

    @model_validator(mode="before")
    @classmethod
    def _normalize_fields(cls, values: dict[str, Any]) -> dict[str, Any]:
        """将旧参数名映射到新参数名，确保向后兼容。"""
        if "path" in values and "file_path" not in values:
            values["file_path"] = values.pop("path")
        return values


class FileReadTool(BaseTool[FileReadToolInput]):
    """读取文本文件和图片文件。

    支持图片（PNG, JPG, GIF, WebP 等），通过 base64 编码传递给多模态模型。
    """

    name = "read_file"
    description = """Reads a file from the local filesystem. You can access any file directly by using this tool.
Assume this tool is able to read all files on the machine. If the User provides a file_path to a file assume that file_path is valid. It is okay to read a file that does not exist; an error will be returned.

Usage:
- The file_path parameter must be an absolute path, not a relative path
- By default, it reads up to 2000 lines starting from the beginning of the file
- You can optionally specify a line offset and limit (especially handy for long files), but it's recommended to read the whole file by not providing these parameters
- Results are returned using cat -n format, with line numbers starting at 1
- This tool allows Illusion Agent to read images (eg PNG, JPG, etc). When reading an image file the contents are presented visually as Illusion Agent is a multimodal LLM.
- This tool can only read files, not directories. To read a directory, use an ls command via the Bash tool.
- You will regularly be asked to read screenshots. If the user provides a file_path to a screenshot, ALWAYS use this tool to view the file at the path. This tool will work with all temporary file paths.
- If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents."""
    input_model = FileReadToolInput

    def is_read_only(self, arguments: FileReadToolInput) -> bool:
        del arguments
        return True

    async def execute(
        self,
        arguments: FileReadToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        # 解析文件路径
        try:
            path = resolve_relative_path(context.cwd, arguments.file_path)
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)
        # 检查文件是否存在
        if not await asyncio.to_thread(path.exists):
            return ToolResult(output=f"File not found: {path}", is_error=True)
        # 检查是否为目录
        if await asyncio.to_thread(path.is_dir):
            return ToolResult(output=f"Cannot read directory: {path}", is_error=True)

        # 获取文件状态缓存
        cache: FileStateCache | None = context.metadata.get("file_state_cache")

        # 检测是否为图片文件
        if _is_image_file(path):
            return await self._read_image_file(path)

        # 读取文本文件
        return await self._read_text_file(path, arguments, cache)

    async def _read_image_file(self, path: Path) -> ToolResult:
        """读取图片文件并返回 base64 编码数据。"""
        raw = await asyncio.to_thread(path.read_bytes)
        file_size = len(raw)

        # 检查文件大小限制
        if file_size > _IMAGE_SIZE_LIMIT:
            limit_mb = _IMAGE_SIZE_LIMIT // (1024 * 1024)
            return ToolResult(
                output=f"Image file too large: {file_size} bytes exceeds {limit_mb} MB limit",
                is_error=True,
            )

        media_type = _get_media_type(path)
        encoded = base64.b64encode(raw).decode("ascii")

        # 生成输出描述
        size_str = _human_size(file_size)
        output = f"[image file: {path} ({size_str}, {media_type})]"

        return ToolResult(
            output=output,
            metadata={
                "media_category": "image",
                "media_type": media_type,
                "media_data": encoded,
                "media_path": str(path),
                "media_size": file_size,
            },
        )

    async def _read_text_file(
        self,
        path: Path,
        arguments: FileReadToolInput,
        cache: FileStateCache | None,
    ) -> ToolResult:
        """读取文本文件。

        Args:
            path: 文件路径
            arguments: 读取参数
            cache: 文件状态缓存（可选）

        Returns:
            ToolResult: 读取结果
        """
        abs_path = str(path)

        # 缓存去重检查：如果文件未被修改且范围相同，返回存根
        if cache is not None:
            cached = cache.get(abs_path)
            if (
                cached is not None
                and cached.offset is not None  # 来自之前的 Read（非 Edit/Write）
                and cached.offset == arguments.offset
                and cached.limit == arguments.limit
            ):
                # 检查 mtime
                try:
                    current_mtime = await asyncio.to_thread(os.path.getmtime, path)
                    if current_mtime == cached.timestamp:
                        return ToolResult(
                            output="File unchanged since last read. "
                            "The content from the earlier Read is still current."
                        )
                except OSError:
                    pass  # 文件可能已被删除，继续读取

        # 实际读取文件
        raw = await asyncio.to_thread(path.read_bytes)
        if b"\x00" in raw:
            return ToolResult(output=f"Binary file cannot be read as text: {path}", is_error=True)

        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        selected = lines[arguments.offset : arguments.offset + arguments.limit]
        numbered = [
            f"{arguments.offset + index + 1:>6}\t{line}"
            for index, line in enumerate(selected)
        ]
        if not numbered:
            return ToolResult(
                output=f"System reminder: The file at {path} exists but has empty contents."
            )

        # 写入缓存
        if cache is not None:
            try:
                mtime = await asyncio.to_thread(os.path.getmtime, path)
                cache.set(abs_path, FileState(
                    content=text,
                    timestamp=mtime,
                    offset=arguments.offset,
                    limit=arguments.limit,
                ))
            except OSError:
                pass  # 无法获取 mtime，跳过缓存

        return ToolResult(output="\n".join(numbered))


def _human_size(size: int) -> str:
    """将字节数转为人类可读的大小字符串。"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"
