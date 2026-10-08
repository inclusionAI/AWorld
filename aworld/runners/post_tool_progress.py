import hashlib
import json
import os
import re
import stat as stat_module
import time
from typing import Any, Mapping

from aworld.core.common import ActionModel, Observation
from aworld.utils.serialized_util import to_serializable

WATCHDOG_STATE_KEY = "post_tool_progress_watchdog"
WATCHDOG_METRICS_KEY = "post_tool_progress_metrics"
SEMANTIC_PROGRESS_KEY = "context_semantic_progress"
_SEMANTIC_RUNTIME_KEY = "semantic_progress"
_POST_TOOL_TURNS_RUNTIME_KEY = "post_tool_turns"
_RECENT_SEMANTIC_PAIR_WINDOW = 8
_PROGRESS_GUARD_REPEAT_THRESHOLD = 3
_SEMANTIC_NO_PROGRESS_THRESHOLD = 6
SEMANTIC_PROGRESS_LEDGER_ENV = "AWORLD_SEMANTIC_PROGRESS_LEDGER"
_VOLATILE_FAILURE_TEXT = re.compile(
    r"(?:0x[0-9a-f]+|sha256:[0-9a-f]+|/[^\s:'\"]+|\b\d+\b)",
    re.IGNORECASE,
)
_PUBLIC_DELIVERABLE_SCHEMA = "aworld.public-deliverables/v1"
_PUBLIC_DELIVERABLE_AUTHORITY = "public_task_advisory"
_PUBLIC_DELIVERABLE_BASELINE_KEY = "public_deliverable_baseline"
_PUBLIC_DELIVERABLE_HASH_MAX_BYTES = 8 * 1024 * 1024


def _public_file_version(
    path: str,
    stat_result: os.stat_result,
    *,
    hash_budget_bytes: int,
) -> tuple[str, int]:
    """Return content identity when bounded, otherwise a conservative size receipt.

    Metadata-only changes such as ``touch`` must not count as candidate
    progress. Large or unreadable files therefore fall back to size-only
    identity: a size change is real content-state change, while same-size
    updates remain deliberately unobservable rather than becoming false
    positive progress.
    """

    if stat_result.st_size > hash_budget_bytes:
        return f"size-only:{stat_result.st_size}", 0
    digest = hashlib.sha256()
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            before = os.fstat(handle.fileno())
            if (
                not stat_module.S_ISREG(before.st_mode)
                or before.st_size > hash_budget_bytes
            ):
                return f"size-only:{before.st_size}", 0
            bytes_read = 0
            while bytes_read <= hash_budget_bytes:
                chunk = handle.read(
                    min(1024 * 1024, hash_budget_bytes + 1 - bytes_read)
                )
                if not chunk:
                    break
                bytes_read += len(chunk)
                if bytes_read > hash_budget_bytes:
                    return f"size-only:{before.st_size}", 0
                digest.update(chunk)
            after = os.fstat(handle.fileno())
        current = os.stat(path)
    except OSError:
        return f"size-only:{stat_result.st_size}", 0
    stable_identity = (
        (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        == (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        == (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
        )
    )
    if not stable_identity or bytes_read != before.st_size:
        return f"size-only:{current.st_size}", 0
    return "sha256:" + digest.hexdigest(), bytes_read


def _public_deliverable_projection(context) -> dict[str, Any] | None:
    """Observe explicit public file milestones without granting acceptance."""

    value = context.context_info.get("public_deliverable_contract")
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != _PUBLIC_DELIVERABLE_SCHEMA
        or value.get("authority") != _PUBLIC_DELIVERABLE_AUTHORITY
        or value.get("source") != "public_task_text"
    ):
        return None
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts or len(artifacts) > 16:
        return None
    projection = []
    remaining_hash_budget = _PUBLIC_DELIVERABLE_HASH_MAX_BYTES
    for item in artifacts:
        if (
            not isinstance(item, dict)
            or item.get("kind") != "file"
            or item.get("authority") != _PUBLIC_DELIVERABLE_AUTHORITY
            or not isinstance(item.get("deliverable_id"), str)
            or not isinstance(item.get("path"), str)
        ):
            return None
        try:
            stat = os.stat(item["path"])
            exists = os.path.isfile(item["path"])
        except OSError:
            stat = None
            exists = False
        version = None
        if exists and stat is not None:
            version, consumed = _public_file_version(
                item["path"],
                stat,
                hash_budget_bytes=remaining_hash_budget,
            )
            remaining_hash_budget = max(0, remaining_hash_budget - consumed)
        projection.append(
            {
                "deliverable_id": item["deliverable_id"],
                "exists": exists,
                "version": version,
            }
        )
    return {
        "declared_count": len(projection),
        "existing_count": sum(item["exists"] for item in projection),
        "artifacts": projection,
    }


def capture_public_deliverable_baseline(context) -> None:
    """Capture candidate versions before ordinary task Tools execute."""

    from aworld.core.context.compiler import semantic_fingerprint

    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return
    scope = _semantic_state_scope(runtime_context)
    current = runtime_context.context_info.get(_PUBLIC_DELIVERABLE_BASELINE_KEY)
    if isinstance(current, dict) and current.get("scope") == scope:
        return
    projection = _public_deliverable_projection(runtime_context)
    runtime_context.context_info[_PUBLIC_DELIVERABLE_BASELINE_KEY] = {
        "scope": scope,
        "fingerprint": (
            semantic_fingerprint(projection) if projection is not None else None
        ),
        "artifacts": (
            {
                item["deliverable_id"]: item.get("version")
                for item in projection.get("artifacts", ())
            }
            if projection is not None
            else {}
        ),
    }


def _observed_action_names(
    tool_name: str,
    actions: list[ActionModel],
) -> tuple[str, ...]:
    """Project exact bounded Tool identities without parsing plan prose."""

    names: list[str] = []
    for value in (tool_name,):
        if isinstance(value, str) and value.strip():
            names.append(value.strip())
    for action in actions:
        tool = getattr(action, "tool_name", None)
        operation = getattr(action, "action_name", None)
        model_visible = getattr(action, "model_visible_tool_name", None)
        for value in (model_visible, tool, operation):
            if isinstance(value, str) and value.strip() and len(value.strip()) <= 256:
                names.append(value.strip())
        if (
            isinstance(tool, str)
            and tool.strip()
            and isinstance(operation, str)
            and operation.strip()
        ):
            names.append(f"{tool.strip()}__{operation.strip()}")
    return tuple(dict.fromkeys(names))[:32]


def _observed_action_signatures(
    actions: list[ActionModel],
) -> tuple[str, ...]:
    """Hash exact executed function identities and params without retaining args."""

    from aworld.core.execution_protocol import action_signature

    signatures: list[str] = []
    for action in actions:
        tool = getattr(action, "tool_name", None)
        operation = getattr(action, "action_name", None)
        model_visible = getattr(action, "model_visible_tool_name", None)
        params = getattr(action, "params", None)
        if (
            not isinstance(tool, str)
            or not tool.strip()
            or not isinstance(params, Mapping)
        ):
            continue
        identities = []
        if isinstance(model_visible, str) and model_visible.strip():
            identities.append(model_visible.strip())
        identities.append(tool.strip())
        if isinstance(operation, str) and operation.strip():
            identities.extend(
                [operation.strip(), f"{tool.strip()}__{operation.strip()}"]
            )
        for identity in identities:
            try:
                signature = action_signature(identity, params)
            except ValueError:
                continue
            if signature not in signatures:
                signatures.append(signature)
            if len(signatures) >= 32:
                return tuple(signatures)
    return tuple(signatures)


def _observed_action_semantics(
    actions: list[ActionModel],
    action_results: list[Any],
) -> tuple[dict[str, Any], ...]:
    """Project only Sandbox-authenticated receipts paired to their Tool call."""

    from aworld.core.execution_protocol import ActionSemanticReceipt
    from aworld.sandbox.tool_observation import ACTION_SEMANTIC_RECEIPT_KEY

    actions_by_call_id = {
        action.tool_call_id: action
        for action in actions
        if isinstance(action.tool_call_id, str) and action.tool_call_id
    }
    receipts: list[dict[str, Any]] = []
    for result in action_results:
        if not isinstance(result, Mapping):
            continue
        metadata = result.get("metadata")
        sandbox_receipt = (
            metadata.get("sandbox_observation")
            if isinstance(metadata, Mapping)
            else None
        )
        candidate = (
            sandbox_receipt.get(ACTION_SEMANTIC_RECEIPT_KEY)
            if isinstance(sandbox_receipt, Mapping)
            else None
        )
        if not isinstance(candidate, Mapping):
            continue
        try:
            receipt = ActionSemanticReceipt.from_dict(candidate)
        except (TypeError, ValueError):
            continue
        if (
            receipt.executed is None
            or receipt.succeeded is None
            or receipt.timed_out is None
        ):
            continue
        call_id = receipt.tool_call_id
        result_call_id = result.get("tool_call_id")
        if (
            not isinstance(call_id, str)
            or call_id not in actions_by_call_id
            or (
                isinstance(result_call_id, str)
                and result_call_id
                and result_call_id != call_id
            )
        ):
            continue
        receipts.append(receipt.to_dict())
        if len(receipts) >= 16:
            break
    return tuple(receipts)


def _select_semantic_state(shared: Any, local: Any) -> dict[str, Any] | None:
    """Choose the newest typed state while retaining ContextState compatibility."""
    shared_state = shared if isinstance(shared, dict) else None
    local_state = local if isinstance(local, dict) else None
    if shared_state is None:
        return local_state
    if local_state is None or local_state == shared_state:
        return shared_state
    shared_revision = shared_state.get("runtime_revision")
    local_revision = local_state.get("runtime_revision")
    # Direct ContextState injection is a supported test/configuration boundary.
    # A value without a runtime revision is therefore an explicit override, not
    # an older transport snapshot.
    if local_revision is None:
        return local_state
    if isinstance(local_revision, int) and isinstance(shared_revision, int):
        return local_state if local_revision > shared_revision else shared_state
    return shared_state


def _runtime_context(context):
    if context is None:
        return None
    event_manager = getattr(context, "event_manager", None)
    root_context = (
        getattr(event_manager, "context", None) if event_manager is not None else None
    )
    return root_context or context


def _runtime_registry_owner(context):
    resolver = getattr(context, "_task_runtime_registry_owner", None)
    return resolver() if callable(resolver) else context


def _working_state_value(context, key: str) -> Any:
    task_state = getattr(context, "task_state", None)
    working_state = getattr(task_state, "working_state", None)
    kv_store = getattr(working_state, "kv_store", None)
    if isinstance(kv_store, Mapping) and key in kv_store:
        return kv_store.get(key)
    return None


def _project_working_state_value(context, key: str, value: Any) -> None:
    task_state = getattr(context, "task_state", None)
    working_state = getattr(task_state, "working_state", None)
    kv_store = getattr(working_state, "kv_store", None)
    if isinstance(kv_store, dict):
        kv_store[key] = value
        return
    put = getattr(context, "put", None)
    if callable(put):
        put(key, value)


def _semantic_state_scope(context) -> dict[str, Any]:
    return {
        "task_id": getattr(context, "task_id", None),
        "task_epoch": getattr(context, "task_epoch", None),
    }


def _semantic_state_in_scope(
    state: dict[str, Any] | None,
    scope: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(state, dict):
        return None
    stored_scope = state.get("scope")
    # Unscoped values remain a supported direct ContextState configuration
    # boundary. States emitted by this runtime are always scoped, so they are
    # rejected after a task/epoch transition instead of contaminating the next
    # task's high-water mark and stagnation counters.
    if stored_scope is not None and stored_scope != scope:
        return None
    return state


def _metrics_dict(context) -> dict[str, Any]:
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return {}
    metrics = runtime_context.context_info.get(WATCHDOG_METRICS_KEY)
    if not isinstance(metrics, dict):
        metrics = {}
        runtime_context.context_info[WATCHDOG_METRICS_KEY] = metrics
    return metrics


def increment_watchdog_metric(context, key: str, delta: int = 1) -> int:
    metrics = _metrics_dict(context)
    metrics[key] = int(metrics.get(key, 0) or 0) + delta
    runtime_context = _runtime_context(context)
    runtime_context.context_info[WATCHDOG_METRICS_KEY] = metrics
    return metrics[key]


def record_adaptive_context_metrics(
    context,
    *,
    checkpoint: bool = False,
    no_progress_checkpoint: bool = False,
    escalation_level: int = 0,
    progress_reset: bool = False,
) -> None:
    """Record bounded adaptive-policy evidence on the runtime Context."""
    if (
        isinstance(escalation_level, bool)
        or not isinstance(escalation_level, int)
        or escalation_level < 0
    ):
        raise ValueError("escalation_level must be non-negative")

    metrics = _metrics_dict(context)

    def count_value(key: str) -> int:
        value = metrics.get(key, 0)
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            else 0
        )

    if checkpoint:
        metrics["adaptive_checkpoint_count"] = (
            count_value("adaptive_checkpoint_count") + 1
        )
    if no_progress_checkpoint:
        metrics["adaptive_no_progress_checkpoint_count"] = (
            count_value("adaptive_no_progress_checkpoint_count") + 1
        )
    if escalation_level > 0:
        metrics["adaptive_escalation_count"] = (
            count_value("adaptive_escalation_count") + 1
        )
        metrics["adaptive_escalation_level_max"] = max(
            count_value("adaptive_escalation_level_max"),
            escalation_level,
        )
    if progress_reset:
        metrics["adaptive_goal_progress_reset_count"] = (
            count_value("adaptive_goal_progress_reset_count") + 1
        )
    runtime_context = _runtime_context(context)
    if runtime_context is not None:
        runtime_context.context_info[WATCHDOG_METRICS_KEY] = metrics


def record_semantic_tool_progress(
    context,
    *,
    tool_name: str,
    agent_id: str,
    actions: list[ActionModel],
    observation: Observation,
) -> dict[str, Any] | None:
    """Serialize evidence derivation across transported Tool result groups."""
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return None
    transaction = getattr(runtime_context, "task_runtime_state_transaction", None)
    if callable(transaction):
        with transaction():
            return _record_semantic_tool_progress_locked(
                runtime_context,
                tool_name=tool_name,
                agent_id=agent_id,
                actions=actions,
                observation=observation,
            )
    return _record_semantic_tool_progress_locked(
        runtime_context,
        tool_name=tool_name,
        agent_id=agent_id,
        actions=actions,
        observation=observation,
    )


def _record_semantic_tool_progress_locked(
    context,
    *,
    tool_name: str,
    agent_id: str,
    actions: list[ActionModel],
    observation: Observation,
) -> dict[str, Any] | None:
    """Record bounded hashes for repetition and low-information-gain signals."""
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return None
    from aworld.core.context.compiler import (
        semantic_fingerprint,
        semantic_result_fingerprint,
    )

    shared_reader = getattr(runtime_context, "read_task_runtime_state", None)
    previous = (
        shared_reader(agent_id, _SEMANTIC_RUNTIME_KEY)
        if callable(shared_reader)
        else None
    )
    durable_owner = _runtime_registry_owner(runtime_context)
    semantic_working_key = f"{SEMANTIC_PROGRESS_KEY}:{agent_id}"
    previous = _select_semantic_state(
        previous,
        _working_state_value(durable_owner, semantic_working_key),
    )
    state_by_agent = runtime_context.context_info.get(SEMANTIC_PROGRESS_KEY)
    if not isinstance(state_by_agent, dict):
        state_by_agent = {}
    previous = _select_semantic_state(previous, state_by_agent.get(agent_id))
    semantic_scope = _semantic_state_scope(runtime_context)
    previous = _semantic_state_in_scope(previous, semantic_scope) or {}

    serialized_observation = to_serializable(observation)
    action_results = (
        serialized_observation.get("action_result", [])
        if isinstance(serialized_observation, dict)
        else []
    )
    artifact_receipts = [
        metadata.get("context_management")
        for result in action_results
        if isinstance(result, dict)
        for metadata in (result.get("metadata"),)
        if isinstance(metadata, dict)
        and isinstance(metadata.get("context_management"), dict)
    ]
    sandbox_receipts = [
        metadata.get("sandbox_observation")
        for result in action_results
        if isinstance(result, dict)
        for metadata in (result.get("metadata"),)
        if isinstance(metadata, dict)
        and isinstance(metadata.get("sandbox_observation"), dict)
    ]
    observed_action_semantics = _observed_action_semantics(actions, action_results)
    known_mutation_executed = any(
        isinstance(receipt.get("terminal_execution_receipt"), dict)
        and receipt["terminal_execution_receipt"].get("effect") == "mutating"
        and receipt["terminal_execution_receipt"].get("executed") is True
        and receipt["terminal_execution_receipt"].get("exit_code") == 0
        and receipt["terminal_execution_receipt"].get("timed_out") is False
        for receipt in sandbox_receipts
    )
    sandbox_workspace_mutated = any(
        receipt.get("workspace_mutated") is True
        for receipt in sandbox_receipts
    )
    sandbox_read_only_observed = bool(sandbox_receipts) and all(
        receipt.get("effect") == "read_only" for receipt in sandbox_receipts
    )
    sandbox_read_only_blocked = any(
        receipt.get("effect") == "blocked_read_only"
        for receipt in sandbox_receipts
    )
    artifact_changed = any(
        receipt.get("artifact_changed") is True for receipt in artifact_receipts
    ) or sandbox_workspace_mutated
    rollback_performed = any(
        receipt.get("rollback_performed") is True for receipt in artifact_receipts
    )
    implicit_artifact_loss = any(
        receipt.get("implicit_artifact_loss_detected") is True
        for receipt in artifact_receipts
    )
    artifact_fingerprint = next(
        (
            receipt.get("artifact_fingerprint_after")
            for receipt in reversed(artifact_receipts)
            if isinstance(receipt.get("artifact_fingerprint_after"), str)
        ),
        None,
    )
    workspace_generation = max(
        (
            receipt.get("workspace_generation", 0)
            for receipt in sandbox_receipts
            if isinstance(receipt.get("workspace_generation"), int)
            and not isinstance(receipt.get("workspace_generation"), bool)
        ),
        default=0,
    )
    raw_feature = os.environ.get(SEMANTIC_PROGRESS_LEDGER_ENV)
    semantic_ledger_enabled = not (
        raw_feature is not None
        and raw_feature.strip().lower() in {"0", "false", "no", "off"}
    )
    if semantic_ledger_enabled:
        try:
            from aworld.core.execution_protocol import ProtocolMode
            from aworld.runners.execution_protocol import execution_protocol_policy

            protocol_policy = execution_protocol_policy(runtime_context, agent_id)
            semantic_ledger_enabled = bool(
                protocol_policy.semantic_progress_enabled
                and protocol_policy.mode is not ProtocolMode.OFF
            )
        except Exception:
            semantic_ledger_enabled = True

    failure_items: list[dict[str, Any]] = []
    for result in action_results:
        if not isinstance(result, dict):
            continue
        metadata = result.get("metadata")
        semantic_failure = (
            metadata.get("semantic_failure") if isinstance(metadata, dict) else None
        )
        code = (
            semantic_failure.get("code") if isinstance(semantic_failure, dict) else None
        )
        if isinstance(code, str):
            failure_items.append({"code": code})
        elif result.get("success") is False or result.get("error"):
            error_shape = _VOLATILE_FAILURE_TEXT.sub(
                "<volatile>",
                str(result.get("error") or "action_result_failed").lower(),
            )[:512]
            failure_items.append({"code": "action_result_failed", "shape": error_shape})
    failure_signature = semantic_fingerprint(failure_items) if failure_items else None
    hypothesis_map = runtime_context.context_info.get(
        f"execution_protocol_hypotheses:{agent_id}"
    )
    hypothesis_ids = []
    if isinstance(hypothesis_map, dict):
        for action in actions:
            call_id = getattr(action, "tool_call_id", None)
            value = hypothesis_map.get(call_id) if isinstance(call_id, str) else None
            if isinstance(value, str):
                hypothesis_ids.append(value)
    hypothesis_id = (
        hypothesis_ids[0] if hypothesis_ids else previous.get("hypothesis_id")
    )

    operation_hash = semantic_fingerprint(
        {
            "tool_name": tool_name,
            "actions": to_serializable(actions),
        }
    )
    result_hash = semantic_result_fingerprint(serialized_observation)
    completion_assessment = None
    try:
        completion_assessment = runtime_context.assess_completion_contract(
            agent_claimed_finished=False
        )
    except Exception:
        completion_assessment = None
    all_artifact_evidence_by_id = {
        str(item.requirement_id): item
        for item in getattr(runtime_context, "_completion_artifact_evidence", ())
    }
    all_self_check_evidence_by_id = {
        str(item.command_id): item
        for item in getattr(runtime_context, "_completion_self_checks", ())
    }
    all_immutable_input_evidence_by_id = {
        str(item.input_id): item
        for item in getattr(runtime_context, "_completion_immutable_input_evidence", ())
    }
    all_final_evidence_codes = sorted(
        set(getattr(runtime_context, "_completion_final_evidence_codes", ()))
    )
    completion_contract = getattr(runtime_context, "completion_contract", None)
    required_artifact_ids = {
        str(item.requirement_id)
        for item in getattr(completion_contract, "required_artifacts", ())
        if item.required
    }
    required_self_check_ids = {
        str(item.command_id)
        for item in getattr(completion_contract, "validation_commands", ())
    }
    required_self_check_ids.update(
        str(value)
        for value in getattr(completion_contract, "required_self_check_ids", ())
    )
    required_immutable_input_ids = {
        str(value) for value in getattr(completion_contract, "immutable_inputs", ())
    }
    required_final_evidence_codes = {
        str(value)
        for value in getattr(completion_contract, "required_final_evidence", ())
    }
    artifact_evidence_by_id = {
        key: value
        for key, value in all_artifact_evidence_by_id.items()
        if key in required_artifact_ids
    }
    self_check_evidence_by_id = {
        key: value
        for key, value in all_self_check_evidence_by_id.items()
        if key in required_self_check_ids
    }
    immutable_input_evidence_by_id = {
        key: value
        for key, value in all_immutable_input_evidence_by_id.items()
        if key in required_immutable_input_ids
    }
    final_evidence_codes = sorted(
        required_final_evidence_codes.intersection(all_final_evidence_codes)
    )
    completion_projection = (
        {
            "status": completion_assessment.status.value,
            "reason_codes": list(completion_assessment.reason_codes),
            # Completion evidence is an identity-keyed latest-state view, just
            # like ``assess_completion``.  Append-only retries of an unchanged
            # check must not manufacture a new durable milestone merely by
            # increasing a raw list length.
            "artifact_evidence_count": len(artifact_evidence_by_id),
            "self_check_count": len(self_check_evidence_by_id),
            "final_evidence_count": len(final_evidence_codes),
            "satisfied_artifact_count": sum(
                evidence.exists is True for evidence in artifact_evidence_by_id.values()
            ),
            "successful_self_check_count": sum(
                evidence.exit_code == 0
                for evidence in self_check_evidence_by_id.values()
            ),
            "valid_immutable_input_count": sum(
                evidence.expected_hash == evidence.observed_hash
                for evidence in immutable_input_evidence_by_id.values()
            ),
            "external_verifier_passed": bool(
                getattr(runtime_context, "_completion_external_verifier", None)
                and runtime_context._completion_external_verifier.passed
            ),
        }
        if completion_assessment is not None
        else None
    )
    completion_fingerprint = (
        semantic_fingerprint(completion_projection)
        if completion_projection is not None
        else None
    )
    completion_evidence_fingerprint = (
        semantic_fingerprint(
            {
                "artifacts": {
                    str(item.requirement_id): {
                        "exists": item.exists,
                        "content_hash": item.content_hash,
                        "media_type": item.media_type,
                    }
                    for item in all_artifact_evidence_by_id.values()
                },
                "immutable_inputs": {
                    str(item.input_id): {
                        "expected_hash": item.expected_hash,
                        "observed_hash": item.observed_hash,
                    }
                    for item in all_immutable_input_evidence_by_id.values()
                },
                "self_checks": {
                    str(item.command_id): {
                        "exit_code": item.exit_code,
                        "output_hash": item.output_hash,
                    }
                    for item in all_self_check_evidence_by_id.values()
                },
                "final_evidence_codes": all_final_evidence_codes,
                "external_verifier_passed": bool(
                    getattr(runtime_context, "_completion_external_verifier", None)
                    and runtime_context._completion_external_verifier.passed
                ),
            }
        )
        if completion_projection is not None
        else None
    )
    validation_evidence_advanced = bool(
        completion_evidence_fingerprint
        and completion_evidence_fingerprint
        != previous.get("completion_evidence_fingerprint")
    )
    public_delivery_projection = _public_deliverable_projection(runtime_context)
    public_delivery_fingerprint = (
        semantic_fingerprint(public_delivery_projection)
        if public_delivery_projection is not None
        else None
    )
    public_delivery_count = (
        int(public_delivery_projection["existing_count"])
        if public_delivery_projection is not None
        else 0
    )
    public_deliverable_declared = public_delivery_projection is not None
    missing_public_deliverable_count = (
        int(public_delivery_projection["declared_count"]) - public_delivery_count
        if public_delivery_projection is not None
        else 0
    )
    candidate_present = (
        public_delivery_count > 0 if public_delivery_projection is not None else None
    )
    baseline = runtime_context.context_info.get(_PUBLIC_DELIVERABLE_BASELINE_KEY)
    baseline_versions = (
        baseline.get("artifacts")
        if isinstance(baseline, dict)
        and baseline.get("scope") == semantic_scope
        and isinstance(baseline.get("artifacts"), dict)
        else {}
    )
    previous_versions = previous.get("public_delivery_versions")
    if not isinstance(previous_versions, dict):
        previous_versions = baseline_versions
    public_delivery_versions = (
        {
            item["deliverable_id"]: item.get("version")
            for item in public_delivery_projection.get("artifacts", ())
        }
        if public_delivery_projection is not None
        else {}
    )
    public_candidate_mutated = bool(
        public_delivery_projection is not None
        and any(
            version is not None and version != baseline_versions.get(deliverable_id)
            for deliverable_id, version in public_delivery_versions.items()
        )
    )
    previous_public_delivery_high_water = int(
        previous.get("public_delivery_high_water_count", 0) or 0
    )
    public_delivery_changed = bool(
        public_delivery_projection is not None
        and any(
            version is not None and version != previous_versions.get(deliverable_id)
            for deliverable_id, version in public_delivery_versions.items()
        )
    )
    recent_public_delivery_fingerprints = [
        value
        for value in (previous.get("recent_public_delivery_fingerprints") or ())
        if isinstance(value, str)
    ][-7:]
    if (
        not recent_public_delivery_fingerprints
        and isinstance(baseline, dict)
        and isinstance(baseline.get("fingerprint"), str)
    ):
        recent_public_delivery_fingerprints.append(baseline["fingerprint"])
    public_delivery_advanced = bool(
        public_delivery_changed
        and public_delivery_fingerprint
        and public_delivery_fingerprint not in recent_public_delivery_fingerprints
    )
    if public_delivery_fingerprint:
        recent_public_delivery_fingerprints.append(public_delivery_fingerprint)
    public_delivery_high_water_count = max(
        previous_public_delivery_high_water,
        public_delivery_count,
    )
    # Workspace receipts and failure signatures make diagnostic change
    # observable, but neither proves that a declared task requirement advanced.
    # Keep contractless work unknown at the goal layer; a separate bounded
    # advisory clock below can still ask the model to checkpoint without
    # deciding that the task should stop.
    diagnostic_progress_observable = bool(
        artifact_receipts
        or failure_signature is not None
        or previous.get("failure_signature") is not None
    )
    if not semantic_ledger_enabled:
        goal_progress_observable = (
            completion_projection is not None or public_delivery_projection is not None
        )
    elif completion_projection is not None or public_delivery_projection is not None:
        goal_progress_observable = True
    else:
        goal_progress_observable = None
    completion_positive_evidence = (
        sum(
            int(completion_projection[key])
            for key in (
                "satisfied_artifact_count",
                "successful_self_check_count",
                "valid_immutable_input_count",
                "final_evidence_count",
                "external_verifier_passed",
            )
        )
        if completion_projection is not None
        else 0
    )
    completion_score = (
        [
            completion_positive_evidence,
            -len(completion_projection["reason_codes"]),
        ]
        if completion_projection is not None
        else None
    )
    previous_completion_score = previous.get("completion_score")
    previous_completion_high_water_score = previous.get("completion_high_water_score")
    if not isinstance(previous_completion_high_water_score, list):
        previous_completion_high_water_score = (
            previous_completion_score
            if isinstance(previous_completion_score, list)
            else None
        )
    completion_advanced = bool(
        completion_score is not None
        and (
            (
                isinstance(previous_completion_high_water_score, list)
                and tuple(completion_score)
                > tuple(previous_completion_high_water_score)
            )
            or (
                not isinstance(previous_completion_high_water_score, list)
                and completion_positive_evidence > 0
            )
        )
    )
    completion_high_water_score = previous_completion_high_water_score
    if completion_score is not None and (
        not isinstance(completion_high_water_score, list)
        or tuple(completion_score) > tuple(completion_high_water_score)
    ):
        completion_high_water_score = list(completion_score)
    recent_artifact_fingerprints = list(
        previous.get("recent_artifact_fingerprints") or []
    )[-7:]
    artifact_advanced = bool(
        artifact_changed
        and not rollback_performed
        and artifact_fingerprint
        and artifact_fingerprint != previous.get("artifact_fingerprint")
        and artifact_fingerprint not in recent_artifact_fingerprints
    )
    recent_failure_signatures = [
        value
        for value in (previous.get("recent_failure_signatures") or [])
        if isinstance(value, str)
    ][-7:]
    failure_resolved = bool(
        previous.get("failure_signature") is not None and failure_signature is None
    )
    failure_novel = bool(
        failure_signature is not None
        and failure_signature not in recent_failure_signatures
    )
    failure_changed = failure_resolved or failure_novel
    if failure_signature is not None:
        recent_failure_signatures.append(failure_signature)
    semantic_progress = bool(
        artifact_advanced
        or validation_evidence_advanced
        or completion_advanced
        or public_delivery_advanced
        or failure_changed
    )
    # New failures and opaque workspace changes are useful evidence, but they
    # are not proof that the requested outcome moved closer to completion.
    # Reserve ``goal_progress`` for monotonic, inspectable milestone evidence;
    # the controller uses it only to reset an advisory no-progress window and
    # never to declare success.
    durable_milestone_advanced = completion_advanced or public_delivery_advanced
    goal_progress = durable_milestone_advanced
    goal_progress_count = int(previous.get("goal_progress_count", 0) or 0) + int(
        goal_progress
    )
    get_agent_step = getattr(runtime_context, "get_agent_step", None)
    current_agent_step = get_agent_step(agent_id) if callable(get_agent_step) else 0
    if not isinstance(current_agent_step, int) or isinstance(current_agent_step, bool):
        current_agent_step = 0
    last_goal_progress_agent_step = (
        current_agent_step
        if goal_progress
        else previous.get("last_goal_progress_agent_step")
    )
    no_goal_progress_count = (
        0
        if goal_progress or not goal_progress_observable
        else int(previous.get("no_goal_progress_count", 0) or 0) + 1
    )
    semantic_pair_hash = semantic_fingerprint(
        {"operation_hash": operation_hash, "result_hash": result_hash}
    )
    progress_guard_reset = durable_milestone_advanced
    previous_pairs = previous.get("recent_operation_result_hashes")
    recent_pairs = (
        [value for value in previous_pairs if isinstance(value, str)][
            -(_RECENT_SEMANTIC_PAIR_WINDOW - 1) :
        ]
        if isinstance(previous_pairs, list) and not progress_guard_reset
        else []
    )
    recent_pairs.append(semantic_pair_hash)
    previous_results = previous.get("recent_result_hashes")
    history = (
        [value for value in previous_results if isinstance(value, str)][
            -(_RECENT_SEMANTIC_PAIR_WINDOW - 1) :
        ]
        if isinstance(previous_results, list) and not progress_guard_reset
        else []
    )
    history.append(result_hash)
    repetition_count = recent_pairs.count(semantic_pair_hash)
    result_repetition_count = history.count(result_hash)
    new_information_observed = result_repetition_count == 1
    # Successful investigation can produce useful observations without
    # advancing a durable, inspectable milestone.  Keep that evidence in
    # ``semantic_progress`` while allowing the advisory clock to continue.
    # This prevents changing network errors, package installs, downloaded
    # pages, and other opaque workspace churn from suppressing replanning.
    # The signal never stops the task, revokes Tools, or imposes a cost limit.
    durable_stagnation_count = (
        0
        if (not semantic_ledger_enabled or durable_milestone_advanced)
        else int(previous.get("durable_stagnation_count", 0) or 0) + 1
    )
    low_information_gain_count = max(
        result_repetition_count,
        (
            durable_stagnation_count
            if durable_stagnation_count >= _SEMANTIC_NO_PROGRESS_THRESHOLD
            else 0
        ),
    )
    progress_guard_required = (
        repetition_count >= _PROGRESS_GUARD_REPEAT_THRESHOLD
        and not progress_guard_reset
    )
    workspace_mutation_observed = bool(
        previous.get("workspace_mutation_observed") or sandbox_workspace_mutated
    )
    previous_read_only_observations = int(
        previous.get("consecutive_read_only_observations", 0) or 0
    )
    if sandbox_read_only_blocked:
        consecutive_read_only_observations = previous_read_only_observations
    elif (
        sandbox_read_only_observed
        and not sandbox_workspace_mutated
        and not candidate_present
    ):
        consecutive_read_only_observations = previous_read_only_observations + 1
    else:
        consecutive_read_only_observations = 0
    if artifact_fingerprint:
        recent_artifact_fingerprints.append(artifact_fingerprint)
    state = {
        "agent_id": agent_id,
        "scope": semantic_scope,
        "operation_hash": operation_hash,
        "result_hash": result_hash,
        "operation_result_hash": semantic_pair_hash,
        "repetition_count": repetition_count,
        "result_repetition_count": result_repetition_count,
        "low_information_gain_count": low_information_gain_count,
        "durable_stagnation_count": durable_stagnation_count,
        "recent_operation_result_hashes": recent_pairs,
        "recent_result_hashes": history,
        "recent_artifact_fingerprints": recent_artifact_fingerprints[-8:],
        "artifact_changed": artifact_changed,
        "artifact_fingerprint": artifact_fingerprint,
        "artifact_advanced": artifact_advanced,
        "workspace_mutated": bool(
            (artifact_changed or sandbox_workspace_mutated)
            and not rollback_performed
        ),
        "workspace_mutation_observed": workspace_mutation_observed,
        # A successful known-mutating terminal execution permits one bounded
        # validation turn. It is deliberately separate from actual mutation
        # evidence and never contributes to goal/completion progress.
        "known_mutation_executed": known_mutation_executed,
        "read_only_observed": sandbox_read_only_observed,
        "consecutive_read_only_observations": (
            consecutive_read_only_observations
        ),
        "workspace_generation": workspace_generation,
        "diagnostic_progress_observable": diagnostic_progress_observable,
        "failure_signature": failure_signature,
        "recent_failure_signatures": recent_failure_signatures[-8:],
        "hypothesis_id": hypothesis_id,
        "semantic_progress_enabled": semantic_ledger_enabled,
        "semantic_progress": semantic_progress,
        "semantic_no_progress_threshold": _SEMANTIC_NO_PROGRESS_THRESHOLD,
        "rollback_performed": rollback_performed,
        "implicit_artifact_loss": implicit_artifact_loss,
        "completion_fingerprint": completion_fingerprint,
        "completion_evidence_fingerprint": completion_evidence_fingerprint,
        "completion_score": completion_score,
        "completion_high_water_score": completion_high_water_score,
        "completion_advanced": completion_advanced,
        "public_delivery_fingerprint": public_delivery_fingerprint,
        "public_delivery_count": public_delivery_count,
        "public_delivery_high_water_count": public_delivery_high_water_count,
        "public_delivery_advanced": public_delivery_advanced,
        "public_delivery_changed": public_delivery_changed,
        "recent_public_delivery_fingerprints": (
            recent_public_delivery_fingerprints[-8:]
        ),
        "public_delivery_versions": public_delivery_versions,
        "public_deliverable_declared": public_deliverable_declared,
        "missing_public_deliverable_count": missing_public_deliverable_count,
        "candidate_present": candidate_present,
        "public_candidate_mutated": public_candidate_mutated,
        # Exact plan/action alignment needs to know whether the declared
        # candidate changed on this Tool turn. Durable goal progress below is
        # stricter: an A→B→A oscillation is not a new milestone.
        "candidate_advanced": public_delivery_changed,
        "validation_evidence_advanced": validation_evidence_advanced,
        "validation_observed": validation_evidence_advanced,
        "new_information_observed": new_information_observed,
        "observed_action_names": _observed_action_names(tool_name, actions),
        "observed_action_signatures": _observed_action_signatures(actions),
        "observed_action_semantics": observed_action_semantics,
        "durable_milestone_advanced": durable_milestone_advanced,
        "progress_guard_reset": progress_guard_reset,
        "progress_guard_required": progress_guard_required,
        "progress_guard_repeat_threshold": _PROGRESS_GUARD_REPEAT_THRESHOLD,
        "progress_guard_recent_window": _RECENT_SEMANTIC_PAIR_WINDOW,
        "goal_progress_observable": goal_progress_observable,
        "goal_progress": goal_progress,
        "goal_progress_count": goal_progress_count,
        "last_goal_progress_agent_step": last_goal_progress_agent_step,
        "last_meaningful_progress_agent_step": (
            current_agent_step
            if semantic_progress
            else previous.get("last_meaningful_progress_agent_step")
        ),
        "last_meaningful_progress_at": (
            time.time()
            if semantic_progress
            else previous.get("last_meaningful_progress_at")
        ),
        "current_agent_step": current_agent_step,
        "no_goal_progress_count": no_goal_progress_count,
        "updated_at": time.time(),
        "observation_count": int(previous.get("observation_count", 0) or 0) + 1,
        "runtime_revision": int(previous.get("runtime_revision", 0) or 0) + 1,
    }
    # A convergence gate may authorize only an exact, framework-bound
    # read-only validation.  Derive validation_observed from that real Tool
    # boundary instead of accepting a caller/model boolean.  Public-probe
    # receipt creation below remains the richer advisory evidence projection.
    try:
        from aworld.runners.execution_protocol import (
            framework_observable_validation_kind,
        )

        observed_call_ids = {
            item.get("tool_call_id")
            for item in action_results
            if isinstance(item, Mapping)
            and isinstance(item.get("tool_call_id"), str)
        }
        if any(
            isinstance(getattr(action, "tool_call_id", None), str)
            and action.tool_call_id in observed_call_ids
            and framework_observable_validation_kind(
                runtime_context, agent_id, action
            )
            is not None
            for action in actions
        ):
            state["validation_observed"] = True
    except Exception:
        # Protocol accounting is advisory and cannot turn a successful Tool
        # observation into a runtime failure.
        pass
    state_by_agent[agent_id] = state
    runtime_context.context_info[SEMANTIC_PROGRESS_KEY] = state_by_agent
    shared_writer = getattr(runtime_context, "write_task_runtime_state", None)
    if callable(shared_writer):
        shared_writer(agent_id, _SEMANTIC_RUNTIME_KEY, state)
    _project_working_state_value(durable_owner, semantic_working_key, state)

    # Bind optional model-designed public probes to the separately observed
    # Tool result before checkpointing the operational work ledger. These are
    # advisory self-check receipts, never canonical acceptance or reward.
    try:
        from aworld.runners.execution_protocol import (
            load_public_probe_receipts,
            record_public_probe_observations,
        )

        observed_probe_count = record_public_probe_observations(
            runtime_context,
            agent_id,
            actions=actions,
            result_projections=action_results,
            artifact_after=artifact_fingerprint,
        )
        if observed_probe_count:
            state["validation_observed"] = True
        public_probe_receipts = load_public_probe_receipts(runtime_context, agent_id)
        if public_probe_receipts:
            state["public_probe_receipt_count"] = len(public_probe_receipts)
            state["public_probe_receipts"] = [
                {
                    key: receipt.get(key)
                    for key in (
                        "receipt_id",
                        "hypothesis_id",
                        "probe_kind",
                        "selected_candidate_id",
                        "tool_execution_succeeded",
                        "probe_assessment",
                        "stale",
                    )
                }
                for receipt in public_probe_receipts[-4:]
            ]
            state_by_agent[agent_id] = state
            runtime_context.context_info[SEMANTIC_PROGRESS_KEY] = state_by_agent
            if callable(shared_writer):
                shared_writer(agent_id, _SEMANTIC_RUNTIME_KEY, state)
            _project_working_state_value(durable_owner, semantic_working_key, state)
    except Exception:
        pass

    # Project the same append-only Tool boundary into a bounded operational
    # ledger.  Runtime fan-in prevents transport-copy loss; Amni WorkingState
    # makes the ledger part of normal checkpoint/resume state.
    from aworld.core.context.compiler import (
        ADAPTIVE_WORK_STATE_KEY,
        advance_adaptive_work_state,
        build_adaptive_work_state_entry,
    )

    serialized_actions = to_serializable(actions)
    work_entry = build_adaptive_work_state_entry(
        tool_name=tool_name,
        actions=serialized_actions if isinstance(serialized_actions, list) else [],
        observation=(
            serialized_observation if isinstance(serialized_observation, dict) else {}
        ),
        semantic_progress=state,
    )
    context_key = f"{ADAPTIVE_WORK_STATE_KEY}:{agent_id}"
    durable_work_state = _working_state_value(durable_owner, context_key)

    def advance(current):
        return advance_adaptive_work_state(
            current if isinstance(current, dict) else durable_work_state,
            work_entry,
        )

    def project_adaptive(value):
        durable_owner.context_info[context_key] = value
        _project_working_state_value(durable_owner, context_key, value)

    atomic_update = getattr(
        runtime_context, "update_and_project_task_runtime_state", None
    )
    update_runtime = getattr(runtime_context, "update_task_runtime_state", None)
    if callable(atomic_update):
        work_state = atomic_update(
            agent_id,
            ADAPTIVE_WORK_STATE_KEY,
            advance,
            project_adaptive,
        )
    elif callable(update_runtime):
        work_state = update_runtime(agent_id, ADAPTIVE_WORK_STATE_KEY, advance)
        project_adaptive(work_state)
    else:
        work_state = advance(runtime_context.context_info.get(context_key))
        project_adaptive(work_state)
    runtime_context.context_info[context_key] = work_state

    metrics = _metrics_dict(runtime_context)
    metrics["semantic_tool_observation_count"] = (
        int(metrics.get("semantic_tool_observation_count", 0) or 0) + 1
    )
    if repetition_count > 1 and not progress_guard_reset:
        metrics["repeated_operation_count"] = (
            int(metrics.get("repeated_operation_count", 0) or 0) + 1
        )
    if low_information_gain_count > 1 and not progress_guard_reset:
        metrics["low_information_gain_count"] = (
            int(metrics.get("low_information_gain_count", 0) or 0) + 1
        )
    if durable_stagnation_count > 0:
        metrics["durable_stagnation_observation_count"] = (
            int(metrics.get("durable_stagnation_observation_count", 0) or 0) + 1
        )
    if progress_guard_reset:
        metrics["semantic_recent_window_reset_count"] = (
            int(metrics.get("semantic_recent_window_reset_count", 0) or 0) + 1
        )
    if artifact_changed:
        metrics["task_artifact_change_count"] = (
            int(metrics.get("task_artifact_change_count", 0) or 0) + 1
        )
    if goal_progress:
        metrics["goal_progress_count"] = (
            int(metrics.get("goal_progress_count", 0) or 0) + 1
        )
    elif goal_progress_observable:
        metrics["no_goal_progress_observation_count"] = (
            int(metrics.get("no_goal_progress_observation_count", 0) or 0) + 1
        )
    if rollback_performed:
        metrics["sandbox_rollback_count"] = (
            int(metrics.get("sandbox_rollback_count", 0) or 0) + 1
        )
    if implicit_artifact_loss:
        metrics["implicit_artifact_loss_count"] = (
            int(metrics.get("implicit_artifact_loss_count", 0) or 0) + 1
        )
    runtime_context.context_info[WATCHDOG_METRICS_KEY] = metrics
    metrics["adaptive_work_state_revision"] = int(work_state.get("revision", 0) or 0)
    try:
        from aworld.runners.execution_protocol import (
            record_acceptance_probe_observation,
            record_tool_protocol_event,
        )

        semantic_failure_codes = [
            failure.get("code")
            for failure in failure_items
            if isinstance(failure.get("code"), str)
        ]
        probe_success = bool(action_results) and all(
            isinstance(result, dict)
            and result.get("success") is True
            and not result.get("error")
            for result in action_results
        )
        probe_result = action_results[0] if action_results else {}
        probe_metadata = (
            probe_result.get("metadata")
            if isinstance(probe_result, dict)
            and isinstance(probe_result.get("metadata"), dict)
            else {}
        )
        probe_content = (
            probe_result.get("content") if isinstance(probe_result, dict) else None
        )
        raw_return_code = next(
            (
                probe_metadata.get(key)
                for key in ("return_code", "exit_code")
                if probe_metadata.get(key) is not None
            ),
            None,
        )
        return_code = (
            int(raw_return_code)
            if isinstance(raw_return_code, str)
            and raw_return_code.strip().lstrip("-").isdigit()
            else raw_return_code
            if isinstance(raw_return_code, int)
            and not isinstance(raw_return_code, bool)
            else None
        )

        def bounded_tail(value: Any) -> str:
            return value[-2048:] if isinstance(value, str) else ""

        serialized_probe_content = (
            probe_content
            if isinstance(probe_content, str)
            else json.dumps(
                probe_content, ensure_ascii=False, sort_keys=True, default=str
            )
            if probe_content is not None
            else ""
        )

        result_projection = {
            "tool_call_id": probe_result.get("tool_call_id")
            if isinstance(probe_result, dict)
            else None,
            "success": probe_result.get("success")
            if isinstance(probe_result, dict)
            else False,
            "return_code": return_code,
            "failure_code": semantic_failure_codes[0]
            if semantic_failure_codes
            else None,
            "observed_content_present": bool(serialized_probe_content),
            "observed_content_hash": semantic_fingerprint(serialized_probe_content),
            "stdout_tail": bounded_tail(probe_metadata.get("stdout")),
            "stderr_tail": bounded_tail(probe_metadata.get("stderr")),
            "content_tail": bounded_tail(serialized_probe_content),
        }
        record_acceptance_probe_observation(
            runtime_context,
            agent_id,
            actions=actions,
            result_projection=result_projection,
            success=probe_success,
            failure_code=(
                semantic_failure_codes[0] if semantic_failure_codes else None
            ),
            artifact_after=artifact_fingerprint,
        )

        record_tool_protocol_event(runtime_context, agent_id, state)
    except Exception:
        # The long-horizon controller is advisory.  Its observation path must
        # never turn a successful Tool call into a task execution failure.
        metrics["execution_protocol_observation_error_count"] = (
            int(metrics.get("execution_protocol_observation_error_count", 0) or 0) + 1
        )
    return state


def semantic_progress_for_agent(context, *, agent_id: str) -> dict[str, Any]:
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return {}
    shared_reader = getattr(runtime_context, "read_task_runtime_state", None)
    state = (
        shared_reader(agent_id, _SEMANTIC_RUNTIME_KEY)
        if callable(shared_reader)
        else None
    )
    durable_owner = _runtime_registry_owner(runtime_context)
    state = _select_semantic_state(
        state,
        _working_state_value(durable_owner, f"{SEMANTIC_PROGRESS_KEY}:{agent_id}"),
    )
    state_by_agent = runtime_context.context_info.get(SEMANTIC_PROGRESS_KEY)
    if not isinstance(state_by_agent, dict):
        state_by_agent = {}
    state = _select_semantic_state(state, state_by_agent.get(agent_id))
    state = _semantic_state_in_scope(state, _semantic_state_scope(runtime_context))
    return dict(state) if isinstance(state, dict) else {}


def refresh_public_probe_receipt_projection(
    context, *, agent_id: str
) -> list[dict[str, Any]]:
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return []
    transaction = getattr(runtime_context, "task_runtime_state_transaction", None)
    if callable(transaction):
        with transaction():
            return _refresh_public_probe_receipt_projection_locked(
                runtime_context, agent_id=agent_id
            )
    return _refresh_public_probe_receipt_projection_locked(
        runtime_context, agent_id=agent_id
    )


def _refresh_public_probe_receipt_projection_locked(
    context, *, agent_id: str
) -> list[dict[str, Any]]:
    """Refresh current advisory probe receipts in both operational ledgers.

    Candidate selection is a model-owned state transition rather than a Tool
    observation.  Re-projecting here prevents a previously-current receipt
    from remaining current in checkpoint/compaction state after the candidate
    changes.  These fields remain explicitly advisory and never imply reward
    or canonical acceptance.
    """
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return []
    try:
        from aworld.runners.execution_protocol import load_public_probe_receipts

        receipts = load_public_probe_receipts(runtime_context, agent_id)
    except Exception:
        return []
    projection = [
        {
            key: receipt.get(key)
            for key in (
                "receipt_id",
                "hypothesis_id",
                "probe_kind",
                "selected_candidate_id",
                "tool_execution_succeeded",
                "probe_assessment",
                "stale",
            )
        }
        for receipt in receipts[-4:]
        if isinstance(receipt, Mapping)
    ]

    shared_reader = getattr(runtime_context, "read_task_runtime_state", None)
    shared_writer = getattr(runtime_context, "write_task_runtime_state", None)
    durable_owner = _runtime_registry_owner(runtime_context)
    semantic_working_key = f"{SEMANTIC_PROGRESS_KEY}:{agent_id}"
    state_by_agent = runtime_context.context_info.get(SEMANTIC_PROGRESS_KEY)
    if not isinstance(state_by_agent, dict):
        state_by_agent = {}
    shared_state = (
        shared_reader(agent_id, _SEMANTIC_RUNTIME_KEY)
        if callable(shared_reader)
        else None
    )
    shared_state = _select_semantic_state(
        shared_state,
        _working_state_value(durable_owner, semantic_working_key),
    )
    semantic_state = _select_semantic_state(shared_state, state_by_agent.get(agent_id))
    semantic_state = _semantic_state_in_scope(
        semantic_state, _semantic_state_scope(runtime_context)
    )
    if isinstance(semantic_state, dict):
        semantic_state = dict(semantic_state)
        previous_projection = semantic_state.get("public_probe_receipts") or []
        previous_count = int(semantic_state.get("public_probe_receipt_count", 0) or 0)
        if projection:
            semantic_state["public_probe_receipt_count"] = len(receipts)
            semantic_state["public_probe_receipts"] = projection
        else:
            semantic_state.pop("public_probe_receipt_count", None)
            semantic_state.pop("public_probe_receipts", None)
        if previous_projection != projection or previous_count != len(receipts):
            semantic_state["runtime_revision"] = (
                int(semantic_state.get("runtime_revision", 0) or 0) + 1
            )
        state_by_agent[agent_id] = semantic_state
        runtime_context.context_info[SEMANTIC_PROGRESS_KEY] = state_by_agent
        if callable(shared_writer):
            shared_writer(agent_id, _SEMANTIC_RUNTIME_KEY, semantic_state)
        _project_working_state_value(
            durable_owner, semantic_working_key, semantic_state
        )

    from aworld.core.context.compiler import ADAPTIVE_WORK_STATE_KEY

    context_key = f"{ADAPTIVE_WORK_STATE_KEY}:{agent_id}"
    adaptive_state = (
        shared_reader(agent_id, ADAPTIVE_WORK_STATE_KEY)
        if callable(shared_reader)
        else None
    )
    if not isinstance(adaptive_state, dict):
        adaptive_state = _working_state_value(durable_owner, context_key)
    if not isinstance(adaptive_state, dict):
        adaptive_state = runtime_context.context_info.get(context_key)
    if isinstance(adaptive_state, dict):
        adaptive_state = dict(adaptive_state)
        previous_projection = adaptive_state.get("public_probe_receipts") or []
        previous_count = int(adaptive_state.get("public_probe_receipt_count", 0) or 0)
        if projection:
            adaptive_state["public_probe_receipt_count"] = len(receipts)
            adaptive_state["public_probe_receipts"] = projection
        else:
            adaptive_state.pop("public_probe_receipt_count", None)
            adaptive_state.pop("public_probe_receipts", None)
        if previous_projection != projection or previous_count != len(receipts):
            adaptive_state["revision"] = int(adaptive_state.get("revision", 0) or 0) + 1
        runtime_context.context_info[context_key] = adaptive_state
        if callable(shared_writer):
            shared_writer(agent_id, ADAPTIVE_WORK_STATE_KEY, adaptive_state)
        _project_working_state_value(durable_owner, context_key, adaptive_state)
    return projection


def acknowledge_semantic_checkpoint(context, *, agent_id: str) -> None:
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return
    transaction = getattr(runtime_context, "task_runtime_state_transaction", None)
    if callable(transaction):
        with transaction():
            _acknowledge_semantic_checkpoint_locked(runtime_context, agent_id=agent_id)
        return
    _acknowledge_semantic_checkpoint_locked(runtime_context, agent_id=agent_id)


def _acknowledge_semantic_checkpoint_locked(context, *, agent_id: str) -> None:
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return
    shared_reader = getattr(runtime_context, "read_task_runtime_state", None)
    shared_state = (
        shared_reader(agent_id, _SEMANTIC_RUNTIME_KEY)
        if callable(shared_reader)
        else None
    )
    durable_owner = _runtime_registry_owner(runtime_context)
    semantic_working_key = f"{SEMANTIC_PROGRESS_KEY}:{agent_id}"
    shared_state = _select_semantic_state(
        shared_state,
        _working_state_value(durable_owner, semantic_working_key),
    )
    state_by_agent = runtime_context.context_info.get(SEMANTIC_PROGRESS_KEY)
    if not isinstance(state_by_agent, dict):
        state_by_agent = {}
    state = _select_semantic_state(shared_state, state_by_agent.get(agent_id))
    state = _semantic_state_in_scope(state, _semantic_state_scope(runtime_context))
    if not isinstance(state, dict):
        return
    state["repetition_count"] = 0
    state["result_repetition_count"] = 0
    state["low_information_gain_count"] = 0
    state["durable_stagnation_count"] = 0
    state["no_goal_progress_count"] = 0
    state["recent_operation_result_hashes"] = []
    state["recent_result_hashes"] = []
    state["progress_guard_required"] = False
    state["progress_guard_reset"] = True
    state["runtime_revision"] = int(state.get("runtime_revision", 0) or 0) + 1
    state_by_agent[agent_id] = state
    runtime_context.context_info[SEMANTIC_PROGRESS_KEY] = state_by_agent
    shared_writer = getattr(runtime_context, "write_task_runtime_state", None)
    if callable(shared_writer):
        shared_writer(agent_id, _SEMANTIC_RUNTIME_KEY, state)
    _project_working_state_value(durable_owner, semantic_working_key, state)


def arm_post_tool_progress_watchdog(
    context,
    *,
    tool_name: str,
    agent_id: str,
    actions: list[ActionModel],
    followup_observation: Observation,
    followup_sender: str | None = None,
) -> dict[str, Any] | None:
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return None

    record_semantic_tool_progress(
        runtime_context,
        tool_name=tool_name,
        agent_id=agent_id,
        actions=actions,
        observation=followup_observation,
    )
    from aworld.core.context.compiler import (
        ADAPTIVE_WORK_STATE_KEY,
        semantic_fingerprint,
    )

    shared_reader = getattr(runtime_context, "read_task_runtime_state", None)
    adaptive_work_state = (
        shared_reader(agent_id, ADAPTIVE_WORK_STATE_KEY)
        if callable(shared_reader)
        else None
    )
    if not isinstance(adaptive_work_state, dict):
        adaptive_work_state = runtime_context.context_info.get(
            f"{ADAPTIVE_WORK_STATE_KEY}:{agent_id}"
        )

    continuation_token = semantic_fingerprint(
        {
            "agent_id": agent_id,
            "tool_name": tool_name,
            "tool_call_ids": [
                action.tool_call_id for action in actions if action.tool_call_id
            ],
            "observation": to_serializable(followup_observation),
        }
    )
    state = {
        "armed_at": time.time(),
        "agent_id": agent_id,
        "tool_name": tool_name,
        "followup_sender": followup_sender or tool_name,
        "tool_call_ids": [
            action.tool_call_id for action in actions if action.tool_call_id
        ],
        "followup_observation": to_serializable(followup_observation),
        "actions": to_serializable(actions),
        "retry_count": 0,
        "continuation_token": continuation_token,
        # Bind the bounded ledger to the same immutable continuation token as
        # the Action/Observation pair.  This gives the immediately-following
        # model request read-your-write access even when an event transport
        # copy cannot yet query Amni WorkingState.
        "adaptive_work_state": adaptive_work_state,
    }
    runtime_context.context_info[WATCHDOG_STATE_KEY] = state
    shared_updater = getattr(runtime_context, "update_task_runtime_state", None)
    if callable(shared_updater):

        def retain_recent_turns(current):
            turns = dict(current) if isinstance(current, dict) else {}
            turns[continuation_token] = {
                "actions": state["actions"],
                "followup_observation": state["followup_observation"],
                "adaptive_work_state": state["adaptive_work_state"],
            }
            while len(turns) > 32:
                turns.pop(next(iter(turns)))
            return turns

        shared_updater(agent_id, _POST_TOOL_TURNS_RUNTIME_KEY, retain_recent_turns)
    return state


def post_tool_turn_for_continuation(
    context, *, agent_id: str, continuation_token: str
) -> dict[str, Any] | None:
    """Return the immutable Tool turn that authorized a continuation.

    Event-driven Amni Memory can briefly expose a view in which the assistant
    Tool call or its result has not become query-visible yet.  The continuation
    token is already carried over the event boundary, so bind it to the exact
    Action/Observation pair as a read-your-write fallback instead of asking the
    model to continue from a stale history snapshot.
    """
    if not isinstance(continuation_token, str) or not continuation_token:
        return None
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return None
    shared_reader = getattr(runtime_context, "read_task_runtime_state", None)
    turns = (
        shared_reader(agent_id, _POST_TOOL_TURNS_RUNTIME_KEY)
        if callable(shared_reader)
        else None
    )
    if isinstance(turns, dict):
        turn = turns.get(continuation_token)
        if isinstance(turn, dict):
            return dict(turn)
    state = runtime_context.context_info.get(WATCHDOG_STATE_KEY)
    if (
        isinstance(state, dict)
        and state.get("agent_id") == agent_id
        and state.get("continuation_token") == continuation_token
    ):
        return {
            "actions": state.get("actions") or [],
            "followup_observation": state.get("followup_observation") or {},
            "adaptive_work_state": state.get("adaptive_work_state"),
        }
    return None


def mark_post_tool_progress_llm_started(context, *, agent_id: str) -> float | None:
    runtime_context = _runtime_context(context)
    if runtime_context is None:
        return None

    state = runtime_context.context_info.get(WATCHDOG_STATE_KEY)
    if not isinstance(state, dict) or state.get("agent_id") != agent_id:
        return None

    latency_seconds = max(time.time() - float(state.get("armed_at") or 0.0), 0.0)
    metrics = _metrics_dict(runtime_context)
    latencies = list(metrics.get("tool_success_to_next_llm_latencies") or [])
    latencies.append(round(latency_seconds, 3))
    metrics["tool_success_to_next_llm_latencies"] = latencies
    metrics["tool_success_to_next_llm_count"] = (
        int(metrics.get("tool_success_to_next_llm_count", 0) or 0) + 1
    )
    runtime_context.context_info[WATCHDOG_METRICS_KEY] = metrics
    runtime_context.context_info.pop(WATCHDOG_STATE_KEY, None)
    return latency_seconds
