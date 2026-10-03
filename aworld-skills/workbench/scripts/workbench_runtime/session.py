"""Private candidate runtime for the explicitly selected Workbench skill.

Configuration is agent-mutable and provides no authority beyond the calling
process's operating-system permissions.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from functools import partial
import hashlib
import json
import os
from pathlib import Path


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

class TaskWorkspaceSession:
    def __init__(self, binding, delivery, *, task_env=None, state_root=None):
        from .store import TaskWorkspaceStore
        from .store_io import atomic_json, locked, read_json

        if binding.get("authority") != "local":
            raise ValueError("standalone Workbench requires local workspace authority")
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
            root=state_root,
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
                    "schema_version": "workbench.session/v1",
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
            if any(key in check for key in ("passed", "success", "metrics")):
                raise ValueError("a check cannot supply its result")
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
