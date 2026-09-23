"""Bind delivery evidence and workbench operations to a framework-owned task.

Neither model arguments nor a tool's process environment choose the session,
filesystem roots, mandatory checks, or selection policy. Persisted snapshots are
recovery aids, not a security boundary against a task running as the same user.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from functools import partial
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from aworld.core.context.compiler.completion import (
    ImmutableInputEvidence,
    SelfCheckEvidence,
)


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def goal_workspace_identity(state):
    """Keep old persisted goals stable too; a new goal receives a fresh UUID."""
    return str(
        state.get("workspace_id")
        or _digest(
            {
                "objective": state.get("objective"),
                "started_at": state.get("started_at"),
                "source": state.get("source"),
            }
        )
    )


_TASK_ENV_NAMES = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "TMPDIR",
    "TMP",
    "TEMP",
    "LANG",
    "TZ",
    "PYTHONPATH",
    "PYTHONHOME",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "PIP_CERT",
    "UV_NATIVE_TLS",
}


def _task_environment():
    return {
        key: value
        for key, value in os.environ.items()
        if key in _TASK_ENV_NAMES or key.startswith("LC_")
    }


def bind_task_workspace(context, workspace_path, scope, *, task_env=None):
    """Trusted local executor entry point. Never expose this as a tool action."""
    if not isinstance(scope, dict) or not scope:
        raise ValueError("a framework-owned task scope is required")
    workspace = str(Path(workspace_path).resolve())
    context.context_info["task_workspace_binding"] = {
        "workspace": workspace,
        "scope": deepcopy(scope),
        "authority": "local",
    }
    context._task_workspace_environment = deepcopy(
        _task_environment() if task_env is None else task_env
    )
    context._task_workspace_session = None


def prepare_task_workspace(context, request, workspace_path, delivery=None):
    from .contracts import derive_delivery_contract

    if delivery is None:
        explicit = context.context_info.get("task_workspace_contract")
        existing = getattr(context, "completion_contract", None)
        session = getattr(context, "_task_workspace_session", None)
        owned = any(existing is getattr(context, key, None) for key in (
            "_workspace_completion_owned_contract", "_goal_completion_owned_contract",
            "_runtime_completion_derived_contract",
        ))
        if (session is not None and existing is not None and owned
                and getattr(context, "_task_workspace_prepared_request", None) == request
                and getattr(context, "_task_workspace_prepared_explicit", None) == explicit
                and getattr(context, "_task_workspace_prepared_binding", None)
                    == context.context_info.get("task_workspace_binding")):
            # Framework extensions are not new caller declarations. In
            # particular, re-entry after goal wiring cannot rebase originals.
            return deepcopy(session.delivery)
        context._task_workspace_caller_check_ids = (
            tuple(existing.required_self_check_ids) + tuple(command.command_id for command in existing.validation_commands)
            if existing else ()
        )
        if explicit is None and existing is not None:
            metadata = context.context_info.get("runtime_completion_contract", {})
            provided_fields = metadata.get("provided_fields")
            # The legacy CompletionContract has an authoritative artifact list.
            # CLI env shortcuts only declare the fields actually supplied.
            explicit = {}
            if provided_fields is None or "outputs" in provided_fields:
                explicit["outputs"] = [
                    {
                        "id": item.requirement_id,
                        "path": item.path,
                        **({"checks": [
                            {
                                "id": "caller-" + _digest(item.requirement_id)[:32],
                                "kind": "regular_file",
                            }
                        ]} if provided_fields is None else {}),
                    }
                    for item in existing.required_artifacts
                    if item.required
                ]
            explicit["inputs"] = [
                {"id": path, "path": path, "immutable": True}
                for path in existing.immutable_inputs
                if "/" in path or "\\" in path
            ]
        delivery = derive_delivery_contract(
            request, workspace_path=workspace_path, explicit=explicit
        )
    delivery = deepcopy(delivery)
    binding = context.context_info.get("task_workspace_binding")
    context.context_info["delivery_contract"] = delivery
    if not binding:
        return delivery
    session = TaskWorkspaceSession(
        binding,
        delivery,
        task_env=getattr(context, "_task_workspace_environment", None),
    )
    context._task_workspace_session = session
    context._task_workspace_prepared_request = request
    context._task_workspace_prepared_explicit = deepcopy(context.context_info.get("task_workspace_contract"))
    context._task_workspace_prepared_binding = deepcopy(binding)
    context.context_info["delivery_contract"] = session.delivery
    context.context_info["task_workspace_summary"] = session.summary()
    return deepcopy(session.delivery)


def get_task_workspace(context):
    if context is None:
        raise ValueError("workbench requires a task context")
    binding = context.context_info.get("task_workspace_binding")
    if not isinstance(binding, dict) or binding.get("authority") != "local":
        raise ValueError("workbench has no local filesystem authority in this context")
    session = getattr(context, "_task_workspace_session", None)
    if session is None:
        delivery = context.context_info.get("delivery_contract")
        if not delivery:
            raise ValueError("delivery workspace has not been prepared by the executor")
        session = TaskWorkspaceSession(
            binding,
            delivery,
            task_env=getattr(context, "_task_workspace_environment", None),
        )
        context._task_workspace_session = session
    return session


class TaskWorkspaceSession:
    def __init__(self, binding, delivery, *, task_env=None):
        from .store import TaskWorkspaceStore
        from .store_io import atomic_json, locked, read_json

        if binding.get("authority") != "local":
            raise ValueError("native workbench requires local execution authority")
        self.workspace = Path(binding["workspace"]).resolve()
        self.scope = deepcopy(binding["scope"])
        self.task_env = deepcopy(_task_environment() if task_env is None else task_env)
        declarations = [
            {"path": item["path"], "kind": "file"}
            for key in ("outputs", "inputs")
            for item in delivery.get(key, [])
        ]
        from .validation import validate_candidate

        self.store = TaskWorkspaceStore(
            self.workspace,
            self.scope,
            declared_roots=declarations,
            validator=partial(validate_candidate, env=self.task_env),
        )
        self.path = self.store.store_path / "delivery-session.json"
        self.lock = self.store.store_path / "delivery-session.lock"
        with locked(self.lock):
            if self.path.exists():
                state = read_json(self.path)
                if state["delivery"] != delivery:
                    raise ValueError(
                        "a continuing task cannot silently replace its public delivery contract"
                    )
            else:
                state = {
                    "schema_version": "aworld.delivery-session/v1",
                    "delivery": deepcopy(delivery),
                    "self_checks": [],
                    "self_check_history": [],
                    "protection_errors": [],
                    "initialized": False,
                }
            self.delivery = deepcopy(state["delivery"])
            if not state["initialized"]:
                # Capture before the first model operation. Subsequent segments
                # use the original snapshot, including when originals are gone.
                for item in self.delivery.get("inputs", []):
                    try:
                        self.store.protect_inputs([item])
                    except (OSError, ValueError, RuntimeError) as exc:
                        state["protection_errors"].append(
                            {
                                "path": item["path"],
                                "immutable": bool(item.get("immutable")),
                                "error": str(exc),
                            }
                        )
                state["initialized"] = True
            atomic_json(self.path, state)
        self.policy = self._policy()

    def _state(self):
        from .store_io import locked, read_json

        with locked(self.lock):
            return read_json(self.path)

    def _public_checks(self):
        mandatory = list(self.delivery.get("checks", []))
        for output in self.delivery.get("outputs", []):
            mandatory.extend(output.get("checks", []))
        return mandatory

    def _checks(self):
        checks = {}
        for check in self._public_checks() + self._state()["self_checks"]:
            if check["id"] in checks and checks[check["id"]] != check:
                raise ValueError("conflicting check definitions")
            checks[check["id"]] = check
        return list(checks.values())

    def _policy(self):
        policy = deepcopy(self.delivery.get("policy") or {})
        policy["mandatory_checks"] = sorted(
            set(policy.get("mandatory_checks", [])) | {c["id"] for c in self._checks()}
        )
        policy["check_definitions_sha256"] = _digest(self._checks())
        policy["execution_environment_sha256"] = _digest(self.task_env)
        return policy

    def _authorize(self, value, *, directory=False):
        path = Path(value)
        path = path if path.is_absolute() else self.workspace / path
        path = Path(os.path.abspath(path))
        if path != path.resolve():
            raise ValueError("symlinked workbench paths are unsupported")
        declared = {
            item["path"]
            for key in ("outputs", "inputs")
            for item in self.delivery.get(key, [])
        }
        if not path.is_relative_to(self.workspace) and (
            directory or str(path) not in declared
        ):
            raise ValueError(
                "path is outside the task workspace and exact public declarations"
            )
        return path

    def summary(self):
        return {
            "scope": self.scope,
            "delivery": deepcopy(self.delivery),
            "store": self.store.status(),
            "candidates": self.store.list_candidates(),
            "self_check_history": self._state().get("self_check_history", []),
            "last_final_validation": self._state().get("last_final_validation"),
            "protection_errors": self._state()["protection_errors"],
            "selection_policy": self._policy(),
            "provenance": self.store.provenance(),
            "task_reward": "not_assessed",
        }

    def record_final_validation(self, result):
        from .store_io import atomic_json, locked, read_json

        with locked(self.lock):
            state = read_json(self.path)
            state["last_final_validation"] = deepcopy(result)
            atomic_json(self.path, state)

    def _add_checks(self, additions, *, remove_ids=(), reason=None):
        from .store_io import atomic_json, locked, read_json
        from .validation import KINDS

        if not isinstance(additions, list):
            raise ValueError("checks must be a list")
        if not isinstance(remove_ids, (list, tuple)) or any(
            not isinstance(i, str) for i in remove_ids
        ):
            raise ValueError("remove_ids must be a list of check IDs")
        additions = deepcopy(additions)
        public = {c["id"]: c for c in self._public_checks()}
        known = {c["id"]: c for c in self._checks()}
        if remove_ids and not reason:
            raise ValueError("removing a self-check requires a reason")
        if set(remove_ids) & set(public):
            raise ValueError("public checks cannot be removed")
        for check in additions:
            if not isinstance(check, dict) or not isinstance(check.get("id"), str):
                raise ValueError("additional checks require unique string IDs")
            if check.get("kind") not in KINDS:
                raise ValueError(
                    "unsupported check kind; inspect the validation schema"
                )
            if check["id"] == "workbench.delivery" or any(
                key in check for key in ("passed", "success", "metrics")
            ):
                raise ValueError(
                    "a check cannot supply its result or use a framework-reserved ID"
                )
            for key in ("path", "input", "same_rows_as"):
                if check.get(key):
                    check[key] = str(self._authorize(check[key]))
            if check["id"] in known and check != known[check["id"]]:
                if check["id"] in public or not reason:
                    raise ValueError(
                        "additional checks cannot replace public checks; use revise_checks for agent self-check corrections"
                    )
            known[check["id"]] = check
        with locked(self.lock):
            state = read_json(self.path)
            retained = {c["id"]: c for c in state["self_checks"]}
            if reason:
                state.setdefault("self_check_history", []).append(
                    {
                        "reason": str(reason),
                        "previous": list(retained.values()),
                        "replacements": deepcopy(additions),
                        "removed": list(remove_ids),
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            for identifier in remove_ids:
                retained.pop(identifier, None)
            for check in additions:
                if (
                    check["id"] in retained
                    and retained[check["id"]] != check
                    and not reason
                ):
                    raise ValueError("concurrent check definition conflict")
                if check["id"] not in public:
                    retained[check["id"]] = deepcopy(check)
            state["self_checks"] = list(retained.values())
            atomic_json(self.path, state)

    async def execute(self, action, params):
        if action == "inspect":
            from .validation import describe_validation
            from .probes import describe_probes

            return {
                **self.summary(),
                "validation": describe_validation(),
                "api_probes": describe_probes(),
            }
        if action == "protect_inputs":
            result = await asyncio.to_thread(
                self.store.protect_inputs,
                [str(self._authorize(p)) for p in params["paths"]],
            )
            from .store_io import atomic_json, locked, read_json

            with locked(self.lock):
                state = read_json(self.path)
                state["protection_errors"] = [
                    e
                    for e in state["protection_errors"]
                    if e["path"] not in result["files"]
                ]
                atomic_json(self.path, state)
            return result
        if action == "working_copy":
            return await asyncio.to_thread(
                self.store.working_copy,
                params["snapshot_id"],
                self._authorize(params["destination"], directory=True),
            )
        if action == "restore_inputs":
            return await asyncio.to_thread(
                self.store.restore_inputs, params["snapshot_id"]
            )
        if action == "probe_api":
            from .probes import probe_argv, probe_python

            cwd = self._authorize(
                params.get("cwd", str(self.workspace)), directory=True
            )
            if params.get("argv"):
                return await probe_argv(
                    params["argv"], working_dir=cwd, env=self.task_env
                )
            return await probe_python(
                params["interpreter"],
                params["module"],
                params.get("object_path"),
                call=params.get("call"),
                working_dir=cwd,
                env=self.task_env,
            )
        if action == "save_candidate":
            files = {
                str(self._authorize(k)): str(self._authorize(v))
                for k, v in params["files"].items()
            }
            required = {item["path"] for item in self.delivery.get("outputs", [])}
            if not required.issubset(files):
                raise ValueError("candidate must include all required output paths")
            provenance = deepcopy(
                params.get("provenance")
                or [{"artifact": k, "note": str(params.get("note", ""))} for k in files]
            )
            for record in provenance:
                record["artifact"] = str(self._authorize(record["artifact"]))
                for source in record.get("sources", []):
                    source["path"] = str(self._authorize(source["path"]))
            return await asyncio.to_thread(
                self.store.register_candidate, files, provenance=provenance
            )
        if action == "revise_checks":
            if not str(params.get("reason", "")).strip():
                raise ValueError("self-check revisions require a concrete reason")
            self._add_checks(
                params.get("checks", []),
                remove_ids=params.get("remove_ids", []),
                reason=params["reason"],
            )
            return {
                "checks": self._checks(),
                "policy": self._policy(),
                "previous_receipts": "require_revalidation",
            }
        if action == "validate_candidate":
            self._add_checks(params.get("checks", []))
            policy = self._policy()
            if self.store.status().get("best"):
                await self.store.revalidate_best(self._checks(), policy)
            return await self.store.validate_candidate(
                params["candidate_id"], self._checks(), policy
            )
        if action == "promote_candidate":
            return await asyncio.to_thread(
                self.store.promote,
                params["candidate_id"],
                params["receipt_id"],
                self._policy(),
            )
        if action == "readback":
            return await asyncio.to_thread(self.store.readback)
        raise ValueError(f"unknown workbench action: {action}")


async def evaluate_delivery(context, contract):
    """Re-execute checks against final files; never accept model-supplied pass data."""
    from .validation import validate_candidate, snapshot_bindings
    from .store import assess_policy

    now = datetime.now(timezone.utc)
    success = False
    receipt = {}
    session = None
    context.context_info.pop("completion_infrastructure_failure", None)
    try:
        session = get_task_workspace(context)
        checks = session._checks()
        caller_ids = {command.command_id for command in contract.validation_commands}
        caller_ids.update(getattr(context, "_task_workspace_caller_check_ids", ()))
        current_contract = getattr(context, "completion_contract", None)
        if current_contract is not None:
            caller_ids.update(command.command_id for command in current_contract.validation_commands)
        if caller_ids.intersection(check["id"] for check in checks):
            raise ValueError("caller check IDs conflict with native workspace check IDs")
        paths = {item["path"] for item in session.delivery.get("outputs", [])}
        paths.update(c["path"] for c in checks if c.get("path"))
        paths.update(
            c["artifact"]
            for c in session._policy().get("hard_constraints", [])
            if c.get("artifact")
        )
        files = {p: session._authorize(p) for p in paths}
        inputs = await asyncio.to_thread(session.store.protected_input_files)
        # Checkers receive expendable input copies, never the only protected
        # blobs. A failing or destructive checker must not destroy recovery.
        with tempfile.TemporaryDirectory(
            prefix="final-check-", dir=session.store.store_path / "validation"
        ) as directory:
            input_copies = {}
            for index, (key, source) in enumerate(inputs.items()):
                target = Path(directory) / str(index) / Path(key).name
                target.parent.mkdir(parents=True)
                shutil.copyfile(source, target)
                input_copies[key] = target
            receipt = (
                await validate_candidate(
                    files,
                    input_copies,
                    checks,
                    scope=_digest(session.scope),
                    working_dir=session.workspace,
                    env=session.task_env,
                )
                if checks
                else {
                    "success": True,
                    "checks": [],
                    "metrics": {},
                    "execution_environment_sha256": _digest(session.task_env),
                    "bindings": {
                        "artifacts": snapshot_bindings(files),
                        "check_definitions_sha256": _digest([]),
                    },
                }
            )
        violations = assess_policy(
            (receipt.get("bindings") or {}).get("artifacts", {}),
            receipt,
            session._policy(),
        )
        receipt["policy_violations"] = violations
        now = datetime.now(timezone.utc)
        success = receipt.get("success") is True and not violations
        error_checks = [
            check
            for check in receipt.get("checks", [])
            if check.get("status") == "error"
        ]
        if error_checks:
            first_error = error_checks[0]
            context.context_info["completion_infrastructure_failure"] = {
                "failure_code": "delivery_validator_error",
                "error_type": str(
                    first_error.get("error_type") or "ValidationError"
                ),
            }
        for check in receipt.get("checks", []):
            context.record_completion_self_check(
                SelfCheckEvidence(
                    check["id"],
                    0 if check.get("success") else 1,
                    "sha256:" + _digest(check),
                    now,
                )
            )
        declarations = {
            item["path"]: item["id"] for item in session.delivery.get("inputs", [])
        }
        for item in await asyncio.to_thread(session.store.immutable_input_evidence):
            success = success and item["unchanged"]
            # Missing or changed bytes cannot be represented as an invented
            # observed hash. The aggregate fails and the expected evidence is absent.
            if item["unchanged"]:
                context.record_completion_immutable_input(
                    ImmutableInputEvidence(
                        input_id=declarations.get(item["path"], item["path"]),
                        expected_hash="sha256:" + item["sha256"],
                        observed_hash="sha256:" + item["sha256"],
                        observed_at=now,
                    )
                )
        if any(e.get("immutable") for e in session._state()["protection_errors"]):
            success = False
        # If an accepted candidate exists, the final paths must still contain
        # those exact accepted bytes. Users can explicitly choose new candidates.
        readback = await asyncio.to_thread(session.store.readback)
        if readback.get("candidate_id") is not None:
            success = success and readback.get("valid") is True
        context.context_info["task_workspace_summary"] = session.summary()
        context.context_info["delivery_validation"] = {
            "receipt": receipt,
            "readback": readback,
            "success": success,
        }
        session.record_final_validation(context.context_info["delivery_validation"])
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        receipt = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        context.context_info["delivery_validation"] = receipt
        context.context_info["completion_infrastructure_failure"] = {
            "failure_code": "delivery_validator_exception",
            "error_type": type(exc).__name__,
        }
        if session is not None:
            try:
                session.record_final_validation(receipt)
            except (OSError, ValueError, RuntimeError):
                pass  # The failing aggregate is still emitted below.
    context.record_completion_self_check(
        SelfCheckEvidence(
            "workbench.delivery",
            0 if success else 1,
            "sha256:" + _digest(receipt),
            datetime.now(timezone.utc),
        )
    )
