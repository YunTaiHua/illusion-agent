"""渠道会话历史持久化（context.jsonl 单一权威）
================================================

渠道会话映射文件（channels/<name>/sessions/<key>.json）只存索引
（session_id / user_id / chat_type / model / cwd），对话历史统一由
实际会话目录的 context.jsonl（CheckpointStore）承载：

    - 消除双份存储：此前映射文件与 context.jsonl 各存一份完整历史，
      compact/rewind 只作用于后者，前者仍是全量旧消息 → 下轮恢复时
      压缩失效、token 无限增长（资源泄露）。
    - 结构对齐：映射内不再保存与实际会话结构不符的裸消息列表，
      rewind/checkpoint 语义与本地终端会话完全一致。

内容说明：
    - resolve_channel_working_directory: 解析渠道会话工作目录
    - load_indexed_history / replace_indexed_history: 历史读写
    - ChannelSessionIndex: 渠道会话索引 dataclass 基类
    - BaseChannelSessionStore: 三渠道 SessionStore 公共基类
    - ChannelSessionStoreProtocol: store 静态接口（命令处理器类型标注）
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable
from uuid import uuid4

from illusion.engine.messages import ConversationMessage
from illusion.services.checkpoint_store import CheckpointStore, RestoreResult
from illusion.services.session_storage import session_dir_for
from illusion.utils.atomic_write import atomic_write_text

if TYPE_CHECKING:
    from illusion.channels.base import InboundMessage

logger = logging.getLogger(__name__)


def resolve_channel_working_directory(channel_name: str) -> str:
    """解析渠道会话的工作目录

    优先渠道配置的 working_directory（每条消息动态读取，配置变更即时
    生效，无需重启守护进程），缺省回退默认工作区（settings.working_directory
    / 进程目录）。

    Args:
        channel_name: 渠道名（feishu/weixin/qq）

    Returns:
        str: 工作目录绝对路径
    """
    from illusion.channels.config import load_channels_config

    cfg = load_channels_config()
    wd = getattr(getattr(cfg, channel_name, None), "working_directory", None)
    if isinstance(wd, str) and wd:
        return wd
    try:
        from illusion.services.workspace_registry import get_default_workspace

        return get_default_workspace()
    except (OSError, ValueError):
        return str(Path.cwd())


def _session_dir(cwd: str, session_id: str) -> Path:
    """会话数据目录（含 context.jsonl）"""
    return session_dir_for(cwd, session_id)


async def load_indexed_history(
    cwd: str,
    session_id: str,
) -> RestoreResult:
    """从 context.jsonl 加载渠道会话历史

    Args:
        cwd: 会话工作目录（定位 session 目录）
        session_id: 会话 ID

    Returns:
        RestoreResult: 消息列表 + checkpoint 计数（供 build_runtime 对齐
        新建 store 的 next_checkpoint_id，避免重复 id 行）
    """
    if not session_id:
        # 尚未分配会话 ID，无历史可加载
        return RestoreResult.empty()
    sdir = _session_dir(cwd, session_id)
    if not (sdir / "context.jsonl").exists():
        return RestoreResult.empty()
    return await CheckpointStore(sdir, session_id).restore()


async def replace_indexed_history(
    cwd: str,
    session_id: str,
    messages: list[ConversationMessage],
) -> None:
    """用给定消息重建会话历史（/resume 注入场景）

    复用 CheckpointStore.rebuild_after_compact 的原子重建：写入
    checkpoint(id=0) + 消息行 + usage 行。

    Args:
        cwd: 会话工作目录
        session_id: 会话 ID
        messages: 替换后的完整消息列表
    """
    if not session_id:
        return
    store = CheckpointStore(_session_dir(cwd, session_id), session_id)
    await store.rebuild_after_compact(messages)


# ---------------------------------------------------------------------------
# 渠道会话索引与存储的公共基类（feishu/weixin/qq 共用）
# ---------------------------------------------------------------------------


@dataclass
class ChannelSessionIndex:
    """渠道会话索引字段基类

    映射 JSON 只序列化这些字段；对话历史由 context.jsonl 权威承载，
    永不内嵌于索引文件。

    Attributes:
        session_id: 会话唯一标识（对应实际会话目录 context.jsonl）
        key: 存储键（各渠道 build_session_key 生成）
        user_id: 关联用户
        chat_type: 会话类型（dm / group）
        model: 会话使用的模型（可被 /model set 覆盖）
        cwd: 会话工作目录（定位 context.jsonl；创建后保持稳定）
    """

    session_id: str = ""
    key: str = ""
    user_id: str = ""
    chat_type: str = "dm"
    model: str = ""
    cwd: str = ""

    def index_data(self) -> dict[str, Any]:
        """会话索引序列化（不含对话历史）"""
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "chat_type": self.chat_type,
            "model": self.model,
            "cwd": self.cwd,
        }


@runtime_checkable
class ChannelSessionStoreProtocol(Protocol):
    """渠道 session_store 的静态接口

    供 BaseCommandHandler 等调用方类型标注：接口语义漂移（如某渠道漏实现
    replace_messages）在类型检查期暴露，而非运行时才被终端用户踩到。
    """

    def build_session_key(self, msg: InboundMessage) -> str: ...

    def clear(self, key: str) -> None: ...

    def set_model(self, key: str, model: str) -> None: ...

    def get_or_create(self, key: str, user_id: str, chat_type: str) -> ChannelSessionIndex: ...

    async def load_messages(self, session: ChannelSessionIndex) -> RestoreResult: ...

    async def replace_messages(
        self, session: ChannelSessionIndex, messages: list[ConversationMessage]
    ) -> None: ...


class BaseChannelSessionStore:
    """三渠道 SessionStore 公共基类

    子类声明两个类属性并实现渠道差异方法：
        - session_cls: 会话索引 dataclass 类型
        - channel_name: 渠道名（resolve_channel_working_directory 用）
        - build_session_key / list_active（渠道键规则与文件名反推各异）
        - feishu 独有 clear_by_session_id 留在其子类

    统一行为约定：
        - 新建索引即分配 uuid4 session_id（此前 QQ 延迟回写是空 sid
          一系列边界 bug 的根源，现三渠道一致）
        - 索引文件名安全替换统一覆盖冒号与斜杠字符
        - 读到遗留 "messages" 字段打 warning（不迁移、不删除——旧字段
          被忽略，仅提示该索引由热更新前的旧版进程写入）
    """

    session_cls: ClassVar[type[ChannelSessionIndex]]
    channel_name: ClassVar[str]

    def __init__(self, data_dir: Path, group_sessions_per_user: bool = True) -> None:
        """初始化

        Args:
            data_dir: 会话索引存储目录
            group_sessions_per_user: 群组会话是否按用户隔离
                （微信只私聊不使用，保留参数对齐工厂签名）
        """
        self.data_dir = data_dir
        self.group_sessions_per_user = group_sessions_per_user
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # --- 键与路径 ---

    @staticmethod
    def _safe_filename(key: str) -> str:
        """存储键 → 安全文件名（冒号/斜杠统一替换为下划线）"""
        return key.replace(":", "_").replace("/", "_").replace("\\", "_")

    def _index_path(self, key: str) -> Path:
        return self.data_dir / f"{self._safe_filename(key)}.json"

    # QQ 侧既有命名别名（tests 与历史调用使用）
    def _session_path(self, key: str) -> Path:
        return self._index_path(key)

    def _read_index_raw(self, key: str) -> dict[str, Any] | None:
        """读取索引 JSON；缺失/损坏返回 None（损坏时记录 warning）"""
        path = self._index_path(key)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            logger.warning("渠道 %s 会话索引损坏，重建: %s", self.channel_name, exc)
            return None
        if not isinstance(raw, dict):
            logger.warning("渠道 %s 会话索引结构异常，重建: %s", self.channel_name, key)
            return None
        if "messages" in raw:
            logger.warning(
                "渠道 %s 会话索引 %s 含遗留 messages 字段（旧版进程写入），"
                "读取时已忽略；历史以会话目录 context.jsonl 为准",
                self.channel_name, key,
            )
        return raw

    # --- 索引读写 ---

    def save(self, session: ChannelSessionIndex) -> None:
        """保存会话索引

        对话历史不经此持久化——agent turn 内已由 CheckpointStore 实时
        写入 context.jsonl，此处仅同步索引字段。
        """
        atomic_write_text(
            self._index_path(session.key),
            json.dumps(session.index_data(), ensure_ascii=False, indent=2),
        )

    def ensure_indexed(self, session: ChannelSessionIndex) -> None:
        """确保会话索引已落盘（仅当文件不存在时创建）

        绝不覆盖已有记录，供进程崩溃后接续同一 session_id。

        注意：新建索引的 session_id 由 get_or_create 即刻分配（uuid4），
        不存在"先落空串再回写"的窗口期。
        """
        path = self._index_path(session.key)
        if path.exists():
            return  # 已有记录，绝不覆盖（避免清空索引）
        atomic_write_text(
            path,
            json.dumps(session.index_data(), ensure_ascii=False, indent=2),
        )

    def get_or_create(self, key: str, user_id: str, chat_type: str) -> ChannelSessionIndex:
        """获取或创建会话索引

        文件存在则读回，不存在则新建（session_id 即刻分配 uuid4）。

        Note:
            会话索引在 _run_agent 进入 agent turn 前即提前落盘，
            保证进程崩溃后下次启动能接续同一 session_id。
        """
        raw = self._read_index_raw(key)
        if raw is not None:
            return self.session_cls(
                session_id=raw.get("session_id") or uuid4().hex[:12],
                key=key,
                user_id=raw.get("user_id", user_id),
                chat_type=raw.get("chat_type", chat_type),
                model=raw.get("model", ""),
                cwd=raw.get("cwd", ""),
            )
        return self.session_cls(
            session_id=uuid4().hex[:12],
            key=key,
            user_id=user_id,
            chat_type=chat_type,
        )

    def clear(self, key: str) -> None:
        """清空指定键的会话（删除索引文件）

        实际会话目录的 context.jsonl 不在此删除——该目录可能被 /resume
        等本地会话引用。清空渠道侧即开启全新 session_id。
        """
        try:
            self._index_path(key).unlink()
        except FileNotFoundError:
            pass

    def set_model(self, key: str, model: str) -> None:
        """设置指定键会话使用的模型（用于 /model set）"""
        existing = self.get_or_create(key, "", "dm")
        existing.model = model
        self.save(existing)

    # --- 历史（context.jsonl 单一权威） ---

    def _effective_cwd(self, session: ChannelSessionIndex) -> str:
        """解析会话生效工作目录：索引优先，缺省回退渠道配置并回写"""
        if session.cwd:
            return session.cwd
        cwd = resolve_channel_working_directory(self.channel_name)
        session.cwd = cwd
        self.save(session)
        return cwd

    async def load_messages(self, session: ChannelSessionIndex) -> RestoreResult:
        """加载会话对话历史

        context.jsonl 不存在时返回空结果；不做任何旧格式迁移。

        Returns:
            RestoreResult: 消息列表 + checkpoint 计数（供 build_runtime
            对齐新建 store，避免重复 id 行）
        """
        return await load_indexed_history(
            self._effective_cwd(session), session.session_id
        )

    async def replace_messages(
        self, session: ChannelSessionIndex, messages: list[ConversationMessage]
    ) -> None:
        """用给定消息重建会话历史（用于 /resume）

        session_id 为空时先分配并持久化（防御路径；get_or_create 已保证
        即刻分配，正常流程不会走到）。
        """
        if not session.session_id:
            session.session_id = uuid4().hex[:12]
            self.save(session)
        await replace_indexed_history(
            self._effective_cwd(session), session.session_id, messages
        )
