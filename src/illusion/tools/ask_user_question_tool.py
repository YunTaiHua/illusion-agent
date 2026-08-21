"""
交互式用户问题工具
==================

本模块提供向交互式用户提问并获取答案的功能，用于收集用户偏好和需求。

主要组件：
    - AskUserQuestionTool: 向用户提问的工具

使用示例：
    >>> from illusion.tools import AskUserQuestionTool
    >>> tool = AskUserQuestionTool()
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field, model_validator

from illusion.tools.base import BaseTool, ToolExecutionContext, ToolResult

# 用户提示回调函数类型
# 回调签名: (question_text: str, questions: list[QuestionItem]) -> str | dict[str, str | list[str]]
# 返回 str 为兼容旧模式，返回 dict 时多选值为 list[str]
AskUserPrompt = Callable[..., Awaitable[Any]]


class QuestionOption(BaseModel):
    """问题的单个选项。

    属性：
        label: 选项的显示文本（1-5 个词）
        description: 选项的解释或选择后会发生什么
        preview: 可选的预览内容（markdown 格式）
    """

    label: str = Field(description="Display text for this option (1-5 words)")
    description: str = Field(description="Explanation of what this option means or what will happen if chosen")
    preview: str | None = Field(default=None, description="Optional preview content (markdown)")


class QuestionItem(BaseModel):
    """单个问题项。

    属性：
        question: 完整的问题文本
        header: 简短的标签，显示为 chip/tag（最多 12 个字符）
        options: 可用选项列表（2-4 个）
        multiSelect: 是否允许多选
    """

    question: str = Field(description="The complete question to ask the user. Should be clear, specific, and end with a question mark.")
    header: str = Field(description="Very short label displayed as a chip/tag (max 12 chars). Examples: 'Auth method', 'Library', 'Approach'.")
    options: list[QuestionOption] = Field(
        description="The available choices for this question. Must have 2-4 options.",
        min_length=2,
        max_length=4,
    )
    multiSelect: bool = Field(
        default=False,
        description="Set to true to allow the user to select multiple options instead of just one.",
    )

    @model_validator(mode="after")
    def _check_preview_multiselect(self) -> QuestionItem:
        """preview 仅支持单选，多选时不能有选项携带 preview。"""
        if self.multiSelect:
            for opt in self.options:
                if opt.preview is not None:
                    raise ValueError(
                        f"Option '{opt.label}' has a preview, but previews are only "
                        "supported for single-select questions (multiSelect must be False)."
                    )
        return self


class AskUserQuestionToolInput(BaseModel):
    """向用户提问的参数。

    属性：
        questions: 要问的问题列表（1-4 个）
        answers: 权限组件收集的用户答案
        annotations: 来自用户的可选的每问题注解
        metadata: 用于跟踪和分析的可选元数据
    """

    questions: list[QuestionItem] = Field(
        description="Questions to ask the user (1-4 questions)",
        min_length=1,
        max_length=4,
    )
    answers: dict[str, str] | None = Field(
        default=None,
        description="Reserved for the permission component to inject user answers. Do not provide.",
    )
    annotations: dict[str, Any] | None = Field(
        default=None,
        description="Reserved for the permission component. Do not provide.",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Reserved for the permission component. Do not provide.",
    )


class AskUserQuestionTool(BaseTool[AskUserQuestionToolInput]):
    """向交互式用户提问并返回答案。

    用于收集用户偏好、澄清模糊指令、获取实现选择决策等。
    """

    name = "ask_user_question"
    description = """Use this tool when you need to ask the user questions during execution. This allows you to:
1. Gather user preferences or requirements
2. Clarify ambiguous instructions
3. Get decisions on implementation choices as you work
4. Offer choices to the user about what direction to take.

Usage notes:
- Users will always be able to select "Other" to provide custom text input
- Users can skip answering a question (skipped questions are omitted from the response; if all are skipped the tool returns "(no response)")
- Use multiSelect: true to allow multiple answers to be selected for a question
- If you recommend a specific option, make that the first option in the list and add "(Recommended)" at the end of the label

Plan mode note: In plan mode, use this tool to clarify requirements or choose between approaches BEFORE finalizing your plan. Do NOT use this tool to ask "Is my plan ready?" or "Should I proceed?" - use ExitPlanMode for plan approval. IMPORTANT: Do not reference "the plan" in your questions (e.g., "Do you have feedback about the plan?", "Does the plan look good?") because the user cannot see the plan in the UI until you call ExitPlanMode. If you need plan approval, use ExitPlanMode instead.

Preview feature:
Use the optional `preview` field on options when presenting concrete artifacts that users need to visually compare:
- ASCII mockups of UI layouts or components
- Code snippets showing different implementations
- Diagram variations
- Configuration examples

Preview content is rendered as markdown in a monospace box. Multi-line text with newlines is supported. When any option has a preview, the UI switches to a side-by-side layout with a vertical option list on the left and preview on the right. Do not use previews for simple preference questions where labels and descriptions suffice. Note: previews are only supported for single-select questions (not multiSelect)."""
    input_model = AskUserQuestionToolInput

    def is_read_only(self, arguments: AskUserQuestionToolInput) -> bool:
        del arguments
        return True

    async def execute(
        self,
        arguments: AskUserQuestionToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        # 获取用户提示回调函数
        prompt: Any = context.metadata.get("ask_user_prompt")
        if not callable(prompt):
            return ToolResult(
                output="ask_user_question is unavailable in this session",
                is_error=True,
            )

        # 构建问题显示文本
        parts: list[str] = []
        for i, q in enumerate(arguments.questions, 1):
            header = f"[{q.header}]" if q.header else ""
            parts.append(f"{header} {q.question}")
            for j, opt in enumerate(q.options, 1):
                preview_note = " (has preview)" if opt.preview else ""
                parts.append(f"  {j}. {opt.label} - {opt.description}{preview_note}")
            if q.multiSelect:
                parts.append("  (multi-select)")

        question_text = "\n".join(parts)

        # 将结构化问题数据传给回调，使其能正确渲染单选/多选UI
        answers = await prompt(question_text, arguments.questions)  # pyright: ignore[reportGeneralTypeIssues]

        if not answers:
            return ToolResult(output="(no response)")

        # 格式化答案：dict 模式支持 list 值（多选），逐项展开
        if isinstance(answers, dict):
            lines: list[str] = []
            for k, v in answers.items():
                if isinstance(v, list):
                    for item in v:
                        lines.append(f"{k}: {item}")
                else:
                    lines.append(f"{k}: {v}")
            return ToolResult(output="\n".join(lines))

        return ToolResult(output=str(answers))
