import re
from datetime import datetime, timezone


DEFAULT_MAX_TURNS = 5
MAX_SUMMARY_LENGTH = 160
ELLIPSIS = "..."
VISIBLE_GOAL_STATUSES = {"active", "paused", "budget_limited", "complete"}


def _started_at_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _coerce_positive_int(value, default=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def summarize_text(text: str | None, limit: int = MAX_SUMMARY_LENGTH) -> str | None:
    if text is None:
        return None
    normalized = " ".join(str(text).split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - len(ELLIPSIS)] + ELLIPSIS


def goal_status(state: dict | None) -> str:
    if not isinstance(state, dict):
        return "none"
    raw_status = str(state.get("status") or "").strip().lower()
    if raw_status in VISIBLE_GOAL_STATUSES:
        return raw_status
    if state.get("active"):
        return "active"
    return "none"


def is_goal_active(state: dict | None) -> bool:
    return goal_status(state) == "active"


def _extract_completion_promise(answer: str | None) -> str | None:
    if not answer:
        return None
    match = re.search(r"<promise>(.*?)</promise>", answer, flags=re.DOTALL)
    if not match:
        return None
    return match.group(1).strip() or None


def new_goal_contract_state(
    objective: str,
    verification_commands: list[str] | None = None,
    completion_promise: str | None = None,
    max_turns: int | None = None,
    *,
    source: str = "goal",
) -> dict:
    commands = [str(item).strip() for item in (verification_commands or []) if str(item).strip()]
    return {
        "active": True,
        "status": "active",
        "objective": str(objective or "").strip(),
        "turn_count": 1,
        "max_turns": _coerce_positive_int(max_turns, DEFAULT_MAX_TURNS),
        "verification_commands": commands,
        "completion_promise": (completion_promise or "").strip() or None,
        "completion_promise_satisfied": False,
        "source": (source or "goal").strip() or "goal",
        "started_at": _started_at_iso(),
        "last_task_status": "initialized",
        "last_final_answer": "",
        "last_final_answer_excerpt": None,
        "last_error": "",
        "last_error_excerpt": None,
        "last_partial_answer": "",
        "last_partial_answer_excerpt": None,
    }


def build_goal_context_prompt(state: dict) -> str:
    status = goal_status(state)
    objective = str(state.get("objective") or "").strip() or "(missing objective)"
    turn_count = _coerce_positive_int(state.get("turn_count"), 1) or 1
    max_turns = _coerce_positive_int(state.get("max_turns"), DEFAULT_MAX_TURNS)
    commands = [str(item).strip() for item in (state.get("verification_commands") or []) if str(item).strip()]
    completion_promise = (state.get("completion_promise") or "").strip() or None
    last_task_status = str(state.get("last_task_status") or "").strip()
    if status == "complete" and last_task_status.lower() == "idle":
        last_task_status = "completed"

    lines = [
        "<goal_contract>",
        f"Objective: {objective}",
        f"Status: {status}",
        f"Turns: {turn_count}/{max_turns if max_turns is not None else 'unbounded'}",
        f"Source: {state.get('source') or 'goal'}",
        "Verification commands:",
    ]

    if commands:
        for index, command in enumerate(commands, start=1):
            lines.append(f"{index}. {command}")
    else:
        lines.append("0. None declared")

    if completion_promise:
        lines.append(f"Completion promise: {completion_promise}")
        lines.append(
            f"Only emit <promise>{completion_promise}</promise> when the objective is fully complete and every verification command passes."
        )
    else:
        lines.append("Completion promise: none")
        if status in {"active", "budget_limited"}:
            lines.append("Keep iterating until the operator pauses, clears, or the goal budget is exhausted.")

    if last_task_status:
        lines.append(f"Last task status: {last_task_status}")
    campaign_id = str(state.get("campaign_id") or "").strip()
    if campaign_id:
        lines.append(f"Self-improvement campaign: {campaign_id}")
        latest_run_id = str(state.get("latest_run_id") or "").strip()
        if latest_run_id:
            lines.append(f"Latest self-evolve run: {latest_run_id}")
        resume_action = str(state.get("campaign_resume_action") or "").strip()
        if resume_action:
            lines.append(f"Resume action after verification: {resume_action}")
    excerpt = state.get("last_final_answer_excerpt") or state.get("last_error_excerpt") or state.get(
        "last_partial_answer_excerpt"
    )
    if excerpt:
        lines.append(f"Last outcome excerpt: {excerpt}")

    lines.append("</goal_contract>")
    return "\n".join(lines)


def apply_turn_outcome(state: dict, event: dict) -> tuple[dict, bool]:
    updated = dict(state)
    current_turn = _coerce_positive_int(updated.get("turn_count"), 1) or 1
    max_turns = _coerce_positive_int(updated.get("max_turns"), DEFAULT_MAX_TURNS)
    final_answer = event.get("final_answer") or ""
    promise = (updated.get("completion_promise") or "").strip() or None
    satisfied_promise = promise is not None and _extract_completion_promise(final_answer) == promise

    updated.update(
        {
            "last_task_status": event.get("task_status") or "completed",
            "last_final_answer": final_answer,
            "last_final_answer_excerpt": summarize_text(final_answer),
            "last_error": "",
            "last_error_excerpt": None,
            "last_partial_answer": "",
            "last_partial_answer_excerpt": None,
            "completion_promise_satisfied": satisfied_promise,
        }
    )

    if satisfied_promise:
        updated.update({"active": False, "status": "complete"})
        return updated, False

    if max_turns is not None and current_turn >= max_turns:
        updated.update({"active": False, "status": "budget_limited"})
        return updated, False

    updated.update({"active": True, "status": "active", "turn_count": current_turn + 1})
    return updated, True


def _persistable_state(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key != "__plugin_state__"}


def handle_event(event, state):
    if not is_goal_active(state):
        return {"action": "allow"}

    handle = state.get("__plugin_state__")
    if handle is None:
        return {"action": "allow"}

    updated_state, should_continue = apply_turn_outcome(state, event)
    handle.write(_persistable_state(updated_state))
    if should_continue:
        return {
            "action": "block_and_continue",
            "follow_up_prompt": build_goal_context_prompt(updated_state),
        }
    return {"action": "allow"}
