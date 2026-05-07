from __future__ import annotations

import json
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
    if final_answer:
        decision = evaluate_turn_end_candidate(final_answer)
        candidate_id = f"{session_id}:{event.get('task_id') or 'task'}:0"
        initial_candidate = candidate_payload(decision, auto_promoted=False) | {
            "candidate_id": candidate_id,
        }
        session_log_path = append_workspace_session_log(
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
                "candidates": [initial_candidate],
            },
        )
        persisted_entry = _read_last_session_log_entry(session_log_path)
        persisted_candidate = _find_persisted_candidate(persisted_entry, candidate_id)
        governed_source_ref = {
            "session_id": session_id,
            "task_id": str(event.get("task_id") or ""),
            "candidate_id": candidate_id,
            "session_log_path": str(session_log_path),
            "session_log_recorded_at": str(persisted_entry.get("recorded_at") or ""),
        }
        governed = evaluate_governed_candidate(
            workspace_path=workspace_path,
            candidate={
                "candidate_id": candidate_id,
                "content": str(persisted_candidate.get("content") or ""),
                "memory_type": str(persisted_candidate.get("memory_type") or decision.memory_type),
                "confidence": str(persisted_candidate.get("confidence") or ""),
                "source_ref": governed_source_ref,
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
                decision_id=governed.decision_id,
                source_ref=governed.source_ref,
            )
        append_promotion_metric(
            workspace_path=workspace_path,
            session_id=session_id,
            task_id=event.get("task_id"),
            decision=promotion_decision,
        )
    else:
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
                "candidates": [],
            },
        )
    return {"action": "allow"}


def _read_last_session_log_entry(session_log_path):
    lines = session_log_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return {}
    payload = json.loads(lines[-1])
    if isinstance(payload, dict):
        return payload
    return {}


def _find_persisted_candidate(payload, candidate_id: str):
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("candidate_id") == candidate_id:
            return candidate
    return {}
