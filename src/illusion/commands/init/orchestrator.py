"""
/init 管道编排器
===============

连接提取、分析、生成三个阶段，执行完整的初始化流程。

流程：
1. 提取阶段：扫描文件、AST 分析、README 解析
2. 分析阶段：规范检测、架构分析、依赖分类、关键模块识别
3. 生成阶段：CLAUDE.md、ILLUSION.md、rules/、MEMORY.md
4. 写入阶段：原子写入所有文件
5. 报告阶段：生成初始化报告
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from illusion.commands.init.analysis.architecture import analyze_architecture
from illusion.commands.init.analysis.conventions import detect_conventions
from illusion.commands.init.analysis.dependencies import analyze_dependencies
from illusion.commands.init.analysis.key_modules import identify_key_modules
from illusion.commands.init.extraction.lsp_symbols import extract_symbols_sync
from illusion.commands.init.extraction.readme import extract_readme
from illusion.commands.init.extraction.scanner import scan_project
from illusion.commands.init.generation.claudemd import generate_claude_md, update_claude_md
from illusion.commands.init.generation.illusionmd import generate_illusion_md
from illusion.commands.init.generation.memory_template import generate_memory_template
from illusion.commands.init.generation.rules import generate_rules
from illusion.commands.init.types import AnalysisResult, ProjectData
from illusion.commands.types import CommandContext, CommandResult
from illusion.config.paths import get_project_config_dir


async def run_init(context: CommandContext) -> CommandResult:
    """执行 /init 命令的完整流程

    Args:
        context: 命令上下文

    Returns:
        命令结果
    """
    root = Path(context.cwd)
    project_dir = get_project_config_dir(context.cwd)
    created: list[str] = []
    updated: list[str] = []

    # Phase 1: Extraction
    data = _run_extraction(root)

    # Phase 2: Analysis
    analysis = _run_analysis(data)

    # Phase 3: Generation
    files_to_write = _run_generation(analysis, data, root, project_dir)

    # Phase 4: Atomic writes
    for path, content, action in files_to_write:
        if action == "skip":
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if action == "update":
            _atomic_write(path, content)
            updated.append(str(path.relative_to(root)))
        elif action == "create":
            _atomic_write(path, content)
            created.append(str(path.relative_to(root)))

    # Phase 5: Report
    if not created and not updated:
        return CommandResult(message="Project already initialized for IllusionAgent.")

    return _build_report(created, updated, analysis, data)


def _run_extraction(root: Path) -> ProjectData:
    """执行提取阶段"""
    data = scan_project(root)

    # README 详细解析
    readme_summary, readme_sections = extract_readme(root)
    if readme_summary:
        data.readme_summary = readme_summary
    data.readme_sections = readme_sections

    # 通过 LSP 提取所有语言的符号
    data.modules = extract_symbols_sync(root, data.languages)

    return data


def _run_analysis(data: ProjectData) -> AnalysisResult:
    """执行分析阶段"""
    conventions = detect_conventions(data)
    directory_tree, architecture_notes = analyze_architecture(data)
    dependency_summary = analyze_dependencies(data)
    key_modules = identify_key_modules(data)

    return AnalysisResult(
        project_name=data.root.name,
        project_description=data.readme_summary or "",
        directory_tree=directory_tree,
        architecture_notes=architecture_notes,
        conventions=conventions,
        key_modules=key_modules,
        dependency_summary=dependency_summary,
    )


def _run_generation(
    analysis: AnalysisResult,
    data: ProjectData,
    root: Path,
    project_dir: Path,
) -> list[tuple[Path, str, str]]:
    """执行生成阶段，返回 [(path, content, action)] 列表

    action: "create", "update", "skip"
    """
    result: list[tuple[Path, str, str]] = []

    # CLAUDE.md
    claudemd_path = root / "CLAUDE.md"
    if claudemd_path.exists():
        try:
            existing = claudemd_path.read_text(encoding="utf-8", errors="replace")
            content = update_claude_md(existing, analysis, data)
            result.append((claudemd_path, content, "update"))
        except OSError:
            result.append((claudemd_path, generate_claude_md(analysis, data), "create"))
    else:
        result.append((claudemd_path, generate_claude_md(analysis, data), "create"))

    # ILLUSION.md
    illusionmd_path = root / "ILLUSION.md"
    if illusionmd_path.exists():
        result.append((illusionmd_path, "", "skip"))
    else:
        result.append((illusionmd_path, generate_illusion_md(analysis, data), "create"))

    # Rules
    rules = generate_rules(analysis, data)
    rules_dir = project_dir / "rules"
    for filename, content in rules.items():
        rule_path = rules_dir / filename
        if rule_path.exists():
            result.append((rule_path, "", "skip"))
        else:
            result.append((rule_path, content, "create"))

    # Memory template
    from illusion.memory.paths import get_memory_entrypoint

    memory_path = get_memory_entrypoint(root)
    if memory_path.exists():
        result.append((memory_path, "", "skip"))
    else:
        result.append((memory_path, generate_memory_template(analysis, data), "create"))

    # .gitkeep files
    for subdir in ("plugins", "skills"):
        gitkeep = project_dir / subdir / ".gitkeep"
        if gitkeep.exists():
            result.append((gitkeep, "", "skip"))
        else:
            result.append((gitkeep, "", "create"))

    return result


def _atomic_write(path: Path, content: str) -> None:
    """原子写入：先写临时文件，再重命名

    Args:
        path: 目标文件路径
        content: 文件内容
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # 使用同目录下的临时文件，避免跨设备 rename 问题
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".tmp_",
        suffix=path.suffix,
    )
    try:
        import os

        os.write(tmp_fd, content.encode("utf-8"))
        os.close(tmp_fd)
        Path(tmp_path).replace(path)
    except Exception:
        # 清理临时文件
        try:
            import os

            os.close(tmp_fd)
        except OSError:
            pass
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _build_report(
    created: list[str],
    updated: list[str],
    analysis: AnalysisResult,
    data: ProjectData,
) -> CommandResult:
    """生成初始化报告"""
    lines = [
        "✨ **IllusionAgent project initialization complete.**\n",
    ]

    if created:
        lines.append("## Files created")
        for item in created:
            lines.append(f"- `{item}`")
        lines.append("")

    if updated:
        lines.append("## Files updated")
        for item in updated:
            lines.append(f"- `{item}`")
        lines.append("")

    lines.append("## Project analysis")

    if data.languages:
        lang_strs = [f"{lang} ({count})" for lang, count in list(data.languages.items())[:5]]
        lines.append(f"- **Languages**: {', '.join(lang_strs)}")
    if data.frameworks:
        lines.append(f"- **Frameworks**: {', '.join(data.frameworks)}")
    if data.package_manager:
        lines.append(f"- **Package Manager**: {data.package_manager}")
    if data.build_commands:
        lines.append(f"- **Build**: `{data.build_commands[0]}`")
    if data.test_commands:
        lines.append(f"- **Test**: `{data.test_commands[0]}`")
    if data.lint_commands:
        lines.append(f"- **Lint**: `{data.lint_commands[0]}`")
    if data.format_commands:
        lines.append(f"- **Format**: `{data.format_commands[0]}`")
    if data.ci_config:
        lines.append(f"- **CI/CD**: {data.ci_config}")

    lines.append("")
    lines.append("## Next steps")
    lines.append("- Review `CLAUDE.md` for project configuration")
    lines.append("- Review `ILLUSION.md` for project-specific guidance")
    lines.append("- Run `/memory` to manage project memories")
    lines.append("- Run `/skills` to view available skills")
    lines.append("- Adjust `CLAUDE.md` as needed")

    return CommandResult(message="\n".join(lines))
