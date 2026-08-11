# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Executable evidence-lifecycle policy for self-evolve replay tools."""

from __future__ import annotations

import json
import hashlib
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


REPLAY_EVIDENCE_POLICY_SCHEMA_VERSION = "aworld.replay.evidence_policy.v1"
_CONTROL_FILES = frozenset(
    {
        "evidence_manifest.jsonl",
        "evidence_bundle.json",
        "execution_request.json",
        "framework_evidence_policy.jsonl",
        "framework_evidence_state.json",
        "metrics.json",
        "trajectory.json",
        "stdout.txt",
        "stderr.txt",
    }
)
_MANIFEST_PAYLOAD_KEYS = frozenset(
    {
        "excerpt",
        "excerpts",
        "bounded_excerpt",
        "bounded_excerpts",
        "field_list",
        "fields",
        "fields_extracted",
        "key_fields",
        "claims_supported",
        "claims_supported_by",
        "summary",
        "structured_summary",
        "metadata",
    }
)
_LOOPBACK_ENDPOINT_PATTERN = re.compile(
    r"(?i)(?P<scheme>https?|wss?|tcp)://"
    r"(?P<host>localhost|127(?:\.\d{1,3}){3}|\[::1\])"
    r"(?::(?P<port>\d{1,5}))?"
)
_PROTECTED_RUNTIME_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:export\s+)?"
    r"(?:HOME|TMPDIR|XDG_(?:CONFIG|CACHE|DATA|STATE)_HOME|AWORLD_MEMORY_ROOT)\s*="
)
_CONTROL_PLANE_COMMAND = re.compile(
    r"(?i)(?:^|[;&|]\s*|\bsudo\s+)"
    r"(?P<command>kill|pkill|killall|systemctl|service|launchctl)\b|"
    r"\b(?P<container>docker|podman)\s+"
    r"(?P<container_action>stop|restart|kill|rm)\b"
)
_HOST_DISCOVERY_COMMAND = re.compile(
    r"(?i)(?:^|[;&|]\s*|\bsudo\s+)"
    r"(?P<command>lsof|netstat|ss|nmap|pgrep|ps)\b"
)
_CONTROL_PLANE_ACTION_NAMES = frozenset(
    {
        "kill",
        "terminate",
        "restart",
        "stop",
        "reconfigure",
        "replace",
    }
)
_COMMAND_PARAMETER_KEYS = frozenset(
    {"command", "cmd", "script", "shell", "shell_command"}
)
_ENVIRONMENT_PARAMETER_KEYS = frozenset({"env", "environment"})
_PROTECTED_RUNTIME_ROOT_KEYS = frozenset(
    {
        "home",
        "tmpdir",
        "xdg_config_home",
        "xdg_cache_home",
        "xdg_data_home",
        "xdg_state_home",
        "aworld_memory_root",
    }
)
_REPLAY_OWNED_BROWSER_CLEANUP_BINARIES = frozenset(
    {"agent-browser", "browser-use"}
)
_REPLAY_OWNED_BROWSER_CLEANUP_ACTIONS = frozenset(
    {"close", "quit"}
)
_SAFE_CLEANUP_REDIRECTION = re.compile(
    r"^(?:(?:[012])?(?:>>?|<)/dev/null|[012]?>&[012]|[012]?<&[012])$"
)


@dataclass(frozen=True)
class ReplayRuntimePolicy:
    """Typed, payload-free policy compiled from the replay environment."""

    artifact_file_limit: int
    artifact_byte_limit: int
    max_consecutive_failed_actions: int
    allowed_loopback_endpoints: frozenset[str]
    allowed_control_actions: frozenset[str]

    @classmethod
    def from_environment(cls) -> "ReplayRuntimePolicy":
        return cls(
            artifact_file_limit=_positive_limit(
                "AWORLD_REPLAY_ARTIFACT_FILE_LIMIT",
                default=8,
            ),
            artifact_byte_limit=_positive_limit(
                "AWORLD_REPLAY_ARTIFACT_BYTE_LIMIT",
                default=2_000_000,
            ),
            max_consecutive_failed_actions=_positive_limit(
                "AWORLD_REPLAY_MAX_CONSECUTIVE_FAILED_ACTIONS",
                default=2,
            ),
            allowed_loopback_endpoints=frozenset(
                endpoint
                for name, value in os.environ.items()
                if name.startswith("AWORLD_REPLAY_ENDPOINT_")
                for endpoint in _loopback_endpoints(value)
            ),
            allowed_control_actions=frozenset(
                token.strip().casefold()
                for token in os.environ.get(
                    "AWORLD_REPLAY_ALLOWED_CONTROL_ACTIONS", ""
                ).split(",")
                if token.strip()
            ),
        )

    def public_state(self) -> dict[str, Any]:
        return {
            "artifact_file_limit": self.artifact_file_limit,
            "artifact_byte_limit": self.artifact_byte_limit,
            "max_consecutive_failed_actions": (
                self.max_consecutive_failed_actions
            ),
            "allowed_loopback_endpoint_count": len(
                self.allowed_loopback_endpoints
            ),
            "allowed_control_action_count": len(
                self.allowed_control_actions
            ),
        }


def enforce_replay_evidence_runtime_policy(
    tool_name: str,
    actions: Iterable[Any],
    message: Any,
) -> str | None:
    """Enforce replay evidence collection and return a violation code.

    The policy is opt-in for self-evolve replay subprocesses. It operates on
    bounded lifecycle metadata only; tool arguments and evidence payloads are
    never persisted in its state or violation records.
    """

    if os.environ.get("AWORLD_REPLAY_EVIDENCE_POLICY") != "1":
        return None
    artifact_root_value = os.environ.get(
        "AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"
    )
    manifest_value = os.environ.get("AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST")
    if not artifact_root_value or not manifest_value:
        return None
    action_items = tuple(actions or ())
    artifact_root = Path(artifact_root_value)
    manifest_path = Path(manifest_value)
    artifact_root.mkdir(parents=True, exist_ok=True)
    manifest_entry_count = _valid_manifest_entry_count(
        manifest_path,
        artifact_root=artifact_root,
    )
    artifact_file_count, artifact_bytes = _artifact_inventory(artifact_root)
    policy = ReplayRuntimePolicy.from_environment()
    owner = getattr(message, "context", None) or message
    attempt_count = int(
        getattr(owner, "_aworld_replay_evidence_policy_attempt_count", 0) or 0
    ) + len(action_items)
    setattr(
        owner,
        "_aworld_replay_evidence_policy_attempt_count",
        attempt_count,
    )
    phase = "evidence_ready" if manifest_entry_count else "collecting"
    state = {
        "schema_version": REPLAY_EVIDENCE_POLICY_SCHEMA_VERSION,
        "enforcement": "tool_boundary",
        "phase": phase,
        "tool_call_attempt_count": attempt_count,
        "manifest_entry_count": manifest_entry_count,
        "artifact_file_count": artifact_file_count,
        "artifact_bytes": artifact_bytes,
        "last_failed_action_fingerprint": (
            getattr(
                owner,
                "_aworld_replay_last_failed_action_fingerprint",
                None,
            )
            or None
        ),
        "consecutive_failed_action_count": int(
            getattr(
                owner,
                "_aworld_replay_consecutive_failed_action_count",
                0,
            )
            or 0
        ),
        **policy.public_state(),
    }
    _write_state(artifact_root, state)

    violation_code: str | None = None
    violation_metadata: dict[str, Any] = {}
    if manifest_entry_count and _allow_single_evidence_ready_cleanup(
        action_items,
        owner=owner,
    ):
        state.update(
            {
                "phase": "finalizing",
                "finalization_action_count": 1,
            }
        )
        _write_state(artifact_root, state)
        return None
    if manifest_entry_count:
        violation_code = "tool_call_after_evidence_ready"
    elif artifact_file_count >= policy.artifact_file_limit:
        violation_code = "artifact_file_limit_exhausted"
    elif artifact_bytes >= policy.artifact_byte_limit:
        violation_code = "artifact_byte_limit_exhausted"
    else:
        for item in action_items:
            violation_code, violation_metadata = _action_policy_violation(
                item,
                owner=owner,
                policy=policy,
            )
            if violation_code is not None:
                break
    if violation_code is None:
        return None

    first_action = action_items[0] if action_items else None
    violation = {
        **state,
        "code": violation_code,
        "tool_name": str(tool_name or "unknown")[:128],
        "action_name": str(
            getattr(first_action, "action_name", None) or "unknown"
        )[:128],
        **violation_metadata,
        "required_transition": _required_transition(
            violation_code,
            phase=phase,
        ),
    }
    _append_violation(artifact_root, violation)
    return violation_code


def _allow_single_evidence_ready_cleanup(
    actions: tuple[Any, ...],
    *,
    owner: Any,
) -> bool:
    """Allow one narrow cleanup of a replay-owned browser after evidence.

    Evidence-ready blocks further collection, but replay-created resources may
    still be released.  The prior blanket denial converted ``agent-browser
    close`` into a task failure and triggered an expensive evidence retry.  A
    single exact cleanup action is safe because replay subprocesses use isolated
    runtime roots; arbitrary shell, host-control, and repeated actions remain
    denied.
    """

    if len(actions) != 1:
        return False
    if int(
        getattr(owner, "_aworld_replay_finalization_action_count", 0) or 0
    ):
        return False
    if not _is_replay_owned_browser_cleanup(actions[0]):
        return False
    setattr(owner, "_aworld_replay_finalization_action_count", 1)
    return True


def _is_replay_owned_browser_cleanup(action: Any) -> bool:
    command_texts = _command_texts(action)
    if len(command_texts) != 1:
        return False
    try:
        tokens = shlex.split(command_texts[0])
    except ValueError:
        return False
    if len(tokens) < 2 or len(tokens) > 5:
        return False
    binary = Path(tokens[0]).name.casefold()
    cleanup_action = tokens[1].casefold()
    return bool(
        binary in _REPLAY_OWNED_BROWSER_CLEANUP_BINARIES
        and cleanup_action in _REPLAY_OWNED_BROWSER_CLEANUP_ACTIONS
        and all(
            _SAFE_CLEANUP_REDIRECTION.fullmatch(token) is not None
            for token in tokens[2:]
        )
    )


def record_replay_runtime_tool_result(
    actions: Iterable[Any],
    result: Any,
    message: Any,
) -> None:
    """Record consecutive failed action paths without retaining payloads."""

    if os.environ.get("AWORLD_REPLAY_EVIDENCE_POLICY") != "1":
        return
    artifact_root_value = os.environ.get(
        "AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"
    )
    if not artifact_root_value:
        return
    action_items = tuple(actions or ())
    action_results = _action_results(result)
    if not action_items or not action_results:
        return
    owner = getattr(message, "context", None) or message
    last_fingerprint = str(
        getattr(owner, "_aworld_replay_last_failed_action_fingerprint", "")
        or ""
    )
    consecutive_count = int(
        getattr(owner, "_aworld_replay_consecutive_failed_action_count", 0)
        or 0
    )
    for action, action_result in zip(action_items, action_results):
        fingerprint = _action_fingerprint(action)
        if _action_result_succeeded(action_result):
            last_fingerprint = ""
            consecutive_count = 0
            continue
        if fingerprint == last_fingerprint:
            consecutive_count += 1
        else:
            last_fingerprint = fingerprint
            consecutive_count = 1
    setattr(
        owner,
        "_aworld_replay_last_failed_action_fingerprint",
        last_fingerprint,
    )
    setattr(
        owner,
        "_aworld_replay_consecutive_failed_action_count",
        consecutive_count,
    )
    artifact_root = Path(artifact_root_value)
    state = _read_state(artifact_root)
    state.update(
        {
            "schema_version": REPLAY_EVIDENCE_POLICY_SCHEMA_VERSION,
            "last_failed_action_fingerprint": last_fingerprint or None,
            "consecutive_failed_action_count": consecutive_count,
        }
    )
    _write_state(artifact_root, state)


def _action_policy_violation(
    action: Any,
    *,
    owner: Any,
    policy: ReplayRuntimePolicy,
) -> tuple[str | None, dict[str, Any]]:
    fingerprint = _action_fingerprint(action)
    last_fingerprint = str(
        getattr(owner, "_aworld_replay_last_failed_action_fingerprint", "")
        or ""
    )
    consecutive_failures = int(
        getattr(owner, "_aworld_replay_consecutive_failed_action_count", 0)
        or 0
    )
    if (
        fingerprint == last_fingerprint
        and consecutive_failures >= policy.max_consecutive_failed_actions
    ):
        return "repeated_failed_action_limit", {
            "action_fingerprint": fingerprint,
            "consecutive_failure_count": consecutive_failures,
        }

    serialized = _bounded_action_parameters(action)
    observed_endpoints = _loopback_endpoints(serialized)
    undeclared_endpoints = observed_endpoints - policy.allowed_loopback_endpoints
    if undeclared_endpoints:
        return "undeclared_loopback_endpoint", {
            "action_fingerprint": fingerprint,
            "observed_endpoint_count": len(observed_endpoints),
            "undeclared_endpoint_count": len(undeclared_endpoints),
        }

    command_texts = _command_texts(action)
    if _protected_runtime_root_override(action, command_texts):
        return "protected_runtime_root_override", {
            "action_fingerprint": fingerprint,
        }

    control_actions = _control_plane_actions(action, command_texts)
    unauthorized = {
        item
        for item in control_actions
        if "*" not in policy.allowed_control_actions
        and item not in policy.allowed_control_actions
    }
    if unauthorized:
        return "unauthorized_control_plane_action", {
            "action_fingerprint": fingerprint,
            "control_action_count": len(unauthorized),
        }
    discovery_actions = _host_discovery_actions(command_texts)
    unauthorized_discovery = {
        item
        for item in discovery_actions
        if "*" not in policy.allowed_control_actions
        and item not in policy.allowed_control_actions
    }
    if unauthorized_discovery:
        return "host_discovery_forbidden", {
            "action_fingerprint": fingerprint,
            "control_action_count": len(unauthorized_discovery),
        }
    return None, {}


def _required_transition(code: str, *, phase: str) -> str:
    transitions = {
        "tool_call_after_evidence_ready": "finalize_task_response",
        "artifact_file_limit_exhausted": (
            "persist_bounded_evidence_or_reduce_collection"
        ),
        "artifact_byte_limit_exhausted": (
            "persist_bounded_evidence_or_reduce_collection"
        ),
        "repeated_failed_action_limit": (
            "switch_strategy_or_fail_with_observed_reason"
        ),
        "undeclared_loopback_endpoint": (
            "use_declared_replay_endpoint_or_fail_prerequisite"
        ),
        "protected_runtime_root_override": "preserve_isolated_runtime_roots",
        "unauthorized_control_plane_action": (
            "attach_without_control_plane_mutation"
        ),
        "host_discovery_forbidden": (
            "use_declared_replay_endpoint_or_fail_prerequisite"
        ),
    }
    return transitions.get(
        code,
        (
            "finalize_task_response"
            if phase == "evidence_ready"
            else "satisfy_replay_runtime_policy"
        ),
    )


def _action_fingerprint(action: Any) -> str:
    payload = {
        "tool_name": str(getattr(action, "tool_name", None) or ""),
        "action_name": str(getattr(action, "action_name", None) or ""),
        "params": getattr(action, "params", None),
    }
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    except (TypeError, ValueError):
        encoded = repr(payload).encode("utf-8", errors="replace")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _bounded_action_parameters(action: Any) -> str:
    try:
        serialized = json.dumps(
            getattr(action, "params", None),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    except (TypeError, ValueError):
        serialized = repr(getattr(action, "params", None))
    return serialized[:65_536]


def _loopback_endpoints(value: str) -> frozenset[str]:
    return frozenset(
        _loopback_authority(match)
        for match in _LOOPBACK_ENDPOINT_PATTERN.finditer(str(value or ""))
    )


def _loopback_authority(match: re.Match[str]) -> str:
    scheme = match.group("scheme").casefold()
    host = match.group("host").casefold()
    port = match.group("port")
    if port is None:
        port = "443" if scheme in {"https", "wss"} else "80"
    return f"{host}:{port}"


def _protected_runtime_root_override(
    action: Any,
    command_texts: Iterable[str],
) -> bool:
    if any(_PROTECTED_RUNTIME_ASSIGNMENT.search(text) for text in command_texts):
        return True
    params = getattr(action, "params", None)
    if not isinstance(params, Mapping):
        return False
    for raw_key, value in params.items():
        key = str(raw_key).casefold()
        if key in _PROTECTED_RUNTIME_ROOT_KEYS:
            return True
        if key not in _ENVIRONMENT_PARAMETER_KEYS or not isinstance(value, Mapping):
            continue
        if any(
            str(environment_key).casefold() in _PROTECTED_RUNTIME_ROOT_KEYS
            for environment_key in value
        ):
            return True
    return False


def _command_texts(action: Any) -> tuple[str, ...]:
    params = getattr(action, "params", None)
    result: list[str] = []

    def visit(value: Any, *, key: str | None = None, depth: int = 0) -> None:
        if depth > 4 or len(result) >= 16:
            return
        if isinstance(value, Mapping):
            for raw_key, nested in list(value.items())[:64]:
                visit(nested, key=str(raw_key).casefold(), depth=depth + 1)
        elif isinstance(value, (list, tuple)):
            for nested in value[:64]:
                visit(nested, key=key, depth=depth + 1)
        elif key in _COMMAND_PARAMETER_KEYS and isinstance(value, str):
            result.append(value[:16_384])

    visit(params)
    return tuple(result)


def _control_plane_actions(
    action: Any,
    command_texts: Iterable[str],
) -> frozenset[str]:
    result: set[str] = set()
    action_name = str(getattr(action, "action_name", None) or "").casefold()
    if action_name in _CONTROL_PLANE_ACTION_NAMES:
        result.add(action_name)
    for text in command_texts:
        for match in _CONTROL_PLANE_COMMAND.finditer(text):
            command = match.group("command")
            if command:
                result.add(command.casefold())
                continue
            container = match.group("container")
            container_action = match.group("container_action")
            if container and container_action:
                result.add(
                    f"{container.casefold()}:{container_action.casefold()}"
                )
    return frozenset(result)


def _host_discovery_actions(
    command_texts: Iterable[str],
) -> frozenset[str]:
    return frozenset(
        match.group("command").casefold()
        for text in command_texts
        for match in _HOST_DISCOVERY_COMMAND.finditer(text)
        if match.group("command")
    )


def _action_results(result: Any) -> tuple[Any, ...]:
    if not isinstance(result, tuple) or not result:
        return ()
    observation = result[0]
    raw = getattr(observation, "action_result", None)
    return tuple(raw) if isinstance(raw, list) else ()


def _action_result_succeeded(result: Any) -> bool:
    return bool(
        getattr(result, "success", False)
        and not getattr(result, "error", None)
    )


def _positive_limit(name: str, *, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _valid_manifest_entry_count(
    manifest_path: Path,
    *,
    artifact_root: Path,
) -> int:
    try:
        raw = manifest_path.read_bytes()[:131_072].decode(
            "utf-8", errors="replace"
        )
    except OSError:
        return 0
    count = 0
    root = artifact_root.resolve(strict=False)
    for line in raw.splitlines()[:32]:
        try:
            entry = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(entry, Mapping):
            continue
        if not str(entry.get("source_id") or "").strip():
            continue
        if not str(entry.get("extraction_method") or "").strip():
            continue
        if not any(entry.get(key) for key in _MANIFEST_PAYLOAD_KEYS):
            continue
        artifact_path = entry.get("artifact_path")
        if isinstance(artifact_path, str) and artifact_path.strip():
            candidate = Path(artifact_path).expanduser()
            if not candidate.is_absolute():
                candidate = artifact_root / candidate
            resolved = candidate.resolve(strict=False)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                continue
        count += 1
    return count


def _artifact_inventory(artifact_root: Path) -> tuple[int, int]:
    file_count = 0
    total_bytes = 0
    for current_root, directories, filenames in os.walk(
        artifact_root,
        followlinks=False,
    ):
        directories[:] = [
            name
            for name in directories[:64]
            if name != "logs" and not (Path(current_root) / name).is_symlink()
        ]
        for name in filenames[:256]:
            if name in _CONTROL_FILES:
                continue
            path = Path(current_root) / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            file_count += 1
            total_bytes += max(0, size)
            if file_count >= 256:
                return file_count, total_bytes
    return file_count, total_bytes


def _write_state(
    artifact_root: Path,
    state: Mapping[str, Any],
) -> None:
    try:
        (artifact_root / "framework_evidence_state.json").write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        pass


def _read_state(artifact_root: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            (artifact_root / "framework_evidence_state.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _append_violation(
    artifact_root: Path,
    violation: Mapping[str, Any],
) -> None:
    try:
        with (artifact_root / "framework_evidence_policy.jsonl").open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write(json.dumps(violation, ensure_ascii=False, sort_keys=True))
            stream.write("\n")
    except OSError:
        pass
