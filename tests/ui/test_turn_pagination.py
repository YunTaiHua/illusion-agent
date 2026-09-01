"""轮次分页纯函数测试模块
=======================

覆盖 build_turn_outline / slice_replay_items_by_turns /
paginate_replay_page 的边界,以及"轮次大纲"与 fork 轮次口径的
一致性（评审要求的 round-trip:同一会话的大纲轮数必须等于 fork
的真实轮数,否则 fork 截断与导航序号会漂移）。
"""

from __future__ import annotations

import json
from pathlib import Path

from illusion.ui.web.ws_web_api import (
    build_turn_outline,
    paginate_replay_page,
    slice_replay_items_by_turns,
)
from illusion.services.session_storage import (
    _is_real_turn_user_record,
    fork_session,
    session_dir_for,
)


def _make_items(turns: int) -> list[dict]:
    """构造 turns 轮的标准重放条目(user → tool → tool_result → assistant)。"""
    items: list[dict] = []
    for i in range(turns):
        items.append({"role": "user", "text": f"q{i + 1}"})
        items.append({"role": "tool", "text": "edit_file",
                      "tool_name": "edit_file", "tool_input": {"file_path": "a.py"}})
        items.append({"role": "tool_result", "text": "Updated a", "tool_use_id": "t"})
        items.append({"role": "assistant", "text": f"a{i + 1}"})
    return items


def test_outline_empty_and_boundaries() -> None:
    assert build_turn_outline([]) == []
    outline = build_turn_outline(_make_items(3))
    assert [e["turn"] for e in outline] == [1, 2, 3]
    assert outline[0]["prompt"] == "q1"
    assert outline[0]["response"] == "a1"


def test_outline_long_text_clipped() -> None:
    outline = build_turn_outline([
        {"role": "user", "text": "x" * 200},
        {"role": "assistant", "text": "y" * 300},
    ])
    assert len(outline[0]["prompt"]) <= 80
    assert outline[0]["prompt"].endswith("…")
    assert len(outline[0]["response"]) <= 120


def test_slice_turn_atomic_and_ordered() -> None:
    items = _make_items(5)
    # 轮 2-4:12 条,含每轮的 tool/tool_result,不把一轮从中间切开
    sliced = slice_replay_items_by_turns(items, 2, 4)
    assert len(sliced) == 12
    assert sliced[0]["text"] == "q2"
    assert sliced[-1]["text"] == "a4"
    # 空区间与越界区间
    assert slice_replay_items_by_turns(items, 4, 2) == []
    assert slice_replay_items_by_turns(items, 6, 9) == []


def test_paginate_fewer_turns_than_page() -> None:
    outline, page, first = paginate_replay_page(_make_items(3), page_turns=10)
    assert len(outline) == 3
    assert first == 1
    assert len(page) == len(_make_items(3))


def test_paginate_exactly_one_page_and_more() -> None:
    # 恰好一页:全量下发,无更早页
    outline, page, first = paginate_replay_page(_make_items(10), page_turns=10)
    assert (len(outline), first) == (10, 1)
    assert len(page) == len(_make_items(10))
    # 超过一页:只下发最近 10 轮
    outline2, page2, first2 = paginate_replay_page(_make_items(13), page_turns=10)
    assert (len(outline2), first2) == (13, 4)
    users = [i for i in page2 if i["role"] == "user"]
    assert [u["text"] for u in users] == [f"q{i}" for i in range(4, 14)]


def _write_session_jsonl(ws: Path, sid: str, turns: int) -> None:
    """直接以 context.jsonl 形态落盘一个 turns 轮的会话。"""
    sd = session_dir_for(ws, sid)
    sd.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in range(turns):
        lines.append(json.dumps({"role": "_checkpoint", "id": i}))
        lines.append(json.dumps({
            "role": "user",
            "message": {"role": "user", "content": [
                {"type": "text", "text": f"q{i + 1}"},
            ]},
        }))
        lines.append(json.dumps({
            "role": "assistant",
            "message": {"role": "assistant", "content": [
                {"type": "text", "text": f"a{i + 1}"},
            ]},
        }))
    (sd / "context.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_outline_turn_count_matches_fork_real_turns(tmp_path: Path) -> None:
    """round-trip:大纲轮数 == fork 截断的真实轮数。

    轮界判定(_is_real_turn_user_record,吃 context.jsonl 记录)与
    build_turn_outline(吃重放条目)是两套代码,若口径漂移,fork 截断
    与导航序号会错位——此测试钉死两者一致。
    """
    from illusion.services.session_storage import read_meta

    ws = tmp_path / "ws"
    ws.mkdir()
    sid = "aaaaaaaaaaaa"
    _write_session_jsonl(ws, sid, 7)

    # 从 JSONL 记录构造与 build_replay_items 同构的条目,得到大纲轮数
    records = [
        json.loads(line)
        for line in (session_dir_for(ws, sid) / "context.jsonl")
        .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    replay_items = []
    for rec in records:
        if rec.get("role") == "user" and _is_real_turn_user_record(rec):
            replay_items.append({"role": "user", "text": "q"})
        elif rec.get("role") == "assistant":
            replay_items.append({"role": "assistant", "text": "a"})
    outline = build_turn_outline(replay_items)

    # fork 保留全部轮次:真实轮数必须等于大纲轮数
    new_sid = fork_session(ws, sid)
    assert new_sid is not None
    assert read_meta(ws, new_sid)["turn_count"] == len(outline) == 7

    # 截断 fork:N 等于大纲的任意前缀,真实轮数都等于 N
    for n in (1, 3, 7):
        trunc_sid = fork_session(ws, sid, n)
        assert trunc_sid is not None
        assert read_meta(ws, trunc_sid)["turn_count"] == n


def test_is_real_turn_filters_notifications_and_empty(tmp_path: Path) -> None:
    def rec(text: str) -> dict:
        return {"role": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": text},
        ]}}

    assert _is_real_turn_user_record(rec("正常提问"))
    assert not _is_real_turn_user_record(rec(""))
    assert not _is_real_turn_user_record(rec("<task-notification>done</task-notification>"))
    assert not _is_real_turn_user_record({"role": "assistant", "message": {"role": "assistant", "content": []}})
