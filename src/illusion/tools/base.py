"""
工具抽象模块
============

本模块提供 IllusionAgent 工具系统的抽象基类和注册表。

主要组件：
    - BaseTool: 所有工具的抽象基类
    - ToolExecutionContext: 工具执行的共享上下文
    - ToolResult: 标准化的工具执行结果
    - ToolRegistry: 工具名称到实现的映射

使用示例：
    >>> from illusion.tools.base import BaseTool, ToolRegistry, ToolResult
    >>> registry = ToolRegistry()
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

# 进度回调类型：接收进度消息文本和进度类型，返回 Awaitable。
# 工具在执行过程中调用此回调上报中间状态，由 query.py 注入，
# 最终通过 ToolProgressEvent 流式传递给前端。
# progress_type 取值：thinking（LLM 思考）、text（LLM 回复）、tool（工具调用）、status（默认）。
# 仅 agent 工具前台模式使用此回调上报子代理的执行进度。
ProgressCallback = Callable[[str, str], Awaitable[None]]


@dataclass
class ToolExecutionContext:
    """工具调用的共享执行上下文

    Attributes:
        cwd: 当前工作目录
        metadata: 元数据字典
        on_progress: 进度回调（可选）。工具执行过程中调用以上报中间状态，
            由 query.py 注入，最终通过 ToolProgressEvent 流式传递给前端。
            回调签名为 (message, progress_type)，仅 agent 工具前台模式使用。
    """

    cwd: Path
    metadata: dict[str, Any] = field(default_factory=dict)
    on_progress: ProgressCallback | None = None


@dataclass(frozen=True)
class ToolResult:
    """标准化的工具执行结果
    
    Attributes:
        output: 输出内容
        is_error: 是否为错误
        metadata: 元数据字典
    """

    output: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


ToolInputT = TypeVar("ToolInputT", bound=BaseModel)


class BaseTool(ABC, Generic[ToolInputT]):
    """所有 IllusionAgent 工具的基类
    
    Attributes:
        name: 工具名称
        description: 工具描述
        input_model: 输入模型类型
    """

    name: str
    description: str
    input_model: type[ToolInputT]

    @abstractmethod
    async def execute(self, arguments: ToolInputT, context: ToolExecutionContext) -> ToolResult:
        """执行工具
        
        Args:
            arguments: 输入参数模型
            context: 执行上下文
        
        Returns:
            ToolResult: 工具执行结果
        """

    def is_read_only(self, arguments: ToolInputT) -> bool:
        """返回调用是否为只读
        
        Args:
            arguments: 输入参数模型
        
        Returns:
            bool: 是否只读
        """
        del arguments
        return False

    def to_api_schema(self) -> dict[str, Any]:
        """返回 Anthropic Messages API 期望的工具模式
        
        Returns:
            dict[str, Any]: API 工具模式
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }


class ToolRegistry:
    """工具名称到实现的映射
    
    Attributes:
        _tools: 工具字典
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool[Any]] = {}

    def register(self, tool: BaseTool[Any]) -> None:
        """注册工具实例

        Args:
            tool: 工具实例
        """
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """按名称移除工具（插件热切换用；名称不存在时静默）。"""
        self._tools.pop(name, None)

    def get(self, name: str) -> BaseTool[Any] | None:
        """按名称返回已注册的工具
        
        Args:
            name: 工具名称
        
        Returns:
            BaseTool | None: 工具或 None
        """
        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool[Any]]:
        """返回所有已注册的工具
        
        Returns:
            list[BaseTool]: 工具列表
        """
        return list(self._tools.values())

    def to_api_schema(self) -> list[dict[str, Any]]:
        """以 API 格式返回所有工具模式
        
        Returns:
            list[dict[str, Any]]: API 工具模式列表
        """
        return [tool.to_api_schema() for tool in self._tools.values()]
