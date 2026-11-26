"""
Goal 提示词模块
===============

goal 子系统的提示词文本集合：

    - 系统提示词 guidance（goal 工具使用规范，blockedAfter 插值）
    - goal round 续跑提示 / 终态 wrap-up 提示
    - 验证缺陷回灌块 / 证据包格式 / 对抗性验证用户提示
"""

from __future__ import annotations

import json

# ---------------------------------------------------------------------------
# 系统提示词 guidance（blockedAfter 插值）
# ---------------------------------------------------------------------------


def goal_guidance(blocked_after: int) -> str:
    """返回 goal guidance 系统提示词，并插入 blockedAfter 阈值。"""
    return (
        "Use goal tools for one long-running completion objective in the current session. "
        "create_goal may infer goal intent from a direct human request in any language; do not "
        "create a goal for routine single-turn work. Call get_goal before update_goal and copy its "
        "exact goal_id and revision. After session resume or fork, an active goal is disarmed: when "
        "a human asks to continue or resume in any wording or language, use update_goal action "
        "resume to rearm it. Mark complete only when the objective is actually achieved. Mark "
        f"blocked only after the same blocking condition persists for at least {blocked_after} "
        "consecutive goal rounds, and report that concrete condition in blocked_reason; difficulty, "
        "uncertainty, or useful remaining work is not blocked."
    )


# ---------------------------------------------------------------------------
# goal round 续跑提示
# ---------------------------------------------------------------------------


def render_goal_round_prompt(
    objective: str,
    round_no: int,
    max_goal_rounds: int,
    goal_id: str | None = None,
    revision: int | None = None,
) -> str:
    """渲染 `<goal_round>` 用户消息（objective 以 JSON 引号包裹）。

    goal_id/revision 为 IllusionAgent 的增量行：把当前目标的精确 CAS ref
    直接交给模型，免去 get_goal 往返（并消除首次 update_goal 凭空猜测
    id/revision 而被 CAS 拒绝的高频失败模式）。
    """
    ref_line = ""
    if goal_id and revision is not None:
        ref_line = (
            f"Current goal: id={goal_id} revision={revision} "
            "(pass these exact values to update_goal)\n\n"
        )
    return (
        "<goal_round>\n"
        f"Objective: {json.dumps(objective, ensure_ascii=False)}\n"
        f"Round: {round_no}/{max_goal_rounds}\n"
        f"{ref_line}"
        "Continue working toward the objective in this same session. Treat the current workspace, "
        "tool results, and durable session state as authoritative; inspect them instead of assuming "
        "earlier narration is still current. Make concrete progress and verify the result. Before "
        "claiming completion, gather evidence that the whole objective is achieved, read the current "
        "goal, and mark it complete. If work remains, leave the goal active for the next round. Follow "
        "the configured goal-tool policy before reporting a blocker.\n"
        "</goal_round>"
    )


# ---------------------------------------------------------------------------
# 终态 wrap-up 提示
# ---------------------------------------------------------------------------

_GROUNDING = (
    "Report only what earlier rounds and tool results in this session actually establish; "
    "when a detail is not in the session, say so instead of inventing it. "
)


def render_wrapup_context(objective: str, blocked_reason: str | None = None) -> str:
    """渲染 `<goal_complete>` / `<goal_blocked>` 收尾消息。"""
    heading = f"Objective: {json.dumps(objective, ensure_ascii=False)}\n"
    if blocked_reason is None:
        return (
            "<goal_complete>\n"
            + heading
            + "The goal is marked complete and this autonomous run is ending. Write the closing "
            "message to the user now: state the outcome, summarize what was done and how it was "
            "verified, and point to the concrete results (files, commits, or other artifacts). "
            + _GROUNDING
            + "Note anything the user should review or do next. Address the user directly. Do not "
            "call any more tools in this run; further work waits for the user's next instruction.\n"
            "</goal_complete>"
        )
    return (
        "<goal_blocked>\n"
        + heading
        + f"Blocked: {json.dumps(blocked_reason, ensure_ascii=False)}\n"
        + "The goal is marked blocked and this autonomous run is ending. Write the closing "
        "message to the user now: state what has been completed so far, describe the concrete "
        "blocking condition and what you tried, and say exactly what you need from the user to "
        "continue. "
        + _GROUNDING
        + "Address the user directly. Do not call any more tools in this run; further work "
        "waits for the user's next instruction.\n"
        "</goal_blocked>"
    )


# ---------------------------------------------------------------------------
# 验证缺陷回灌块
# ---------------------------------------------------------------------------


def render_verifier_gaps_block(gaps: str) -> str:
    """渲染验证拒绝后的缺陷回灌块。"""
    if not gaps:
        return ""
    return (
        "Verification REJECTED your last `update_goal(action: \"complete\")` claim. "
        "Fix every gap the verifier flagged below — these take priority — "
        f"before claiming completion again:\n{gaps}\n\n"
    )


# ---------------------------------------------------------------------------
# 证据包构建
# ---------------------------------------------------------------------------

CHANGES_UNAVAILABLE = "(unavailable)"
PLAN_UNAVAILABLE = "(unavailable)"
PLAN_CHANGES_NONE = "(none)"
CHANGED_FILES_MAX = 300
FINAL_RESPONSE_MAX_CHARS = 8192
VERIFIER_REPORT_MAX_CHARS = 4000


def build_evidence_packet(
    objective: str,
    changes_file: str,
    changed_files: list[str],
    plan_file: str | None,
    final_response: str,
    prior_gaps: str,
) -> str:
    """构建验证者证据包。"""
    out: list[str] = []
    out.append("OBJECTIVE:\n")
    out.append(objective)
    out.append("\n\nCHANGES_FILE: ")
    out.append(changes_file)
    out.append("\n\nCHANGED_FILES:\n")
    if not changed_files:
        out.append("(none captured)\n")
    else:
        for path in changed_files[:CHANGED_FILES_MAX]:
            out.append("- ")
            out.append(_sanitize_final_response(_sanitize_path_control_chars(path)))
            out.append("\n")
        if len(changed_files) > CHANGED_FILES_MAX:
            out.append(f"(… and {len(changed_files) - CHANGED_FILES_MAX} more)\n")
    out.append("\nPLAN_FILE: ")
    out.append(plan_file if plan_file is not None else PLAN_UNAVAILABLE)
    out.append("\n\nPLAN_CHANGES: ")
    out.append(PLAN_CHANGES_NONE)
    out.append("\n\nFINAL_RESPONSE:\n")
    out.append(_sanitize_final_response(final_response[:FINAL_RESPONSE_MAX_CHARS]))
    out.append("\n\nPRIOR_GAPS:\n")
    out.append(prior_gaps if prior_gaps else "(none)\n")
    return "".join(out)


def _sanitize_final_response(text: str) -> str:
    """以零宽空格打断框架闭合标签，
    防止模型产出文本伪造 </system-reminder> 等帧边界。"""
    return (
        text.replace("</system-reminder>", "</system-reminder\u200b>")
        .replace("<goal-state>", "<goal-state\u200b>")
        .replace("</goal-state>", "</goal-state\u200b>")
    )


def _sanitize_path_control_chars(path: str) -> str:
    """行断裂控制字符替换为 U+FFFD。"""
    return "".join(
        "\ufffd" if (ord(c) < 32 or c in "\u2028\u2029") else c for c in path
    )


# goal harness 注入消息的标签前缀（重放/渲染/摘要过滤依据，仿 task-notification）
_GOAL_MESSAGE_TAGS = ("<goal_round>", "<goal_complete>", "<goal_blocked>")


def is_goal_system_message(text: str) -> bool:
    """判断文本是否为 goal harness 注入的消息（<goal_round>/<goal_complete>/<goal_blocked>）。

    这些消息作为 user 消息注入 transcript 供 LLM 消费，但不应被当作真实
    用户消息参与渲染、重放、轮次计算、会话摘要与回退选择。各过滤点与
    is_task_notification 并列使用（illusion.tasks.types.is_task_notification
    的同款模式）。

    Args:
        text: 消息文本

    Returns:
        bool: 是否为 goal harness 注入消息
    """
    stripped = (text or "").lstrip()
    return stripped.startswith(_GOAL_MESSAGE_TAGS)


# ---------------------------------------------------------------------------
# 验证者用户提示（对抗性验证指令 + illusion 验证代理自身的 VERDICT 契约）
# ---------------------------------------------------------------------------

VERIFIER_USER_PROMPT_TEMPLATE = """You are an **adversarial verifier** for an active goal. You are
NOT the agent that produced the work below. Your job is to **refute** that the
objective has been met. **Default to `VERDICT: FAIL` if uncertain** — a
false-positive (passing broken work) ends the goal wrongly and is far worse
than one more iteration.

## Evidence

- OBJECTIVE: the user's goal, verbatim.
- CHANGES_FILE: a unified-diff changelog — a scope pointer and the honesty-check
  anchor, NOT your sole evidence; may be truncated or `{changes_unavailable}`.
- CHANGED_FILES: the COMPLETE list of files this goal created/modified. Read
  their CURRENT contents.
- FINAL_RESPONSE: the agent's own summary. Prose is NOT evidence — use it only
  to find claims to attack.
- PRIOR_GAPS: the gaps the previous verification round told the implementer to
  fix (a "(none)" marker on the first round).

{evidence_packet}
## Anti-ratchet — converge, don't re-litigate

On a re-verification round (PRIOR_GAPS non-empty), your PRIMARY job is to check
that each prior gap is genuinely fixed. The bar does NOT rise between rounds: a
NEW objection that earlier rounds did not raise is grounds to refute ONLY when
it is a demonstrable defect in shipped behavior or an unmet gating criterion of
the objective — never a stylistic or test-construction preference the prior
round implicitly accepted. Raising a fresh nitpick each round while the
criteria hold is the failure mode that makes goals unfinishable; when every
prior gap is fixed and every gating criterion holds, return `VERDICT: PASS`.

## Audit, don't author

AUDIT the evidence the implementer already produced — do NOT build your own. The
implementer was required to run real checks that exercise the shipped code and
capture run output; that captured evidence is your PRIMARY proof. If the
implementer's tests/evidence are MISSING or INSUFFICIENT, do NOT fill the gap
yourself — REFUTE with a specific, actionable request that the IMPLEMENTER
produce it (the next round's gap).

## Decision rules

1. OBJECTIVE and any artifacts it explicitly names are the immutable contract.
   Enumerate every explicit OBJECTIVE requirement and inspect every named file
   or document; if a required named artifact cannot be inspected, refute.
   Corroborate every criterion against the **current workspace** (CHANGED_FILES)
   and the implementer's checks + captured evidence. Cite concrete evidence per
   assertion (`path:line`, a captured transcript, an observed artifact, a diff
   hunk). A criterion you cannot corroborate is grounds to refute. A criterion
   whose evidence holds is PASSED — do NOT refute it for missing edge cases,
   error handling of malformed input, extra input formats, or any extension the
   objective did not require. Inventing requirements beyond the contract is the
   most common FALSE refute: when every criterion is met, return `VERDICT: PASS`
   even if you can imagine more the author *could* have built.
2. Honesty check: a FINAL_RESPONSE claim of work on a file absent from
   CHANGED_FILES is fabricated — refute.
3. TODO/FIXME stubs or skipped tests on what this goal added — refute.
4. If CHANGES_FILE is `{changes_unavailable}`, investigate yourself (`git
   log/status/diff`, read files) and apply rules 1-3. No evidence at all ⇒ refute.
5. Genuinely ambiguous evidence ⇒ refute.

## Output contract

Per your verification contract, every check must carry the exact command run
and the output observed, and you MUST end with exactly one line:

VERDICT: PASS
or
VERDICT: FAIL
or
VERDICT: PARTIAL

PARTIAL is for environmental limitations only — not for "I'm unsure whether
this is a bug." A FAIL/PARTIAL verdict's findings become the gaps the
implementer must fix next round: cite each as `path:line — one concrete line`.
"""


def render_verifier_user_prompt(evidence_packet: str) -> str:
    """组装验证者用户提示（模板 + 证据包）。"""
    return VERIFIER_USER_PROMPT_TEMPLATE.format(
        changes_unavailable=CHANGES_UNAVAILABLE,
        evidence_packet=evidence_packet.rstrip("\n") + "\n",
    )


__all__ = [
    "CHANGED_FILES_MAX",
    "CHANGES_UNAVAILABLE",
    "FINAL_RESPONSE_MAX_CHARS",
    "PLAN_CHANGES_NONE",
    "PLAN_UNAVAILABLE",
    "VERIFIER_REPORT_MAX_CHARS",
    "build_evidence_packet",
    "goal_guidance",
    "is_goal_system_message",
    "render_goal_round_prompt",
    "render_verifier_gaps_block",
    "render_verifier_user_prompt",
    "render_wrapup_context",
]
