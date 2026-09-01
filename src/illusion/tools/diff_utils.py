"""
变更工具差异统计辅助
===================

为 edit_file / write_file 等变更工具计算单次编辑的增减行数，
并以 ToolResult.metadata 的形式下发（前端工具气泡与单轮变更卡片
据此显示 +N/-M，恢复会话后前端也从 tool_result 文本按同一规则
回算，两条路径口径一致）。

主要功能：
    - count_diff_lines: 统计统一 diff 文本的增删行数
    - count_lines: 统计文本行数（创建新文件时的全增量）
    - change_metadata: 组装变更工具的标准 metadata 载荷
"""

from __future__ import annotations

from typing import Any


def count_diff_lines(diff_text: str) -> tuple[int, int]:
    """统计统一差异文本的增删行数。

    跳过 ``+++``/``---`` 文件头与 ``@@`` 块头；以 ``+`` 开头的行计入
    增行，以 ``-`` 开头的行计入删行（unified diff 的格式标记即行首
    第一个字符，不能 trimStart 后再判断）。

    Args:
        diff_text: unified diff 文本（可能为空串）

    Returns:
        tuple[int, int]: (insertions, deletions)
    """
    insertions = 0
    deletions = 0
    in_hunk = False
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            in_hunk = True
            continue
        # 文件头（+++ / ---）只出现在首个 @@ 之前；hunk 内以 --- 开头
        # 的行是"内容以 -- 开头的删除行"，不能误判为文件头（±1 误差）
        if not in_hunk and line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            insertions += 1
        elif line.startswith("-"):
            deletions += 1
    return insertions, deletions


def count_lines(text: str) -> int:
    """统计文本行数（空文本为 0；与 _count_lines 的字节版口径一致）。"""
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def change_metadata(
    file_path: str,
    *,
    is_create: bool,
    diff_text: str = "",
    content: str = "",
) -> dict[str, Any]:
    """组装变更工具的标准 metadata 载荷。

    Args:
        file_path: 目标文件绝对路径
        is_create: 是否为新建文件（新建时增减为"全文新增"）
        diff_text: unified diff 文本（更新场景）
        content: 写入后的完整内容（创建场景，用于行数统计）

    Returns:
        dict[str, Any]: {file_path, is_create, line_count, insertions, deletions}
    """
    if is_create:
        insertions = count_lines(content)
        deletions = 0
    else:
        insertions, deletions = count_diff_lines(diff_text)
    return {
        "file_path": file_path,
        "is_create": is_create,
        "line_count": insertions if is_create else count_lines(content),
        "insertions": insertions,
        "deletions": deletions,
    }


__all__ = ["change_metadata", "count_diff_lines", "count_lines"]
