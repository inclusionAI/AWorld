"""Opt-in completion contracts for callers that explicitly request checks.

The default CLI path leaves task completion to the model. Structured checks
remain available for callers that choose observe or enforce mode.
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


COMPLETION_MODE_ENV = "AWORLD_COMPLETION_MODE"
COMPLETION_MAX_REPAIRS_ENV = "AWORLD_COMPLETION_MAX_REPAIRS"
REQUIRED_ARTIFACTS_ENV = "AWORLD_REQUIRED_ARTIFACTS_JSON"
VALIDATION_COMMANDS_ENV = "AWORLD_VALIDATION_COMMANDS_JSON"


def resolve_completion_mode(value: str | None = None) -> CompletionMode:
    """Model finalization is authoritative unless a caller opts into checks."""

    default = "off"
    raw_value = os.environ.get(COMPLETION_MODE_ENV, default) if value is None else value
    normalized = (raw_value or "off").strip().lower()
    try:
        return CompletionMode(normalized)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in CompletionMode)
        raise ValueError(f"{COMPLETION_MODE_ENV} must be one of: {allowed}") from exc


def resolve_completion_max_repairs(value: str | None = None) -> int | None:
    """Resolve an optional runtime-owned completion repair limit.

    An unset or blank value preserves the historical unbounded runtime contract.
    Callers can opt into a finite limit with any non-negative integer, including
    zero when no model-driven repair turn should be attempted.
    """

    raw_value = (
        os.environ.get(COMPLETION_MAX_REPAIRS_ENV)
        if value is None
        else value
    )
    if raw_value is None or not raw_value.strip():
        return None
    normalized = raw_value.strip()
    if not re.fullmatch(r"[0-9]+", normalized):
        raise ValueError(
            f"{COMPLETION_MAX_REPAIRS_ENV} must be a non-negative integer"
        )
    return int(normalized)


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
    validation_commands: Sequence[ValidationCommand] = (),
) -> CompletionContract | None:
    """Build an explicit artifact/command contract, or ``None`` if empty."""

    candidates = list(explicit_paths)
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
        max_repairs=resolve_completion_max_repairs(),
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


def configure_runtime_completion(
    context,
    *,
    request: str | None,
    workspace_path: str | os.PathLike[str],
) -> CompletionContract | None:
    """Bind only caller-selected artifact and validation requirements."""
    mode = resolve_completion_mode()
    existing = getattr(context, "completion_contract", None)
    explicit_mode = (os.environ.get(COMPLETION_MODE_ENV) or "").strip().lower()
    previous_enforcement = context.context_info.get(
        "completion_enforcement_explicit"
    )
    context.context_info["completion_enforcement_explicit"] = (
        previous_enforcement
        if isinstance(previous_enforcement, bool)
        else bool(
            explicit_mode == CompletionMode.ENFORCE.value
            or (
                existing is not None
                and getattr(context, "completion_mode", None)
                is CompletionMode.ENFORCE
            )
        )
    )
    if mode is CompletionMode.OFF and existing is None:
        context.context_info["runtime_completion_contract"] = {
            "mode": "off", "requested_mode": "off", "source": "model_final_response",
        }
        return None
    explicit_paths = _configured_artifact_paths()
    artifact_field_provided = bool((os.environ.get(REQUIRED_ARTIFACTS_ENV) or "").strip())
    validation_commands = _configured_validation_commands()
    if existing is not None:
        logger.info("Keeping the completion contract already installed by the caller")
        return existing

    # Only caller-supplied structure can activate this generic contract path.
    if artifact_field_provided or validation_commands:
        contract = build_runtime_completion_contract(
            request, workspace_path=workspace_path, explicit_paths=explicit_paths,
            validation_commands=validation_commands,
        )
        if contract is None:
            contract = CompletionContract(
                (),
                (),
                (),
                None,
                ("agent_final_response",),
                max_repairs=resolve_completion_max_repairs(),
            )
        context.configure_completion_contract(contract, mode=mode,
                                              evidence_resolver=resolve_runtime_completion_evidence)
        context.context_info["runtime_completion_contract"] = {
            "mode": mode.value, "requested_mode": mode.value, "source": "explicit_structured",
            "required_artifacts": [item.path for item in contract.required_artifacts],
            "provided_fields": ["outputs"] if artifact_field_provided else [],
            "max_repairs": contract.max_repairs,
        }
        return contract

    context.context_info["runtime_completion_contract"] = {
        "mode": "off",
        "requested_mode": mode.value,
        "source": "no_explicit_contract",
        "required_artifacts": [],
        "max_repairs": resolve_completion_max_repairs(),
    }
    return None


def configure_goal_completion(context, *, verification_commands: Sequence[str], workspace_path) -> CompletionContract | None:
    """Bind explicit user goal verification commands; no model text is executed.

    Goal CLI commands are shell strings by contract. pipefail prevents a failing
    validation hidden behind a successful log formatter from claiming success.
    """
    if not verification_commands:
        return getattr(context, "completion_contract", None)
    context.context_info["completion_enforcement_explicit"] = True
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
        max_repairs=(
            previous.max_repairs
            if previous
            else resolve_completion_max_repairs()
        ),
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
        "max_repairs": contract.max_repairs,
    }
    context._goal_completion_owned_contract = contract
    context._goal_completion_base_contract = base_resolver_contract
    context._goal_completion_base_resolver = previous_resolver
    return contract


__all__ = [
    "COMPLETION_MODE_ENV",
    "COMPLETION_MAX_REPAIRS_ENV",
    "REQUIRED_ARTIFACTS_ENV",
    "VALIDATION_COMMANDS_ENV",
    "build_runtime_completion_contract",
    "configure_runtime_completion",
    "configure_goal_completion",
    "resolve_completion_mode",
    "resolve_completion_max_repairs",
    "resolve_runtime_completion_evidence",
]
