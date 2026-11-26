"""
记忆文件扫描模块
================

本模块提供记忆文件的扫描和解析功能。

主要功能：
    - 扫描项目中的记忆Markdown文件
    - 解析YAML frontmatter提取元数据
    - 按修改时间排序返回记忆列表

函数说明：
    - scan_memory_files: 扫描记忆文件
    - _parse_memory_file: 解析单个记忆文件

使用示例：
    >>> from illusion.memory import scan_memory_files
    >>> headers = scan_memory_files(".", max_files=50)
"""

from __future__ import annotations

from pathlib import Path

from illusion.memory.paths import get_memory_dir_for_cwd_no_create
from illusion.memory.types import MemoryHeader


def scan_memory_files(cwd: str | Path, *, max_files: int = 50) -> list[MemoryHeader]:
    """扫描并返回记忆文件头，按最新修改时间排序

    扫描根目录下的 *.md（兼容旧布局）以及
    user/feedback/project/reference 类型子目录下的 *.md。

    Args:
        cwd: 当前工作目录
        max_files: 最大返回文件数量

    Returns:
        list[MemoryHeader]: 按修改时间倒序排列的记忆头列表
    """
    from illusion.memory.paths import MEMORY_TYPE_DIRS

    memory_dir = get_memory_dir_for_cwd_no_create(cwd)  # 获取记忆目录
    headers: list[MemoryHeader] = []  # 初始化头列表
    for path in memory_dir.glob("*.md"):  # 遍历根目录所有md文件
        if path.name == "MEMORY.md":  # 跳过索引文件
            continue
        try:
            text = path.read_text(encoding="utf-8")  # 读取文件内容
        except OSError:  # 读取失败则跳过
            continue
        header = _parse_memory_file(path, text)  # 解析文件
        headers.append(header)  # 添加到列表
    # 类型子目录（user/feedback/project/reference）
    for sub in MEMORY_TYPE_DIRS:
        for path in (memory_dir / sub).glob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            header = _parse_memory_file(path, text)
            headers.append(header)
    headers.sort(key=lambda item: item.modified_at, reverse=True)  # 按时间倒序排序
    return headers[:max_files]  # 返回Top N结果


def _parse_memory_file(path: Path, content: str) -> MemoryHeader:
    """解析记忆文件，提取YAML frontmatter中的元数据
    
    支持从frontmatter提取: name, description, type
    如果没有frontmatter，使用第一行非空非标题行作为description
    
    Args:
        path: 文件路径
        content: 文件内容
    
    Returns:
        MemoryHeader: 解析后的记忆头
    """
    lines = content.splitlines()  # 分割行
    title = path.stem  # 默认使用文件名作为标题
    description = ""  # 描述
    memory_type = ""  # 记忆类型
    body_start = 0  # 内容起始行索引

    # 解析YAML frontmatter (--- ... ---)
    if lines and lines[0].strip() == "---":  # 检查是否有frontmatter
        for i, line in enumerate(lines[1:], 1):  # 遍历内容行
            if line.strip() == "---":  # 找到结束标记
                for fm_line in lines[1:i]:  # 解析frontmatter
                    key, _, value = fm_line.partition(":")  # 分割键值对
                    key = key.strip()  # 清理键
                    value = value.strip().strip("'\"")  # 清理值
                    if not value:  # 跳过空值
                        continue
                    if key == "name":  # 名称字段
                        title = value
                    elif key == "description":  # 描述字段
                        description = value
                    elif key == "type":  # 类型字段
                        memory_type = value
                body_start = i + 1  # 内容从结束标记后开始
                break

    # 后备方案：第一行非空非frontmatter非标题行作为描述
    desc_line_idx: int | None = None  # 描述行索引
    if not description:  # 如果没有描述
        for idx, line in enumerate(lines[body_start:body_start + 10], body_start):
            stripped = line.strip()  # 去除空白
            if stripped and stripped != "---" and not stripped.startswith("#"):  # 非空非标记非标题
                description = stripped[:200]  # 截取前200字符
                desc_line_idx = idx  # 记录行索引
                break

    # 从frontmatter之后的内容构建body preview
    # 排除已用作description的行以保持搜索评分一致性
    body_lines = [
        line.strip()  # 去除空白
        for idx, line in enumerate(lines[body_start:], body_start)
        if line.strip()  # 非空行
        and not line.strip().startswith("#")  # 非标题行
        and idx != desc_line_idx  # 排除描述行
    ]
    body_preview = " ".join(body_lines)[:300]  # 合并并截断

    return MemoryHeader(
        path=path,  # 文件路径
        title=title,  # 标题
        description=description,  # 描述
        modified_at=path.stat().st_mtime,  # 修改时间
        memory_type=memory_type,  # 记忆类型
        body_preview=body_preview,  # 内容预览
    )