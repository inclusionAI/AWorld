from aworld_cli.builtin_plugins.ralph_session_loop.common import (
    build_loop_prompt,
    extract_completion_promise,
)


def _coerce_iteration(value) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 1
    return parsed if parsed > 0 else 1


def _coerce_positive_limit(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def handle_event(event, state):
    handle = state.get("__plugin_state__")
    if handle is None:
        if state.get("active"):
            return {"action": "deny", "reason": "Ralph session state is unavailable."}
        return {"action": "allow"}

    current = handle.read()
    if not current.get("active"):
        return {"action": "allow"}

    completion_promise = current.get("completion_promise")
    answer_promise = extract_completion_promise(current.get("last_final_answer"))
    if completion_promise and answer_promise == completion_promise:
        handle.clear()
        return {
            "action": "allow",
            "reason": f"Ralph completion promise satisfied: {completion_promise}",
        }

    iteration = _coerce_iteration(current.get("iteration", 1))
    max_iterations = _coerce_positive_limit(current.get("max_iterations"))
    if max_iterations is not None and iteration >= max_iterations:
        handle.clear()
        return {
            "action": "allow",
            "reason": f"Ralph max iterations reached: {max_iterations}",
        }

    next_iteration = iteration + 1
    prompt = build_loop_prompt(
        current.get("prompt", ""),
        current.get("verify_commands") or [],
        completion_promise,
    )
    handle.update(
        {
            "iteration": next_iteration,
            "last_stop_reason": "continue",
        }
    )
    return {
        "action": "block_and_continue",
        "follow_up_prompt": prompt,
        "system_message": f"Ralph iteration {next_iteration}",
    }
