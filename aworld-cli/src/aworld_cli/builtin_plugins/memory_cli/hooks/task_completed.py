from __future__ import annotations

from aworld_cli.builtin_plugins.memory_cli.common import append_workspace_session_log
from aworld_cli.memory.metrics import append_promotion_metric
from aworld_cli.memory.promotion import (
    candidate_payload,
    evaluate_turn_end_candidate,
    mark_auto_promoted,
    should_auto_promote,
)
from aworld_cli.memory.provider import CliDurableMemoryProvider


def handle_event(event, state):
    workspace_path = event.get("workspace_path") or state.get("workspace_path")
    session_id = event.get("session_id")
    if not workspace_path or not session_id:
        return {"action": "allow"}

    final_answer = event.get("final_answer") or ""
    candidates = []
    if final_answer:
        decision = evaluate_turn_end_candidate(final_answer)
        auto_promoted = False
        if should_auto_promote(decision):
            CliDurableMemoryProvider().append_durable_memory_record(
                workspace_path=workspace_path,
                text=decision.content,
                memory_type=decision.memory_type,
                source="auto_promotion",
            )
            decision = mark_auto_promoted(decision)
            auto_promoted = True

        candidates.append(candidate_payload(decision, auto_promoted=auto_promoted))
        append_promotion_metric(
            workspace_path=workspace_path,
            session_id=session_id,
            task_id=event.get("task_id"),
            decision=decision,
        )

    append_workspace_session_log(
        workspace_path=workspace_path,
        session_id=session_id,
        payload={
            "event": "task_completed",
            "session_id": session_id,
            "task_id": event.get("task_id"),
            "task_status": event.get("task_status") or "idle",
            "workspace_path": workspace_path,
            "final_answer": final_answer,
            "candidates": candidates,
        },
    )
    return {"action": "allow"}
