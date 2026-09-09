"""Run one instruction against an already-running Docker container with AWorld.

Example:
    docker run -d --rm --name terminal-task IMAGE sleep infinity
    LLM_MODEL_NAME=... LLM_API_KEY=... LLM_BASE_URL=... \
      python examples/sandbox/docker_terminal_bench.py \
        --container terminal-task \
        --instruction /path/to/instruction.md \
        --output-dir ./artifacts/terminal-task \
        --allowed-directory /workspace

The caller owns container startup, verifier execution, and container removal.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath


# Example scripts must exercise the worktree that contains them, not whichever
# editable AWorld installation happens to be first in the user's environment.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


SYSTEM_PROMPT = (
    "You are solving a tool-using benchmark with an attached Docker container. "
    "Use the provided tools to inspect the real task environment, gather any required "
    "evidence, implement the solution when files must change, and perform focused "
    "verification before returning a final answer."
)

PYTHON_FUNCTION_VERIFIER_TEMPLATE = """
import importlib.util
import inspect
import sys
import types

class _Raises:
    def __init__(self, expected):
        self.expected = expected
    def __enter__(self):
        return self
    def __exit__(self, exception_type, exception, traceback):
        if exception_type is None:
            raise AssertionError("expected exception was not raised")
        return issubclass(exception_type, self.expected)

class _Mark:
    def __getattr__(self, name):
        def marker(*args, **kwargs):
            if len(args) == 1 and callable(args[0]) and not kwargs:
                return args[0]
            return lambda value: value
        return marker

pytest = types.ModuleType("pytest")
pytest.fail = lambda message="": (_ for _ in ()).throw(AssertionError(message))
pytest.raises = lambda expected: _Raises(expected)
pytest.mark = _Mark()
sys.modules.setdefault("pytest", pytest)

spec = importlib.util.spec_from_file_location("terminal_bench_verifier", {test_path!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
tests = [
    value
    for name, value in sorted(vars(module).items())
    if name.startswith("test_") and callable(value)
]
for class_name, class_value in sorted(vars(module).items()):
    if not class_name.startswith("Test") or not inspect.isclass(class_value):
        continue
    instance = class_value()
    tests.extend(
        getattr(instance, name)
        for name in sorted(dir(instance))
        if name.startswith("test_") and callable(getattr(instance, name))
    )
if not tests:
    raise RuntimeError("verifier contains no test_* functions")
unsupported = [test.__name__ for test in tests if inspect.signature(test).parameters]
if unsupported:
    raise RuntimeError("python-functions verifier does not support fixtures: " + ",".join(unsupported))
for test in tests:
    test()
""".strip()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, payload) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode(
        "utf-8"
    )
    path.write_bytes(encoded)
    return _sha256_bytes(encoded)


def _llm_calls_digest(calls: list) -> str:
    encoded = json.dumps(
        calls,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _llm_calls_identity_digest(calls: list) -> str:
    """Hash a multi-Context capture without treating branch merge order as data loss."""
    normalized = []
    identities = set()
    for call in calls:
        if not isinstance(call, dict):
            raise ValueError("LLM call capture entries must be objects")
        identity = next(
            (
                (field, value)
                for field in ("request_id", "call_id")
                if isinstance((value := call.get(field)), str) and value
            ),
            None,
        )
        if identity is None or identity in identities:
            raise ValueError("LLM call capture requires unique stable identities")
        identities.add(identity)
        normalized.append({"identity": list(identity), "call": call})
    normalized.sort(key=lambda item: tuple(item["identity"]))
    return _llm_calls_digest(normalized)


def run_python_function_verifier_sidecar(
    *,
    docker_binary: str,
    container: str,
    test_path: str,
    timeout: float,
    env_names: tuple[str, ...] = (),
    artifact_paths: tuple[str, ...] = (),
    scratch_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run zero-argument verifier functions in a disposable task snapshot.

    A temporary image captures the complete post-agent filesystem, so verifier
    correctness does not depend on benchmark-specific artifact declarations.
    Bind-mounted verifier files are copied separately because ``docker commit``
    excludes mount contents. The verifier container has no network and is always
    removed.
    """
    if not test_path.startswith("/") or not test_path.endswith("/test_outputs.py"):
        raise ValueError("test_path must be an absolute test_outputs.py path")
    if timeout <= 0:
        raise ValueError("verifier timeout must be positive")
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in env_names):
        raise ValueError("verifier environment names must be safe identifiers")
    if any(
        not isinstance(path, str)
        or not PurePosixPath(path).is_absolute()
        or path == "/"
        for path in artifact_paths
    ):
        raise ValueError("verifier artifact paths must be narrow absolute paths")
    deadline = time.monotonic() + timeout

    def remaining() -> float:
        value = deadline - time.monotonic()
        if value <= 0:
            raise subprocess.TimeoutExpired("python-functions-sidecar", timeout)
        return value

    if scratch_root is not None:
        scratch_root = scratch_root.resolve()
        scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="aworld-verifier-",
        dir=str(scratch_root) if scratch_root is not None else None,
    ) as temporary:
        root = Path(temporary)
        verifier = root / "verifier"
        verifier.mkdir()
        verifier_directory = posixpath.dirname(test_path)
        copy_tests = subprocess.run(
            [docker_binary, "cp", f"{container}:{verifier_directory}/.", str(verifier)],
            capture_output=True,
            text=True,
            timeout=remaining(),
            check=False,
        )
        if copy_tests.returncode != 0:
            return copy_tests
        commit = subprocess.run(
            [docker_binary, "commit", "--no-pause", container],
            capture_output=True,
            text=True,
            timeout=remaining(),
            check=False,
        )
        if commit.returncode != 0:
            return commit
        image_match = re.search(r"sha256:[0-9a-fA-F]{64}", commit.stdout or "")
        if image_match is None:
            return subprocess.CompletedProcess(
                commit.args,
                1,
                stdout=commit.stdout,
                stderr=(commit.stderr or "")
                + "\ndocker commit did not return an immutable image id",
            )
        image_id = image_match.group(0)
        try:
            command = [
                docker_binary,
                "run",
                "--rm",
                "--network",
                "none",
                "--tmpfs",
                "/tmp:rw,nosuid,size=64m",
            ]
            for name in env_names:
                command.extend(["--env", name])
            command.extend(
                [
                    "-v",
                    f"{verifier.resolve()}:{verifier_directory}:ro",
                    "--entrypoint",
                    "python3",
                    image_id,
                    "-B",
                    "-c",
                    PYTHON_FUNCTION_VERIFIER_TEMPLATE.format(test_path=test_path),
                ]
            )
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=remaining(),
                check=False,
            )
        finally:
            subprocess.run(
                [docker_binary, "image", "rm", "-f", image_id],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )


def _context_lifecycle_evidence(agent) -> dict:
    """Export typed, privacy-safe lifecycle evidence for canary verification."""
    from aworld.core.context.compiler import canonical_json_hash
    from aworld.core.context.compiler.lifecycle import ContextLifecycleState

    context = getattr(agent, "context", None)
    state = getattr(context, "context_lifecycle_state", None)
    if not isinstance(state, ContextLifecycleState):
        return {
            "schema_version": "aworld.context.lifecycle-evidence.v1",
            "status": "unavailable",
            "reason_code": "typed_lifecycle_state_unavailable",
        }
    projection = {
        "session_id_hash": canonical_json_hash({"session_id": state.session_id}),
        "session_epoch": state.session_epoch,
        "task_epoch": state.task_epoch,
        "turn_epoch": state.turn_epoch,
        "branch_id_hash": canonical_json_hash({"branch_id": state.branch_id}),
        "checkpoint_revision": state.checkpoint_revision,
    }
    return {
        "schema_version": "aworld.context.lifecycle-evidence.v1",
        "status": "available",
        "state": projection,
        "state_hash": canonical_json_hash(projection),
    }


def _completion_contract_evidence(agent) -> dict:
    context = getattr(agent, "context", None)
    assessment = getattr(context, "_completion_assessment", None)
    contract = getattr(context, "completion_contract", None)
    if contract is None:
        return {
            "schema_version": "aworld.completion-contract-evidence/v1",
            "status": "not_configured",
        }
    return {
        "schema_version": "aworld.completion-contract-evidence/v1",
        "status": "assessed" if assessment is not None else "configured",
        "mode": str(getattr(context, "completion_mode", "off").value),
        "required_artifact_count": len(contract.required_artifacts),
        "validation_command_ids": [
            command.command_id for command in contract.validation_commands
        ],
        "required_final_evidence": list(contract.required_final_evidence),
        "assessment": (
            {
                "status": assessment.status.value,
                "reason_codes": list(assessment.reason_codes),
                "repair_attempt": assessment.repair_attempt,
            }
            if assessment is not None
            else None
        ),
    }


def _semantic_progress_evidence(agent) -> dict:
    """Export bounded framework progress counters without Tool payloads."""
    context = getattr(agent, "context", None)
    event_manager = getattr(context, "event_manager", None)
    runtime_context = (
        getattr(event_manager, "context", None) if event_manager is not None else None
    ) or context
    info = getattr(runtime_context, "context_info", None)
    if info is None or not callable(getattr(info, "get", None)):
        return {
            "schema_version": "aworld.context.semantic-progress-evidence/v1",
            "status": "unavailable",
            "reason_code": "runtime_context_info_unavailable",
        }
    raw_metrics = info.get("post_tool_progress_metrics")
    raw_agents = info.get("context_semantic_progress")
    allowed_counts = {
        "semantic_tool_observation_count",
        "repeated_operation_count",
        "low_information_gain_count",
        "task_artifact_change_count",
        "goal_progress_count",
        "no_goal_progress_observation_count",
        "sandbox_rollback_count",
        "implicit_artifact_loss_count",
        "watchdog_trigger_count",
        "sanitized_history_retry_count",
        "tool_success_to_next_llm_count",
        "adaptive_checkpoint_count",
        "adaptive_no_progress_checkpoint_count",
        "adaptive_escalation_count",
        "adaptive_escalation_level_max",
        "adaptive_goal_progress_reset_count",
        "agent_step_budget_extension_count",
        "agent_step_budget_extended_steps",
        "agent_step_budget_effective_limit",
        "agent_step_budget_hard_limit",
    }
    counts = {
        key: int(value)
        for key, value in sorted((raw_metrics or {}).items())
        if key in allowed_counts
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    }
    agent_id_getter = getattr(agent, "id", None)
    agent_id = agent_id_getter() if callable(agent_id_getter) else None
    get_agent_step = getattr(runtime_context, "get_agent_step", None)
    if agent_id and callable(get_agent_step):
        step_count = get_agent_step(agent_id)
        if isinstance(step_count, int) and not isinstance(step_count, bool):
            counts["agent_step_count"] = max(step_count, 0)
        if info.get(f"agent_loop_budget_exhausted:{agent_id}") is not None:
            counts["agent_loop_budget_exhausted_count"] = 1
        budget_receipt = info.get(f"agent_step_budget:{agent_id}")
        if (
            isinstance(budget_receipt, dict)
            and budget_receipt.get("schema_version")
            == "aworld.context.elastic-step-budget/v1"
        ):
            for source, target in (
                ("extension_count", "agent_step_budget_extension_count"),
                ("total_extended_steps", "agent_step_budget_extended_steps"),
                ("effective_limit", "agent_step_budget_effective_limit"),
                ("hard_limit", "agent_step_budget_hard_limit"),
            ):
                value = budget_receipt.get(source)
                if (
                    isinstance(value, int)
                    and not isinstance(value, bool)
                    and value >= 0
                ):
                    counts[target] = value
    configured_max_steps = getattr(agent, "max_loop_steps", None)
    if (
        isinstance(configured_max_steps, int)
        and not isinstance(configured_max_steps, bool)
        and configured_max_steps > 0
    ):
        counts["configured_max_steps"] = configured_max_steps
    agent_rows = []
    if isinstance(raw_agents, dict):
        for agent_id, state in sorted(
            raw_agents.items(), key=lambda item: str(item[0])
        ):
            if not isinstance(state, dict):
                continue
            row = {
                "agent_id_hash": _sha256_bytes(str(agent_id).encode("utf-8")),
            }
            for key in (
                "repetition_count",
                "low_information_gain_count",
                "no_goal_progress_count",
                "goal_progress_count",
                "last_goal_progress_agent_step",
            ):
                value = state.get(key)
                if (
                    isinstance(value, int)
                    and not isinstance(value, bool)
                    and value >= 0
                ):
                    row[key] = value
            for key in ("goal_progress", "artifact_advanced", "completion_advanced"):
                value = state.get(key)
                if isinstance(value, bool):
                    row[key] = value
            for key in ("operation_hash", "result_hash"):
                value = state.get(key)
                if isinstance(value, str) and re.fullmatch(
                    r"sha256:[0-9a-f]{64}", value
                ):
                    row[key] = value
            agent_rows.append(row)
    return {
        "schema_version": "aworld.context.semantic-progress-evidence/v1",
        "status": "available",
        "counts": counts,
        "agents": agent_rows,
    }


async def _configure_benchmark_completion_contract(
    agent,
    sandbox,
    variant: dict,
    *,
    required_artifacts: tuple[str, ...] = (),
    completion_env_names: tuple[str, ...] = (),
    verifier_mode: str = "packaged",
    verifier_scratch_root: Path | None = None,
) -> None:
    """Construct a task-independent contract from the mounted verifier boundary."""
    mode = str(variant.get("context_compiler", {}).get("completion_contract", "off"))
    if mode == "off":
        return
    from aworld.core.context.compiler import (
        ArtifactEvidence,
        ArtifactRequirement,
        CompletionContract,
        CompletionMode,
        SelfCheckEvidence,
        ValidationCommand,
        canonical_json_hash,
    )
    from datetime import datetime, timezone

    validator_path = None
    for candidate in ("/verifier/test.sh", "/tests/test.sh"):
        inspected = await sandbox.run_validation(
            ("test", "-f", candidate),
            cwd="/",
            timeout=10,
        )
        if inspected.returncode == 0:
            validator_path = candidate
            break
    if verifier_mode not in {"packaged", "python-functions"}:
        raise ValueError("unsupported completion verifier mode")
    if validator_path is None:
        commands = ()
    elif verifier_mode == "packaged":
        commands = (
            ValidationCommand(
                command_id="packaged-verifier",
                argv=("/bin/bash", validator_path),
                cwd=sandbox.container_workdir,
                timeout_seconds=900,
            ),
        )
    else:
        test_path = validator_path.rsplit("/", 1)[0] + "/test_outputs.py"
        commands = (
            ValidationCommand(
                command_id="python-functions-verifier",
                argv=(
                    "python3",
                    "-c",
                    PYTHON_FUNCTION_VERIFIER_TEMPLATE.format(test_path=test_path),
                ),
                cwd=sandbox.container_workdir,
                timeout_seconds=900,
            ),
        )
    if not commands:
        raise RuntimeError(
            "completion_contract requires a mounted /tests/test.sh or /verifier/test.sh"
        )
    artifact_requirements = tuple(
        ArtifactRequirement(
            requirement_id=f"task-artifact-{index:03d}",
            path=path,
        )
        for index, path in enumerate(required_artifacts, start=1)
    )
    contract = CompletionContract(
        required_artifacts=artifact_requirements,
        immutable_inputs=(),
        validation_commands=commands,
        max_evidence_age_seconds=900,
        required_final_evidence=(),
        max_repairs=1,
    )

    async def resolve(context, configured_contract) -> None:
        for requirement in configured_contract.required_artifacts:
            result = await sandbox.run_validation(
                ("test", "-e", requirement.path),
                timeout=30,
            )
            context.record_completion_artifact(
                ArtifactEvidence(
                    requirement_id=requirement.requirement_id,
                    exists=result.returncode == 0,
                    content_hash=None,
                    media_type=None,
                    observed_at=datetime.now(timezone.utc),
                )
            )
        for command in configured_contract.validation_commands:
            timed_out = False
            try:
                if command.command_id == "python-functions-verifier":
                    result = await asyncio.to_thread(
                        run_python_function_verifier_sidecar,
                        docker_binary=sandbox.docker_binary,
                        container=sandbox.container,
                        test_path=test_path,
                        timeout=command.timeout_seconds,
                        env_names=completion_env_names,
                        artifact_paths=required_artifacts,
                        scratch_root=verifier_scratch_root,
                    )
                else:
                    result = await sandbox.run_validation(
                        command.argv,
                        cwd=command.cwd,
                        timeout=command.timeout_seconds,
                        env_names=completion_env_names,
                    )
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                result = subprocess.CompletedProcess(
                    exc.cmd,
                    124,
                    stdout=exc.stdout or "",
                    stderr=exc.stderr or "",
                )
            context.record_completion_self_check(
                SelfCheckEvidence(
                    command_id=command.command_id,
                    exit_code=result.returncode,
                    output_hash=canonical_json_hash(
                        {
                            "return_code": result.returncode,
                            "timed_out": timed_out,
                            "stdout": result.stdout,
                            "stderr": result.stderr,
                        }
                    ),
                    observed_at=datetime.now(timezone.utc),
                )
            )

    agent.configure_completion_contract(
        contract,
        mode=CompletionMode(mode),
        evidence_resolver=resolve,
    )


def _export_context_tool_output_artifacts(agent, output_dir: Path) -> list[dict]:
    """Persist checksum-bound Context artifacts beside the raw trajectory."""
    context = getattr(agent, "context", None)
    if context is None:
        return []
    records = context.get_tool_output_records()
    destination = output_dir / "tool-output-artifacts"
    exported: dict[str, dict] = {}
    for record in records:
        artifact = getattr(record, "artifact", None)
        if artifact is None or artifact.ref in exported:
            continue
        data = context.read_tool_output_artifact(artifact.ref)
        digest = _sha256_bytes(data)
        content_hash = f"sha256:{digest}"
        if artifact.content_hash != content_hash or artifact.byte_count != len(data):
            raise RuntimeError("context_tool_output_artifact_mismatch")
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / f"context-{digest}.bin"
        if target.exists() and target.read_bytes() != data:
            raise RuntimeError("context_tool_output_artifact_export_collision")
        target.write_bytes(data)
        exported[artifact.ref] = {
            "artifact_ref_hash": f"sha256:{_sha256_bytes(artifact.ref.encode('utf-8'))}",
            "content_hash": content_hash,
            "byte_count": len(data),
            "path": str(target.relative_to(output_dir)),
        }
    return [exported[key] for key in sorted(exported)]


def _trajectory_action_results(raw_trajectory) -> list[dict]:
    """Return typed ActionResult payloads without interpreting Tool text."""
    found: list[dict] = []

    def visit(value) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        action_results = value.get("action_result")
        if isinstance(action_results, list):
            found.extend(item for item in action_results if isinstance(item, dict))
        for key, item in value.items():
            if key != "action_result":
                visit(item)

    visit(raw_trajectory)
    return found


def _export_upstream_tool_output_artifacts(
    raw_trajectory, output_dir: Path
) -> list[dict]:
    """Bind sandbox-owned output artifacts into the immutable run manifest.

    The sandbox Tool server is the authority for these files.  We accept only
    typed upstream receipts already attached at the Tool boundary, require the
    reference to resolve directly below this run's artifact directory, and
    re-read both length and checksum before publishing evidence.
    """
    destination = (output_dir / "tool-output-artifacts").resolve()
    exported: dict[str, dict] = {}
    for result in _trajectory_action_results(raw_trajectory):
        metadata = result.get("metadata")
        policy = (
            metadata.get("tool_output_policy") if isinstance(metadata, dict) else None
        )
        upstream = (
            policy.get("upstream_artifacts") if isinstance(policy, dict) else None
        )
        if not isinstance(upstream, list):
            continue
        for receipt in upstream:
            if not isinstance(receipt, dict):
                raise RuntimeError("upstream_tool_output_artifact_receipt_invalid")
            ref = receipt.get("ref")
            content_hash = receipt.get("content_hash")
            byte_count = receipt.get("byte_count")
            if (
                not isinstance(ref, str)
                or not isinstance(content_hash, str)
                or not content_hash.startswith("sha256:")
                or len(content_hash) != 71
                or isinstance(byte_count, bool)
                or not isinstance(byte_count, int)
                or byte_count < 0
            ):
                raise RuntimeError("upstream_tool_output_artifact_receipt_invalid")
            artifact = Path(ref).expanduser().resolve()
            if artifact.parent != destination or not artifact.is_file():
                raise RuntimeError("upstream_tool_output_artifact_outside_run")
            digest = f"sha256:{_sha256_bytes(artifact.read_bytes())}"
            if artifact.stat().st_size != byte_count or digest != content_hash:
                raise RuntimeError("upstream_tool_output_artifact_mismatch")
            evidence = {
                "artifact_ref_hash": f"sha256:{_sha256_bytes(ref.encode('utf-8'))}",
                "content_hash": content_hash,
                "byte_count": byte_count,
                "path": str(artifact.relative_to(output_dir.resolve())),
            }
            previous = exported.get(ref)
            if previous is not None and previous != evidence:
                raise RuntimeError("upstream_tool_output_artifact_receipt_conflict")
            exported[ref] = evidence
    return [exported[key] for key in sorted(exported)]


def _resolve_llm_call_capture(
    response,
    agent,
    *,
    journal_calls: list | None = None,
) -> tuple[list, str, dict]:
    """Preserve blocked-call evidence when TaskResponse propagation is incomplete."""
    response_calls = list(getattr(response, "llm_calls", None) or [])
    live_context = getattr(agent, "context", None)
    live_getter = getattr(live_context, "get_llm_calls", None)
    live_calls = list(live_getter() or []) if callable(live_getter) else []
    continuity = {
        "task_response_count": len(response_calls),
        "live_context_count": len(live_calls),
        "counts_match": len(response_calls) == len(live_calls),
        "task_response_sha256": _llm_calls_digest(response_calls),
        "live_context_sha256": _llm_calls_digest(live_calls),
        "snapshots_match": _llm_calls_digest(response_calls)
        == _llm_calls_digest(live_calls),
    }

    if journal_calls is not None:

        def identities(calls: list) -> set[tuple[str, str]] | None:
            found: set[tuple[str, str]] = set()
            for call in calls:
                if not isinstance(call, dict):
                    return None
                identity = next(
                    (
                        (field, value)
                        for field in ("request_id", "call_id")
                        if isinstance((value := call.get(field)), str) and value
                    ),
                    None,
                )
                if identity is None or identity in found:
                    return None
                found.add(identity)
            return found

        journal_identities = identities(journal_calls)
        response_identities = identities(response_calls)
        live_identities = identities(live_calls)
        response_covered = bool(
            journal_identities is not None
            and response_identities is not None
            and response_identities.issubset(journal_identities)
        )
        live_covered = bool(
            journal_identities is not None
            and live_identities is not None
            and live_identities.issubset(journal_identities)
        )
        journal_superset = bool(
            journal_calls
            and response_covered
            and live_covered
            and journal_identities is not None
        )
        continuity.update(
            {
                "journal_count": len(journal_calls),
                "journal_sha256": _llm_calls_digest(journal_calls),
                "task_response_journal_identity_coverage": response_covered,
                "live_context_journal_identity_coverage": live_covered,
                "journal_superset": journal_superset,
            }
        )
        if journal_superset:
            continuity["reconciled_count"] = len(journal_calls)
            continuity["reconciled_sha256"] = _llm_calls_digest(journal_calls)
            return list(journal_calls), "finalized_append_only_journal", continuity
    if response_calls and live_calls:
        reconciled: list = []
        index_by_identity: dict[tuple[str, str], int] = {}

        def identity(call) -> tuple[str, str] | None:
            if not isinstance(call, dict):
                return None
            for field in ("request_id", "call_id"):
                value = call.get(field)
                if isinstance(value, str) and value:
                    return field, value
            return None

        for call in (*response_calls, *live_calls):
            call_identity = identity(call)
            if call_identity is not None and call_identity in index_by_identity:
                reconciled[index_by_identity[call_identity]] = call
            else:
                if call_identity is not None:
                    index_by_identity[call_identity] = len(reconciled)
                reconciled.append(call)
        continuity["reconciled_count"] = len(reconciled)
        continuity["reconciled_sha256"] = _llm_calls_digest(reconciled)
        if continuity["snapshots_match"]:
            return reconciled, "task_response", continuity
        return reconciled, "reconciled_task_response_live_context", continuity
    if response_calls:
        continuity["reconciled_count"] = len(response_calls)
        continuity["reconciled_sha256"] = _llm_calls_digest(response_calls)
        return response_calls, "task_response", continuity
    if live_calls:
        continuity["reconciled_count"] = len(live_calls)
        continuity["reconciled_sha256"] = _llm_calls_digest(live_calls)
        return live_calls, "live_context_fallback", continuity
    continuity["reconciled_count"] = 0
    continuity["reconciled_sha256"] = _llm_calls_digest([])
    return [], "unavailable", continuity


def _is_provider_bound_call(call) -> bool:
    if not isinstance(call, dict) or call.get("provider_invoked") is not True:
        return False
    provider_request = call.get("provider_request") or {}
    if (
        provider_request.get("capture_stage") == "provider_prepared"
        and provider_request.get("fidelity") == "provider_prepared"
        and isinstance(provider_request.get("payload"), dict)
    ):
        return True
    rollout = call.get("context_rollout") or {}
    lowering = rollout.get("provider_lowering") or {}
    provider_request = lowering.get("provider_request") or {}
    return (
        rollout.get("candidate_applied") is True
        and provider_request.get("capture_stage") == "provider_prepared"
        and provider_request.get("fidelity") == "provider_prepared"
    )


def _load_variant(path: Path | None) -> dict:
    if path is None:
        return {
            "schema_version": "aworld.context-eval-variant/v1",
            "name": "baseline",
            "agent_memory_config": {},
            "context_compiler": {},
            "docker_output_policy": {},
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    allowed = {
        "schema_version",
        "name",
        "agent_memory_config",
        "context_compiler",
        "docker_output_policy",
    }
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise ValueError(
            "Context evaluation variants may only change context/output policy; "
            f"unexpected fields: {', '.join(unexpected)}"
        )
    if not payload.get("name"):
        raise ValueError("variant config requires a non-empty name")
    payload.setdefault("agent_memory_config", {})
    payload.setdefault("context_compiler", {})
    payload.setdefault("docker_output_policy", {})
    from aworld.evaluations.context_benefit import ContextVariant

    settings = {
        key: payload[key]
        for key in ("agent_memory_config", "context_compiler", "docker_output_policy")
    }
    try:
        ContextVariant.build(str(payload["name"]), settings)
    except (TypeError, ValueError) as exc:
        message = str(exc)
        if message == "context_compiler variant contains non-Context fields":
            message = "context_compiler contains unsupported fields"
        raise ValueError(f"Variant {message}") from exc
    return payload


def _load_task_skills(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    skills_directory = path.resolve()
    if not skills_directory.is_dir():
        raise ValueError(f"Skills directory does not exist: {skills_directory}")
    from aworld.skills.compat_provider import build_compat_registry

    registry = build_compat_registry(skills_directory)
    descriptors = registry.list_descriptors()
    if not descriptors:
        raise ValueError(f"Skills directory contains no Skills: {skills_directory}")
    return {
        descriptor.skill_name: registry.build_skill_config(descriptor.skill_id)
        for descriptor in descriptors
    }


def load_external_mcp_config(path: Path | None) -> tuple[dict, dict]:
    """Load an invariant external Tool profile without persisting its values."""
    payload: dict = {"mcpServers": {}}
    status = "disabled"
    if path is not None:
        resolved = path.resolve()
        if not resolved.is_file():
            raise ValueError(f"MCP config does not exist: {resolved}")
        loaded = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or set(loaded) != {"mcpServers"}:
            raise ValueError(
                "MCP config must contain only a top-level mcpServers object"
            )
        servers = loaded.get("mcpServers")
        if not isinstance(servers, dict) or not servers:
            raise ValueError("MCP config requires at least one server")
        for name, config in servers.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
                raise ValueError(f"Unsafe MCP server name: {name!r}")
            if name == "docker":
                raise ValueError(
                    "MCP server name 'docker' is reserved by DockerSandbox"
                )
            if not isinstance(config, dict):
                raise ValueError(f"MCP server {name!r} must be an object")
        payload = loaded
        status = "enabled"
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    evidence = {
        "status": status,
        "config_sha256": _sha256_bytes(canonical),
        "server_names": sorted(payload["mcpServers"]),
    }
    return payload, evidence


def _agent_loop_budget(
    max_steps: int, context_compiler: dict | None = None
) -> dict[str, int]:
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps <= 0:
        raise ValueError("--max-steps must be positive")
    # BaseAgent owns the actual loop guard through max_loop_steps. AgentConfig's
    # max_steps is retained for compatibility but does not bind that guard.
    budget = {"max_loop_steps": max_steps}
    compiler = context_compiler or {}
    if compiler.get("elastic_step_budget") is True:
        extension_steps = compiler.get("step_budget_extension_steps", 40)
        hard_limit = compiler.get("step_budget_hard_limit", max_steps + 120)
        progress_window = compiler.get("step_budget_recent_progress_window", 20)
        if (
            isinstance(extension_steps, bool)
            or not isinstance(extension_steps, int)
            or extension_steps <= 0
        ):
            raise ValueError("step_budget_extension_steps must be positive")
        if (
            isinstance(hard_limit, bool)
            or not isinstance(hard_limit, int)
            or hard_limit <= max_steps
        ):
            raise ValueError("step_budget_hard_limit must exceed --max-steps")
        if (
            isinstance(progress_window, bool)
            or not isinstance(progress_window, int)
            or progress_window <= 0
        ):
            raise ValueError("step_budget_recent_progress_window must be positive")
        budget.update(
            {
                "loop_step_extension_steps": extension_steps,
                "max_extended_loop_steps": hard_limit,
                "loop_step_progress_window": progress_window,
            }
        )
    return budget


def _git_snapshot() -> dict:
    runtime_pathspecs = (
        "aworld",
        "examples/evaluations",
        "examples/sandbox",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    status_text = status.stdout if status.returncode == 0 else ""
    tracked_diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD", "--", *runtime_pathspecs],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    untracked = subprocess.run(
        [
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            *runtime_pathspecs,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    untracked_digest = hashlib.sha256()
    untracked_count = 0
    if untracked.returncode == 0:
        for raw_path in sorted(filter(None, untracked.stdout.split(b"\0"))):
            try:
                relative = raw_path.decode("utf-8")
                content = (REPO_ROOT / relative).read_bytes()
            except (OSError, UnicodeDecodeError):
                continue
            untracked_digest.update(raw_path)
            untracked_digest.update(b"\0")
            untracked_digest.update(content)
            untracked_digest.update(b"\0")
            untracked_count += 1
    tracked_diff_sha256 = "sha256:" + _sha256_bytes(
        tracked_diff.stdout if tracked_diff.returncode == 0 else b""
    )
    untracked_source_sha256 = "sha256:" + untracked_digest.hexdigest()
    source_fingerprint = "sha256:" + _sha256_bytes(
        json.dumps(
            {
                "commit": commit.stdout.strip() if commit.returncode == 0 else None,
                "tracked_runtime_diff_sha256": tracked_diff_sha256,
                "untracked_runtime_source_sha256": untracked_source_sha256,
                "untracked_runtime_source_count": untracked_count,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return {
        "source_root": str(REPO_ROOT),
        "commit": commit.stdout.strip() if commit.returncode == 0 else None,
        "dirty": bool(status_text.strip()),
        "status_sha256": _sha256_bytes(status_text.encode("utf-8")),
        "tracked_runtime_diff_sha256": tracked_diff_sha256,
        "untracked_runtime_source_sha256": untracked_source_sha256,
        "untracked_runtime_source_count": untracked_count,
        "source_fingerprint": source_fingerprint,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument("--instruction", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--workdir")
    parser.add_argument(
        "--allowed-directory",
        action="append",
        dest="allowed_directories",
        help="Allowed absolute container path; may be specified more than once.",
    )
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument(
        "--llm-max-attempts",
        type=int,
        default=3,
        help="Invariant per-call transport attempt budget shared by paired variants.",
    )
    parser.add_argument(
        "--llm-retry-delay-sec",
        type=float,
        default=10.0,
        help="Invariant base delay for exponential LLM transport retry backoff.",
    )
    parser.add_argument("--model-seed", type=int)
    parser.add_argument(
        "--variant-config",
        type=Path,
        help="JSON variant that may change context/output policy only, never task prompts or answers.",
    )
    parser.add_argument(
        "--skills-directory",
        type=Path,
        help=(
            "Task-provided Skills directory. The harness mounts the same directory in the "
            "task container at /aworld-skills."
        ),
    )
    parser.add_argument(
        "--mcp-config",
        type=Path,
        help=(
            "Invariant external MCP Tool profile shared by every paired variant. "
            "Only its checksum and server names are persisted."
        ),
    )
    parser.add_argument(
        "--allow-missing-provider-trace",
        action="store_true",
        help="Compatibility escape hatch; context evaluations should keep the provider trace hard gate enabled.",
    )
    parser.add_argument(
        "--deterministic-capture-provider",
        action="store_true",
        help=(
            "Use a local one-response provider for structural capture validation only. "
            "This mode requires --allow-missing-provider-trace and must not be used "
            "as benchmark quality or Reward evidence."
        ),
    )
    parser.add_argument(
        "--required-artifact",
        action="append",
        default=[],
        help="Task-declared artifact path to include in the runtime completion contract.",
    )
    parser.add_argument(
        "--completion-env",
        action="append",
        default=[],
        help="Name of an inherited environment variable needed by the validator.",
    )
    parser.add_argument(
        "--completion-verifier-mode",
        choices=("packaged", "python-functions"),
        default="packaged",
        help="Use the same verifier execution mode for the runtime completion contract.",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    started_at = time.time()
    if args.llm_max_attempts < 1:
        raise ValueError("--llm-max-attempts must be positive")
    if args.llm_retry_delay_sec < 0:
        raise ValueError("--llm-retry-delay-sec must be non-negative")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    log_dir = args.output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    os.environ["AWORLD_LOG_PATH"] = str(log_dir.resolve())
    os.environ["AWORLD_TRAJECTORY_FORMAT"] = "dual"
    os.environ["AWORLD_LLM_CALL_JOURNAL_PATH"] = str(
        (args.output_dir / "llm_calls.journal.jsonl").resolve()
    )
    os.environ["AWORLD_TOOL_ACTION_JOURNAL_PATH"] = str(
        (args.output_dir / "tool_actions.journal.jsonl").resolve()
    )
    # A paired rollout must not scan or mutate state left by another task/run.
    # This isolates framework persistence only; task instructions, container,
    # Tools and verifier remain byte-identical across variants.
    os.environ["AWORLD_MEMORY_ROOT"] = str((args.output_dir / "memory").resolve())
    os.environ["DB_PATH"] = str((args.output_dir / "amni_context.db").resolve())

    # Import after AWORLD_LOG_PATH is configured so trajectory.log is placed
    # beside the canonical TaskResponse artifacts.
    from aworld.agents.llm_agent import Agent
    from aworld.config.conf import AgentConfig, AgentMemoryConfig
    from aworld.runner import Runners
    from aworld.sandbox import DockerSandbox
    from aworld.core.llm_call_journal import read_llm_call_journal
    from aworld.core.tool_action_journal import read_tool_action_journal
    from aworld.utils.serialized_util import to_serializable

    if args.deterministic_capture_provider and not args.allow_missing_provider_trace:
        raise RuntimeError(
            "--deterministic-capture-provider requires --allow-missing-provider-trace"
        )
    if args.deterministic_capture_provider and args.variant_config is not None:
        raise RuntimeError(
            "deterministic capture validation must use the baseline/off variant"
        )

    model_name = (
        "aworld-deterministic-capture-v1"
        if args.deterministic_capture_provider
        else os.environ.get("LLM_MODEL_NAME")
    )
    api_key = (
        "not-used"
        if args.deterministic_capture_provider
        else os.environ.get("LLM_API_KEY")
    )
    if not model_name or not api_key:
        raise RuntimeError("LLM_MODEL_NAME and LLM_API_KEY must be set")
    provider_name = (
        "deterministic_capture"
        if args.deterministic_capture_provider
        else os.environ.get("LLM_PROVIDER", "openai")
    )

    instruction = args.instruction.read_text(encoding="utf-8")
    variant = _load_variant(args.variant_config)
    skill_configs = _load_task_skills(args.skills_directory)
    external_mcp_config, external_mcp_evidence = load_external_mcp_config(
        args.mcp_config
    )
    output_policy = variant["docker_output_policy"]
    sandbox = DockerSandbox(
        container=args.container,
        workdir=args.workdir,
        allowed_directories=args.allowed_directories,
        max_inline_output_bytes=int(
            output_policy.get("max_inline_output_bytes", 1_048_576)
        ),
        output_head_bytes=output_policy.get("output_head_bytes"),
        artifact_directory=str((args.output_dir / "tool-output-artifacts").resolve()),
        mcp_config=external_mcp_config,
        destructive_checkpoint=bool(
            variant["context_compiler"].get("destructive_sandbox_checkpoint", False)
        ),
        tracked_artifact_paths=(args.required_artifact or args.allowed_directories),
        checkpoint_directory=str((args.output_dir / "sandbox-checkpoints").resolve()),
        reuse=True,
    )
    try:
        system_prompt = SYSTEM_PROMPT + (
            " Task-provided Skill assets are mounted read-only at /aworld-skills. "
            "Activate only relevant Skills and use the container paths documented there."
            if skill_configs
            else ""
        )
        agent = Agent(
            name="terminal_bench_solver",
            conf=AgentConfig(
                llm_provider=os.environ.get("LLM_PROVIDER", "openai"),
                llm_model_name=model_name,
                llm_api_key=api_key,
                llm_base_url=os.environ.get("LLM_BASE_URL"),
                llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0")),
                params={"seed": args.model_seed} if args.model_seed is not None else {},
                max_steps=args.max_steps,
                use_vision=False,
                memory_config=AgentMemoryConfig(**variant["agent_memory_config"]),
                context_compiler=variant["context_compiler"],
                skill_configs=skill_configs,
            ),
            sandbox=sandbox,
            feedback_tool_result=True,
            system_prompt=system_prompt,
            llm_max_attempts=args.llm_max_attempts,
            llm_retry_delay=args.llm_retry_delay_sec,
            **_agent_loop_budget(args.max_steps, variant["context_compiler"]),
        )
        if args.deterministic_capture_provider:
            from aworld.core.llm_provider import LLMProviderBase
            from aworld.models.llm import LLMModel
            from aworld.models.model_response import ModelResponse

            class DeterministicCaptureProvider(LLMProviderBase):
                """Local structural probe; deliberately has no benchmark ability."""

                def _init_provider(self):
                    return None

                def postprocess_response(self, response):
                    return response

                @staticmethod
                def _response() -> ModelResponse:
                    content = "Deterministic structural capture completed."
                    return ModelResponse(
                        id="deterministic-capture-response",
                        model="aworld-deterministic-capture-v1",
                        content=content,
                        usage={
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                        message={"role": "assistant", "content": content},
                        finish_reason="stop",
                    )

                def completion(self, messages, **kwargs):
                    return self._response()

                async def acompletion(self, messages, **kwargs):
                    return self._response()

            agent._llm = LLMModel(
                conf=agent.conf.llm_config,
                custom_provider=DeterministicCaptureProvider(
                    model_name="aworld-deterministic-capture-v1"
                ),
            )
        await _configure_benchmark_completion_contract(
            agent,
            sandbox,
            variant,
            required_artifacts=tuple(args.required_artifact),
            completion_env_names=tuple(args.completion_env),
            verifier_mode=args.completion_verifier_mode,
            verifier_scratch_root=args.output_dir,
        )
        response = await Runners.run(instruction, agent=agent)
        response_payload = to_serializable(response.to_dict())
        trajectory_payload = to_serializable(response.trajectory)
        llm_journal_path = args.output_dir / "llm_calls.journal.jsonl"
        llm_journal_recovery = read_llm_call_journal(llm_journal_path)
        journal_calls = list(llm_journal_recovery.merged_llm_calls)
        captured_llm_calls, llm_capture_source, llm_capture_continuity = (
            _resolve_llm_call_capture(
                response,
                agent,
                journal_calls=journal_calls if llm_journal_recovery.available else None,
            )
        )
        llm_calls = to_serializable(captured_llm_calls)
        provider_calls = [call for call in llm_calls if _is_provider_bound_call(call)]
        try:
            reconciled_identity_hash = _llm_calls_identity_digest(llm_calls)
            journal_identity_hash = _llm_calls_identity_digest(journal_calls)
            journal_capture_match = (
                len(llm_calls) == len(journal_calls)
                and reconciled_identity_hash == journal_identity_hash
            )
            journal_capture_reason = None
        except ValueError:
            reconciled_identity_hash = None
            journal_identity_hash = None
            journal_capture_match = False
            journal_capture_reason = "stable_identity_unavailable_or_duplicated"
        llm_capture_continuity["journal_reconciliation"] = {
            "status": "available" if llm_journal_recovery.available else "unavailable",
            "comparison_basis": "stable_provider_identity",
            "journal_count": len(journal_calls),
            "reconciled_count": len(llm_calls),
            "journal_sha256": journal_identity_hash,
            "reconciled_sha256": reconciled_identity_hash,
            "snapshots_match": journal_capture_match,
            "reason_code": journal_capture_reason,
        }
        provider_capture_gate_passed = bool(provider_calls) and journal_capture_match
        trajectory_build_result = getattr(response, "trajectory_build_result", None)
        trajectory_llm_call_count = getattr(
            trajectory_build_result, "llm_call_count", None
        )
        finalized_projection_match = bool(
            isinstance(trajectory_llm_call_count, int)
            and not isinstance(trajectory_llm_call_count, bool)
            and trajectory_llm_call_count == len(llm_calls)
        )
        finalized_projection_reconciliation = {
            "status": (
                "available"
                if isinstance(trajectory_llm_call_count, int)
                and not isinstance(trajectory_llm_call_count, bool)
                else "unavailable"
            ),
            "comparison_basis": "trajectory_build_result_llm_call_count",
            "trajectory_llm_call_count": trajectory_llm_call_count,
            "final_llm_call_count": len(llm_calls),
            "call_count_delta": (
                len(llm_calls) - trajectory_llm_call_count
                if isinstance(trajectory_llm_call_count, int)
                and not isinstance(trajectory_llm_call_count, bool)
                else None
            ),
            "snapshots_match": finalized_projection_match,
        }
        lifecycle_evidence = _context_lifecycle_evidence(agent)
        context_artifacts = _export_context_tool_output_artifacts(
            agent, args.output_dir
        )
        upstream_artifacts = _export_upstream_tool_output_artifacts(
            trajectory_payload, args.output_dir
        )
        tool_journal_path = args.output_dir / "tool_actions.journal.jsonl"
        tool_journal_recovery = read_tool_action_journal(tool_journal_path)
        checksums = {
            "task_response.json": _write_json(
                args.output_dir / "task_response.json", response_payload
            ),
            "raw_trajectory.json": _write_json(
                args.output_dir / "raw_trajectory.json", trajectory_payload
            ),
            "llm_calls.json": _write_json(
                args.output_dir / "llm_calls.json", llm_calls
            ),
            "provider_calls.json": _write_json(
                args.output_dir / "provider_calls.json", provider_calls
            ),
            "context_lifecycle.json": _write_json(
                args.output_dir / "context_lifecycle.json", lifecycle_evidence
            ),
            "completion_contract.json": _write_json(
                args.output_dir / "completion_contract.json",
                _completion_contract_evidence(agent),
            ),
            "semantic_progress.json": _write_json(
                args.output_dir / "semantic_progress.json",
                _semantic_progress_evidence(agent),
            ),
            "context_trace.json": _write_json(
                args.output_dir / "context_trace.json",
                [
                    {
                        "request_id": call.get("request_id"),
                        "status": call.get("status"),
                        "error_code": call.get("error_code"),
                        "provider_invoked": call.get("provider_invoked"),
                        "request_trace_match": call.get("request_trace_match"),
                        "assembly_observability": call.get("assembly_observability"),
                        "request_metrics": call.get("request_metrics"),
                        "context_rollout": call.get("context_rollout"),
                    }
                    for call in llm_calls
                    if isinstance(call, dict)
                ],
            ),
        }
        if tool_journal_path.exists():
            checksums[tool_journal_path.name] = _sha256_bytes(
                tool_journal_path.read_bytes()
            )
        inspect = subprocess.run(
            [
                sandbox.docker_binary,
                "inspect",
                "--format",
                "{{json .Image}}",
                args.container,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        manifest = {
            "schema_version": "aworld.context-eval-run/v1",
            "variant": variant,
            "invariants": {
                "model": model_name,
                "provider": provider_name,
                "temperature": float(os.environ.get("LLM_TEMPERATURE", "0")),
                "model_seed": args.model_seed,
                "max_steps": args.max_steps,
                "llm_max_attempts": args.llm_max_attempts,
                "llm_retry_delay_sec": args.llm_retry_delay_sec,
                "system_prompt_sha256": _sha256_bytes(system_prompt.encode("utf-8")),
                "instruction_sha256": _sha256_bytes(instruction.encode("utf-8")),
                "task_skill_count": len(skill_configs),
                "task_skill_catalog_sha256": _sha256_bytes(
                    json.dumps(
                        {
                            name: {
                                "description": config.get("description"),
                                "usage_sha256": _sha256_bytes(
                                    str(config.get("usage") or "").encode("utf-8")
                                ),
                            }
                            for name, config in sorted(skill_configs.items())
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ).encode("utf-8")
                ),
                "external_mcp": external_mcp_evidence,
                "structural_capture_only": args.deterministic_capture_provider,
                "context_storage_isolated": True,
            },
            "container": {
                "name": args.container,
                "image_id": inspect.stdout.strip().strip('"')
                if inspect.returncode == 0
                else None,
                "workdir": sandbox.container_workdir,
            },
            "capture": {
                "provider_call_count": len(provider_calls),
                "llm_call_count": len(llm_calls),
                "llm_call_source": llm_capture_source,
                "llm_call_continuity": llm_capture_continuity,
                "provider_capture_gate_passed": provider_capture_gate_passed,
                "finalized_projection_reconciliation": (
                    finalized_projection_reconciliation
                ),
                "trajectory_items": len(response.trajectory or []),
                "context_tool_output_artifacts": context_artifacts,
                "upstream_tool_output_artifacts": upstream_artifacts,
                "tool_action_journal": tool_journal_recovery.to_evidence(),
                "checksums": checksums,
                "semantic_progress_status": _semantic_progress_evidence(agent)[
                    "status"
                ],
            },
            "started_at_epoch": started_at,
            "finished_at_epoch": time.time(),
            "aworld_source": _git_snapshot(),
            "python": sys.version,
        }
        _write_json(args.output_dir / "run_manifest.json", manifest)
        print(
            json.dumps(
                {
                    "success": response.success,
                    "status": str(response.status),
                    "trajectory_items": len(response.trajectory or []),
                    "output_dir": str(args.output_dir.resolve()),
                },
                ensure_ascii=False,
            )
        )
        if not provider_capture_gate_passed and not args.allow_missing_provider_trace:
            raise RuntimeError(
                "Provider-bound request capture is missing or TaskResponse/live Context continuity "
                "does not match; diagnostic artifacts were preserved, but reward cannot be "
                "attributed to a context-management variant"
            )
        if not response.success:
            print(
                json.dumps(
                    {
                        "schema_version": "aworld.run.failure.v1",
                        "reason_code": "task_response_unsuccessful",
                        "status": str(response.status),
                        "message": str(response.msg or "")[:500],
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        return 0
    finally:
        await sandbox.cleanup()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
