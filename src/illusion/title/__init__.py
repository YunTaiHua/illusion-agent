"""
会话标题模块
============

本模块提供会话自动标题生成功能，风格与记忆提取/整合（memory.extract /
memory.auto_dream）对齐：回合结束后在后台运行一个轻量子代理，分析对话
内容生成简洁标题并写入会话 meta.json 的 title 字段。后台执行不阻塞主对话。

示例：
    >>> from illusion.title.auto_title import maybe_schedule_title
    >>> # 在回合结束后调用，自动判断并异步调度标题生成
    >>> maybe_schedule_title(engine)
"""

from illusion.title.auto_title import maybe_schedule_title

__all__ = ["maybe_schedule_title"]