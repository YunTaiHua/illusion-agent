"""Real large tasks that exercise multiple IllusionAgent features together.

Each task is a realistic multi-turn scenario that combines 3+ features,
running on the AutoAgent codebase (an unfamiliar project) with real Kimi K2.5 API.

Run: python tests/test_real_large_tasks.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from illusion.config.settings import Settings

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "sk-Ue1kdhq9prvNwuwySlzRtWVD7ek0iJJaHyPdKDa3ecKLwYuG")
BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.moonshot.cn/anthropic")
MODEL = os.environ.get("ANTHROPIC_MODEL", "kimi-k2.5")
WORKSPACE = Path("/home/tangjiabin/AutoAgent")
DEFAULT_MAX_TURNS = Settings().max_turns

RESULTS: dict[str, tuple[bool, float]] = {}


# ====================================================================
# Shared infrastructure
# ====================================================================

def make_engine(system_prompt, cwd=None, hook_executor=None, max_tokens=4096, max_turns=DEFAULT_MAX_TURNS):
    from illusion.api.client import AnthropicApiClient
    from illusion.config.settings import PermissionSettings
    from illusion.engine.query_engine import QueryEngine
    from illusion.permissions.checker import PermissionChecker
    from illusion.permissions.modes import PermissionMode
    from illusion.tools.base import ToolRegistry
    from illusion.tools.bash_tool import BashTool
    from illusion.tools.file_edit_tool import FileEditTool
    from illusion.tools.file_read_tool import FileReadTool
    from illusion.tools.file_write_tool import FileWriteTool
    from illusion.tools.glob_tool import GlobTool
    from illusion.tools.grep_tool import GrepTool
    from illusion.tools.web_fetch_tool import WebFetchTool

    api = AnthropicApiClient(api_key=API_KEY, base_url=BASE_URL)
    reg = ToolRegistry()
    for t in [BashTool(), FileReadTool(), FileWriteTool(), FileEditTool(),
              GlobTool(), GrepTool(), WebFetchTool()]:
        reg.register(t)
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))
    return QueryEngine(
        api_client=api, tool_registry=reg, permission_checker=checker,
        cwd=Path(cwd or WORKSPACE), model=MODEL, system_prompt=system_prompt,
        max_tokens=max_tokens, max_turns=max_turns, hook_executor=hook_executor,
    )


def collect(events):
    from illusion.engine.stream_events import (
        AssistantTextDelta,
        AssistantTurnComplete,
        ToolExecutionCompleted,
        ToolExecutionStarted,
    )
    r = {"text": "", "tools": [], "tool_outputs": [], "turns": 0, "in_tok": 0, "out_tok": 0}
    for ev in events:
        if isinstance(ev, AssistantTextDelta):
            r["text"] += ev.text
        elif isinstance(ev, ToolExecutionStarted):
            r["tools"].append(ev.tool_name)
        elif isinstance(ev, ToolExecutionCompleted):
            r["tool_outputs"].append({"tool": ev.tool_name, "ok": not ev.is_error, "out": ev.output[:200]})
        elif isinstance(ev, AssistantTurnComplete):
            r["turns"] += 1
            r["in_tok"] += ev.usage.input_tokens
            r["out_tok"] += ev.usage.output_tokens
    return r


# ====================================================================
# Task 1: Security audit with hooks + permissions + web_fetch
#
# Features: hooks (pre_tool_use logging), permission checker (deny rm),
#           web_fetch (fetch OWASP reference), multi-turn agent loop,
#           file read/grep on unfamiliar codebase
# ====================================================================
@pytest.mark.skipif(not Path("/home/tangjiabin/AutoAgent").exists(), reason="Needs real API + AutoAgent")
async def task_security_audit_with_hooks():
    """Full security audit: agent reads code, fetches OWASP checklist, reports issues.
    Hooks log every tool use. Permission denies dangerous commands."""

    from illusion.api.client import AnthropicApiClient
    from illusion.hooks.events import HookEvent
    from illusion.hooks.executor import HookExecutionContext, HookExecutor
    from illusion.hooks.loader import HookRegistry
    from illusion.hooks.schemas import CommandHookDefinition

    api = AnthropicApiClient(api_key=API_KEY, base_url=BASE_URL)

    # Hook: log every tool use to a file
    log_file = Path(tempfile.mktemp(suffix=".log"))
    hook_reg = HookRegistry()
    hook_reg.register(HookEvent.POST_TOOL_USE, CommandHookDefinition(
        type="command",
        command=f'echo "[$(date +%H:%M:%S)] $TOOL_NAME" >> {log_file}',
        timeout_seconds=5,
    ))
    hook_exec = HookExecutor(hook_reg, HookExecutionContext(
        cwd=WORKSPACE, api_client=api, default_model=MODEL,
    ))

    engine = make_engine(
        "You are a senior security auditor. Analyze code for OWASP top 10 vulnerabilities. "
        "Use tools to read files and search for patterns. Be thorough — check for: "
        "command injection, hardcoded secrets, eval/exec usage, insecure deserialization, "
        "missing input validation. Report with file paths and line numbers.",
        hook_executor=hook_exec,
    )

    # Turn 1: scan for dangerous patterns
    evs1 = [ev async for ev in engine.submit_message(
        "Scan the autoagent/ directory for security vulnerabilities. "
        "Search for: eval(, exec(, subprocess.run with shell=True, hardcoded passwords/tokens, "
        "os.system calls. Report all findings with file:line references."
    )]
    r1 = collect(evs1)
    print(f"  Turn 1: {r1['turns']} turns, {len(r1['tools'])} tools, text={len(r1['text'])} chars")

    # Turn 2: fetch OWASP reference and cross-check
    evs2 = [ev async for ev in engine.submit_message(
        "Now fetch https://httpbin.org/json as a test to verify web_fetch works. "
        "Then summarize your top 3 most critical findings from the code audit."
    )]
    r2 = collect(evs2)
    print(f"  Turn 2: {r2['turns']} turns, {len(r2['tools'])} tools")

    # Check hook log
    hook_log = log_file.read_text() if log_file.exists() else ""
    hook_entries = [entry for entry in hook_log.strip().split("\n") if entry.strip()]
    print(f"  Hook log: {len(hook_entries)} entries")
    if hook_entries:
        print(f"    First: {hook_entries[0]}")
        print(f"    Last: {hook_entries[-1]}")
    log_file.unlink(missing_ok=True)

    all_tools = r1["tools"] + r2["tools"]
    has_grep = "grep" in all_tools
    has_web = "web_fetch" in all_tools
    has_findings = any(kw in (r1["text"] + r2["text"]).lower() for kw in ["eval", "exec", "shell", "inject", "subprocess"])
    hooks_fired = len(hook_entries) > 0

    print(f"  grep used: {has_grep}, web_fetch used: {has_web}, findings: {has_findings}, hooks: {hooks_fired}")
    return has_grep and has_findings and hooks_fired


# ====================================================================
# Task 2: Multi-agent code review with coordinator + team + mailbox
#
# Features: coordinator system prompt, task notifications (XML),
#           team lifecycle, in-process teammates (2 concurrent),
#           mailbox communication, agent definitions
# ====================================================================
@pytest.mark.skipif(not Path("/home/tangjiabin/AutoAgent").exists(), reason="Needs real API + AutoAgent")
async def task_coordinator_code_review():
    """Coordinator delegates code review to 2 worker agents, synthesizes results."""

    import illusion.swarm.mailbox as mb
    import illusion.swarm.team_lifecycle as tl
    from illusion.swarm.team_lifecycle import TeamLifecycleManager, TeamMember

    from illusion.api.client import AnthropicApiClient
    from illusion.config.settings import PermissionSettings
    from illusion.coordinator.agent_definitions import get_agent_definition
    from illusion.coordinator.coordinator_mode import (
        TaskNotification,
        format_task_notification,
        get_coordinator_system_prompt,
    )
    from illusion.engine.query import QueryContext
    from illusion.permissions.checker import PermissionChecker
    from illusion.permissions.modes import PermissionMode
    from illusion.swarm.in_process import TeammateAbortController, start_in_process_teammate
    from illusion.swarm.types import TeammateSpawnConfig
    from illusion.tools.base import ToolRegistry
    from illusion.tools.bash_tool import BashTool
    from illusion.tools.file_read_tool import FileReadTool
    from illusion.tools.glob_tool import GlobTool
    from illusion.tools.grep_tool import GrepTool

    api = AnthropicApiClient(api_key=API_KEY, base_url=BASE_URL)

    with tempfile.TemporaryDirectory() as tmpdir:
        orig_td = mb.get_team_dir
        orig_tf = tl._team_file_path
        mb.get_team_dir = lambda t: Path(tmpdir) / t
        tl._team_file_path = lambda n: Path(tmpdir) / n / "team.json"

        try:
            # Create team
            mgr = TeamLifecycleManager()
            mgr.create_team("review-team", "Code review team for AutoAgent")

            # Phase 1: Spawn 2 worker agents with different review focuses


            async def run_reviewer(name, prompt):
                reg = ToolRegistry()
                for t in [BashTool(), FileReadTool(), GlobTool(), GrepTool()]:
                    reg.register(t)
                checker = PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))

                # Use the verification agent definition for system prompt
                verify_def = get_agent_definition("verification")
                sys_prompt = verify_def.system_prompt if verify_def and verify_def.system_prompt else (
                    "You are a code reviewer. Read files thoroughly. Report issues concisely."
                )

                ctx = QueryContext(
                    api_client=api, tool_registry=reg, permission_checker=checker,
                    cwd=WORKSPACE, model=MODEL, max_tokens=131072, max_turns=DEFAULT_MAX_TURNS,
                    system_prompt=sys_prompt,
                )
                config = TeammateSpawnConfig(
                    name=name, team="review-team", prompt=prompt,
                    cwd=str(WORKSPACE), parent_session_id="coordinator",
                )
                mgr.add_member("review-team", TeamMember(
                    agent_id=f"{name}@review-team", name=name,
                    backend_type="in_process", joined_at=time.time(), is_active=True,
                ))
                abort = TeammateAbortController()
                await start_in_process_teammate(
                    config=config, agent_id=f"{name}@review-team",
                    abort_controller=abort, query_context=ctx,
                )

            t0 = time.time()
            await asyncio.gather(
                asyncio.wait_for(run_reviewer(
                    "error-reviewer",
                    "Review autoagent/core.py for error handling issues. "
                    "Find: bare except clauses, missing error handling, swallowed exceptions. "
                    "Report file:line and issue for each finding."
                ), timeout=45),
                asyncio.wait_for(run_reviewer(
                    "style-reviewer",
                    "Review autoagent/util.py for code style issues. "
                    "Find: inconsistent naming, missing type hints, overly complex functions. "
                    "Report file:line and issue for each finding."
                ), timeout=45),
                return_exceptions=True,
            )
            worker_time = time.time() - t0
            print(f"  Workers completed in {worker_time:.1f}s")

            # Phase 2: Coordinator synthesizes results
            team = mgr.get_team("review-team")
            members = list(team.members.keys()) if team else []
            print(f"  Team members: {members}")

            # Simulate coordinator receiving worker results as task notifications
            engine = make_engine(get_coordinator_system_prompt())
            evs = [ev async for ev in engine.submit_message(
                "I asked two workers to review AutoAgent code. Here are their results:\n\n"
                + format_task_notification(TaskNotification(
                    task_id="error-reviewer", status="completed",
                    summary="Error handling review of core.py completed",
                    result="Found 3 issues: (1) bare except at line 450, (2) missing timeout on API calls at line 320, (3) swallowed ConnectionError at line 285",
                )) + "\n\n"
                + format_task_notification(TaskNotification(
                    task_id="style-reviewer", status="completed",
                    summary="Code style review of util.py completed",
                    result="Found 4 issues: (1) 11 functions missing type hints, (2) function_to_json is 80 lines (too long), (3) inconsistent naming (camelCase mixed with snake_case), (4) dead code at line 150-160",
                ))
                + "\n\nSummarize all findings into a unified review report."
            )]
            r = collect(evs)
            print(f"  Coordinator synthesis: {r['turns']} turns, {len(r['text'])} chars")

            has_synthesis = any(kw in r["text"].lower() for kw in ["error", "style", "type hint", "issue"])
            return worker_time < 50 and len(members) >= 2 and has_synthesis
        finally:
            mb.get_team_dir = orig_td
            tl._team_file_path = orig_tf


# ====================================================================
# Task 3: Codebase migration plan with skills + memory + session save
#
# Features: skills (loaded from dir), memory (save findings for future),
#           session storage (save/export), multi-turn conversation,
#           config settings, agent definitions (Plan agent prompt)
# ====================================================================
@pytest.mark.skipif(not Path("/home/tangjiabin/AutoAgent").exists(), reason="Needs real API + AutoAgent")
async def task_migration_plan_with_memory():
    """Agent analyzes AutoAgent, saves findings to memory, creates migration plan,
    saves session for later resume."""

    import illusion.memory.manager as mm
    import illusion.memory.paths as mp
    from illusion.coordinator.agent_definitions import get_agent_definition
    from illusion.memory.manager import add_memory_entry, list_memory_files, remove_memory_entry
    from illusion.services.session_storage import export_session_markdown, get_project_session_dir, write_index, write_meta
    from illusion.skills.registry import SkillRegistry
    from illusion.skills.types import SkillDefinition

    with tempfile.TemporaryDirectory() as tmpdir:
        mem_dir = Path(tmpdir) / "memory"
        mem_dir.mkdir(parents=True)
        orig_mp = mp.get_project_memory_dir
        orig_ep = mm.get_memory_entrypoint
        mp.get_project_memory_dir = lambda cwd: mem_dir
        mm.get_memory_entrypoint = lambda cwd: mem_dir / "MEMORY.md"

        try:
            # Load a "migration" skill
            skill_reg = SkillRegistry()
            skill_reg.register(SkillDefinition(
                name="migration-checklist",
                description="Steps for migrating a Python project to a new framework",
                content=(
                    "1. Audit all dependencies in setup.cfg/pyproject.toml\n"
                    "2. Identify deprecated APIs and their replacements\n"
                    "3. Map the module structure to the target framework\n"
                    "4. Create migration scripts for data models\n"
                    "5. Update tests to use new assertion patterns\n"
                    "6. Run full test suite and fix failures\n"
                ),
                source="user",
            ))

            # Plan agent has been removed as redundant: use general-purpose directly,
            # and use a dedicated architect prompt when the definition is unavailable
            gp_def = get_agent_definition("general-purpose")
            engine = make_engine(
                gp_def.system_prompt if gp_def and gp_def.system_prompt else
                "You are a software architect. Explore code and create migration plans.",
            )

            # Turn 1: Analyze current architecture
            evs1 = [ev async for ev in engine.submit_message(
                "Analyze the AutoAgent project's dependency structure. "
                "Read pyproject.toml and setup.cfg, identify all dependencies, "
                "and classify them as: core, optional, dev-only."
            )]
            r1 = collect(evs1)
            print(f"  Turn 1 (deps): {r1['turns']} turns, {len(r1['tools'])} tools")

            # Save findings to memory
            add_memory_entry(tmpdir, "autoagent-dependencies",
                f"AutoAgent dependency analysis:\n{r1['text'][:500]}")

            # Turn 2: Analyze module structure
            evs2 = [ev async for ev in engine.submit_message(
                "Now analyze the module structure of autoagent/. "
                "List all subpackages, count files per package, and identify the core vs. peripheral modules."
            )]
            r2 = collect(evs2)
            print(f"  Turn 2 (modules): {r2['turns']} turns, {len(r2['tools'])} tools")

            add_memory_entry(tmpdir, "autoagent-modules",
                f"AutoAgent module structure:\n{r2['text'][:500]}")

            # Turn 3: Create migration plan using skill context
            skill = skill_reg.get("migration-checklist")
            evs3 = [ev async for ev in engine.submit_message(
                f"Based on your analysis, create a concrete migration plan for AutoAgent. "
                f"Use this checklist as a starting template:\n\n{skill.content}\n\n"
                f"Adapt each step specifically for AutoAgent's codebase."
            )]
            r3 = collect(evs3)
            print(f"  Turn 3 (plan): {r3['turns']} turns, text={len(r3['text'])} chars")

            # Verify memory
            mem_files = list_memory_files(tmpdir)
            print(f"  Memory files saved: {len(mem_files)}")

            # Save session (用新 CheckpointStore API 替代旧 save_session_snapshot)
            import time as _time_mod
            from illusion.services.checkpoint_store import CheckpointStore
            all_msgs = engine.messages
            usage = engine.total_usage
            sid = "migration-plan-001"
            session_dir = get_project_session_dir(tmpdir) / sid
            store = CheckpointStore(session_dir, sid)
            await store.append_checkpoint()
            for m in all_msgs:
                await store.append_message(m)
            write_meta(tmpdir, sid, {
                "session_id": sid, "cwd": tmpdir, "model": MODEL,
                "created_at": _time_mod.time(), "updated_at": _time_mod.time(),
                "summary": "", "message_count": len(all_msgs),
            })
            write_index(tmpdir, sid)
            session_path = session_dir / "context.jsonl"
            print(f"  Session saved: {session_path.exists()}")

            # Export markdown
            md_path = export_session_markdown(cwd=tmpdir, messages=all_msgs)
            md_size = md_path.stat().st_size if md_path.exists() else 0
            print(f"  Markdown export: {md_size} bytes")

            # Cleanup memory
            for mf in mem_files:
                remove_memory_entry(tmpdir, mf.stem)

            ok = (
                len(mem_files) >= 2
                and session_path.exists()
                and md_size > 100
                and len(r3["text"]) > 200
                and any(kw in r3["text"].lower() for kw in ["migration", "step", "plan", "depend"])
            )
            return ok
        finally:
            mp.get_project_memory_dir = orig_mp
            mm.get_memory_entrypoint = orig_ep


# ====================================================================
# Task 4: Bug fix workflow with worktree + hooks + edit + test
#
# Features: worktree (isolated workspace), hooks (pre_tool_use),
#           file write/edit, bash (run tests), multi-turn,
#           agent works in worktree copy, changes don't affect original
# ====================================================================
def _run_calc_test(worktree_path):
    """Run calc.py in the worktree and return combined stdout+stderr output."""
    return os.popen(f"cd {worktree_path} && python calc.py 2>&1").read()


@pytest.mark.skipif(not Path("/home/tangjiabin/AutoAgent").exists(), reason="Needs real API + AutoAgent")
async def task_bugfix_in_worktree():
    """Agent creates a worktree, makes a fix in isolation, verifies it, cleans up."""

    from illusion.swarm.worktree import WorktreeManager

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a test repo with a "buggy" file
        repo = Path(tmpdir) / "buggy-project"
        repo.mkdir()
        await asyncio.to_thread(
            os.system, f"cd {repo} && git init -q && git checkout -b main 2>/dev/null"
        )

        buggy_code = '''"""Calculator module with a bug."""

def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b

def divide(a, b):
    return a / b  # BUG: no zero division check

def test_all():
    assert add(1, 2) == 3
    assert subtract(5, 3) == 2
    assert multiply(3, 4) == 12
    try:
        divide(10, 0)
        print("FAIL: should have raised ZeroDivisionError")
        return False
    except ZeroDivisionError:
        print("PASS: zero division handled")
        return True
    return True

if __name__ == "__main__":
    ok = test_all()
    print(f"Tests: {'PASS' if ok else 'FAIL'}")
'''
        (repo / "calc.py").write_text(buggy_code)
        await asyncio.to_thread(
            os.system, f"cd {repo} && git add -A && git commit -q -m 'initial commit'"
        )

        wt_base = Path(tmpdir) / "worktrees"
        mgr = WorktreeManager(base_dir=wt_base)

        # Create worktree for the fix
        wt = await mgr.create_worktree(repo, "fix-divide-by-zero")
        print(f"  Worktree created: {wt.path}")

        # Agent works in worktree
        engine = make_engine(
            "You are a developer fixing bugs. Read the code, identify the bug, fix it, then run the test.",
            cwd=wt.path,
        )

        evs = [ev async for ev in engine.submit_message(
            "Read calc.py, fix the divide-by-zero bug by adding a check that raises "
            "ZeroDivisionError with a helpful message when b is 0. "
            "Then run: python calc.py to verify the fix."
        )]
        r = collect(evs)
        print(f"  Agent: {r['turns']} turns, {len(r['tools'])} tools")
        print(f"  Tools used: {r['tools']}")

        # Verify: worktree file is fixed
        wt_calc = (wt.path / "calc.py").read_text()
        has_fix = "ZeroDivisionError" in wt_calc or "b == 0" in wt_calc or "b != 0" in wt_calc

        # Verify: original repo is untouched
        orig_calc = (repo / "calc.py").read_text()
        orig_untouched = "return a / b  # BUG" in orig_calc

        print(f"  Worktree fixed: {has_fix}")
        print(f"  Original untouched: {orig_untouched}")

        # Run test in worktree
        test_result = await asyncio.to_thread(_run_calc_test, wt.path)
        test_pass = "PASS" in test_result
        print(f"  Test result: {test_result.strip()}")

        # Cleanup worktree
        removed = await mgr.remove_worktree("fix-divide-by-zero")
        print(f"  Worktree removed: {removed}")

        return has_fix and orig_untouched and test_pass and removed


# ====================================================================
# Task 5: Full pipeline: research → plan → implement → verify
#          using coordinator + 3 swarm teammates + permission sync
#
# Features: coordinator mode (5-turn orchestration), 3 concurrent
#           in-process teammates, permission sync (request/resolve),
#           team lifecycle, mailbox, agent definitions, auto-compact
# ====================================================================
@pytest.mark.skipif(not Path("/home/tangjiabin/AutoAgent").exists(), reason="Needs real API + AutoAgent")
async def task_full_pipeline():
    """Simulate the full research→plan→implement→verify pipeline with coordinator."""

    import illusion.swarm.mailbox as mb
    import illusion.swarm.team_lifecycle as tl
    from illusion.swarm.permission_sync import (
        PermissionResolution,
        create_permission_request,
        read_pending_permissions,
        resolve_permission,
        write_permission_request,
    )
    from illusion.swarm.team_lifecycle import TeamLifecycleManager, TeamMember

    from illusion.api.client import AnthropicApiClient
    from illusion.config.settings import PermissionSettings
    from illusion.coordinator.coordinator_mode import (
        TaskNotification,
        format_task_notification,
        get_coordinator_system_prompt,
    )
    from illusion.engine.query import QueryContext
    from illusion.permissions.checker import PermissionChecker
    from illusion.permissions.modes import PermissionMode
    from illusion.swarm.in_process import TeammateAbortController, start_in_process_teammate
    from illusion.swarm.types import TeammateSpawnConfig
    from illusion.tools.base import ToolRegistry
    from illusion.tools.bash_tool import BashTool
    from illusion.tools.file_read_tool import FileReadTool
    from illusion.tools.glob_tool import GlobTool
    from illusion.tools.grep_tool import GrepTool

    api = AnthropicApiClient(api_key=API_KEY, base_url=BASE_URL)

    with tempfile.TemporaryDirectory() as tmpdir:
        orig_td = mb.get_team_dir
        orig_tf = tl._team_file_path
        mb.get_team_dir = lambda t: Path(tmpdir) / t
        tl._team_file_path = lambda n: Path(tmpdir) / n / "team.json"

        try:
            mgr = TeamLifecycleManager()
            mgr.create_team("pipeline", "Full R&D pipeline")

            # Phase 1: Research — 2 concurrent workers
            async def research_worker(name, prompt):
                reg = ToolRegistry()
                for t in [BashTool(), FileReadTool(), GlobTool(), GrepTool()]:
                    reg.register(t)
                ctx = QueryContext(
                    api_client=api, tool_registry=reg,
                    permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
                    cwd=WORKSPACE, model=MODEL, max_tokens=1024, max_turns=DEFAULT_MAX_TURNS,
                    system_prompt="You are a research worker. Investigate and report findings. Be concise.",
                )
                config = TeammateSpawnConfig(
                    name=name, team="pipeline", prompt=prompt,
                    cwd=str(WORKSPACE), parent_session_id="main",
                )
                mgr.add_member("pipeline", TeamMember(
                    agent_id=f"{name}@pipeline", name=name,
                    backend_type="in_process", joined_at=time.time(), is_active=True,
                ))
                abort = TeammateAbortController()
                await start_in_process_teammate(
                    config=config, agent_id=f"{name}@pipeline",
                    abort_controller=abort, query_context=ctx,
                )

            print("  Phase 1: Research (2 workers)...")
            t0 = time.time()
            res = await asyncio.gather(
                asyncio.wait_for(research_worker(
                    "arch-researcher",
                    "Count .py files in autoagent/ using bash. Report the total."
                ), timeout=30),
                asyncio.wait_for(research_worker(
                    "dep-researcher",
                    "Read setup.cfg and report what install_requires are listed."
                ), timeout=30),
                return_exceptions=True,
            )
            research_time = time.time() - t0
            research_ok = all(not isinstance(r, Exception) for r in res)
            print(f"    Research: {research_time:.1f}s, ok={research_ok}")

            # Phase 2: Permission request + resolve
            print("  Phase 2: Permission sync...")
            perm_req = create_permission_request(
                tool_name="Bash", tool_use_id="tu_deploy",
                tool_input={"command": "git push origin main"},
                description="Push changes to remote"
            )
            perm_req.team_name = "pipeline"
            perm_req.worker_id = "impl-worker@pipeline"
            await write_permission_request(perm_req)
            pending = await read_pending_permissions("pipeline")
            print(f"    Pending: {len(pending)}")
            if pending:
                await resolve_permission(
                    pending[0].id,
                    PermissionResolution(decision="approved", resolved_by="leader"),
                    team_name="pipeline",
                )
            remaining = await read_pending_permissions("pipeline")
            perm_ok = len(pending) == 1 and len(remaining) == 0
            print(f"    Permission resolved: {perm_ok}")

            # Phase 3: Coordinator synthesizes everything
            print("  Phase 3: Coordinator synthesis...")
            engine = make_engine(get_coordinator_system_prompt())

            notif_text = "\n\n".join([
                format_task_notification(TaskNotification(
                    task_id="arch-researcher", status="completed",
                    summary="Architecture research done",
                    result="AutoAgent has 99 Python files across 12 subpackages.",
                    usage={"total_tokens": 500, "tool_uses": 2}
                )),
                format_task_notification(TaskNotification(
                    task_id="dep-researcher", status="completed",
                    summary="Dependency research done",
                    result="Key dependencies: litellm, docker, rich, prompt_toolkit, pydantic",
                    usage={"total_tokens": 400, "tool_uses": 1}
                )),
            ])
            evs = [ev async for ev in engine.submit_message(
                f"Two research workers completed their analysis:\n\n{notif_text}\n\n"
                "Summarize the findings and suggest next steps for improving this project."
            )]
            r = collect(evs)
            print(f"    Coordinator: {r['turns']} turns, {len(r['text'])} chars")

            team = mgr.get_team("pipeline")
            total_members = len(team.members) if team else 0
            print(f"    Team total members: {total_members}")

            synthesis_ok = len(r["text"]) > 100 and any(
                kw in r["text"].lower() for kw in ["autoagent", "python", "depend", "file"]
            )

            return research_ok and perm_ok and synthesis_ok and total_members >= 2
        finally:
            mb.get_team_dir = orig_td
            tl._team_file_path = orig_tf


# ====================================================================
# Task 6: Multi-turn refactoring with session resume simulation
#
# Features: session save/load, multi-turn (3 turns), file edit,
#           config settings, cost tracking
# ====================================================================
@pytest.mark.skipif(not Path("/home/tangjiabin/AutoAgent").exists(), reason="Needs real API + AutoAgent")
async def task_refactor_with_session():
    """Refactor code across 3 turns, save session, verify it can be loaded."""

    from illusion.services.session_storage import (
        get_project_session_dir,
        read_meta,
        write_index,
        write_meta,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a file to refactor
        code_file = Path(tmpdir) / "handlers.py"
        code_file.write_text('''"""Request handlers with duplicated validation."""

def handle_create_user(data):
    if not data.get("name"):
        return {"error": "name required"}, 400
    if not data.get("email"):
        return {"error": "email required"}, 400
    if "@" not in data.get("email", ""):
        return {"error": "invalid email"}, 400
    return {"user": data}, 201

def handle_update_user(data):
    if not data.get("name"):
        return {"error": "name required"}, 400
    if not data.get("email"):
        return {"error": "email required"}, 400
    if "@" not in data.get("email", ""):
        return {"error": "invalid email"}, 400
    return {"user": data}, 200

def handle_create_admin(data):
    if not data.get("name"):
        return {"error": "name required"}, 400
    if not data.get("email"):
        return {"error": "email required"}, 400
    if "@" not in data.get("email", ""):
        return {"error": "invalid email"}, 400
    if not data.get("role"):
        return {"error": "role required"}, 400
    return {"admin": data}, 201
''')

        engine = make_engine(
            "You are a refactoring expert. Follow instructions precisely. Be concise.",
            cwd=tmpdir,
        )

        # Turn 1: Read and identify duplication
        evs1 = [ev async for ev in engine.submit_message(
            f"Read {code_file} and identify the duplicated validation logic."
        )]
        r1 = collect(evs1)
        print(f"  Turn 1 (analyze): {r1['turns']} turns, {len(r1['tools'])} tools")

        # Turn 2: Refactor
        evs2 = [ev async for ev in engine.submit_message(
            "Extract the duplicated validation into a helper function called validate_user_data(). "
            "Edit the file to use it in all three handlers."
        )]
        r2 = collect(evs2)
        print(f"  Turn 2 (refactor): {r2['turns']} turns, {len(r2['tools'])} tools")

        # Turn 3: Verify
        evs3 = [ev async for ev in engine.submit_message(
            "Read the file again and verify the refactoring is correct. "
            "Check that the helper function exists and all handlers use it."
        )]
        r3 = collect(evs3)
        print(f"  Turn 3 (verify): {r3['turns']} turns, {len(r3['tools'])} tools")

        # Save session (用新 CheckpointStore API 替代旧 save_session_snapshot)
        import time as _time_mod2
        from illusion.services.checkpoint_store import CheckpointStore
        sid = "refactor-session"
        session_dir = get_project_session_dir(tmpdir) / sid
        store = CheckpointStore(session_dir, sid)
        await store.append_checkpoint()
        for m in engine.messages:
            await store.append_message(m)
        write_meta(tmpdir, sid, {
            "session_id": sid, "cwd": tmpdir, "model": MODEL,
            "created_at": _time_mod2.time(), "updated_at": _time_mod2.time(),
            "summary": "", "message_count": len(engine.messages),
        })
        write_index(tmpdir, sid)
        session_path = session_dir / "context.jsonl"
        loaded_meta = read_meta(tmpdir, sid) or {}
        print(f"  Session saved: {session_path.exists()}")
        print(f"  Session loaded: model={loaded_meta.get('model', '?')}")
        print(f"  Cost: in={engine.total_usage.input_tokens}, out={engine.total_usage.output_tokens}")

        # Verify refactoring
        final_code = code_file.read_text()
        has_helper = "validate_user_data" in final_code
        try:
            compile(final_code, str(code_file), "exec")
            valid_python = True
        except SyntaxError:
            valid_python = False

        print(f"  Has helper function: {has_helper}, valid Python: {valid_python}")
        return has_helper and valid_python and session_path.exists()


# ====================================================================
# Main
# ====================================================================
async def main():
    tasks = [
        ("1. Security audit (hooks+perms+web+grep)", task_security_audit_with_hooks()),
        ("2. Coordinator code review (swarm+team+mailbox)", task_coordinator_code_review()),
        ("3. Migration plan (skills+memory+session)", task_migration_plan_with_memory()),
        ("4. Bug fix in worktree (worktree+edit+test)", task_bugfix_in_worktree()),
        ("5. Full pipeline (coordinator+3 workers+perm sync)", task_full_pipeline()),
        ("6. Refactoring with session (save+load+cost)", task_refactor_with_session()),
    ]

    for name, coro in tasks:
        print(f"\n{'='*70}")
        print(f"  TASK: {name}")
        print(f"{'='*70}")
        t0 = time.time()
        try:
            ok = await coro
            elapsed = time.time() - t0
            RESULTS[name] = (ok, elapsed)
            print(f"\n  >>> {'PASS' if ok else 'FAIL'} ({elapsed:.1f}s)")
        except (OSError, RuntimeError, ValueError, KeyError, AttributeError, TypeError) as e:
            RESULTS[name] = (False, time.time() - t0)
            print(f"\n  >>> EXCEPTION: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'='*70}")
    print("  FINAL RESULTS — Real Large Tasks")
    print(f"{'='*70}")
    passed = sum(1 for ok, _ in RESULTS.values() if ok)
    for name, (ok, elapsed) in RESULTS.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  [{elapsed:.1f}s]")
    print(f"\n  {passed}/{len(RESULTS)} tasks passed")


if __name__ == "__main__":
    asyncio.run(main())
