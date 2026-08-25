"""checkpoint 对齐修复的回归测试。

覆盖本轮重构的核心根因：restore_messages 场景（Web/渠道每轮重建
runtime、CLI -r/-c 恢复入口）下，新建 CheckpointStore 若不对齐磁盘上
已有的 checkpoint 计数，后续 append 会从 id=0 重复写 checkpoint 行，
resume/rewind 按 id 定位时整体偏移。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from illusion.engine.messages import ConversationMessage
from illusion.services.checkpoint_store import CheckpointStore
from illusion.services.session_storage import session_dir_for


def _checkpoint_ids(store: CheckpointStore) -> list[int]:
    """读取 context.jsonl 中全部 checkpoint id（按出现顺序）"""
    ids: list[int] = []
    for line in store.session_dir.joinpath("context.jsonl").read_text(
        encoding="utf-8"
    ).splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("role") == "_checkpoint":
            ids.append(record["id"])
    return ids


@pytest.mark.asyncio
async def test_align_checkpoint_id_continues_after_disk_history(tmp_path: Path) -> None:
    """align 后 append 的 checkpoint id 从磁盘计数续接，无重复行。

    回归：每轮重建 runtime 时新 store next=0，若不对齐，第二轮会写出
    重复的 id=0 行——rewind 按 id 定位时命中旧行导致整体偏移。
    """
    sid = "align-cont"
    sdir = session_dir_for(str(tmp_path), sid)
    disk = CheckpointStore(sdir, sid)
    # 第一轮（模拟上一 runtime 写入）：cp0 + user + assistant
    await disk.append_checkpoint()
    await disk.append_message(ConversationMessage.from_user_text("round1"))
    await disk.append_message(ConversationMessage(role="assistant", content=[]))

    # 新一轮 runtime：新建 store + 按磁盘实况对齐（build_runtime 自动兜底路径）
    fresh = CheckpointStore(session_dir_for(str(tmp_path), sid), sid)
    fresh.align_checkpoint_id(fresh.count_disk_checkpoints())
    assert fresh.next_checkpoint_id == 1

    await fresh.append_checkpoint()
    await fresh.append_message(ConversationMessage.from_user_text("round2"))
    await fresh.append_message(ConversationMessage(role="assistant", content=[]))

    ids = _checkpoint_ids(fresh)
    assert ids == [0, 1], f"checkpoint id 应为 [0, 1]，实际 {ids}"

    # rewind 语义恢复一致：回退最后一轮保留第一轮
    result = await fresh.rewind_to(fresh.next_checkpoint_id - 1)
    texts = [m.text for m in result.messages if m.role == "user"]
    assert texts == ["round1"]


@pytest.mark.asyncio
async def test_build_runtime_auto_align_restores_session(tmp_path: Path) -> None:
    """build_runtime 在 restore_messages 场景自动按磁盘对齐（未显式传计数）。

    端到端验证恢复入口的对齐兜底：预置一轮历史后经真实 build_runtime
    重建 runtime，store 的 next_checkpoint_id 应续接磁盘计数而非归零。
    """
    cwd = str(tmp_path / "ws")
    sid = "auto-align-sid"

    # 预置第一轮历史（模拟上一 runtime 已写入）
    sdir = session_dir_for(cwd, sid)
    first = CheckpointStore(sdir, sid)
    await first.append_checkpoint()
    await first.append_message(ConversationMessage.from_user_text("q1"))
    await first.append_message(ConversationMessage(role="assistant", content=[]))

    from illusion.ui.runtime import build_runtime

    # api_client 传入桩对象：external_api_client=True，跳过凭据构建；
    # 仅构建 runtime，不发起对话
    bundle = await build_runtime(
        api_client=MagicMock(),
        restore_messages=[
            ConversationMessage.from_user_text("q1").model_dump(mode="json"),
            ConversationMessage(role="assistant", content=[]).model_dump(mode="json"),
        ],
        restore_session_id=sid,
        cwd=cwd,
    )
    store = bundle.engine._checkpoint_store
    assert store is not None and store.session_id == sid
    assert store.next_checkpoint_id == 1, (
        f"restore_messages 场景应自动对齐磁盘 checkpoint 计数，实际 next={store.next_checkpoint_id}"
    )

    # 续写第二轮：id 无重复
    await store.append_checkpoint()
    assert _checkpoint_ids(store) == [0, 1]
