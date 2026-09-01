"""
字符串替换文件编辑工具
======================

本模块提供在现有文件中进行精确字符串替换的功能。

主要组件：
    - FileEditTool: 替换文件中文本的工具

使用示例：
    >>> from illusion.tools import FileEditTool
    >>> tool = FileEditTool()
"""

from __future__ import annotations

import asyncio
import os
from difflib import unified_diff
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from illusion.config.paths import resolve_relative_path
from illusion.tools.base import BaseTool, ToolExecutionContext, ToolResult
from illusion.tools.diff_utils import change_metadata
from illusion.utils.atomic_write import atomic_write_text
from illusion.utils.file_state_cache import FileState, FileStateCache


def mark_file_read(abs_path: str) -> None:
    """兼容性函数：记录文件已被读取。

    注意：此函数现在仅用于向后兼容测试代码。
    实际的缓存逻辑已迁移到 FileStateCache。
    """


def has_file_been_read(abs_path: str) -> bool:
    """兼容性函数：检查文件是否已被读取。

    注意：此函数现在仅用于向后兼容测试代码。
    实际的缓存逻辑已迁移到 FileStateCache。
    """
    return True  # 始终返回 True，实际验证由缓存完成


class FileEditToolInput(BaseModel):
    """文件编辑参数。

    属性：
        file_path: 要编辑的文件路径
        old_string: 要替换的现有文本
        new_string: 替换文本
        replace_all: 是否替换所有匹配项

    兼容旧参数名：path, old_str, new_str 均可传入，会自动映射。
    """

    file_path: str = Field(description="Path of the file to edit")
    old_string: str = Field(description="Existing text to replace")
    new_string: str = Field(description="Replacement text")
    replace_all: bool = Field(default=False)

    @model_validator(mode="before")
    @classmethod
    def _normalize_fields(cls, values: dict[str, Any]) -> dict[str, Any]:
        """将旧参数名映射到新参数名，确保向后兼容。"""
        if "path" in values and "file_path" not in values:
            values["file_path"] = values.pop("path")
        if "old_str" in values and "old_string" not in values:
            values["old_string"] = values.pop("old_str")
        if "new_str" in values and "new_string" not in values:
            values["new_string"] = values.pop("new_str")
        return values


class FileEditTool(BaseTool[FileEditToolInput]):
    """替换现有文件中的文本。

    用于对文件进行精确的字符串替换编辑。
    """

    name = "edit_file"
    description = """Performs exact string replacements in files.

Usage:
- You must use your `Read` tool at least once in the conversation before editing. This tool will error if you attempt an edit without reading the file.
- When editing text from Read tool output, ensure you preserve the exact indentation (tabs/spaces) as it appears AFTER the line number prefix. The line number prefix format is: spaces + line number + arrow. Everything after that arrow is the actual file content to match. Never include any part of the line number prefix in the old_string or new_string.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.
- The edit will FAIL if `old_string` is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`.
- Use `replace_all` for replacing and renaming strings across the file. This parameter is useful if you want to rename a variable for instance.
- Use the smallest old_string that's clearly unique — usually 2-4 adjacent lines is sufficient. Avoid including 10+ lines of context when less uniquely identifies the target."""
    input_model = FileEditToolInput

    def __init__(self) -> None:
        super().__init__()
        # 文件级互斥锁：同一文件的读-改-写必须串行化。
        # 引擎并发执行同一消息中的多个工具调用（见 query.py 多工具分支），
        # 若两个 edit_file 并行编辑同一文件，后写者会覆盖先写者的修改
        # （读-改-写竞争，表现为"工具返回成功但修改丢失"）。
        self._file_locks: dict[str, asyncio.Lock] = {}

    def _get_file_lock(self, abs_path: str) -> asyncio.Lock:
        """获取指定文件的互斥锁（按绝对路径，跨调用复用）。"""
        lock = self._file_locks.get(abs_path)
        if lock is None:
            lock = asyncio.Lock()
            self._file_locks[abs_path] = lock
        return lock

    async def execute(
        self,
        arguments: FileEditToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """执行文件编辑操作，替换指定文本并返回差异信息。

        Args:
            arguments: 文件编辑参数
            context: 工具执行上下文

        Returns:
            ToolResult: 包含编辑结果和差异文本的执行结果
        """
        # 解析文件路径
        try:
            path = resolve_relative_path(context.cwd, arguments.file_path)
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)

        # 获取文件状态缓存
        cache: FileStateCache | None = context.metadata.get("file_state_cache")

        # 处理新文件创建：仅当 old_string 为空时允许
        if not await asyncio.to_thread(path.exists):
            if arguments.old_string:
                return ToolResult(
                    output=f"File not found: {path}. To create a new file, set old_string to empty string.",
                    is_error=True,
                )
            # 创建新文件
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(atomic_write_text, path, arguments.new_string)
            # 将新创建的文件写入缓存
            if cache is not None:
                try:
                    mtime = await asyncio.to_thread(os.path.getmtime, path)
                    cache.set(str(path), FileState(
                        content=arguments.new_string,
                        timestamp=mtime,
                        offset=None,
                        limit=None,
                    ))
                except OSError:
                    pass
            # 生成新文件内容预览
            preview = _generate_create_preview(str(path), arguments.new_string)
            return ToolResult(
                output=f"Created {path}\n{preview}",
                metadata=change_metadata(str(path), is_create=True, content=arguments.new_string),
            )

        # 已存在文件编辑：加文件级互斥锁，防止并发读-改-写竞争
        abs_path = str(path)
        async with self._get_file_lock(abs_path):
            return await self._do_edit(path, abs_path, arguments, context, cache)

    async def _do_edit(
        self,
        path: Path,
        abs_path: str,
        arguments: FileEditToolInput,
        context: ToolExecutionContext,
        cache: FileStateCache | None,
    ) -> ToolResult:
        """对已存在的文件执行编辑（在文件级互斥锁内调用，不会并发执行）。"""
        # 读后编辑强制检查（基于缓存）
        if cache is not None:
            cached = cache.get(abs_path)
            if cached is None:
                return ToolResult(
                    output=(
                        f"You must read the file at {path} using the Read tool "
                        "before you can edit it. This tool will error if you attempt "
                        "an edit without reading the file first."
                    ),
                    is_error=True,
                )

            # mtime 过期检测（使用容差比较，避免 Windows 浮点精度误判）
            try:
                current_mtime = await asyncio.to_thread(os.path.getmtime, path)
                if current_mtime - cached.timestamp > 1e-6:
                    # 对于完整读取（offset=None, limit=None），进行内容比较回退
                    # 这解决了 Windows 上 mtime 误报的问题（云同步、杀毒软件等）
                    if cached.offset is None and cached.limit is None:
                        current_content = await asyncio.to_thread(
                            path.read_text, encoding="utf-8"
                        )
                        if current_content != cached.content:
                            return ToolResult(
                                output=(
                                    f"File {path} has been modified since last read. "
                                    "Please read it again before editing."
                                ),
                                is_error=True,
                            )
                    else:
                        return ToolResult(
                            output=(
                                f"File {path} has been modified since last read. "
                                "Please read it again before editing."
                            ),
                            is_error=True,
                        )
            except OSError:
                pass  # 无法获取 mtime，继续编辑

        # 空操作保护
        if arguments.old_string == arguments.new_string:
            return ToolResult(
                output="old_string and new_string are identical — no changes needed.",
                is_error=True,
            )

        # 非空文件上的空 old_string
        original = await asyncio.to_thread(path.read_text, encoding="utf-8")
        if not arguments.old_string and original.strip():
            return ToolResult(
                output=(
                    "old_string is empty but the file is not empty. "
                    "To replace the entire file content, use the Write tool instead."
                ),
                is_error=True,
            )

        # 空文件上的空 old_string = 写入新内容
        if not arguments.old_string and not original.strip():
            await asyncio.to_thread(atomic_write_text, path, arguments.new_string)
            # 更新缓存
            if cache is not None:
                try:
                    mtime = await asyncio.to_thread(os.path.getmtime, path)
                    cache.set(abs_path, FileState(
                        content=arguments.new_string,
                        timestamp=mtime,
                        offset=None,
                        limit=None,
                    ))
                except OSError:
                    pass
            diff_text = _generate_diff(str(path), original, arguments.new_string)
            return ToolResult(
                output=f"Updated {path}\n{diff_text}",
                metadata=change_metadata(
                    str(path), is_create=False, diff_text=diff_text, content=arguments.new_string
                ),
            )

        # 检查 old_string 是否存在于文件中
        if arguments.old_string not in original:
            # 尝试提供关于文件中内容的帮助上下文
            _similar = _find_similar_lines(original, arguments.old_string)
            msg = "old_string was not found in the file."
            if _similar:
                msg += f"\n\nThe closest matches in the file are:\n{_similar}"
            return ToolResult(output=msg, is_error=True)

        # 唯一性检查（当不是替换所有时）
        if not arguments.replace_all:
            count = original.count(arguments.old_string)
            if count > 1:
                return ToolResult(
                    output=(
                        f"old_string appears {count} times in the file. "
                        "Either provide a larger string with more surrounding context to make it unique, "
                        "or use replace_all=true to change every instance."
                    ),
                    is_error=True,
                )

        # 应用编辑
        if arguments.replace_all:
            updated = original.replace(arguments.old_string, arguments.new_string)
        else:
            updated = original.replace(arguments.old_string, arguments.new_string, 1)

        await asyncio.to_thread(atomic_write_text, path, updated)

        # 更新缓存
        if cache is not None:
            try:
                mtime = await asyncio.to_thread(os.path.getmtime, path)
                cache.set(abs_path, FileState(
                    content=updated,
                    timestamp=mtime,
                    offset=None,  # 标记为非 Read 来源
                    limit=None,
                ))
            except OSError:
                pass

        # 生成差异文本
        diff_text = _generate_diff(str(path), original, updated)
        return ToolResult(
            output=f"Updated {path}\n{diff_text}",
            metadata=change_metadata(
                str(path), is_create=False, diff_text=diff_text, content=updated
            ),
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


def _find_similar_lines(content: str, target: str, max_lines: int = 5) -> str:
    """在内容中找到与目标字符串部分匹配的行。

    返回格式化的字符串，显示最接近的匹配，或空字符串。
    """
    target_lines = [line.strip() for line in target.splitlines() if line.strip()]
    if not target_lines:
        return ""

    content_lines = content.splitlines()
    matches: list[str] = []
    first_target = target_lines[0].lower()

    for i, line in enumerate(content_lines):
        stripped = line.strip().lower()
        # 检查此行是否包含第一个目标行或被其包含
        if first_target in stripped or stripped in first_target:
            start = max(0, i - 1)
            end = min(len(content_lines), i + 2)
            block = "\n".join(f"  {j+1}: {content_lines[j]}" for j in range(start, end))
            matches.append(block)
            if len(matches) >= max_lines:
                break

    return "\n\n".join(matches) if matches else ""
