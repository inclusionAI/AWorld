from __future__ import annotations

from dataclasses import replace

from aworld_cli.builtin_plugins.memory_cli.common import append_workspace_session_log
from aworld_cli.memory.governance import append_governed_decision, evaluate_governed_candidate
from aworld_cli.memory.metrics import append_promotion_metric
from aworld_cli.memory.promotion import (
    candidate_payload,
    evaluate_turn_end_candidate,
)
from aworld_cli.memory.provider import CliDurableMemoryProvider


def handle_event(event, state):
    workspace_path = event.get("workspace_path") or state.get("workspace_path")
    session_id = event.get("session_id")
    if not workspace_path or not session_id:
        return {"action": "allow"}

    final_answer = event.get("final_answer") or ""
    usage = event.get("usage")
    llm_calls = event.get("llm_calls")
    if not isinstance(llm_calls, list):
        llm_calls = []
    provider = CliDurableMemoryProvider()
    candidates = []
    if final_answer:
        decision = evaluate_turn_end_candidate(final_answer)
        candidate_id = f"{session_id}:{event.get('task_id') or 'task'}:{len(candidates)}"
        governed = evaluate_governed_candidate(
            workspace_path=workspace_path,
            candidate={
                "candidate_id": candidate_id,
                "content": decision.content,
                "memory_type": decision.memory_type,
                "confidence": decision.confidence,
                "source_ref": {
                    "session_id": session_id,
                    "task_id": str(event.get("task_id") or ""),
                    "candidate_id": candidate_id,
                },
            },
        )
        append_governed_decision(workspace_path, governed.to_payload())

        auto_promoted = governed.decision == "durable_memory"
        promotion_decision = replace(
            decision,
            promotion="durable_memory" if auto_promoted else "session_log_only",
        )
        if auto_promoted:
            provider.append_durable_memory_record(
                workspace_path=workspace_path,
                text=governed.content,
                memory_type=governed.memory_type,
                source="governed_auto_promotion",
            )

        candidates.append(
            candidate_payload(promotion_decision, auto_promoted=auto_promoted)
            | {
                "candidate_id": candidate_id,
                "governed_decision_id": governed.decision_id,
                "governed_decision": governed.decision,
                "governed_reason": governed.reason,
                "governed_blockers": list(governed.blockers),
            }
        )
        append_promotion_metric(
            workspace_path=workspace_path,
            session_id=session_id,
            task_id=event.get("task_id"),
            decision=promotion_decision,
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
            "usage": usage if isinstance(usage, dict) else {},
            "llm_calls": llm_calls,
            "candidates": candidates,
        },
    )
    return {"action": "allow"}
