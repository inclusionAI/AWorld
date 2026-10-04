#!/usr/bin/env python3
"""Standalone artifact candidate workbench using agent-mutable configuration.

This CLI records agent self-check evidence. It never infers a contract, decides
benchmark reward, or represents its receipts as caller/canonical verification.
Optional environment and AWorld control-state values are conveniences, not a
security boundary; changing them cannot expand terminal permissions.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

CONTRACT_SCHEMA = "workbench.contract/v1"
RESULT_SCHEMA = "workbench.cli-result/v1"
_REEXEC_MARKER = "WORKBENCH_AWORLD_PYTHON_ACTIVE"
_MAX_JSON_BYTES = 512 * 1024
_MAX_OUTPUT_BYTES = 128 * 1024
_MAX_REASON_CHARS = 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class WorkbenchCliError(ValueError):
    """A bounded, user-correctable CLI contract error."""


def _emit(payload: Mapping[str, Any], *, stream=None) -> bool:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, allow_nan=False
    ).encode("utf-8")
    bounded = len(encoded) <= _MAX_OUTPUT_BYTES
    if not bounded:
        encoded = json.dumps(
            {
                "schema_version": RESULT_SCHEMA,
                "success": False,
                "authority": "agent_self_check",
                "task_reward": "not_assessed",
                "error_type": "WorkbenchOutputTooLarge",
                "error": (
                    "Bounded Workbench output exceeded 128 KiB; request one "
                    "inspect --section value instead"
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    (stream or sys.stdout).write(encoded.decode("utf-8") + "\n")
    return bounded


def _maybe_reexec() -> None:
    target = str(os.environ.get("AWORLD_PYTHON_EXECUTABLE") or sys.executable).strip()
    if os.environ.get(_REEXEC_MARKER) == "1":
        return
    target_path = Path(target)
    if not target_path.is_absolute() or not target_path.is_file():
        raise WorkbenchCliError("AWORLD_PYTHON_EXECUTABLE is not an absolute executable")
    if os.path.realpath(target) == os.path.realpath(sys.executable):
        return
    env = dict(os.environ)
    env[_REEXEC_MARKER] = "1"
    os.execve(target, [target, str(Path(__file__).resolve()), *sys.argv[1:]], env)


def _configured_env_path(name: str, *, must_exist: bool) -> Path | None:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        raise WorkbenchCliError(f"Explicit configuration {name} must be absolute")
    if path.is_symlink():
        raise WorkbenchCliError(f"Explicit configuration {name} must not be a symbolic link")
    resolved = path.resolve(strict=must_exist)
    if must_exist and not resolved.is_dir():
        raise WorkbenchCliError(f"Explicit configuration {name} must be a directory")
    return resolved


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or path.is_relative_to(parent)


def _default_state_root(workspace: Path) -> Path:
    raw_control = str(os.environ.get("AWORLD_CONTROL_ROOT") or "").strip()
    if raw_control:
        control = Path(raw_control).expanduser().resolve(strict=False)
        state = control / "workbench"
        if _is_within(state, workspace):
            raise WorkbenchCliError(
                "AWORLD_CONTROL_ROOT must keep Workbench state outside the workspace"
            )
        return state

    raw_xdg = str(os.environ.get("XDG_STATE_HOME") or "").strip()
    if raw_xdg and Path(raw_xdg).expanduser().is_absolute():
        control = Path(raw_xdg).expanduser().resolve(strict=False) / "aworld"
    else:
        control = Path.home().resolve(strict=False) / ".local" / "state" / "aworld"
    state = control / "workbench"
    if _is_within(state, workspace):
        state = (
            Path(tempfile.gettempdir()).resolve()
            / f"aworld-control-{os.getuid()}"
            / "workbench"
        )
    if _is_within(state, workspace):
        raise WorkbenchCliError("Unable to derive state storage outside the workspace")
    return state


def _derived_scope_id(workspace: Path) -> str:
    identity = {
        name: str(os.environ.get(name) or "").strip()
        for name in ("AWORLD_SESSION_ID", "AWORLD_TASK_ID", "AWORLD_TASK_EPOCH")
        if str(os.environ.get(name) or "").strip()
    }
    if identity:
        source = json.dumps(identity, ensure_ascii=False, sort_keys=True)
        kind = "task"
    else:
        source = str(workspace)
        kind = "workspace"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return f"aworld-{kind}-{digest}"


def _runtime_roots() -> tuple[Path, Path, str]:
    workspace = _configured_env_path("WORKBENCH_WORKSPACE_ROOT", must_exist=True)
    if workspace is None:
        workspace = Path.cwd().resolve(strict=True)
    state = _configured_env_path("WORKBENCH_STATE_ROOT", must_exist=False)
    if state is None:
        state = _default_state_root(workspace)
    if _is_within(state, workspace):
        raise WorkbenchCliError("Workbench state root must be outside the workspace")
    if state.exists() and (state.is_symlink() or not state.is_dir()):
        raise WorkbenchCliError("Workbench state root must be a regular directory")
    scope = str(os.environ.get("WORKBENCH_SCOPE_ID") or "").strip()
    if not scope:
        scope = _derived_scope_id(workspace)
    if not scope or len(scope) > 256 or any(ord(char) < 33 for char in scope):
        raise WorkbenchCliError("Explicit configuration WORKBENCH_SCOPE_ID is invalid")
    return workspace, state, scope


def _workspace_path(value: object, workspace: Path, *, existing: bool = False) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise WorkbenchCliError("Workspace paths must be nonempty strings")
    raw = Path(value.strip())
    candidate = raw if raw.is_absolute() else workspace / raw
    absolute = Path(os.path.abspath(candidate))
    try:
        resolved = absolute.resolve(strict=existing)
    except OSError as exc:
        raise WorkbenchCliError(f"Workspace path is unavailable: {absolute}") from exc
    if absolute != resolved:
        raise WorkbenchCliError(f"Symbolic-link workspace paths are unsupported: {absolute}")
    if not absolute.is_relative_to(workspace):
        raise WorkbenchCliError(f"Path is outside configured workspace: {absolute}")
    if existing and not absolute.is_file():
        raise WorkbenchCliError(f"Expected a regular workspace file: {absolute}")
    return absolute


def _read_json_file(path_value: str, workspace: Path) -> dict[str, Any]:
    path = _workspace_path(path_value, workspace, existing=True)
    size = path.stat().st_size
    if size <= 0 or size > _MAX_JSON_BYTES:
        raise WorkbenchCliError("JSON input must be nonempty and at most 512 KiB")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkbenchCliError("JSON input is unreadable or invalid") from exc
    if not isinstance(value, dict):
        raise WorkbenchCliError("JSON input must be an object")
    return value


def _normalize_check(raw: object, workspace: Path) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise WorkbenchCliError("Every check must be an object")
    check = deepcopy(dict(raw))
    identifier = check.get("id")
    kind = check.get("kind")
    if not isinstance(identifier, str) or _IDENTIFIER.fullmatch(identifier) is None:
        raise WorkbenchCliError("Every check requires a stable id")
    if not isinstance(kind, str) or not kind:
        raise WorkbenchCliError(f"Check {identifier} requires a kind")
    if any(name in check for name in ("success", "passed", "metrics")):
        raise WorkbenchCliError("Check definitions cannot supply results or metrics")
    for name in ("path", "input", "same_rows_as"):
        if check.get(name) is not None:
            check[name] = str(_workspace_path(check[name], workspace))
    return check


def _normalize_contract(raw: object, workspace: Path) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise WorkbenchCliError("Contract must be an object")
    value = deepcopy(dict(raw))
    if value.get("schema_version") != CONTRACT_SCHEMA:
        raise WorkbenchCliError(f"Contract schema must be {CONTRACT_SCHEMA}")
    if value.get("authority") != "agent_self_check":
        raise WorkbenchCliError("Contract authority must be agent_self_check")
    if set(value) - {
        "schema_version",
        "authority",
        "outputs",
        "inputs",
        "checks",
        "policy",
    }:
        raise WorkbenchCliError("Contract contains unsupported fields")

    raw_outputs = value.get("outputs")
    if not isinstance(raw_outputs, list) or not raw_outputs or len(raw_outputs) > 128:
        raise WorkbenchCliError("Contract requires 1..128 explicit outputs")
    outputs: list[dict[str, Any]] = []
    output_ids: set[str] = set()
    output_paths: set[str] = set()
    checks: list[dict[str, Any]] = []
    for raw_output in raw_outputs:
        if not isinstance(raw_output, Mapping):
            raise WorkbenchCliError("Every output must be an object")
        if set(raw_output) - {"id", "path", "checks"}:
            raise WorkbenchCliError("Output contains unsupported fields")
        identifier = raw_output.get("id")
        if not isinstance(identifier, str) or _IDENTIFIER.fullmatch(identifier) is None:
            raise WorkbenchCliError("Every output requires a stable id")
        if identifier in output_ids:
            raise WorkbenchCliError("Output ids must be unique")
        path = str(_workspace_path(raw_output.get("path"), workspace))
        if path in output_paths:
            raise WorkbenchCliError("Output paths must be unique")
        output_checks = [
            _normalize_check(item, workspace)
            for item in raw_output.get("checks", [])
        ]
        for check in output_checks:
            if check.get("path") != path:
                raise WorkbenchCliError(
                    f"Output check {check['id']} must target its declared output path"
                )
        outputs.append({"id": identifier, "path": path, "checks": output_checks})
        output_ids.add(identifier)
        output_paths.add(path)
        checks.extend(output_checks)

    raw_inputs = value.get("inputs", [])
    if not isinstance(raw_inputs, list) or len(raw_inputs) > 128:
        raise WorkbenchCliError("Contract inputs must be a bounded list")
    inputs: list[dict[str, Any]] = []
    input_ids: set[str] = set()
    for raw_input in raw_inputs:
        if not isinstance(raw_input, Mapping) or set(raw_input) - {
            "id",
            "path",
            "immutable",
        }:
            raise WorkbenchCliError("Every input must use id/path/immutable fields")
        identifier = raw_input.get("id")
        immutable = raw_input.get("immutable", False)
        if not isinstance(identifier, str) or _IDENTIFIER.fullmatch(identifier) is None:
            raise WorkbenchCliError("Every input requires a stable id")
        if identifier in input_ids or not isinstance(immutable, bool):
            raise WorkbenchCliError("Input ids must be unique and immutable must be boolean")
        path = str(_workspace_path(raw_input.get("path"), workspace, existing=True))
        inputs.append({"id": identifier, "path": path, "immutable": immutable})
        input_ids.add(identifier)

    top_checks = value.get("checks", [])
    if not isinstance(top_checks, list):
        raise WorkbenchCliError("Contract checks must be a list")
    checks.extend(_normalize_check(item, workspace) for item in top_checks)
    check_ids = [check["id"] for check in checks]
    if not checks or len(checks) > 128 or len(set(check_ids)) != len(check_ids):
        raise WorkbenchCliError("Contract requires unique, bounded explicit checks")
    for check in checks:
        if check.get("kind") != "command" and check.get("path") not in output_paths:
            raise WorkbenchCliError(
                f"Check {check['id']} must target a declared output path"
            )
        for reference in (check.get("input"), check.get("same_rows_as")):
            if reference is not None and reference not in {item["path"] for item in inputs}:
                raise WorkbenchCliError(
                    f"Check {check['id']} references an undeclared input"
                )

    policy = value.get("policy")
    if not isinstance(policy, Mapping):
        raise WorkbenchCliError("Contract requires an explicit policy object")
    policy = deepcopy(dict(policy))
    if set(policy) - {
        "mandatory_checks",
        "hard_constraints",
        "objective",
    }:
        raise WorkbenchCliError("Policy contains unsupported fields")
    mandatory = policy.get("mandatory_checks")
    if not isinstance(mandatory, list) or mandatory != check_ids:
        raise WorkbenchCliError(
            "Policy mandatory_checks must explicitly list every check in contract order"
        )
    hard_constraints = policy.get("hard_constraints", [])
    if not isinstance(hard_constraints, list):
        raise WorkbenchCliError("Policy hard_constraints must be a list")
    policy["hard_constraints"] = hard_constraints
    objective = policy.get("objective")
    if objective is not None and (
        not isinstance(objective, Mapping)
        or not isinstance(objective.get("metric"), str)
        or not objective.get("metric")
        or objective.get("direction") not in {"maximize", "minimize"}
        or set(objective) != {"metric", "direction"}
    ):
        raise WorkbenchCliError(
            "Policy objective must contain metric and maximize/minimize direction"
        )

    top_ids = {check["id"] for check in top_checks if isinstance(check, Mapping)}
    return {
        "schema_version": CONTRACT_SCHEMA,
        "authority": "agent_self_check",
        "outputs": outputs,
        "inputs": inputs,
        "checks": [check for check in checks if check["id"] in top_ids],
        "policy": policy,
        "task_reward": "not_assessed",
    }


def _session_path(state_root: Path, scope_id: str) -> Path:
    from workbench_runtime.store_io import fingerprint

    scope = {"runtime_scope_id": scope_id}
    return state_root / fingerprint(scope) / "delivery-session.json"


def _open_session(
    workspace: Path,
    state_root: Path,
    scope_id: str,
    *,
    contract: dict[str, Any] | None = None,
):
    from workbench_runtime.session import TaskWorkspaceSession

    scope = {"runtime_scope_id": scope_id}
    if contract is None:
        path = _session_path(state_root, scope_id)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_JSON_BYTES:
            raise WorkbenchCliError("Workbench is not initialized for this configured scope")
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkbenchCliError("Stored Workbench contract is invalid") from exc
        contract = state.get("delivery") if isinstance(state, dict) else None
        if not isinstance(contract, dict):
            raise WorkbenchCliError("Stored Workbench contract is missing")
    return TaskWorkspaceSession(
        {"authority": "local", "workspace": str(workspace), "scope": scope},
        contract,
        state_root=state_root,
    )


def _compact(value: object, *, depth: int = 0) -> object:
    if depth >= 6:
        return "[bounded]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 512 else value[:509] + "..."
    if isinstance(value, list):
        return [_compact(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, Mapping):
        return {
            str(key)[:128]: _compact(item, depth=depth + 1)
            for key, item in list(value.items())[:48]
        }
    return str(value)[:512]


def _contract_summary(contract: Mapping[str, Any]) -> dict[str, Any]:
    checks = [
        check
        for output in contract.get("outputs", [])
        for check in output.get("checks", [])
    ] + list(contract.get("checks", []))
    return {
        "schema_version": contract.get("schema_version"),
        "authority": contract.get("authority"),
        "outputs": [item.get("path") for item in contract.get("outputs", [])],
        "inputs": [item.get("path") for item in contract.get("inputs", [])],
        "check_ids": [item.get("id") for item in checks],
        "policy": _compact(contract.get("policy", {})),
        "task_reward": "not_assessed",
    }


def _explicit_supersede(session, candidate_id: str, receipt_id: str, reason: str):
    from workbench_runtime.store_io import (
        StoreConflictError,
        StoreError,
        fingerprint,
        locked,
        verify_blob,
    )

    store = session.store
    policy = session._policy()
    if policy.get("objective") is not None:
        raise WorkbenchCliError("--supersede is only valid when policy has no objective")
    reason = reason.strip()
    if not reason or len(reason) > _MAX_REASON_CHARS:
        raise WorkbenchCliError("--supersede requires a bounded nonempty --reason")
    with locked(store._lock_path):
        store._recover_locked()
        candidate = store._candidate(candidate_id)
        receipt = store._receipt(receipt_id)
        store._verify_checkers(receipt["validation"])
        state = store._state()
        if (
            receipt["candidate_id"] != candidate_id
            or receipt["candidate_sha256"] != candidate_id
            or receipt["input_snapshot_id"] != state["input_snapshot_id"]
            or receipt["policy_sha256"] != fingerprint(policy)
            or candidate["input_snapshot_id"] != state["input_snapshot_id"]
        ):
            raise StoreConflictError("stale candidate, policy or validation receipt")
        store._verify_files(candidate["files"])
        snapshot = store._input(candidate["input_snapshot_id"])
        store._verify_files(snapshot["files"])
        violations = store._eligibility(candidate, receipt["validation"], policy)
        if violations:
            return {
                "promoted": False,
                "candidate_id": candidate_id,
                "reason": "constraints_failed",
                "violations": violations,
            }
        outputs = {str(store._target(key)): entry for key, entry in candidate["files"].items()}
        if set(outputs) & set(snapshot["immutable_paths"]):
            raise WorkbenchCliError("Candidate cannot overwrite immutable inputs")
        previous = deepcopy(state["best"])
        best = {
            "candidate_id": candidate_id,
            "receipt_id": receipt_id,
            "files": outputs,
            "objective": None,
            "eligible": True,
            "selection": {
                "mode": "explicit_supersede",
                "reason": reason,
                "previous_candidate_id": (
                    previous.get("candidate_id") if isinstance(previous, dict) else None
                ),
            },
        }
        publication = store._publish_locked(outputs, kind="promotion", best=best)
        for path, entry in outputs.items():
            try:
                verify_blob(Path(path), entry)
            except (OSError, StoreError) as exc:
                raise StoreConflictError("published candidate failed readback") from exc
        return {
            "promoted": True,
            "candidate_id": candidate_id,
            "receipt_id": receipt_id,
            "reason": "explicit_supersede",
            "publication_id": publication,
            "readback": store._readback_locked(),
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage explicit agent self-check artifact candidates",
        epilog=(
            "WORKBENCH_* values are optional, agent-mutable configuration, not a "
            "security boundary. Overrides do not expand terminal permissions or "
            "create canonical verifier/reward evidence."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Initialize an explicit self-check contract")
    init.add_argument("--contract", required=True, help="Contract JSON inside workspace")

    inspect = commands.add_parser("inspect", help="Inspect bounded Workbench state")
    inspect.add_argument(
        "--section",
        choices=("summary", "contract", "candidates", "readback", "provenance"),
        default="summary",
    )

    save = commands.add_parser("save-candidate", help="Snapshot an explicit candidate")
    save.add_argument("--manifest", required=True, help="Candidate JSON inside workspace")

    validate = commands.add_parser(
        "validate-candidate", help="Execute contract checks against a candidate"
    )
    validate.add_argument("--candidate-id", required=True)

    promote = commands.add_parser(
        "promote-candidate", help="Publish a validated eligible candidate"
    )
    promote.add_argument("--candidate-id", required=True)
    promote.add_argument("--receipt-id", required=True)
    promote.add_argument("--supersede", action="store_true")
    promote.add_argument("--reason")

    commands.add_parser("readback", help="Verify currently published candidate bytes")
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    workspace, state_root, scope_id = _runtime_roots()
    if args.command == "init":
        contract = _normalize_contract(_read_json_file(args.contract, workspace), workspace)
        session = _open_session(
            workspace,
            state_root,
            scope_id,
            contract=contract,
        )
        return {
            "initialized": True,
            "contract": _contract_summary(contract),
            "store": _compact(session.store.status()),
        }

    session = _open_session(workspace, state_root, scope_id)
    contract = session.delivery
    if args.command == "inspect":
        summary = {
            "contract": _contract_summary(contract),
            "store": _compact(session.store.status()),
        }
        if args.section == "summary":
            return summary
        if args.section == "contract":
            return {"contract": _compact(contract)}
        if args.section == "candidates":
            return {"candidates": _compact(session.store.list_candidates(limit=20))}
        if args.section == "readback":
            return {"readback": _compact(session.store.readback())}
        return {"provenance": _compact(session.store.provenance())}

    if args.command == "save-candidate":
        manifest = _read_json_file(args.manifest, workspace)
        if set(manifest) - {"files", "note", "provenance"}:
            raise WorkbenchCliError("Candidate manifest contains unsupported fields")
        raw_files = manifest.get("files")
        if not isinstance(raw_files, Mapping) or not raw_files or len(raw_files) > 128:
            raise WorkbenchCliError("Candidate manifest requires a bounded files object")
        files = {
            str(_workspace_path(key, workspace)): str(
                _workspace_path(value, workspace, existing=True)
            )
            for key, value in raw_files.items()
        }
        declared = {item["path"] for item in contract["outputs"]}
        if set(files) != declared:
            raise WorkbenchCliError("Candidate files must exactly match declared outputs")
        provenance = manifest.get("provenance")
        if provenance is None:
            provenance = [
                {"artifact": key, "note": str(manifest.get("note") or "")[:512]}
                for key in files
            ]
        return await session.execute(
            "save_candidate", {"files": files, "provenance": provenance}
        )

    if args.command == "validate-candidate":
        return await session.execute(
            "validate_candidate", {"candidate_id": args.candidate_id, "checks": []}
        )

    if args.command == "promote-candidate":
        if args.supersede:
            return _explicit_supersede(
                session,
                args.candidate_id,
                args.receipt_id,
                str(args.reason or ""),
            )
        if args.reason:
            raise WorkbenchCliError("--reason is only accepted with --supersede")
        return await session.execute(
            "promote_candidate",
            {"candidate_id": args.candidate_id, "receipt_id": args.receipt_id},
        )

    if args.command == "readback":
        return session.store.readback()
    raise WorkbenchCliError("Unsupported command")


def main() -> int:
    try:
        _maybe_reexec()
        args = _parser().parse_args()
        result = asyncio.run(_run(args))
        bounded = _emit(
            {
                "schema_version": RESULT_SCHEMA,
                "success": True,
                "authority": "agent_self_check",
                "task_reward": "not_assessed",
                "result": _compact(result),
            }
        )
        return 0 if bounded else 3
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - CLI boundary returns a typed failure.
        _emit(
            {
                "schema_version": RESULT_SCHEMA,
                "success": False,
                "authority": "agent_self_check",
                "task_reward": "not_assessed",
                "error_type": type(exc).__name__,
                "error": str(exc)[:2048],
            },
            stream=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
