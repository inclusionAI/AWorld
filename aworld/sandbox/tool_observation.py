"""Sandbox-owned Tool observation policy and bounded replay protection.

This module belongs to the Sandbox control plane.  Providers execute calls;
they do not own cross-Tool state, progress semantics, or transcript policy.
Only exact, provably read-only core workspace operations can be compacted.
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from aworld.core.common import ActionResult
from aworld.sandbox.terminal_receipt import (
    TERMINAL_CACHEABLE_EFFECT_SOURCES,
    TERMINAL_EFFECTS,
    TERMINAL_EXECUTION_ANALYZER_VERSION,
    TERMINAL_EXECUTION_RECEIPT_KEY,
    TERMINAL_EXECUTION_RECEIPT_SCHEMA,
    plan_terminal_execution,
    terminal_command_sha256,
)
from aworld.utils.serialized_util import to_serializable


OBSERVATION_SCHEMA = "aworld.sandbox-tool-observation/v1"
_MAX_CACHE_ENTRIES = 256
_FILESYSTEM_READ_ACTIONS = frozenset(
    {
        "download_file",
        "get_file_info",
        "list_allowed_directories",
        "list_directory",
        "read_file",
        "read_media_file",
        "read_output_artifact",
        "search_content",
        "search_files",
    }
)
_FILESYSTEM_MUTATION_ACTIONS = frozenset(
    {
        "copy_file",
        "create_directory",
        "edit_file",
        "edit_file_by_line_range",
        "move_file",
        "upload_file",
        "write_file",
        "write_file_base64",
    }
)
_FILESYSTEM_CAPABILITY_TOOLS = frozenset(
    {
        "filesystem",
        "docker",
        "docker-sandbox",
        "docker-sandbox-server",
    }
)
_TERMINAL_CAPABILITY_TOOLS = frozenset(
    {
        "terminal",
        "terminal-server",
        "docker",
        "docker-sandbox",
        "docker-sandbox-server",
    }
)


def _value(action: Any, name: str, default: Any = None) -> Any:
    if isinstance(action, Mapping):
        return action.get(name, default)
    return getattr(action, name, default)


def canonical_tool_identity(action: Any) -> tuple[str, str]:
    """Return provider capability identity independent of dispatcher encoding."""

    tool = str(_value(action, "tool_name", "") or "")
    operation = str(_value(action, "action_name", "") or "")
    if tool == "mcp" and "__" in operation:
        tool, operation = operation.split("__", 1)
    return tool, operation


def _trusted_terminal_receipt_identity(action: Any) -> bool:
    tool, operation = canonical_tool_identity(action)
    normalized = tool.strip().lower().replace("_", "-")
    return operation == "run_code" and normalized in {
        "terminal",
        "docker",
        "terminal-server",
        "docker-sandbox",
        "docker-sandbox-server",
    }


@dataclass(frozen=True, slots=True)
class ToolEffect:
    identity: str
    effect: str
    cacheable: bool
    operation_hash: str


def classify_tool_effect(action: Any) -> ToolEffect:
    """Classify only generic mechanical effects; unknown fails open."""

    tool, operation = canonical_tool_identity(action)
    params = _value(action, "params", {})
    params = dict(params) if isinstance(params, Mapping) else {}
    identity = f"{tool}.{operation}" if operation else tool
    effect = "unknown"
    cacheable = False
    normalized_tool = tool.strip().lower().replace("_", "-")
    if (
        normalized_tool in _FILESYSTEM_CAPABILITY_TOOLS
        and operation in _FILESYSTEM_READ_ACTIONS
    ):
        effect = "read_only"
        # Filesystem providers own their fileEpoch/range validation.  An outer
        # replay here would bypass that provider-side recheck.
        cacheable = False
    elif (
        normalized_tool in _FILESYSTEM_CAPABILITY_TOOLS
        and operation in _FILESYSTEM_MUTATION_ACTIONS
    ):
        effect = "mutating"
    elif normalized_tool in _TERMINAL_CAPABILITY_TOOLS and operation == "run_code":
        code = params.get("code")
        if isinstance(code, str):
            execution_plan = plan_terminal_execution(code)
            effect = execution_plan.effect
            # Terminal replay is enabled only after a provider receipt binds
            # the parser decision to the actual execution environment.
            cacheable = False
    operation_params = dict(params)
    if "env_content" in operation_params:
        encoded_env_content = json.dumps(
            operation_params["env_content"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        operation_params["env_content"] = {
            "sha256": hashlib.sha256(encoded_env_content).hexdigest(),
            "type": type(params["env_content"]).__name__,
        }
    operation_hash = "sha256:" + hashlib.sha256(
        json.dumps(
            {"identity": identity, "params": operation_params},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return ToolEffect(identity, effect, cacheable, operation_hash)


def actions_are_provably_read_only(actions: Sequence[Any]) -> bool:
    return bool(actions) and all(
        classify_tool_effect(action).effect == "read_only" for action in actions
    )


def _scope(context: Any) -> tuple[str, str, str]:
    return (
        str(getattr(context, "task_id", "") or ""),
        str(getattr(context, "task_epoch", "") or ""),
        str(getattr(context, "session_id", "") or ""),
    )


def _result_success(result: Any) -> bool:
    value = _value(result, "success")
    return value is True


def _metadata(result: Any) -> dict[str, Any]:
    value = _value(result, "metadata", {})
    return dict(value) if isinstance(value, Mapping) else {}


def _validated_terminal_execution_receipt(
    action: Any,
    result: Any,
) -> tuple[dict[str, Any] | None, bool]:
    """Return a validated provider receipt and whether one was supplied."""

    if not _trusted_terminal_receipt_identity(action):
        return None, False
    metadata = _metadata(result)
    if TERMINAL_EXECUTION_RECEIPT_KEY not in metadata:
        return None, False
    candidate = metadata.get(TERMINAL_EXECUTION_RECEIPT_KEY)
    if not isinstance(candidate, Mapping):
        return None, True
    receipt = dict(candidate)
    if receipt.get("schema_version") != TERMINAL_EXECUTION_RECEIPT_SCHEMA:
        return None, True
    if receipt.get("parser_version") != TERMINAL_EXECUTION_ANALYZER_VERSION:
        return None, True
    result_params = _value(result, "parameter", {})
    action_params = _value(action, "params", {})
    params = (
        result_params
        if isinstance(result_params, Mapping) and isinstance(result_params.get("code"), str)
        else action_params
    )
    code = params.get("code") if isinstance(params, Mapping) else None
    if not isinstance(code, str):
        return None, True
    if receipt.get("command_sha256") != terminal_command_sha256(code):
        return None, True
    effect = receipt.get("effect")
    potential_effect = receipt.get("potential_effect")
    effect_source = receipt.get("effect_source")
    executed = receipt.get("executed")
    cacheable = receipt.get("cacheable")
    generation_delta = receipt.get("workspace_generation_delta")
    if effect not in TERMINAL_EFFECTS:
        return None, True
    if potential_effect not in TERMINAL_EFFECTS:
        return None, True
    if not isinstance(effect_source, str) or not effect_source:
        return None, True
    if not isinstance(executed, bool) or not isinstance(cacheable, bool):
        return None, True
    if isinstance(generation_delta, bool) or not isinstance(generation_delta, int):
        return None, True
    if generation_delta < 0 or generation_delta > 1:
        return None, True
    if not executed and generation_delta != 0:
        return None, True
    if effect == "read_only" and generation_delta != 0:
        return None, True
    if executed and effect != "read_only" and generation_delta != 1:
        return None, True
    if cacheable and (not executed or effect != "read_only"):
        return None, True
    if cacheable and effect_source not in TERMINAL_CACHEABLE_EFFECT_SOURCES:
        return None, True
    if not isinstance(receipt.get("language"), str):
        return None, True
    if not isinstance(receipt.get("parsed"), bool):
        return None, True
    if not isinstance(receipt.get("read_set_complete"), bool):
        return None, True
    if cacheable and receipt.get("read_set_complete") is not True:
        return None, True
    if not isinstance(receipt.get("timed_out"), bool):
        return None, True
    if not isinstance(receipt.get("scope_volatile"), bool):
        return None, True
    mutation_observed = receipt.get("mutation_observed")
    if not isinstance(mutation_observed, (bool, type(None))):
        return None, True
    if not executed and mutation_observed is not None:
        return None, True
    if effect == "read_only" and mutation_observed is True:
        return None, True
    exit_code = receipt.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, (int, type(None))):
        return None, True
    for key in ("read_paths", "write_paths"):
        paths = receipt.get(key)
        if (
            not isinstance(paths, list)
            or len(paths) > 16
            or any(not isinstance(path, str) or len(path) > 512 for path in paths)
        ):
            return None, True
    read_paths = receipt["read_paths"]
    read_path_epochs = receipt.get("read_path_epochs")
    if not isinstance(read_path_epochs, list) or len(read_path_epochs) > 16:
        return None, True
    if cacheable and read_paths and len(read_path_epochs) != len(read_paths):
        return None, True
    for epoch in read_path_epochs:
        if not isinstance(epoch, Mapping):
            return None, True
        if any(
            isinstance(epoch.get(key), bool)
            or not isinstance(epoch.get(key), int)
            for key in (
                "link_inode",
                "link_mtime_ns",
                "mode",
                "size",
                "mtime_ns",
                "ctime_ns",
                "inode",
            )
        ):
            return None, True
        if any(
            not isinstance(epoch.get(key), str) or len(epoch.get(key)) > 1024
            for key in ("path", "resolved_path")
        ):
            return None, True
    return receipt, True


def _result_content_hash(result: Any) -> str:
    value = to_serializable(_value(result, "content"))
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _read_epoch_matches(epoch: Mapping[str, Any]) -> bool:
    path = Path(str(epoch.get("path") or ""))
    if not path.is_absolute():
        return False
    try:
        link_stat = path.lstat()
        resolved = path.resolve()
        target_stat = resolved.stat()
    except OSError:
        return False
    current = {
        "path": str(path),
        "resolved_path": str(resolved),
        "link_inode": link_stat.st_ino,
        "link_mtime_ns": link_stat.st_mtime_ns,
        "mode": target_stat.st_mode,
        "size": target_stat.st_size,
        "mtime_ns": target_stat.st_mtime_ns,
        "ctime_ns": target_stat.st_ctime_ns,
        "inode": target_stat.st_ino,
    }
    return all(current.get(key) == value for key, value in epoch.items())


class SandboxToolObservationRuntime:
    """Task-scoped observation cache owned by one Sandbox control plane."""

    def __init__(self, *, max_cache_entries: int = _MAX_CACHE_ENTRIES) -> None:
        self._max_cache_entries = max(1, max_cache_entries)
        self._generation: dict[tuple[str, str, str], int] = {}
        self._cache: "OrderedDict[tuple[tuple[str, str, str], int, str], dict[str, Any]]" = OrderedDict()
        self._authoritative_effects: "OrderedDict[tuple[tuple[str, str, str], str], ToolEffect]" = OrderedDict()
        self._volatile_scopes: set[tuple[str, str, str]] = set()

    def _current_generation(self, context: Any) -> int:
        return self._generation.get(_scope(context), 0)

    def current_generation(self, context: Any) -> int:
        """Return the bounded workspace generation for control-plane receipts."""

        return self._current_generation(context)

    def lookup(self, action: Any, *, context: Any) -> ActionResult | None:
        fallback_effect = classify_tool_effect(action)
        scope = _scope(context)
        if scope in self._volatile_scopes:
            return None
        learned_key = (scope, fallback_effect.operation_hash)
        effect = self._authoritative_effects.get(learned_key, fallback_effect)
        if not effect.cacheable:
            return None
        if learned_key in self._authoritative_effects:
            self._authoritative_effects.move_to_end(learned_key)
        generation = self._current_generation(context)
        key = (scope, generation, effect.operation_hash)
        cached = self._cache.get(key)
        if cached is None:
            return None
        if not all(
            _read_epoch_matches(epoch)
            for epoch in cached.get("read_path_epochs", ())
        ):
            self._cache.pop(key, None)
            generation += 1
            self._generation[scope] = generation
            return None
        self._cache.move_to_end(key)
        receipt = {
            "schema_version": OBSERVATION_SCHEMA,
            "canonical_tool": effect.identity,
            "effect": "read_only",
            "cache_hit": True,
            "changed": False,
            "workspace_generation": generation,
            "operation_hash": effect.operation_hash,
            "observation_id": cached["observation_id"],
            "content_sha256": cached["content_sha256"],
        }
        return ActionResult(
            success=True,
            tool_name=canonical_tool_identity(action)[0],
            action_name=canonical_tool_identity(action)[1],
            content=json.dumps(
                {
                    "type": "unchanged",
                    "observationId": cached["observation_id"],
                    "contentSha256": cached["content_sha256"],
                    "message": "unchanged since the referenced Sandbox observation; reuse retained facts",
                },
                ensure_ascii=False,
            ),
            keep=True,
            metadata={"sandbox_observation": receipt},
            parameter=deepcopy(_value(action, "params", {}) or {}),
        )

    def record(self, action: Any, result: Any, *, context: Any) -> Any:
        fallback_effect = classify_tool_effect(action)
        terminal_receipt, terminal_receipt_supplied = (
            _validated_terminal_execution_receipt(action, result)
        )
        effect = fallback_effect
        if terminal_receipt is not None:
            effect = ToolEffect(
                identity=fallback_effect.identity,
                effect=str(terminal_receipt["effect"]),
                cacheable=bool(terminal_receipt["cacheable"]),
                operation_hash=fallback_effect.operation_hash,
            )
        elif terminal_receipt_supplied:
            # A malformed or future-version provider claim is not evidence.
            # Execute fail-open semantics and conservatively invalidate replay.
            effect = ToolEffect(
                identity=fallback_effect.identity,
                effect="unknown",
                cacheable=False,
                operation_hash=fallback_effect.operation_hash,
            )
        elif _trusted_terminal_receipt_identity(action):
            # A legacy/mismatched terminal cannot establish replay safety from
            # Sandbox-side source parsing alone.
            effect = ToolEffect(
                identity=fallback_effect.identity,
                effect="unknown",
                cacheable=False,
                operation_hash=fallback_effect.operation_hash,
            )
        scope = _scope(context)
        result_metadata = _metadata(result)
        scope_volatile = bool(
            terminal_receipt is not None
            and terminal_receipt.get("scope_volatile") is True
        ) or result_metadata.get("background_output_detached") is True
        if result_metadata.get("capture_complete") is False:
            scope_volatile = True
        if scope_volatile:
            self._volatile_scopes.add(scope)
        generation = self._current_generation(context)
        success = _result_success(result)
        effective_effect = effect.effect
        workspace_mutated: bool | None = None
        generation_delta = (
            int(terminal_receipt["workspace_generation_delta"])
            if terminal_receipt is not None
            else (0 if effect.effect == "read_only" else 1)
        )
        if generation_delta:
            generation += generation_delta
            self._generation[scope] = generation
        if terminal_receipt is not None:
            observed_mutation = terminal_receipt.get("mutation_observed")
            if effect.effect == "read_only":
                workspace_mutated = False
            elif isinstance(observed_mutation, bool):
                workspace_mutated = observed_mutation
            else:
                workspace_mutated = None
            if effect.effect == "mutating" and not success:
                effective_effect = "unknown"
        elif effect.effect == "mutating":
            # Legacy providers have no filesystem-diff evidence.  Preserve
            # the former successful-known-mutation signal for compatibility.
            workspace_mutated = True if success else None
            if not success:
                effective_effect = "unknown"
        elif effect.effect == "unknown":
            # Unknown calls must execute and conservatively invalidate replay,
            # but do not claim progress merely because a command ran.
            workspace_mutated = None
        if terminal_receipt is not None:
            learned_key = (scope, effect.operation_hash)
            self._authoritative_effects[learned_key] = effect
            self._authoritative_effects.move_to_end(learned_key)
            while len(self._authoritative_effects) > self._max_cache_entries:
                self._authoritative_effects.popitem(last=False)
        content_sha256 = _result_content_hash(result)
        observation_id = "sha256:" + hashlib.sha256(
            json.dumps(
                {
                    "scope": scope,
                    "generation": generation,
                    "operation_hash": effect.operation_hash,
                    "content_sha256": content_sha256,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        receipt = {
            "schema_version": OBSERVATION_SCHEMA,
            "canonical_tool": effect.identity,
            "effect": effective_effect,
            "cache_hit": False,
            "changed": workspace_mutated,
            "workspace_mutated": workspace_mutated,
            "workspace_generation": generation,
            "operation_hash": effect.operation_hash,
            "observation_id": observation_id,
            "content_sha256": content_sha256,
            "scope_volatile": scope in self._volatile_scopes,
        }
        if terminal_receipt is not None:
            receipt["terminal_execution_receipt"] = terminal_receipt
        metadata = _metadata(result)
        metadata["sandbox_observation"] = receipt
        try:
            result.metadata = metadata
        except (AttributeError, TypeError):
            if isinstance(result, dict):
                result["metadata"] = metadata
        if effect.cacheable and success and scope not in self._volatile_scopes:
            key = (scope, generation, effect.operation_hash)
            self._cache[key] = {
                "observation_id": observation_id,
                "content_sha256": content_sha256,
                "read_path_epochs": deepcopy(
                    terminal_receipt.get("read_path_epochs", ())
                    if terminal_receipt is not None
                    else ()
                ),
            }
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_cache_entries:
                self._cache.popitem(last=False)
        return result


__all__ = [
    "OBSERVATION_SCHEMA",
    "SandboxToolObservationRuntime",
    "ToolEffect",
    "actions_are_provably_read_only",
    "canonical_tool_identity",
    "classify_tool_effect",
]
