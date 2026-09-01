"""diff_utils 单元测试模块
=======================

覆盖变更工具的增删行数统计：unified diff 行计数（含 "\\ No newline
at end of file" 标记行不误计）、创建场景全文增量、metadata 组装。
"""

from __future__ import annotations

from illusion.tools.diff_utils import (
    change_metadata,
    count_diff_lines,
    count_lines,
)

DIFF = """--- a/x.py
+++ b/x.py
@@ -1,4 +1,5 @@
 context
-removed
+added
+added2
 context2
\\ No newline at end of file
"""


def test_count_diff_lines_basic() -> None:
    ins, dele = count_diff_lines(DIFF)
    assert ins == 2
    assert dele == 1


def test_count_diff_lines_headers_and_empty() -> None:
    # 仅文件头与空串
    ins, dele = count_diff_lines("--- a/x\n+++ b/x\n")
    assert (ins, dele) == (0, 0)
    assert count_diff_lines("") == (0, 0)


def test_count_diff_lines_removed_line_starting_with_dashes() -> None:
    # hunk 内删除行的内容以 -- 开头时,渲染为 "---…",不能误判为文件头
    diff = (
        "--- a/x\n"
        "+++ b/x\n"
        "@@ -1,2 +1,2 @@\n"
        "---flag\n"
        "+-flag\n"
    )
    assert count_diff_lines(diff) == (1, 1)


def test_count_diff_lines_plus_minus_prefix_content_not_miscounted() -> None:
    # +++ / --- 开头是文件头不是增删行；@@ 块头同样跳过
    ins, dele = count_diff_lines(
        "+++ b/x\n@@ -1 +1 @@\n-old\n+new\n")
    assert (ins, dele) == (1, 1)


def test_count_lines_conventions() -> None:
    assert count_lines("") == 0
    assert count_lines("a") == 1
    assert count_lines("a\n") == 1
    assert count_lines("a\nb") == 2


def test_change_metadata_create_counts_full_content() -> None:
    meta = change_metadata("x.py", is_create=True, content="l1\nl2\nl3")
    assert meta == {
        "file_path": "x.py",
        "is_create": True,
        "line_count": 3,
        "insertions": 3,
        "deletions": 0,
    }


def test_change_metadata_update_counts_diff() -> None:
    meta = change_metadata(
        "x.py", is_create=False, diff_text=DIFF, content="whatever")
    assert meta["insertions"] == 2
    assert meta["deletions"] == 1
    assert meta["is_create"] is False
