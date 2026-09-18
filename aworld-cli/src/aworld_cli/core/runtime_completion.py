"""Runtime-owned completion contracts for direct execution tasks.

Caller contracts take priority. Locally bound execution also enforces literal
public delivery requirements with source provenance and executed checks.
Ambiguous requirements remain visible without guessing paths or results.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import signal
import shutil
import os
import re
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Sequence

from aworld.core.context.compiler import (
    ArtifactEvidence,
    ArtifactRequirement,
    CompletionContract,
    CompletionMode,
    SelfCheckEvidence,
    ValidationCommand,
)
from aworld.logs.util import logger
from aworld.core.task_workspace.contracts import derive_delivery_contract, infer_declared_output_paths


COMPLETION_MODE_ENV = "AWORLD_COMPLETION_MODE"
INFER_ARTIFACTS_ENV = "AWORLD_INFER_REQUIRED_ARTIFACTS"
REQUIRED_ARTIFACTS_ENV = "AWORLD_REQUIRED_ARTIFACTS_JSON"
VALIDATION_COMMANDS_ENV = "AWORLD_VALIDATION_COMMANDS_JSON"

def _truthy_env(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def resolve_completion_mode(value: str | None = None) -> CompletionMode:
    """Resolve the caller mode; local literal delivery checks enforce by default."""

    default = "enforce"
    raw_value = os.environ.get(COMPLETION_MODE_ENV, default) if value is None else value
    normalized = (raw_value or "off").strip().lower()
    try:
        return CompletionMode(normalized)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in CompletionMode)
        raise ValueError(f"{COMPLETION_MODE_ENV} must be one of: {allowed}") from exc


def _configured_artifact_paths(raw_value: str | None = None) -> tuple[str, ...]:
    raw_value = os.environ.get(REQUIRED_ARTIFACTS_ENV) if raw_value is None else raw_value
    if raw_value is None or not raw_value.strip():
        return ()
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{REQUIRED_ARTIFACTS_ENV} must be a JSON array") from exc
    if not isinstance(parsed, list) or any(
        not isinstance(item, str) or not item.strip() for item in parsed
    ):
        raise ValueError(f"{REQUIRED_ARTIFACTS_ENV} must be a JSON array of paths")
    return tuple(item.strip() for item in parsed)


def _configured_validation_commands() -> tuple[ValidationCommand, ...]:
    raw = os.environ.get(VALIDATION_COMMANDS_ENV)
    if not raw:
        return ()
    try:
        values = json.loads(raw)
        if not isinstance(values, list):
            raise ValueError("expected array")
        if any(not isinstance(item, dict) or not isinstance(item.get("argv"), list)
               or (item.get("cwd") is not None and not isinstance(item["cwd"], str))
               for item in values):
            raise ValueError("expected command objects with argv arrays")
        commands = tuple(ValidationCommand(**item) for item in values)
        if len({item.command_id for item in commands}) != len(commands):
            raise ValueError("duplicate command_id")
        return commands
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{VALIDATION_COMMANDS_ENV} must be an array of explicit ValidationCommand objects") from exc


async def _run_validation(command: ValidationCommand) -> tuple[int, str]:
    """Execute only caller-supplied argv; drain bounded memory, preserve real exit."""
    digest = hashlib.sha256()
    process = await asyncio.create_subprocess_exec(
        *command.argv, cwd=command.cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    async def drain():
        while chunk := await process.stdout.read(65536):
            digest.update(chunk)
    reader = asyncio.create_task(drain())
    try:
        # Descendants keeping stdout open are also part of a bounded check.
        async with asyncio.timeout(command.timeout_seconds):
            await process.wait()
            await reader
        return process.returncode, "sha256:" + digest.hexdigest()
    except (TimeoutError, asyncio.CancelledError):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        if asyncio.current_task().cancelling():
            raise
        return 124, "sha256:" + digest.hexdigest()


def _resolve_artifact_paths(
    values: Iterable[str], *, workspace_path: str | os.PathLike[str]
) -> tuple[str, ...]:
    workspace = Path(workspace_path).expanduser().resolve()
    resolved: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = workspace / candidate
        normalized = str(candidate.resolve(strict=False))
        key = os.path.normcase(normalized)
        if key not in seen:
            seen.add(key)
            resolved.append(normalized)
    return tuple(resolved)


def build_runtime_completion_contract(
    request: str | None,
    *,
    workspace_path: str | os.PathLike[str],
    explicit_paths: Sequence[str] = (),
    infer_paths: bool = False,
    validation_commands: Sequence[ValidationCommand] = (),
) -> CompletionContract | None:
    """Build an artifact-existence contract, or ``None`` if no target exists."""

    candidates = list(explicit_paths)
    if infer_paths:
        candidates.extend(infer_declared_output_paths(request))
    paths = _resolve_artifact_paths(candidates, workspace_path=workspace_path)
    if not paths and not validation_commands:
        return None
    requirements = tuple(
        ArtifactRequirement(
            requirement_id=f"declared-output-{index}",
            path=path,
        )
        for index, path in enumerate(paths, start=1)
    )
    return CompletionContract(
        required_artifacts=requirements,
        immutable_inputs=(),
        validation_commands=tuple(replace(command, cwd=str((Path(workspace_path) / (command.cwd or ".")).resolve()))
                                  for command in validation_commands),
        max_evidence_age_seconds=None,
        required_final_evidence=("agent_final_response",),
        max_repairs=None,
    )


async def resolve_runtime_completion_evidence(
    context,
    contract: CompletionContract,
) -> None:
    """Collect fresh files and opt-in caller validation, never inferred commands."""

    for command in contract.validation_commands:
        try:
            exit_code, output_hash = await _run_validation(command)
        except (OSError, ValueError):
            exit_code, output_hash = 127, None
        context.record_completion_self_check(SelfCheckEvidence(
            command_id=command.command_id, exit_code=exit_code,
            output_hash=output_hash, observed_at=datetime.now(timezone.utc),
        ))
    if context.context_info.get("task_workspace_binding") is not None:
        await _evaluate_workspace_completion(context, contract)
    _record_runtime_artifacts(context, contract)


def _record_runtime_artifacts(context, contract):
    observed_at = datetime.now(timezone.utc)
    for requirement in contract.required_artifacts:
        path = Path(requirement.path).expanduser()
        try:
            exists = path.exists()
        except OSError:
            exists = False
        content_hash = None
        if exists and requirement.expected_hash:
            try:
                with path.open("rb") as stream:
                    content_hash = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
            except OSError:
                exists = False
        context.record_completion_artifact(
            ArtifactEvidence(
                requirement_id=requirement.requirement_id,
                exists=exists,
                content_hash=content_hash,
                observed_at=observed_at,
            )
        )


async def _evaluate_workspace_completion(context, contract):
    from aworld.core.task_workspace.session import evaluate_delivery
    delivery_ids = set(context.context_info.get("runtime_completion_contract", {}).get("delivery_check_ids", ()))
    if "workbench.delivery" in contract.required_self_check_ids:
        delivery_ids.add("workbench.delivery")
    for check_id in delivery_ids:
        # A resolver error must not leave an earlier successful receipt current.
        context.record_completion_self_check(SelfCheckEvidence(
            command_id=check_id, exit_code=1, output_hash=None,
            observed_at=datetime.now(timezone.utc),
        ))
    await evaluate_delivery(context, contract)


def _attach_workspace_completion(context, existing):
    delivery = context.context_info.get("delivery_contract", {})
    if any(command.command_id == "workbench.delivery" for command in existing.validation_commands):
        raise ValueError("workbench.delivery is reserved for executed workspace evidence")
    if any(existing is getattr(context, key, None) for key in (
        "_workspace_completion_owned_contract", "_goal_completion_owned_contract",
        "_runtime_completion_derived_contract",
    )):
        return existing
    requirements = list(existing.required_artifacts)
    known_paths = {item.path for item in requirements}
    requirements.extend(ArtifactRequirement(item["id"], item["path"])
                        for item in delivery.get("outputs", []) if item["path"] not in known_paths)
    input_ids = tuple(item["id"] for item in delivery.get("inputs", []) if item.get("immutable") is True)
    check_ids = tuple(check["id"] for item in delivery.get("outputs", []) for check in item["checks"])
    check_ids += tuple(check["id"] for check in delivery.get("checks", []))
    caller_ids = {command.command_id for command in existing.validation_commands}
    caller_ids.update(existing.required_self_check_ids)
    if caller_ids.intersection((*check_ids, "workbench.delivery")):
        raise ValueError("caller check IDs conflict with native workspace check IDs")
    extended = replace(
        existing, required_artifacts=tuple(requirements),
        immutable_inputs=tuple(dict.fromkeys((*existing.immutable_inputs, *input_ids))),
        required_self_check_ids=tuple(dict.fromkeys(
            (*existing.required_self_check_ids, *check_ids, "workbench.delivery")
        )),
    )
    previous_resolver = getattr(context, "_completion_evidence_resolver", None)
    resolver = previous_resolver
    if previous_resolver is not resolve_runtime_completion_evidence:
        async def resolver(target, configured):
            if previous_resolver is not None:
                await previous_resolver(target, existing)
            await _evaluate_workspace_completion(target, configured)
            previous_ids = {item.requirement_id for item in existing.required_artifacts}
            local_contract = configured if previous_resolver is None else replace(
                configured, required_artifacts=tuple(item for item in configured.required_artifacts
                                                     if item.requirement_id not in previous_ids),
            )
            _record_runtime_artifacts(target, local_contract)
    # Extending a caller contract must retain its evidence, mode and checks.
    context._completion_contract = extended
    context._completion_evidence_resolver = resolver
    context._workspace_completion_caller_contract = existing
    context._workspace_completion_owned_contract = extended
    return extended


def configure_runtime_completion(
    context,
    *,
    request: str | None,
    workspace_path: str | os.PathLike[str],
) -> CompletionContract | None:
    """Bind actual caller or high-confidence public delivery requirements.

    The native workspace binding is issued by the local executor. An arbitrary
    remote sandbox cannot make derived checks inspect the host filesystem.
    """
    mode = resolve_completion_mode()
    existing = getattr(context, "completion_contract", None)
    explicit_paths = _configured_artifact_paths()
    artifact_field_provided = bool((os.environ.get(REQUIRED_ARTIFACTS_ENV) or "").strip())
    validation_commands = _configured_validation_commands()
    binding = context.context_info.get("task_workspace_binding")
    if existing is not None:
        if binding is not None:
            from aworld.core.task_workspace.session import prepare_task_workspace
            prepare_task_workspace(context, request=request or "", workspace_path=workspace_path)
            existing = _attach_workspace_completion(context, existing)
        logger.info("Keeping the completion contract already installed by the caller")
        return existing

    # Install caller structure before preparing workspace state, so the facade
    # can recognize its authority and never replace it with inferred paths.
    if artifact_field_provided or validation_commands:
        contract = build_runtime_completion_contract(
            request, workspace_path=workspace_path, explicit_paths=explicit_paths,
            validation_commands=validation_commands,
        )
        if contract is None:
            contract = CompletionContract((), (), (), None, ("agent_final_response",), max_repairs=None)
        context.configure_completion_contract(contract, mode=mode,
                                              evidence_resolver=resolve_runtime_completion_evidence)
        context.context_info["runtime_completion_contract"] = {
            "mode": mode.value, "requested_mode": mode.value, "source": "explicit_structured",
            "required_artifacts": [item.path for item in contract.required_artifacts],
            "provided_fields": ["outputs"] if artifact_field_provided else [],
        }
        if binding is not None:
            from aworld.core.task_workspace.session import prepare_task_workspace
            prepare_task_workspace(context, request=request or "", workspace_path=workspace_path)
            contract = _attach_workspace_completion(context, contract)
        return contract

    if binding is not None:
        from aworld.core.task_workspace.session import prepare_task_workspace
        delivery = prepare_task_workspace(context, request=request or "", workspace_path=workspace_path)
    else:
        delivery = derive_delivery_contract(request or "", workspace_path=workspace_path)
        context.context_info["delivery_contract"] = delivery
        context.context_info["delivery_evaluation_unavailable"] = "local_workspace_not_bound"
        return None
    context.context_info["delivery_contract"] = delivery
    if mode is CompletionMode.OFF:
        return None
    requirements = tuple(ArtifactRequirement(item["id"], item["path"]) for item in delivery["outputs"])
    inputs = tuple(item["id"] for item in delivery["inputs"] if item.get("immutable") is True)
    check_ids = tuple(dict.fromkeys(
        [check["id"] for item in delivery["outputs"] for check in item["checks"]]
        + [check["id"] for check in delivery.get("checks", [])]
    ))
    # Even an ordinary question keeps the aggregate hook: it passes with no
    # deliverables, but rechecks any artifacts/self-checks added later through
    # the workbench. Ambiguous text still creates no guessed file requirement.
    check_ids = (*check_ids, "workbench.delivery")
    contract = CompletionContract(
        required_artifacts=requirements, immutable_inputs=inputs,
        validation_commands=(), required_self_check_ids=check_ids,
        max_evidence_age_seconds=None, required_final_evidence=("agent_final_response",),
        max_repairs=None,
    )
    context.configure_completion_contract(contract, mode=mode,
                                          evidence_resolver=resolve_runtime_completion_evidence)
    context._runtime_completion_derived_contract = contract
    context.context_info["runtime_completion_contract"] = {
        "mode": mode.value, "requested_mode": mode.value,
        "source": "explicit_structured" if delivery["coverage_status"] == "explicit" else "public_literal",
        "source_hash": delivery["source_hash"], "coverage_status": delivery["coverage_status"],
        "required_artifacts": [item.path for item in requirements],
        "delivery_check_ids": list(check_ids),
    }
    return contract


def configure_goal_completion(context, *, verification_commands: Sequence[str], workspace_path) -> CompletionContract | None:
    """Bind explicit user goal verification commands; no model text is executed.

    Goal CLI commands are shell strings by contract. pipefail prevents a failing
    validation hidden behind a successful log formatter from claiming success.
    """
    if not verification_commands:
        return getattr(context, "completion_contract", None)
    if any(not isinstance(command, str) or not command.strip() for command in verification_commands):
        raise ValueError("goal verification commands must be non-empty strings")
    shell = shutil.which("bash")
    if shell is None:
        raise ValueError("explicit shell validation requires bash with pipefail")
    previous = getattr(context, "completion_contract", None)
    previous_resolver = getattr(context, "_completion_evidence_resolver", None)
    metadata = context.context_info.get("runtime_completion_contract", {})
    owns_previous = previous is getattr(context, "_goal_completion_owned_contract", None)
    owned_ids = set(metadata.get("validation_command_ids", ())) if owns_previous else set()
    if owns_previous:
        previous_resolver = getattr(context, "_goal_completion_base_resolver", None)
    base_resolver_contract = getattr(context, "_goal_completion_base_contract", None) if owns_previous else previous
    base_checks = tuple(c for c in (previous.validation_commands if previous else ())
                        if c.command_id not in owned_ids)
    used_ids = {c.command_id for c in base_checks}
    used_ids.update(previous.required_self_check_ids if previous else ())
    checks = []
    for index, command in enumerate(verification_commands, 1):
        base_id = f"goal-verify-{index}"
        command_id = base_id
        suffix = 1
        while command_id in used_ids:
            command_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(command_id)
        checks.append(ValidationCommand(
            command_id=command_id,
            argv=(shell, "-o", "pipefail", "-c", command),
            cwd=str(Path(workspace_path).expanduser().resolve()),
        ))
    checks = tuple(checks)
    contract = CompletionContract(
        required_artifacts=previous.required_artifacts if previous else (),
        immutable_inputs=previous.immutable_inputs if previous else (),
        validation_commands=base_checks + checks,
        max_evidence_age_seconds=previous.max_evidence_age_seconds if previous else None,
        required_final_evidence=tuple(dict.fromkeys((previous.required_final_evidence if previous else ()) + ("agent_final_response",))),
        max_repairs=previous.max_repairs if previous else None,
        required_self_check_ids=previous.required_self_check_ids if previous else (),
    )
    resolver = resolve_runtime_completion_evidence
    if previous_resolver is not None and previous_resolver is not resolve_runtime_completion_evidence:
        async def resolver(target, configured):
            await previous_resolver(target, base_resolver_contract)
            # The caller resolver owns the original checks. Only execute this
            # goal's added commands here; rerunning caller commands could mutate
            # outputs twice and overwrite their authoritative evidence.
            await resolve_runtime_completion_evidence(target, replace(
                configured, validation_commands=checks, required_artifacts=(),
            ))
    context.configure_completion_contract(contract, mode=CompletionMode.ENFORCE, evidence_resolver=resolver)
    context.context_info["runtime_completion_contract"] = {
        "mode": "enforce", "requested_mode": "enforce", "source": "explicit_goal_verification",
        "required_artifacts": [item.path for item in contract.required_artifacts],
        "validation_command_ids": [item.command_id for item in checks],
        "delivery_check_ids": metadata.get("delivery_check_ids", []),
    }
    context._goal_completion_owned_contract = contract
    context._goal_completion_base_contract = base_resolver_contract
    context._goal_completion_base_resolver = previous_resolver
    return contract


__all__ = [
    "COMPLETION_MODE_ENV",
    "INFER_ARTIFACTS_ENV",
    "REQUIRED_ARTIFACTS_ENV",
    "VALIDATION_COMMANDS_ENV",
    "build_runtime_completion_contract",
    "configure_runtime_completion",
    "configure_goal_completion",
    "infer_declared_output_paths",
    "resolve_completion_mode",
    "resolve_runtime_completion_evidence",
]
