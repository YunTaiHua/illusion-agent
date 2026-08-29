"""
对话消息模型模块
================

本模块提供查询引擎使用的对话消息模型。

主要类：
    - TextBlock: 纯文本内容块
    - ToolUseBlock: 模型执行命名工具的请求
    - ToolResultBlock: 发送回模型的工具结果内容
    - ConversationMessage: 单个助手或用户消息

使用示例：
    >>> from illusion.engine.messages import ConversationMessage, TextBlock
    >>> msg = ConversationMessage.from_user_text("Hello")
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class TextBlock(BaseModel):
    """纯文本内容块
    
    Attributes:
        type: 块类型（固定为 "text"）
        text: 文本内容
    """

    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(BaseModel):
    """模型执行命名工具的请求

    Attributes:
        type: 块类型（固定为 "tool_use"）
        id: 工具调用唯一标识
        name: 工具名称
        input: 工具输入参数
        provider_data: 供应商特有、需原样回传的不透明元数据。Gemini 3 思考模型
            对每个 functionCall 附加 ``thought_signature``，必须在后续请求中回传，
            否则 API 返回 400 "missing thought_signature"。此处存放形如
            ``{"extra_content": {"google": {"thought_signature": "..."}}}``，
            非思考模型或非 Gemini 模型保持为空字典。仅在 OpenAI 兼容路径中
            被 ``_convert_assistant_message`` 读取；Anthropic 路径的
            ``serialize_content_block`` 不读取此字段，保持透明。
    """

    type: Literal["tool_use"] = "tool_use"
    id: str = Field(default_factory=lambda: f"toolu_{uuid4().hex}")
    name: str
    input: dict[str, Any] = Field(default_factory=dict[str, Any])
    provider_data: dict[str, Any] = Field(default_factory=dict[str, Any])


class ToolResultBlock(BaseModel):
    """发送回模型的工具结果内容

    Attributes:
        type: 块类型（固定为 "tool_result"）
        tool_use_id: 对应的工具调用 ID
        content: 工具返回的内容（纯文本或内容块列表）
        is_error: 是否为错误结果
    """

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str | list[ContentBlock] = ""
    is_error: bool = False

    @property
    def text_content(self) -> str:
        """返回纯文本内容。

        Returns:
            str: 文本字符串（content 为列表时提取 TextBlock 的文本）
        """
        if isinstance(self.content, str):
            return self.content
        return "".join(
            block.text for block in self.content if isinstance(block, TextBlock)
        )


class ThinkingBlock(BaseModel):
    """思考内容块（Anthropic extended thinking / DeepSeek thinking mode）

    Attributes:
        type: 块类型（固定为 "thinking"）
        thinking: 思考文本
        signature: 加密签名（Anthropic API 需要回传以验证思考内容未被篡改）；
            redacted 块时存放上游的加密数据（data 字段）
        redacted: 是否为 redacted_thinking 块（上游拒绝明文返回思考内容时，
            以加密数据形式下发）。回放时需原样序列化回 redacted_thinking，
            否则 Anthropic 会因思考链缺失拒绝请求
    """

    type: Literal["thinking"] = "thinking"
    thinking: str
    signature: str = ""
    redacted: bool = False


class MediaBlock(BaseModel):
    """图片文件内容块。

    Attributes:
        type: 块类型（固定为 "media"）
        file_path: 文件绝对路径
        media_type: MIME 类型，如 "image/png"
        data: base64 编码的文件数据
        metadata: 额外信息（文件大小等）
    """

    type: Literal["media"] = "media"
    file_path: str
    media_type: str
    data: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict[str, Any])


def _build_tool_result_content(
    output: str,
    metadata: dict[str, Any],
) -> str | list[ContentBlock]:
    """从工具输出和元数据构建 ToolResultBlock 的内容。

    如果元数据中包含媒体信息，返回 TextBlock + MediaBlock 的列表；
    否则返回原始文本。
    """
    if "media_category" not in metadata:
        return output

    media_block = MediaBlock(
        file_path=metadata.get("media_path", ""),
        media_type=metadata.get("media_type", "application/octet-stream"),
        data=metadata.get("media_data", ""),
        metadata={"size": metadata.get("media_size", 0)},
    )
    return [TextBlock(text=output), media_block]


def _messages_have_media(messages: list[ConversationMessage]) -> bool:
    """检查消息列表中是否包含 MediaBlock"""
    for msg in messages:
        for block in msg.content:
            if isinstance(block, MediaBlock):
                return True
            if (
                isinstance(block, ToolResultBlock)
                and isinstance(block.content, list)
                and any(isinstance(b, MediaBlock) for b in block.content)
            ):
                return True
    return False


def _media_placeholder(block: MediaBlock) -> str:
    """生成图片块被降级为文本时的占位描述。

    无能力的模型看到的是文件元信息占位而非图片内容本身，
    避免模型误以为看到了媒体内容。
    """
    # MediaBlock.metadata 的键为 "size"——由 _build_tool_result_content 从
    # ToolResult 侧的 media_size 转码而来（file_read_tool 写入）
    size_str = f" ({block.metadata['size']} bytes)" if "size" in block.metadata else ""
    return (
        f"[image file: {block.file_path}{size_str}, {block.media_type}] "
        "This model does not support image input"
    )


def _strip_media_from_messages(messages: list[ConversationMessage]) -> list[ConversationMessage]:
    """将消息中的 MediaBlock 替换为文本描述（用于不支持图片的模型优雅降级）。

    Args:
        messages: 原始消息列表（不修改）

    Returns:
        转换后的消息列表
    """
    result: list[ConversationMessage] = []
    for msg in messages:
        new_blocks: list[Any] = []
        for block in msg.content:
            if isinstance(block, MediaBlock):
                new_blocks.append(TextBlock(text=_media_placeholder(block)))
            elif isinstance(block, ToolResultBlock) and isinstance(block.content, list):
                stripped: list[Any] = []
                for b in block.content:
                    if isinstance(b, MediaBlock):
                        stripped.append(TextBlock(text=_media_placeholder(b)))
                    else:
                        stripped.append(b)
                new_blocks.append(ToolResultBlock(
                    tool_use_id=block.tool_use_id,
                    content=stripped,
                    is_error=block.is_error,
                ))
            else:
                new_blocks.append(block)
        result.append(ConversationMessage(role=msg.role, content=new_blocks))
    return result


def strip_media_if_unsupported(
    messages: list[ConversationMessage],
    capabilities: Any,
) -> list[ConversationMessage] | None:
    """模型无图片能力时，将消息中的图片块转为文本占位。

    capabilities 为 None（未声明）视为无图片能力（fail-closed）。

    Args:
        messages: 原始消息列表（不修改）
        capabilities: ModelCapabilities 或 None

    Returns:
        需要替换时返回转换后的消息列表；无需任何修改时返回 None
        （调用方直接复用原列表，避免无谓深拷贝）。
    """
    if bool(getattr(capabilities, "supports_images", False)):
        return None
    if not _messages_have_media(messages):
        return None
    return _strip_media_from_messages(messages)


# 内容块联合类型
ContentBlock = Annotated[
    TextBlock | ToolUseBlock | ToolResultBlock | ThinkingBlock | MediaBlock,
    Field(discriminator="type"),
]


class ConversationMessage(BaseModel):
    """单个助手或用户消息
    
    Attributes:
        role: 消息角色（"user" 或 "assistant"）
        content: 内容块列表
    """

    role: Literal["user", "assistant"]
    content: list[ContentBlock] = Field(default_factory=list[Any])

    @classmethod
    def from_user_text(cls, text: str) -> ConversationMessage:
        """从原始文本构造用户消息
        
        Args:
            text: 用户输入文本
        
        Returns:
            ConversationMessage: 用户消息
        """
        return cls(role="user", content=[TextBlock(text=text)])

    @property
    def text(self) -> str:
        """返回连接的文本块
        
        Returns:
            str: 所有文本块的连接字符串
        """
        return "".join(
            block.text for block in self.content if isinstance(block, TextBlock)
        )

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        """返回消息中包含的所有工具调用
        
        Returns:
            list[ToolUseBlock]: 工具调用列表
        """
        return [block for block in self.content if isinstance(block, ToolUseBlock)]

    @property
    def thinking_text(self) -> str:
        """返回连接后的思考文本块。"""
        return "\n\n".join(
            block.thinking.strip()
            for block in self.content
            if isinstance(block, ThinkingBlock) and block.thinking.strip()
        )

    def to_api_param(self, *, provider_type: str = "anthropic") -> dict[str, Any]:
        """将消息转换为提供商 SDK 消息参数。

        Args:
            provider_type: 提供商类型

        Returns:
            dict[str, Any]: API 参数格式的字典
        """
        content_blocks = list(self.content)
        
        # MiMo 等 API 要求 Text block 的 text 字段最小长度为 1
        # 过滤掉空文本块，避免 "content or tool_calls must be set" 错误
        if self.role == "assistant":
            content_blocks = [
                b for b in content_blocks
                if not (isinstance(b, TextBlock) and not b.text.strip())
            ]
            # 如果过滤后没有内容，添加占位符
            if not content_blocks:
                content_blocks = [TextBlock(text="...")]
        
        return {
            "role": self.role,
            "content": [serialize_content_block(block, provider_type=provider_type) for block in content_blocks],
        }


def serialize_content_block(block: ContentBlock, *, provider_type: str = "anthropic") -> dict[str, Any]:
    """将本地内容块转换为提供商线格式。

    Args:
        block: 内容块
        provider_type: 提供商类型（"anthropic"、"openai_compat"、"openai_codex"）

    Returns:
        dict[str, Any]: 线格式字典
    """
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}

    if isinstance(block, ToolUseBlock):
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }

    if isinstance(block, ThinkingBlock):
        if block.redacted:
            # redacted_thinking 回放：无明文，仅上游加密数据（存于 signature）
            return {"type": "redacted_thinking", "data": block.signature}
        result: dict[str, Any] = {"type": "thinking", "thinking": block.thinking}
        if block.signature:
            result["signature"] = block.signature
        return result

    if isinstance(block, MediaBlock):
        return _serialize_media_block(block, provider_type)

    # tool_result
    if isinstance(block.content, list):
        serialized_content: list[dict[str, Any]] | str = [
            serialize_content_block(inner, provider_type=provider_type)
            for inner in block.content
        ]
    else:
        serialized_content = block.content
    return {
        "type": "tool_result",
        "tool_use_id": block.tool_use_id,
        "content": serialized_content,
        "is_error": block.is_error,
    }


def _serialize_media_block(block: MediaBlock, provider_type: str) -> dict[str, Any]:
    """将图片 MediaBlock 按提供商格式序列化。"""
    if provider_type == "anthropic":
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": block.media_type,
                "data": block.data,
            },
        }
    if provider_type == "openai_codex":
        return {
            "type": "input_image",
            "image_url": f"data:{block.media_type};base64,{block.data}",
        }
    # openai_compat 图片
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{block.media_type};base64,{block.data}"},
    }


def assistant_message_from_api(raw_message: Any) -> ConversationMessage:
    """将 Anthropic SDK 消息对象转换为对话消息
    
    Args:
        raw_message: Anthropic SDK 原始消息
    
    Returns:
        ConversationMessage: 转换后的对话消息
    """
    content: list[ContentBlock] = []

    for raw_block in getattr(raw_message, "content", []):
        block_type = getattr(raw_block, "type", None)
        if block_type == "text":
            content.append(TextBlock(text=getattr(raw_block, "text", "")))
        elif block_type == "tool_use":
            content.append(
                ToolUseBlock(
                    id=getattr(raw_block, "id", f"toolu_{uuid4().hex}"),
                    name=getattr(raw_block, "name", ""),
                    input=dict(getattr(raw_block, "input", {}) or {}),
                )
            )
        elif block_type == "thinking":
            content.append(
                ThinkingBlock(
                    thinking=getattr(raw_block, "thinking", ""),
                    signature=getattr(raw_block, "signature", "") or "",
                )
            )
        elif block_type == "redacted_thinking":
            # redacted_thinking 无明文（data 为加密思考数据），必须原样回放，
            # 丢弃会导致 Anthropic 因思考链缺失拒绝后续请求
            content.append(
                ThinkingBlock(
                    thinking="",
                    signature=getattr(raw_block, "data", "") or "",
                    redacted=True,
                )
            )

    return ConversationMessage(role="assistant", content=content)
