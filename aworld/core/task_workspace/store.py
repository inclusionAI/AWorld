"""Scoped candidate transactions and recoverable public-input provenance.

The caller binds scope, roots, policy and the validator. Tools must not expose
those authority choices to model arguments. The store protects against ordinary
workspace deletion/crashes, not a task root deliberately modifying its state.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
import inspect
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import tempfile
import time
from typing import Mapping

from .store_inputs import RegularFileStrategy, SQLiteGroupStrategy, capture_group
from .store_io import (StoreConflictError, StoreError, StoreIntegrityError, atomic_bytes,
                       atomic_json, canonical, capture, fingerprint, identity, locked,
                       read_json, sync_directory, verify_blob)

SCHEMA = "aworld.task-workspace/v1"
_ID = re.compile(r"[a-f0-9]{64}")


class StorePolicyError(StoreError):
    pass


class TaskWorkspaceStore:
    def __init__(self, workspace, scope: Mapping, *, root=None, declared_roots=None,
                 validator=None, policy=None, max_bytes=1024 * 1024 * 1024,
                 max_files=1024, input_strategies=None):
        self.workspace = Path(workspace).resolve()
        if not scope or not isinstance(scope, Mapping):
            raise ValueError("a caller-bound nonempty scope is required")
        self.scope = deepcopy(dict(scope))
        self.scope_id = fingerprint(self.scope)
        self.validator = validator
        self.policy = deepcopy(policy)
        self.max_bytes = int(max_bytes)
        self.max_files = int(max_files)
        if self.max_bytes < 1 or self.max_files < 1:
            raise ValueError("snapshot resource allowances must be positive")
        roots = [{"path": str(self.workspace), "kind": "directory"}]
        for supplied in declared_roots or []:
            if isinstance(supplied, Mapping):
                path = Path(supplied["path"]).absolute()
                kind = supplied["kind"]
            else:
                path = Path(supplied).absolute()
                kind = "directory" if path.is_dir() else "file"
            if kind not in {"file", "directory"}:
                raise ValueError("declared root kind must be file or directory")
            if str(path) not in {r["path"] for r in roots}:
                roots.append({"path": str(path), "kind": kind})
        self.declared_roots = roots
        self._related_paths = set()
        selected_root = root or os.environ.get("AWORLD_TASK_WORKSPACE_ROOT")
        if selected_root is None:
            selected_root = Path.home() / ".local/state/aworld/task-workspaces"
            if Path(selected_root).resolve().is_relative_to(self.workspace):
                selected_root = Path(tempfile.gettempdir()) / f"aworld-task-workspaces-{os.getuid()}"
        base = Path(selected_root).resolve()
        if base.is_relative_to(self.workspace):
            raise ValueError("scope store must be outside the ordinary workspace")
        self.path = base / self.scope_id
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.blobs = self.path / "blobs"
        for directory in (self.blobs, self.path / "inputs", self.path / "candidates",
                          self.path / "receipts", self.path / "validation", self.path / "accepted"):
            directory.mkdir(exist_ok=True, mode=0o700)
        self._lock_path = self.path / "lock"
        self._state_path = self.path / "state.json"
        self._journal_path = self.path / "journal.json"
        self._strategies = list(input_strategies or [SQLiteGroupStrategy(), RegularFileStrategy()])
        self._fault_injector = None  # Internal failure-injection seam; never a tool argument.
        with locked(self._lock_path):
            config_path = self.path / "scope.json"
            config = {"schema_version": SCHEMA, "scope": self.scope,
                      "workspace": str(self.workspace),
                      "declared_roots": self.declared_roots,
                      "max_bytes": self.max_bytes, "max_files": self.max_files}
            if config_path.exists():
                existing = read_json(config_path)
                if [r["path"] for r in existing["declared_roots"]] == [r["path"] for r in roots]:
                    # A deleted input file must not turn into a directory grant.
                    self.declared_roots = existing["declared_roots"]
                    config["declared_roots"] = self.declared_roots
                if existing != config:
                    raise StoreConflictError("scope is already bound to different workspace roots")
            else:
                atomic_json(config_path, config)
            key_path = self.path / "receipt-key"
            if not key_path.exists():
                atomic_bytes(key_path, secrets.token_bytes(32))
            self._key = key_path.read_bytes()
            if len(self._key) != 32:
                raise StoreIntegrityError("invalid receipt authority key")
            if not self._state_path.exists():
                atomic_json(self._state_path, {"schema_version": SCHEMA, "scope_id": self.scope_id,
                                             "input_snapshot_id": None, "best": None,
                                             "last_transaction": None})
            self._input(self._state()["input_snapshot_id"])
            self._recover_locked()

    @classmethod
    def open_existing(cls, store_path, *, validator=None, policy=None):
        path = Path(store_path).resolve()
        config = read_json(path / "scope.json")
        if path.name != fingerprint(config["scope"]):
            raise StoreIntegrityError("store path does not match its bound scope")
        return cls(config["workspace"], config["scope"], root=path.parent,
                   declared_roots=config["declared_roots"], validator=validator, policy=policy,
                   max_bytes=config["max_bytes"], max_files=config["max_files"])

    def _state(self):
        value = read_json(self._state_path)
        if value.get("scope_id") != self.scope_id or value.get("schema_version") != SCHEMA:
            raise StoreIntegrityError("invalid scoped store state")
        return value

    def _record(self, directory, identifier):
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
            raise StoreIntegrityError("invalid store record identity")
        value = read_json(self.path / directory / (identifier + ".json"))
        if value.get("scope_id") != self.scope_id:
            raise StoreIntegrityError("record belongs to another scope")
        return value

    def _authorize(self, value):
        path = Path(value)
        path = path if path.is_absolute() else self.workspace / path
        path = Path(os.path.abspath(path))
        resolved = path.resolve()
        if path != resolved:
            raise StoreIntegrityError("symlinked input/output paths are unsupported")
        # A declared existing file authorizes exactly that file, not its parent.
        if str(path) not in self._related_paths and not any(
            path == Path(r["path"]) or (r["kind"] == "directory" and path.is_relative_to(Path(r["path"])))
            for r in self.declared_roots
        ):
            raise StorePolicyError(f"path is outside caller-declared roots: {path}")
        return path

    def _target(self, key):
        if not isinstance(key, str) or not key or ".." in PurePosixPath(key).parts:
            raise StorePolicyError("invalid candidate output path")
        return self._authorize(key)

    def _verify_files(self, files):
        for entry in files.values():
            verify_blob(self.blobs / entry["sha256"], entry)

    def _input(self, identifier):
        if identifier is None:
            return {"snapshot_id": None, "input_sha256": fingerprint({}),
                    "files": {}, "groups": [], "immutable_paths": [], "provenance": []}
        value = self._record("inputs", identifier)
        if fingerprint(value["content"]) != identifier:
            raise StoreIntegrityError("input manifest changed")
        self._related_paths.update(value["content"]["files"])
        return {"snapshot_id": identifier, "input_sha256": identifier, **value["content"]}

    def _candidate(self, identifier):
        value = self._record("candidates", identifier)
        if fingerprint(value["content"]) != identifier:
            raise StoreIntegrityError("candidate manifest changed")
        return {"candidate_id": identifier, "candidate_sha256": identifier, **value["content"]}

    def protect_inputs(self, paths, *, immutable=False, provenance=None):
        """Capture public input groups. Only explicitly immutable paths gate completion."""
        declarations = []
        for item in paths:
            item = {"path": item, "immutable": immutable} if isinstance(item, (str, Path)) else dict(item)
            original = Path(item["path"])
            original = Path(os.path.abspath(original if original.is_absolute() else self.workspace / original))
            original = original if str(original) in self._related_paths else self._authorize(original)
            declarations.append({**item, "path": str(original)})
        if len(declarations) > self.max_files:
            raise StoreError("input count exceeds snapshot allowance")
        with locked(self._lock_path):
            self._recover_locked()
            previous = self._input(self._state()["input_snapshot_id"])
            files, groups = deepcopy(previous["files"]), deepcopy(previous["groups"])
            immutable_paths = list(previous["immutable_paths"])
            origins = deepcopy(previous["provenance"])
            remaining = self.max_bytes - sum(e["size"] for e in files.values())
            for declaration in declarations:
                anchor = Path(declaration["path"])
                if str(anchor) in files:
                    if declaration.get("immutable") is True:
                        immutable_paths.extend(next((g["members"] for g in groups
                                                     if str(anchor) in g["members"]), [str(anchor)]))
                    continue
                strategy = next((s for s in self._strategies if s.matches(anchor)), None)
                if strategy is None:
                    raise StorePolicyError("no supported input-group strategy")
                entries, group = capture_group(anchor, strategy, self.blobs, remaining)
                if len(files) + len(entries) > self.max_files:
                    raise StoreError("input group count exceeds snapshot allowance")
                remaining -= sum(e["size"] for e in entries.values())
                files.update(entries)
                groups.append(group)
                origins.append(deepcopy(declaration))
                if declaration.get("immutable") is True:
                    immutable_paths.extend(entries)
            content = {"files": files, "groups": groups,
                       "immutable_paths": sorted(set(immutable_paths)),
                       "provenance": origins + deepcopy(provenance or [])}
            identifier = fingerprint(content)
            atomic_json(self.path / "inputs" / (identifier + ".json"),
                        {"schema_version": SCHEMA, "scope_id": self.scope_id, "content": content})
            state = self._state()
            state["input_snapshot_id"] = identifier
            atomic_json(self._state_path, state)
            return self._input(identifier)

    def _input_views(self, snapshot):
        views = {}
        for group in snapshot["groups"]:
            anchor = group["anchor"]
            views[anchor] = group.get("working_copy", snapshot["files"][anchor])
        return views

    def _materialize(self, files, directory):
        result = {}
        for index, (key, entry) in enumerate(files.items()):
            verify_blob(self.blobs / entry["sha256"], entry)
            path = directory / str(index) / Path(key).name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.blobs / entry["sha256"], path)
            os.chmod(path, entry.get("mode", 0o600) | 0o600)
            result[key] = path
        return result

    def working_copy(self, snapshot_id, destination):
        destination = self._authorize(destination)
        with locked(self._lock_path):
            snapshot = self._input(snapshot_id)
            if destination.exists() and any(destination.iterdir()):
                raise StoreConflictError("working-copy destination must be empty")
            destination.mkdir(parents=True, exist_ok=True)
            files = self._materialize(self._input_views(snapshot), destination)
            return {"snapshot_id": snapshot_id, "destination": str(destination),
                    "files": {k: str(v) for k, v in files.items()},
                    "provenance": [{"path": str(files[k]), "source_path": k,
                                    "snapshot_id": snapshot_id, "sha256": entry["sha256"],
                                    "source_ranges": next(g.get("derived_from", [
                                        {"path": k, "sha256": entry["sha256"], "start": 0,
                                         "end": entry["size"]}]) for g in snapshot["groups"]
                                        if g["anchor"] == k)}
                                   for k, entry in self._input_views(snapshot).items()]}

    def restore_inputs(self, snapshot_id, *, destination=None, overwrite=False):
        if destination is not None:
            return self.working_copy(snapshot_id, destination)
        if overwrite:
            raise StorePolicyError("restore never overwrites changed files; use a fresh working copy")
        with locked(self._lock_path):
            self._recover_locked()
            snapshot = self._input(snapshot_id)
            self._verify_files(snapshot["files"])
            conflicts = []
            for original, entry in snapshot["files"].items():
                path = Path(original)
                if path.exists():
                    try:
                        verify_blob(path, entry)
                    except StoreIntegrityError:
                        conflicts.append(original)
            if conflicts:
                raise StoreConflictError("input paths changed; restore to a new working copy: " + ", ".join(conflicts))
            entries = {p: e for p, e in snapshot["files"].items() if not Path(p).exists()}
            self._publish_locked(entries, kind="restore", best=None)
            return {"snapshot_id": snapshot_id, "restored": sorted(entries), "conflicts": []}

    def _provenance(self, records, files, snapshot):
        result = []
        for record in records or []:
            record = deepcopy(dict(record))
            output = record.get("artifact") or record.get("output")
            if output not in files:
                raise StorePolicyError("provenance must name a registered artifact")
            start, end = record.get("start", 0), record.get("end", files[output]["size"])
            if (isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int)
                    or not isinstance(end, int) or not 0 <= start <= end <= files[output]["size"]):
                raise StorePolicyError("invalid artifact provenance byte range")
            sources = []
            for source in record.get("sources", []):
                path = str(source["path"])
                if path not in snapshot["files"]:
                    raise StorePolicyError("provenance source is not in the protected input snapshot")
                entry = snapshot["files"][path]
                left, right = source.get("start", 0), source.get("end", entry["size"])
                if (isinstance(left, bool) or isinstance(right, bool) or not isinstance(left, int)
                        or not isinstance(right, int) or not 0 <= left <= right <= entry["size"]):
                    raise StorePolicyError("invalid source provenance byte range")
                sources.append({"path": path, "sha256": entry["sha256"], "start": left, "end": right})
            result.append({**record, "artifact": output, "sha256": files[output]["sha256"],
                           "start": start, "end": end, "sources": sources,
                           "claim_type": "declared_derivation_not_semantic_proof"})
        return result

    def register_candidate(self, files: Mapping, *, provenance=None):
        if not files or len(files) > self.max_files:
            raise StorePolicyError("a bounded nonempty candidate file mapping is required")
        with locked(self._lock_path):
            self._recover_locked()
            snapshot = self._input(self._state()["input_snapshot_id"])
            captured, remaining = {}, self.max_bytes
            for key, source in files.items():
                self._target(key)
                entry = capture(self._authorize(source), self.blobs, remaining)
                captured[key] = entry
                remaining -= entry["size"]
            content = {"files": captured, "input_snapshot_id": snapshot["snapshot_id"],
                       "input_sha256": snapshot["input_sha256"],
                       "provenance": self._provenance(provenance, captured, snapshot)}
            identifier = fingerprint(content)
            atomic_json(self.path / "candidates" / (identifier + ".json"),
                        {"schema_version": SCHEMA, "scope_id": self.scope_id, "content": content})
            return self._candidate(identifier)

    def _policy(self, policy):
        value = deepcopy(self.policy if policy is None else policy)
        if not isinstance(value, dict):
            raise StorePolicyError("a caller-declared selection policy is required")
        objective = value.get("objective")
        if objective is not None and (not isinstance(objective, dict)
                or not isinstance(objective.get("metric"), str) or not objective["metric"]
                or objective.get("direction") not in {"maximize", "minimize"}):
            raise StorePolicyError("promotion requires a named metric and maximize/minimize objective")
        mandatory = value.get("mandatory_checks")
        if not isinstance(mandatory, list) or not mandatory or any(not isinstance(x, str) or not x for x in mandatory):
            raise StorePolicyError("promotion requires explicit mandatory executed checks")
        canonical(value)
        return value

    def _eligibility(self, candidate, validation, policy):
        problems = []
        checks = {c["id"]: c for c in validation["checks"]}
        for identifier in policy["mandatory_checks"]:
            if checks.get(identifier, {}).get("success") is not True:
                problems.append("mandatory_check:" + identifier)
        if validation.get("success") is not True:
            problems.append("validation_failed")
        snapshot = self._input(candidate["input_snapshot_id"])
        for path in snapshot["immutable_paths"]:
            try:
                verify_blob(Path(path), snapshot["files"][path])
            except (OSError, StoreError):
                problems.append("immutable_input_changed:" + path)
        metrics = validation["metrics"]
        objective = (policy.get("objective") or {}).get("metric")
        if objective and objective not in metrics:
            problems.append("missing_objective:" + objective)
        comparisons = {"<=": lambda a, b: a <= b, "<": lambda a, b: a < b,
                       ">=": lambda a, b: a >= b, ">": lambda a, b: a > b,
                       "==": lambda a, b: a == b}
        for constraint in policy.get("hard_constraints", []):
            if "artifact" in constraint:
                entry = candidate["files"].get(constraint["artifact"])
                ok = entry is not None
                if entry is not None:
                    ok = constraint.get("min_bytes", 0) <= entry["size"] <= constraint.get("max_bytes", self.max_bytes)
            else:
                op, metric = constraint.get("op"), constraint.get("metric")
                if op not in comparisons or not isinstance(constraint.get("value"), (int, float)):
                    raise StorePolicyError("invalid hard metric constraint")
                ok = metric in metrics and comparisons[op](metrics[metric], constraint["value"])
            if not ok:
                problems.append("hard_constraint:" + fingerprint(constraint))
        return problems

    async def validate_candidate(self, candidate_id, checks, policy=None):
        policy = self._policy(policy)
        definitions_hash = fingerprint(checks)
        if policy.get("check_definitions_sha256", definitions_hash) != definitions_hash:
            raise StorePolicyError("checks differ from the caller-bound selection policy")
        if not isinstance(checks, list) or not checks:
            raise StorePolicyError("real check definitions are required")
        with locked(self._lock_path):
            candidate = self._candidate(candidate_id)
            state = self._state()
            if state["input_snapshot_id"] != candidate["input_snapshot_id"]:
                raise StoreConflictError("candidate is bound to stale protected inputs")
            snapshot = self._input(candidate["input_snapshot_id"])
            self._verify_files(candidate["files"])
            self._verify_files(snapshot["files"])
        validator = self.validator
        if validator is None:
            from .validation import validate_candidate as validator
        with tempfile.TemporaryDirectory(prefix="check-", dir=self.path / "validation") as temp:
            directory = Path(temp)
            candidate_files = self._materialize(candidate["files"], directory / "candidate")
            input_files = self._materialize(self._input_views(snapshot), directory / "inputs")
            validation = validator(candidate_files, input_files, deepcopy(checks),
                                   scope=self.scope_id, working_dir=self.workspace)
            if inspect.isawaitable(validation):
                validation = await validation
            validation = deepcopy(validation)
            expected = {"artifacts": {k: {"state": "regular", "sha256": e["sha256"], "size": e["size"]}
                                      for k, e in candidate["files"].items()},
                        "inputs": {k: {"state": "regular", "sha256": e["sha256"], "size": e["size"]}
                                   for k, e in self._input_views(snapshot).items()},
                        "check_definitions_sha256": definitions_hash}
            if validation.get("schema_version") != "aworld.validation/v1" or validation.get("bindings") != expected:
                raise StoreIntegrityError("validator receipt is not bound to these exact bytes/check definitions")
            for key, path in candidate_files.items():
                verify_blob(path, candidate["files"][key])
            for key, path in input_files.items():
                verify_blob(path, self._input_views(snapshot)[key])
        metrics = validation.get("metrics")
        if not isinstance(metrics, dict) or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                               or not math.isfinite(v) for v in metrics.values()):
            raise StoreIntegrityError("validator metrics must be finite observed numbers")
        observed = validation.get("checks")
        if not isinstance(observed, list) or len({x.get("id") for x in observed}) != len(observed):
            raise StoreIntegrityError("validator must return unique executed checks")
        violations = self._eligibility(candidate, validation, policy)
        receipt = {"schema_version": SCHEMA, "scope_id": self.scope_id,
                   "candidate_id": candidate_id, "candidate_sha256": candidate_id,
                   "input_snapshot_id": candidate["input_snapshot_id"],
                   "input_sha256": candidate["input_sha256"],
                   "check_definitions_sha256": definitions_hash,
                   "policy_sha256": fingerprint(policy), "validation": validation,
                   "check_definitions": deepcopy(checks),
                   "created_ns": time.time_ns(), "nonce": secrets.token_hex(16),
                   "eligible": not violations, "violations": violations}
        identifier = fingerprint(receipt)
        with locked(self._lock_path):
            if self._state()["input_snapshot_id"] != candidate["input_snapshot_id"]:
                raise StoreConflictError("protected inputs changed during validation")
            self._verify_files(candidate["files"])
            self._verify_files(snapshot["files"])
            atomic_json(self.path / "receipts" / (identifier + ".json"),
                        {"scope_id": self.scope_id, "receipt": receipt,
                         "mac": hmac.new(self._key, canonical(receipt), hashlib.sha256).hexdigest()})
            candidate_record = self._record("candidates", candidate_id)
            candidate_record["latest_receipt_id"] = identifier
            atomic_json(self.path / "candidates" / (candidate_id + ".json"), candidate_record)
        return {"receipt_id": identifier, "candidate_id": candidate_id,
                "eligible": not violations, "violations": violations,
                "metrics": metrics, "checks": observed, "bindings": expected,
                "policy_sha256": receipt["policy_sha256"]}

    def _receipt(self, identifier):
        record = self._record("receipts", identifier)
        receipt = record["receipt"]
        expected = hmac.new(self._key, canonical(receipt), hashlib.sha256).hexdigest()
        if fingerprint(receipt) != identifier or not hmac.compare_digest(expected, record.get("mac", "")):
            raise StoreIntegrityError("forged or modified validation receipt")
        return receipt

    def _archive_best(self, best):
        if best:
            atomic_json(self.path / "accepted" / (fingerprint(best) + ".json"), best)

    async def revalidate_best(self, checks, policy=None):
        """Execute current checks against retained incumbent bytes/current inputs.

        A newly failing incumbent remains recoverable history, but cannot block
        replacement by a candidate satisfying the new caller policy.
        """
        policy = self._policy(policy)
        with locked(self._lock_path):
            self._recover_locked()
            state = self._state()
            previous = deepcopy(state["best"])
            if previous is None:
                return {"revalidated": False, "reason": "no_promoted_candidate"}
            retained = self._candidate(previous["candidate_id"])
            snapshot = self._input(state["input_snapshot_id"])
            content = {k: deepcopy(retained[k]) for k in ("files", "provenance")}
            content.update(input_snapshot_id=snapshot["snapshot_id"], input_sha256=snapshot["input_sha256"])
            candidate_id = fingerprint(content)
            atomic_json(self.path / "candidates" / (candidate_id + ".json"),
                        {"schema_version": SCHEMA, "scope_id": self.scope_id, "content": content})
        result = await self.validate_candidate(candidate_id, checks, policy)
        with locked(self._lock_path):
            state = self._state()
            if state["best"] != previous:
                raise StoreConflictError("incumbent changed while revalidation was running")
            self._archive_best(previous)
            objective = policy.get("objective")
            state["best"] = {**previous, "candidate_id": candidate_id,
                             "receipt_id": result["receipt_id"], "eligible": result["eligible"],
                             "objective": {**objective, "value": result["metrics"].get(objective["metric"])}
                             if objective else None}
            atomic_json(self._state_path, state)
        return {**result, "revalidated": True}

    def promote(self, candidate_id, receipt_id, policy=None):
        policy = self._policy(policy)
        with locked(self._lock_path):
            self._recover_locked()
            candidate = self._candidate(candidate_id)
            receipt = self._receipt(receipt_id)
            state = self._state()
            if (receipt["candidate_id"] != candidate_id or receipt["candidate_sha256"] != candidate_id
                    or receipt["input_snapshot_id"] != state["input_snapshot_id"]
                    or receipt["policy_sha256"] != fingerprint(policy)
                    or candidate["input_snapshot_id"] != state["input_snapshot_id"]):
                raise StoreConflictError("stale candidate, policy or validation receipt")
            self._verify_files(candidate["files"])
            snapshot = self._input(candidate["input_snapshot_id"])
            self._verify_files(snapshot["files"])
            violations = self._eligibility(candidate, receipt["validation"], policy)
            if violations:
                return {"promoted": False, "candidate_id": candidate_id,
                        "reason": "constraints_failed", "violations": violations}
            objective = policy.get("objective")
            metric = receipt["validation"]["metrics"][objective["metric"]] if objective else None
            outputs = {str(self._target(k)): e for k, e in candidate["files"].items()}
            if set(outputs) & set(snapshot["immutable_paths"]):
                raise StorePolicyError("candidate publication cannot overwrite immutable input paths")
            best = {"candidate_id": candidate_id, "receipt_id": receipt_id,
                    "files": outputs, "objective": {**objective, "value": metric} if objective else None,
                    "eligible": True}
            if state["best"] and outputs == state["best"]["files"]:
                self._archive_best(state["best"])
                intact = True
                for path, entry in outputs.items():
                    try:
                        verify_blob(Path(path), entry)
                    except (OSError, StoreError):
                        intact = False
                if intact:
                    state["best"] = best
                    atomic_json(self._state_path, state)
                    return {"promoted": False, "candidate_id": candidate_id,
                            "reason": "receipt_refreshed", "readback": self._readback_locked()}
                publication = self._publish_locked(outputs, kind="promotion", best=best)
                return {"promoted": True, "candidate_id": candidate_id, "reason": "published_bytes_restored",
                        "publication_id": publication, "readback": self._readback_locked()}
            if state["best"]:
                prior = self._receipt(state["best"]["receipt_id"])
                if prior["policy_sha256"] != receipt["policy_sha256"]:
                    raise StoreConflictError("call revalidate_best under the changed selection policy")
                incumbent = self._candidate(state["best"]["candidate_id"])
                qualified = (prior["input_snapshot_id"] == state["input_snapshot_id"]
                             and not self._eligibility(incumbent, prior["validation"], policy))
                if qualified and objective is None:
                    return {"promoted": False, "candidate_id": candidate_id,
                            "reason": "equivalent_no_objective"}
                previous = prior["validation"]["metrics"].get(objective["metric"]) if objective else None
                if qualified and not (metric > previous if objective["direction"] == "maximize" else metric < previous):
                    return {"promoted": False, "candidate_id": candidate_id, "reason": "not_better"}
            publication = self._publish_locked(outputs, kind="promotion", best=best)
            return {"promoted": True, "candidate_id": candidate_id, "receipt_id": receipt_id,
                    "publication_id": publication, "readback": self._readback_locked()}

    def _current(self, path):
        if not path.exists() and not path.is_symlink():
            return None
        return capture(path, self.blobs, self.max_bytes)

    def _install(self, target: Path, entry):
        self._authorize(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry is None:
            target.unlink(missing_ok=True)
            sync_directory(target.parent)
            return
        verify_blob(self.blobs / entry["sha256"], entry)
        # Temp file and rename share the destination filesystem.
        fd, name = tempfile.mkstemp(prefix=".aworld-publish-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as outgoing, (self.blobs / entry["sha256"]).open("rb") as incoming:
                shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                outgoing.flush()
                os.fchmod(outgoing.fileno(), entry.get("mode", 0o600))
                os.fsync(outgoing.fileno())
            os.replace(name, target)
            sync_directory(target.parent)
        finally:
            Path(name).unlink(missing_ok=True)

    def _publish_locked(self, outputs, *, kind, best):
        if not outputs:
            return None
        identifier = secrets.token_hex(16)
        journal = {"schema_version": SCHEMA, "scope_id": self.scope_id, "id": identifier,
                   "kind": kind, "best": best, "entries": [
                       {"path": p, "before": self._current(Path(p)), "after": e}
                       for p, e in outputs.items()]}
        atomic_json(self._journal_path, journal)
        try:
            for index, entry in enumerate(journal["entries"]):
                self._install(Path(entry["path"]), entry["after"])
                if self._fault_injector:
                    self._fault_injector("installed", index)
            for entry in journal["entries"]:
                verify_blob(Path(entry["path"]), entry["after"])
            state = self._state()
            if best is not None:
                self._archive_best(state["best"])
                state["best"] = best
            state["last_transaction"] = identifier
            atomic_json(self._state_path, state)
            if self._fault_injector:
                self._fault_injector("committed", 0)
            self._journal_path.unlink()
            sync_directory(self.path)
            return identifier
        except BaseException:
            self._recover_locked()
            raise

    def _recover_locked(self):
        if not self._journal_path.exists():
            return {"recovered": False, "action": "none"}
        journal = read_json(self._journal_path)
        if journal.get("scope_id") != self.scope_id or journal.get("schema_version") != SCHEMA:
            raise StoreIntegrityError("recovery journal belongs to a different scope")
        committed = self._state()["last_transaction"] == journal["id"]
        # Validate every destination before changing any during recovery. An
        # unrelated later user edit is a conflict, never silently overwritten.
        for entry in journal["entries"]:
            current = self._current(self._authorize(entry["path"]))
            allowed = {None, *(x["sha256"] for x in (entry["before"], entry["after"]) if x)}
            if (current["sha256"] if current else None) not in allowed:
                raise StoreConflictError("publication recovery found a newer destination edit")
        for entry in journal["entries"]:
            self._install(Path(entry["path"]), entry["after"] if committed else entry["before"])
        self._journal_path.unlink()
        sync_directory(self.path)
        return {"recovered": True, "action": "complete" if committed else "rollback",
                "candidate_id": (self._state()["best"] or {}).get("candidate_id")}

    def recover(self):
        with locked(self._lock_path):
            return self._recover_locked()

    def _readback_locked(self):
        state = self._state()
        best = state["best"]
        problems, files, immutable = [], {}, {}
        if best is None:
            problems.append("no_promoted_candidate")
        else:
            receipt = self._receipt(best["receipt_id"])
            candidate = self._candidate(best["candidate_id"])
            expected_files = {str(self._target(k)): e for k, e in candidate["files"].items()}
            if receipt["candidate_id"] != best["candidate_id"] or best["files"] != expected_files:
                raise StoreIntegrityError("publication state does not match its validated candidate")
            if receipt["input_snapshot_id"] != state["input_snapshot_id"]:
                problems.append("stale_input_validation")
            if best.get("eligible") is False or receipt.get("eligible") is False:
                problems.append("incumbent_failed_current_checks")
            for path, entry in best["files"].items():
                try:
                    verify_blob(self._authorize(path), entry)
                    files[path] = {**entry, "valid": True}
                except (OSError, StoreError):
                    files[path] = {**entry, "valid": False}
                    problems.append("published_bytes_changed:" + path)
        snapshot = self._input(state["input_snapshot_id"])
        for path in snapshot["immutable_paths"]:
            try:
                verify_blob(Path(path), snapshot["files"][path])
                immutable[path] = True
            except (OSError, StoreError):
                immutable[path] = False
                problems.append("immutable_input_changed:" + path)
        return {"valid": not problems, "candidate_id": best["candidate_id"] if best else None,
                "files": files, "problems": problems, "immutable_inputs": immutable}

    def readback(self):
        with locked(self._lock_path):
            self._recover_locked()
            return self._readback_locked()

    @property
    def store_path(self):
        return self.path

    @property
    def current_input_snapshot_id(self):
        with locked(self._lock_path):
            return self._state()["input_snapshot_id"]

    def input_snapshot(self, snapshot_id=None):
        with locked(self._lock_path):
            return self._input(snapshot_id or self._state()["input_snapshot_id"])

    def protected_input_files(self, snapshot_id=None):
        """Return verified snapshot views, read-only by convention (never work here)."""
        with locked(self._lock_path):
            snapshot = self._input(snapshot_id or self._state()["input_snapshot_id"])
            views = self._input_views(snapshot)
            self._verify_files(views)
            return {p: self.blobs / e["sha256"] for p, e in views.items()}

    def immutable_input_evidence(self):
        with locked(self._lock_path):
            snapshot = self._input(self._state()["input_snapshot_id"])
            result = []
            for path in snapshot["immutable_paths"]:
                entry = snapshot["files"][path]
                try:
                    verify_blob(Path(path), entry)
                    unchanged = True
                except (OSError, StoreError):
                    unchanged = False
                result.append({"path": path, "sha256": entry["sha256"], "size": entry["size"],
                               "exists": Path(path).is_file(), "unchanged": unchanged})
            return result

    def status(self):
        with locked(self._lock_path):
            state = self._state()
            snapshot = self._input(state["input_snapshot_id"])
            return {"schema_version": SCHEMA, "scope_id": self.scope_id,
                    "store_path": str(self.path), "workspace": str(self.workspace),
                    "input_snapshot_id": state["input_snapshot_id"],
                    "input_sha256": snapshot["input_sha256"], "best": deepcopy(state["best"]),
                    "pending_transaction": self._journal_path.exists(),
                    "candidate_count": len(list((self.path / "candidates").glob("*.json"))),
                    "receipt_count": len(list((self.path / "receipts").glob("*.json")))}

    def list_candidates(self, limit=20, offset=0):
        if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 100 or offset < 0:
            raise ValueError("candidate pagination requires limit 1..100 and nonnegative offset")
        with locked(self._lock_path):
            paths = sorted((self.path / "candidates").glob("*.json"),
                           key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
            items = []
            for path in paths[offset:offset + limit]:
                record = self._record("candidates", path.stem)
                candidate = self._candidate(path.stem)
                receipt_id = record.get("latest_receipt_id")
                receipt = self._receipt(receipt_id) if receipt_id else None
                items.append({"candidate_id": path.stem, "candidate_sha256": path.stem,
                              "files": list(candidate["files"]),
                              "input_snapshot_id": candidate["input_snapshot_id"],
                              "receipt_id": receipt_id, "eligible": receipt.get("eligible") if receipt else None,
                              "metrics": receipt["validation"]["metrics"] if receipt else {}})
            return {"items": items, "total": len(paths), "offset": offset, "limit": limit}

    def provenance(self, candidate_id=None):
        with locked(self._lock_path):
            state = self._state()
            candidate_id = candidate_id or (state["best"] or {}).get("candidate_id")
            candidate = self._candidate(candidate_id) if candidate_id else None
            snapshot = self._input(candidate["input_snapshot_id"] if candidate else state["input_snapshot_id"])
            return {"candidate_id": candidate_id, "input_snapshot_id": snapshot["snapshot_id"],
                    "artifacts": deepcopy(candidate["provenance"] if candidate else []),
                    "inputs": deepcopy(snapshot["provenance"]), "groups": deepcopy(snapshot["groups"])}
