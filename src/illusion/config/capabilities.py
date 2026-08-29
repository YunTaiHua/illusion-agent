"""
模型能力声明模块
================

定义模型的多模态能力（图片输入）声明与解析。

模型能力由用户在 settings.json 中为每个模型手动声明（``capabilities``
字段），未声明一律视为无能力（fail-closed），确保不支持视觉的模型
不会被静默注入图片媒体块。

能力对象：

    >>> from illusion.config.capabilities import parse_capabilities
    >>> caps = parse_capabilities(["image"])
    >>> caps.supports_images
    True
"""

from __future__ import annotations

from dataclasses import dataclass

# 合法能力标识（与 settings.json 中 capabilities 数组的值对应）
IMAGE_CAPABILITY = "image"

_VALID_CAPABILITIES: frozenset[str] = frozenset({IMAGE_CAPABILITY})


@dataclass(frozen=True)
class ModelCapabilities:
    """模型支持的多模态能力（默认关闭，fail-closed）。

    Attributes:
        supports_images: 是否支持图片输入（image_url / input_image / image
            等图片内容块）
    """

    supports_images: bool = False

    def __bool__(self) -> bool:
        """是否有任意媒体能力。"""
        return self.supports_images

    def describe(self) -> str:
        """人类可读的能力描述，如 "image" / "none"。"""
        return IMAGE_CAPABILITY if self.supports_images else "none"


def is_valid_capability(value: str) -> bool:
    """校验单个能力标识是否合法。"""
    return value in _VALID_CAPABILITIES


def parse_capabilities(values: list[str] | None) -> ModelCapabilities:
    """从配置能力数组解析能力对象。

    ``"image"`` → ``supports_images=True``；其他值忽略。None（未配置）
    返回全 False。

    Args:
        values: settings.json 中 capabilities 数组的值

    Returns:
        ModelCapabilities: 解析结果
    """
    if not values:
        return ModelCapabilities()
    return ModelCapabilities(supports_images=IMAGE_CAPABILITY in values)


def flatten_capabilities(caps: ModelCapabilities) -> list[str]:
    """将能力对象转回配置数组（写回 settings.json 用）。

    Args:
        caps: 能力对象

    Returns:
        list[str]: 能力标识列表（空 = 无能力）
    """
    return [IMAGE_CAPABILITY] if caps.supports_images else []
