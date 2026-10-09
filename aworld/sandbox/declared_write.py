"""Framework-owned contracts and receipts for exact public write targets.

The model may name exact paths that an opaque ``run_code`` invocation intends
to write.  Those names are only observational hints until they intersect the
hidden public-deliverable contract injected by the framework.  Providers bind
stable pre/post content versions to that intersection; consumers still keep
the command's overall effect ``unknown``.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
import posixpath
import re
import stat as stat_module
import threading
import weakref
from typing import Any, AsyncIterator, Callable, Mapping, Sequence


DECLARED_PUBLIC_WRITE_CONTRACT_SCHEMA = "aworld.declared-public-write-contract/v1"
DECLARED_PUBLIC_WRITE_RECEIPT_SCHEMA = "aworld.declared-public-write-receipt/v1"
DECLARED_PUBLIC_WRITE_CONTRACT_KEY = "declared_public_write_contract"
DECLARED_PUBLIC_WRITE_RECEIPTS_KEY = "declared_public_write_receipts"
DECLARED_PUBLIC_WRITE_AUTHORITY = "aworld_framework"
DECLARED_PUBLIC_WRITE_HASH_MAX_BYTES = 8 * 1024 * 1024
DECLARED_WRITE_LOCK_TIMEOUT_SECONDS = 30.0
_PUBLIC_DELIVERABLE_SCHEMA = "aworld.public-deliverables/v1"
_PUBLIC_DELIVERABLE_AUTHORITY = "public_task_advisory"
_MAX_TARGETS = 16
_MAX_PATH_CHARS = 4096
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")


class DeclaredWriteLeaseUnavailable(RuntimeError):
    """A process-shared write lease could not be established safely."""

    def __init__(self, reason: str = "declared_write_lease_unavailable") -> None:
        self.reason = reason
        super().__init__(reason)


class ControllerWriteBarrierTimeout(TimeoutError):
    """A public-target controller barrier could not be entered in time."""

    def __init__(self) -> None:
        self.failure_category = "task_budget"
        self.failure_code = "controller_write_barrier_timeout"
        super().__init__("controller_write_barrier_timeout")


def _canonical_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def semantic_target_sha256(path: str) -> str:
    """Hash one bounded lexical target without retaining it downstream."""

    if not isinstance(path, str) or not path.strip() or len(path) > _MAX_PATH_CHARS:
        raise ValueError("semantic target must be a bounded nonempty path")
    normalized = posixpath.normpath(path.strip().replace("\\", "/"))
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return (
        "sha256:"
        + hashlib.sha256(
            ("workspace-target/v1\0" + normalized).encode("utf-8")
        ).hexdigest()
    )


def framework_scope_values(context: Any) -> tuple[str, ...]:
    """Return the task/call scope shared by injection and observation."""

    lifecycle = getattr(context, "context_lifecycle_state", None)

    def lifecycle_value(name: str, default: Any = None) -> Any:
        return (
            lifecycle.get(name, default)
            if isinstance(lifecycle, Mapping)
            else getattr(lifecycle, name, default)
        )

    epoch = getattr(context, "task_epoch", lifecycle_value("task_epoch"))
    session_epoch = lifecycle_value("session_epoch")
    checkpoint_revision = lifecycle_value("checkpoint_revision")
    agent_info = getattr(context, "agent_info", None)
    try:
        current_agent_id = (
            agent_info.get("current_agent_id")
            if isinstance(agent_info, Mapping)
            else getattr(agent_info, "current_agent_id", None)
        )
    except (AttributeError, KeyError, TypeError):
        current_agent_id = None
    try:
        context_agent_id = getattr(context, "agent_id", None)
    except (AttributeError, KeyError, TypeError):
        context_agent_id = None
    agent_id = current_agent_id or context_agent_id
    session_id = getattr(context, "session_id", None) or lifecycle_value("session_id")
    return (
        str(getattr(context, "task_id", "") or "").strip(),
        "" if epoch is None or isinstance(epoch, bool) else str(epoch),
        str(session_id or "").strip(),
        (
            str(session_epoch)
            if isinstance(session_epoch, int)
            and not isinstance(session_epoch, bool)
            and session_epoch >= 0
            else ""
        ),
        str(lifecycle_value("branch_id") or "").strip(),
        (
            str(checkpoint_revision)
            if isinstance(checkpoint_revision, int)
            and not isinstance(checkpoint_revision, bool)
            and checkpoint_revision >= 0
            else ""
        ),
        str(agent_id or "").strip(),
        str(agent_id or "").strip(),
    )


def framework_scope_from_hidden(env_content: Any) -> tuple[str, ...]:
    if not isinstance(env_content, Mapping):
        return ("",) * 8
    epoch = env_content.get("task_epoch")
    session_epoch = env_content.get("session_epoch")
    checkpoint_revision = env_content.get("checkpoint_revision")
    return (
        str(env_content.get("task_id") or "").strip(),
        "" if epoch is None or isinstance(epoch, bool) else str(epoch),
        str(env_content.get("session_id") or "").strip(),
        (
            str(session_epoch)
            if isinstance(session_epoch, int)
            and not isinstance(session_epoch, bool)
            and session_epoch >= 0
            else ""
        ),
        str(env_content.get("branch_id") or "").strip(),
        (
            str(checkpoint_revision)
            if isinstance(checkpoint_revision, int)
            and not isinstance(checkpoint_revision, bool)
            and checkpoint_revision >= 0
            else ""
        ),
        str(env_content.get("agent_id") or "").strip(),
        str(env_content.get("prompt_namespace") or "").strip(),
    )


def framework_scope_sha256(scope: Sequence[str]) -> str:
    values = tuple(scope)
    if len(values) != 8 or any(
        not isinstance(item, str) or not item for item in values
    ):
        raise ValueError("declared write scope must be complete")
    return _canonical_hash(
        {"schema_version": "aworld.framework-tool-scope/v1", "values": list(values)}
    )


def _runtime_owner(context: Any) -> Any:
    resolver = getattr(context, "_task_runtime_registry_owner", None)
    try:
        owner = resolver() if callable(resolver) else None
    except Exception:
        owner = None
    return owner or context


def _context_value(context: Any, key: str) -> Any:
    for source in (context, _runtime_owner(context)):
        context_info = getattr(source, "context_info", None)
        getter = getattr(context_info, "get", None)
        value = getter(key) if callable(getter) else None
        if value is not None:
            return value
    return None


def _workspace_root(context: Any) -> str | None:
    for source in (context, _runtime_owner(context)):
        value = getattr(source, "workspace_path", None)
        if isinstance(value, str) and value.strip():
            return os.path.abspath(os.path.expanduser(value.strip()))
    return None


def _canonical_contract_path(path: str, *, workspace_root: str | None) -> str:
    if (
        not isinstance(path, str)
        or not path.strip()
        or len(path) > _MAX_PATH_CHARS
        or any(marker in path for marker in ("\0", "\r", "\n"))
    ):
        raise ValueError("declared public path is invalid")
    candidate = os.path.expanduser(path.strip())
    if not os.path.isabs(candidate):
        if workspace_root is None:
            raise ValueError("relative declared public path has no workspace")
        candidate = os.path.join(workspace_root, candidate)
    return os.path.abspath(candidate)


def build_declared_public_write_contract(context: Any) -> dict[str, Any] | None:
    """Project the trusted public-deliverable contract into hidden Tool data."""

    value = _context_value(context, "public_deliverable_contract")
    if (
        not isinstance(value, Mapping)
        or value.get("schema_version") != _PUBLIC_DELIVERABLE_SCHEMA
        or value.get("authority") != _PUBLIC_DELIVERABLE_AUTHORITY
        or value.get("source") != "public_task_text"
    ):
        return None
    artifacts = value.get("artifacts")
    if (
        not isinstance(artifacts, list)
        or not artifacts
        or len(artifacts) > _MAX_TARGETS
    ):
        return None
    try:
        scope_sha256 = framework_scope_sha256(framework_scope_values(context))
    except ValueError:
        return None
    workspace_root = _workspace_root(context)
    targets: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        for item in artifacts:
            if (
                not isinstance(item, Mapping)
                or item.get("kind") != "file"
                or item.get("authority") != _PUBLIC_DELIVERABLE_AUTHORITY
                or not isinstance(item.get("deliverable_id"), str)
                or not item["deliverable_id"]
                or len(item["deliverable_id"]) > 256
                or not isinstance(item.get("path"), str)
            ):
                return None
            path = _canonical_contract_path(item["path"], workspace_root=workspace_root)
            target_id = semantic_target_sha256(path)
            if target_id in seen:
                return None
            seen.add(target_id)
            targets.append(
                {
                    "deliverable_id": item["deliverable_id"],
                    "path": path,
                    "target_id": target_id,
                }
            )
    except (OSError, ValueError):
        return None
    payload: dict[str, Any] = {
        "schema_version": DECLARED_PUBLIC_WRITE_CONTRACT_SCHEMA,
        "authority": DECLARED_PUBLIC_WRITE_AUTHORITY,
        "scope_sha256": scope_sha256,
        "targets": targets,
    }
    payload["contract_sha256"] = _canonical_hash(payload)
    return payload


def validate_declared_public_write_contract(
    value: Any,
    *,
    expected_scope_sha256: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "authority",
        "scope_sha256",
        "targets",
        "contract_sha256",
    }:
        return None
    if (
        value.get("schema_version") != DECLARED_PUBLIC_WRITE_CONTRACT_SCHEMA
        or value.get("authority") != DECLARED_PUBLIC_WRITE_AUTHORITY
        or _SHA256.fullmatch(str(value.get("scope_sha256") or "")) is None
        or _SHA256.fullmatch(str(value.get("contract_sha256") or "")) is None
        or (
            expected_scope_sha256 is not None
            and value.get("scope_sha256") != expected_scope_sha256
        )
    ):
        return None
    raw_targets = value.get("targets")
    if (
        not isinstance(raw_targets, list)
        or not raw_targets
        or len(raw_targets) > _MAX_TARGETS
    ):
        return None
    targets: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_targets:
        if not isinstance(item, Mapping) or set(item) != {
            "deliverable_id",
            "path",
            "target_id",
        }:
            return None
        deliverable_id = item.get("deliverable_id")
        path = item.get("path")
        target_id = item.get("target_id")
        if (
            not isinstance(deliverable_id, str)
            or not deliverable_id
            or len(deliverable_id) > 256
            or not isinstance(path, str)
            or not os.path.isabs(path)
            or len(path) > _MAX_PATH_CHARS
            or any(marker in path for marker in ("\0", "\r", "\n"))
            or target_id != semantic_target_sha256(path)
            or target_id in seen
        ):
            return None
        seen.add(target_id)
        targets.append(dict(item))
    payload = {
        "schema_version": value["schema_version"],
        "authority": value["authority"],
        "scope_sha256": value["scope_sha256"],
        "targets": targets,
    }
    if value.get("contract_sha256") != _canonical_hash(payload):
        return None
    payload["contract_sha256"] = value["contract_sha256"]
    return payload


def normalize_declared_write_paths(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or not value or len(value) > _MAX_TARGETS:
        raise ValueError("declared_write_paths must contain 1-16 exact paths")
    paths: list[str] = []
    for path in value:
        if (
            not isinstance(path, str)
            or not path.strip()
            or path != path.strip()
            or len(path) > _MAX_PATH_CHARS
            or any(marker in path for marker in ("\0", "\r", "\n"))
            or any(marker in path for marker in ("*", "?", "[", "]"))
            or path in paths
        ):
            raise ValueError("declared_write_paths must contain unique exact paths")
        paths.append(path)
    return tuple(paths)


def declared_write_operation_sha256(
    *,
    code: str,
    language: str,
    cwd: str | None,
    declared_write_paths: Any,
) -> str:
    if not isinstance(code, str) or language not in {"shell", "python"}:
        raise ValueError("invalid declared write operation")
    paths = normalize_declared_write_paths(declared_write_paths)
    if not paths:
        raise ValueError("declared write operation requires exact paths")
    if cwd is not None and (not isinstance(cwd, str) or len(cwd) > _MAX_PATH_CHARS):
        raise ValueError("invalid declared write cwd")
    return _canonical_hash(
        {
            "schema_version": "aworld.declared-write-operation/v1",
            "command_sha256": "sha256:"
            + hashlib.sha256(code.encode("utf-8")).hexdigest(),
            "language": language,
            "cwd": cwd,
            "declared_write_paths": list(paths),
        }
    )


def authorized_declared_write_targets(
    declared_write_paths: Any,
    contract: Mapping[str, Any] | None,
    *,
    normalize_path: Callable[[str], str],
) -> tuple[dict[str, str], ...]:
    """Return only exact requested paths present in the hidden contract."""

    if contract is None:
        return ()
    try:
        requested = normalize_declared_write_paths(declared_write_paths)
    except ValueError:
        return ()
    by_target = {item["target_id"]: item for item in contract["targets"]}
    authorized: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_path in requested:
        try:
            path = normalize_path(raw_path)
            target_id = semantic_target_sha256(path)
        except (OSError, TypeError, ValueError):
            continue
        target = by_target.get(target_id)
        if target is None or target["path"] != path or target_id in seen:
            continue
        seen.add(target_id)
        authorized.append(dict(target))
    return tuple(authorized)


def _valid_version(value: Any) -> bool:
    return value == "missing" or (
        isinstance(value, str) and _SHA256.fullmatch(value) is not None
    )


def build_declared_public_write_receipt(
    *,
    scope_sha256: str,
    contract_sha256: str,
    tool_call_id: str,
    operation_sha256: str,
    deliverable_id: str,
    target_id: str,
    before_version: str,
    after_version: str,
    executed: bool,
    exit_code: int | None,
    timed_out: bool,
) -> dict[str, Any]:
    for value in (scope_sha256, contract_sha256, operation_sha256, target_id):
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise ValueError("declared write receipt hash is invalid")
    if (
        not isinstance(tool_call_id, str)
        or not tool_call_id
        or len(tool_call_id) > 512
        or not isinstance(deliverable_id, str)
        or not deliverable_id
        or len(deliverable_id) > 256
        or not _valid_version(before_version)
        or not _valid_version(after_version)
        or not isinstance(executed, bool)
        or isinstance(exit_code, bool)
        or not isinstance(exit_code, (int, type(None)))
        or not isinstance(timed_out, bool)
    ):
        raise ValueError("declared write receipt value is invalid")
    payload: dict[str, Any] = {
        "schema_version": DECLARED_PUBLIC_WRITE_RECEIPT_SCHEMA,
        "scope_sha256": scope_sha256,
        "contract_sha256": contract_sha256,
        "tool_call_id": tool_call_id,
        "operation_sha256": operation_sha256,
        "deliverable_id": deliverable_id,
        "target_id": target_id,
        "before_version": before_version,
        "after_version": after_version,
        "changed": before_version != after_version,
        "executed": executed,
        "exit_code": exit_code,
        "timed_out": timed_out,
    }
    payload["receipt_id"] = _canonical_hash(payload)
    return payload


def validate_declared_public_write_receipt(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "scope_sha256",
        "contract_sha256",
        "tool_call_id",
        "operation_sha256",
        "deliverable_id",
        "target_id",
        "before_version",
        "after_version",
        "changed",
        "executed",
        "exit_code",
        "timed_out",
        "receipt_id",
    }:
        return None
    if value.get("schema_version") != DECLARED_PUBLIC_WRITE_RECEIPT_SCHEMA:
        return None
    try:
        rebuilt = build_declared_public_write_receipt(
            **{
                key: value[key]
                for key in (
                    "scope_sha256",
                    "contract_sha256",
                    "tool_call_id",
                    "operation_sha256",
                    "deliverable_id",
                    "target_id",
                    "before_version",
                    "after_version",
                    "executed",
                    "exit_code",
                    "timed_out",
                )
            }
        )
    except (KeyError, TypeError, ValueError):
        return None
    return rebuilt if dict(value) == rebuilt else None


def local_content_version(
    path: str,
    *,
    max_bytes: int = DECLARED_PUBLIC_WRITE_HASH_MAX_BYTES,
) -> str | None:
    """Return one stable exact regular-file version, ``missing``, or unknown."""

    try:
        link_before = os.lstat(path)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return None
    if stat_module.S_ISLNK(link_before.st_mode):
        return None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            before = os.fstat(handle.fileno())
            if (
                not stat_module.S_ISREG(before.st_mode)
                or before.st_size < 0
                or before.st_size > max_bytes
            ):
                return None
            digest = hashlib.sha256()
            remaining = before.st_size
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    return None
                digest.update(chunk)
                remaining -= len(chunk)
            after = os.fstat(handle.fileno())
        link_after = os.lstat(path)
    except OSError:
        return None

    def identity(item: os.stat_result) -> tuple[int, ...]:
        return (
            item.st_dev,
            item.st_ino,
            item.st_mode,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )

    if identity(link_before) != identity(before) or identity(before) != identity(after):
        return None
    if identity(link_before) != identity(link_after):
        return None
    return "sha256:" + digest.hexdigest()


def content_versions_for_targets(
    targets: Sequence[Mapping[str, str]],
) -> dict[str, str] | None:
    versions: dict[str, str] = {}
    for target in targets:
        version = local_content_version(target["path"])
        if version is None:
            return None
        versions[target["target_id"]] = version
    return versions


def build_receipts_for_versions(
    targets: Sequence[Mapping[str, str]],
    *,
    before_versions: Mapping[str, str] | None,
    after_versions: Mapping[str, str] | None,
    contract: Mapping[str, Any] | None,
    tool_call_id: Any,
    operation_sha256: str | None,
    executed: bool,
    exit_code: int | None,
    timed_out: bool,
) -> tuple[dict[str, Any], ...]:
    if (
        not targets
        or before_versions is None
        or after_versions is None
        or contract is None
        or not isinstance(tool_call_id, str)
        or not tool_call_id
        or operation_sha256 is None
    ):
        return ()
    receipts: list[dict[str, Any]] = []
    for target in targets:
        target_id = target["target_id"]
        before = before_versions.get(target_id)
        after = after_versions.get(target_id)
        if not _valid_version(before) or not _valid_version(after):
            continue
        receipts.append(
            build_declared_public_write_receipt(
                scope_sha256=contract["scope_sha256"],
                contract_sha256=contract["contract_sha256"],
                tool_call_id=tool_call_id,
                operation_sha256=operation_sha256,
                deliverable_id=target["deliverable_id"],
                target_id=target_id,
                before_version=before,
                after_version=after,
                executed=executed,
                exit_code=exit_code,
                timed_out=timed_out,
            )
        )
    return tuple(receipts)


def _lease_path(path: str) -> str:
    value = posixpath.normpath(path.replace("\\", "/"))
    if value.startswith("//"):
        value = "/" + value.lstrip("/")
    return value.rstrip("/") or "/"


def _paths_overlap(first: str, second: str) -> bool:
    if first == second:
        return True
    first_prefix = first if first.endswith("/") else first + "/"
    second_prefix = second if second.endswith("/") else second + "/"
    return first.startswith(second_prefix) or second.startswith(first_prefix)


def _bounded_lease_timeout(timeout_seconds: float | None) -> float:
    try:
        requested_timeout = float(
            DECLARED_WRITE_LOCK_TIMEOUT_SECONDS
            if timeout_seconds is None
            else timeout_seconds
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise DeclaredWriteLeaseUnavailable() from exc
    if not 0 < requested_timeout <= 86_400:
        raise DeclaredWriteLeaseUnavailable()
    return min(requested_timeout, DECLARED_WRITE_LOCK_TIMEOUT_SECONDS)


class _PathLeaseManager:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._active: dict[int, tuple[str, tuple[str, ...]]] = {}
        self._waiting: list[tuple[int, str, tuple[str, ...]]] = []
        self._next_ticket = 0

    @staticmethod
    def _overlaps(
        namespace: str,
        paths: tuple[str, ...],
        other_namespace: str,
        other_paths: tuple[str, ...],
    ) -> bool:
        return namespace == other_namespace and any(
            _paths_overlap(path, other) for path in paths for other in other_paths
        )

    async def acquire(self, namespace: str, paths: Sequence[str]) -> int | None:
        normalized = tuple(sorted({_lease_path(path) for path in paths if path}))
        if not normalized:
            return None
        async with self._condition:
            ticket = self._next_ticket
            self._next_ticket += 1
            request = (ticket, namespace, normalized)
            self._waiting.append(request)
            try:
                while True:
                    active_conflict = any(
                        self._overlaps(
                            namespace,
                            normalized,
                            active_ns,
                            active_paths,
                        )
                        for active_ns, active_paths in self._active.values()
                    )
                    earlier_conflict = any(
                        prior_ticket < ticket
                        and self._overlaps(
                            namespace,
                            normalized,
                            prior_ns,
                            prior_paths,
                        )
                        for prior_ticket, prior_ns, prior_paths in self._waiting
                    )
                    if not active_conflict and not earlier_conflict:
                        self._waiting.remove(request)
                        self._active[ticket] = (namespace, normalized)
                        return ticket
                    await self._condition.wait()
            except BaseException:
                if request in self._waiting:
                    self._waiting.remove(request)
                    self._condition.notify_all()
                raise

    async def release(self, ticket: int | None) -> None:
        if ticket is None:
            return
        async with self._condition:
            self._active.pop(ticket, None)
            self._condition.notify_all()


_LEASE_MANAGERS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, weakref.ReferenceType[_PathLeaseManager]]" = weakref.WeakKeyDictionary()
_LEASE_MANAGERS_LOCK = threading.Lock()


def _retire_lease_manager(
    manager_reference: weakref.ReferenceType[_PathLeaseManager],
    *,
    loop_reference: weakref.ReferenceType[asyncio.AbstractEventLoop],
) -> None:
    loop = loop_reference()
    if loop is None:
        return
    with _LEASE_MANAGERS_LOCK:
        if _LEASE_MANAGERS.get(loop) is manager_reference:
            _LEASE_MANAGERS.pop(loop, None)


def _lease_manager() -> _PathLeaseManager:
    loop = asyncio.get_running_loop()
    with _LEASE_MANAGERS_LOCK:
        manager_reference = _LEASE_MANAGERS.get(loop)
        manager = manager_reference() if manager_reference is not None else None
        if manager is None:
            manager = _PathLeaseManager()
            # A contended ``asyncio.Condition`` binds back to its event loop.
            # Keeping the manager strongly in a weak-key map would therefore
            # form registry -> manager -> loop and prevent the weak key from
            # ever dying. Active/waiting lease contexts already keep their
            # manager alive, so a weak value preserves sharing while work
            # exists and releases closed loops once it does not.
            loop_reference = weakref.ref(loop)
            manager_reference = weakref.ref(
                manager,
                lambda reference: _retire_lease_manager(
                    reference,
                    loop_reference=loop_reference,
                ),
            )
            _LEASE_MANAGERS[loop] = manager_reference
        return manager


@asynccontextmanager
async def overlapping_path_leases(
    namespace: str,
    paths: Sequence[str],
    *,
    timeout_seconds: float | None = None,
) -> AsyncIterator[None]:
    """Serialize cooperative overlapping targets within one event loop."""

    active_paths = tuple(path for path in paths if path)
    if not active_paths:
        yield
        return
    if any(
        not isinstance(path, str)
        or len(path) > _MAX_PATH_CHARS
        or any(marker in path for marker in ("\0", "\r", "\n"))
        for path in active_paths
    ):
        raise DeclaredWriteLeaseUnavailable()
    manager = _lease_manager()
    lease_timeout = _bounded_lease_timeout(timeout_seconds)
    try:
        ticket = await asyncio.wait_for(
            manager.acquire(namespace, active_paths),
            timeout=lease_timeout,
        )
    except asyncio.TimeoutError as exc:
        raise DeclaredWriteLeaseUnavailable("declared_write_lease_timeout") from exc
    try:
        yield
    finally:
        release_task = asyncio.create_task(manager.release(ticket))
        try:
            await asyncio.shield(release_task)
        except asyncio.CancelledError:
            await release_task
            raise


class _ControllerLeaseState:
    __slots__ = ("paths", "body_open", "retained_tasks")

    def __init__(self, paths: tuple[str, ...]) -> None:
        self.paths = paths
        self.body_open = True
        self.retained_tasks: set[Any] = set()


class ControllerWriteBarrierLease:
    """One trusted parent-dispatch lease, transferable to live provider tasks."""

    __slots__ = ("_manager", "_ticket", "paths", "_released")

    def __init__(
        self,
        manager: "_ControllerWriteBarrierManager",
        ticket: int,
        paths: tuple[str, ...],
    ) -> None:
        self._manager = manager
        self._ticket = ticket
        self.paths = paths
        self._released = False

    def retain_until(self, task: Any) -> None:
        """Keep the target barrier closed until a cancelled provider is quiescent."""

        self._manager.retain_until(self._ticket, task)

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._manager.release_body(self._ticket)


class _ControllerWriteBarrierManager:
    """Process-global, cross-event-loop barrier for public artifact targets."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[int, _ControllerLeaseState] = {}
        self._waiting: list[tuple[int, tuple[str, ...]]] = []
        self._next_ticket = 0

    @staticmethod
    def _normalized_paths(paths: Sequence[str]) -> tuple[str, ...]:
        normalized = tuple(sorted({_lease_path(path) for path in paths if path}))
        if (
            not normalized
            or len(normalized) > _MAX_TARGETS
            or any(
                len(path) > _MAX_PATH_CHARS
                or any(marker in path for marker in ("\0", "\r", "\n"))
                for path in normalized
            )
        ):
            raise DeclaredWriteLeaseUnavailable()
        return normalized

    @staticmethod
    def _overlaps(first: Sequence[str], second: Sequence[str]) -> bool:
        return any(_paths_overlap(path, other) for path in first for other in second)

    def enqueue(self, paths: Sequence[str]) -> tuple[int, tuple[str, ...]]:
        normalized = self._normalized_paths(paths)
        with self._lock:
            ticket = self._next_ticket
            self._next_ticket += 1
            request = (ticket, normalized)
            self._waiting.append(request)
            return request

    def try_acquire(
        self, request: tuple[int, tuple[str, ...]]
    ) -> ControllerWriteBarrierLease | None:
        ticket, paths = request
        with self._lock:
            if request not in self._waiting:
                return None
            if any(
                self._overlaps(paths, state.paths) for state in self._active.values()
            ):
                return None
            if any(
                prior_ticket < ticket and self._overlaps(paths, prior_paths)
                for prior_ticket, prior_paths in self._waiting
            ):
                return None
            self._waiting.remove(request)
            self._active[ticket] = _ControllerLeaseState(paths)
        return ControllerWriteBarrierLease(self, ticket, paths)

    def cancel_wait(self, request: tuple[int, tuple[str, ...]]) -> None:
        with self._lock:
            if request in self._waiting:
                self._waiting.remove(request)

    def release_body(self, ticket: int) -> None:
        with self._lock:
            state = self._active.get(ticket)
            if state is None:
                return
            state.body_open = False
            state.retained_tasks = {
                task for task in state.retained_tasks if not task.done()
            }
            if not state.retained_tasks:
                self._active.pop(ticket, None)

    def retain_until(self, ticket: int, task: Any) -> None:
        if task.done():
            return
        with self._lock:
            state = self._active.get(ticket)
            if state is None or task in state.retained_tasks:
                return
            state.retained_tasks.add(task)
        task.add_done_callback(
            lambda completed, retained_ticket=ticket: self._provider_done(
                retained_ticket, completed
            )
        )

    def _provider_done(self, ticket: int, task: Any) -> None:
        with self._lock:
            state = self._active.get(ticket)
            if state is None:
                return
            state.retained_tasks.discard(task)
            if not state.body_open and not state.retained_tasks:
                self._active.pop(ticket, None)


_CONTROLLER_WRITE_BARRIERS = _ControllerWriteBarrierManager()


def controller_public_write_paths(context: Any) -> tuple[str, ...]:
    """Resolve trusted public targets for one parent-side provider dispatch."""

    contract = build_declared_public_write_contract(context)
    if contract is None:
        return ()
    return tuple(target["path"] for target in contract["targets"])


@asynccontextmanager
async def controller_public_write_barrier(
    context: Any,
    *,
    timeout_seconds: float | None = None,
) -> AsyncIterator[ControllerWriteBarrierLease | None]:
    """Serialize all provider calls that can affect overlapping public targets."""

    paths = controller_public_write_paths(context)
    if not paths:
        yield None
        return
    if timeout_seconds is not None:
        try:
            bounded_timeout = float(timeout_seconds)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ControllerWriteBarrierTimeout() from exc
        if not 0 < bounded_timeout <= 86_400:
            raise ControllerWriteBarrierTimeout()
        deadline = asyncio.get_running_loop().time() + bounded_timeout
    else:
        deadline = None
    request = _CONTROLLER_WRITE_BARRIERS.enqueue(paths)
    lease: ControllerWriteBarrierLease | None = None
    try:
        while lease is None:
            lease = _CONTROLLER_WRITE_BARRIERS.try_acquire(request)
            if lease is None:
                remaining = (
                    deadline - asyncio.get_running_loop().time()
                    if deadline is not None
                    else None
                )
                if remaining is not None and remaining <= 0:
                    raise ControllerWriteBarrierTimeout()
                await asyncio.sleep(
                    0.01 if remaining is None else min(0.01, remaining)
                )
    except BaseException:
        _CONTROLLER_WRITE_BARRIERS.cancel_wait(request)
        raise
    try:
        yield lease
    finally:
        lease.release()


__all__ = [
    "DECLARED_PUBLIC_WRITE_AUTHORITY",
    "DECLARED_PUBLIC_WRITE_CONTRACT_KEY",
    "DECLARED_PUBLIC_WRITE_CONTRACT_SCHEMA",
    "DECLARED_PUBLIC_WRITE_HASH_MAX_BYTES",
    "DECLARED_PUBLIC_WRITE_RECEIPTS_KEY",
    "DECLARED_PUBLIC_WRITE_RECEIPT_SCHEMA",
    "ControllerWriteBarrierTimeout",
    "ControllerWriteBarrierLease",
    "DeclaredWriteLeaseUnavailable",
    "authorized_declared_write_targets",
    "build_declared_public_write_contract",
    "build_declared_public_write_receipt",
    "build_receipts_for_versions",
    "content_versions_for_targets",
    "controller_public_write_barrier",
    "controller_public_write_paths",
    "declared_write_operation_sha256",
    "framework_scope_from_hidden",
    "framework_scope_sha256",
    "framework_scope_values",
    "local_content_version",
    "normalize_declared_write_paths",
    "overlapping_path_leases",
    "semantic_target_sha256",
    "validate_declared_public_write_contract",
    "validate_declared_public_write_receipt",
]
