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
import posixpath
from pathlib import Path
import re
import shlex
from typing import Any, Mapping, Sequence

from aworld.core.common import ActionResult
from aworld.core.execution_protocol.models import ActionSemanticReceipt
from aworld.sandbox.terminal_receipt import (
    TERMINAL_CACHEABLE_EFFECT_SOURCES,
    TERMINAL_EFFECTS,
    TERMINAL_EXECUTION_ANALYZER_VERSION,
    TERMINAL_EXECUTION_RECEIPT_KEY,
    TERMINAL_EXECUTION_RECEIPT_SCHEMA,
    TERMINAL_LANGUAGE_CONTRACT_VERSION,
    TERMINAL_LANGUAGES,
    plan_terminal_execution,
    shell_command_working_directory,
    terminal_command_sha256,
)
from aworld.utils.serialized_util import to_serializable


OBSERVATION_SCHEMA = "aworld.sandbox-tool-observation/v1"
ACTION_SEMANTIC_RECEIPT_KEY = "action_semantic_receipt"
READ_OBSERVATION_RECEIPT_KEY = "read_observation_receipt"
# Provider-neutral, additive metadata for one bounded file read. ``epoch`` is
# authoritative only in its named environment; the Sandbox may revalidate
# ``authority=host`` itself, while container authorities must be revalidated by
# their provider. The receipt contains hashes/coverage, representation,
# checkpoint and replay-vs-execution truth, never retained bytes.
READ_OBSERVATION_SCHEMA = "aworld.read-observation/v1"
_MAX_CACHE_ENTRIES = 256
# Exact replay keeps the original Tool body in the Sandbox control plane so a
# checkpoint that discarded it can be hydrated without executing the Tool
# again.  Bound both the per-entry footprint and the total LRU footprint.  Tool
# output larger than this remains executable/readable, but is deliberately not
# eligible for exact replay.
_MAX_EXACT_REPLAY_CONTENT_BYTES = 64 * 1024
_FILESYSTEM_READ_ACTIONS = frozenset(
    {
        "download_file",
        "get_file_info",
        "list_allowed_directories",
        "list_directory",
        "read_file",
        "read_media_file",
        "observe_artifact",
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


def semantic_target_sha256(path: str) -> str:
    """Hash one lexical workspace target without resolving or retaining it."""

    if not isinstance(path, str) or not path.strip() or len(path) > 4096:
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


def _semantic_tool_parts(action: Any) -> tuple[str, str]:
    tool, operation = canonical_tool_identity(action)
    if not operation and "__" in tool:
        tool, operation = tool.split("__", 1)
    return tool.strip(), operation.strip()


def _semantic_capability_aliases(
    action: Any,
    *,
    effect: str,
) -> tuple[str, ...]:
    tool, operation = _semantic_tool_parts(action)
    normalized_tool = tool.casefold().replace("_", "-")
    normalized_operation = operation.casefold().replace("_", "-")
    aliases: list[str] = []
    if normalized_tool and normalized_operation:
        aliases.append(f"{normalized_tool}.{normalized_operation}")
    terminal_capability = normalized_tool in {
        "terminal",
        "terminal-server",
        "docker",
        "docker-sandbox",
        "docker-sandbox-server",
    } and normalized_operation in {"execute", "run-code"}
    workspace_capability = terminal_capability or normalized_tool in {
        "filesystem",
        "docker",
        "docker-sandbox",
        "docker-sandbox-server",
    }
    if terminal_capability:
        aliases.append("workspace.execute")
    if workspace_capability and effect == "read_only":
        aliases.append("workspace.read")
    elif workspace_capability and effect == "mutating":
        aliases.append("workspace.mutate")
    elif workspace_capability and effect == "validation":
        aliases.append("workspace.validate")
    return tuple(dict.fromkeys(aliases))[:8] or ("unknown.capability",)


def _completion_contract(context: Any) -> Any:
    contract = getattr(context, "completion_contract", None)
    if contract is not None:
        return contract
    owner_resolver = getattr(context, "_task_runtime_registry_owner", None)
    owner = owner_resolver() if callable(owner_resolver) else None
    return getattr(owner, "completion_contract", None)


def canonical_invocation_cwd(context: Any, cwd: Any = None) -> str | None:
    """Canonicalize a Tool invocation cwd against the trusted workspace."""

    owner_resolver = getattr(context, "_task_runtime_registry_owner", None)
    owner = owner_resolver() if callable(owner_resolver) else None
    workspace_root = getattr(context, "workspace_path", None)
    if (not isinstance(workspace_root, str) or not workspace_root.strip()) and owner:
        workspace_root = getattr(owner, "workspace_path", None)
    workspace_root = (
        posixpath.normpath(workspace_root.replace("\\", "/"))
        if isinstance(workspace_root, str) and workspace_root.strip()
        else None
    )
    selected = cwd if isinstance(cwd, str) and cwd.strip() else workspace_root
    if not isinstance(selected, str) or not selected.strip():
        return None
    normalized = posixpath.normpath(selected.replace("\\", "/"))
    if workspace_root is not None and not posixpath.isabs(normalized):
        normalized = posixpath.normpath(posixpath.join(workspace_root, normalized))
    return normalized


def _declared_target_ids(context: Any) -> frozenset[str]:
    contract = _completion_contract(context)
    targets: set[str] = set()

    owner_resolver = getattr(context, "_task_runtime_registry_owner", None)
    owner = owner_resolver() if callable(owner_resolver) else None
    workspace_root = canonical_invocation_cwd(context)

    def declared_identity(path: str) -> str:
        normalized = posixpath.normpath(path.replace("\\", "/"))
        if workspace_root is not None and not posixpath.isabs(normalized):
            normalized = posixpath.normpath(posixpath.join(workspace_root, normalized))
        return semantic_target_sha256(normalized)

    for requirement in getattr(contract, "required_artifacts", ()) or ():
        path = getattr(requirement, "path", None)
        if not isinstance(path, str):
            continue
        try:
            targets.add(declared_identity(path))
        except ValueError:
            continue
    owners = [context]
    if owner is not None and owner is not context:
        owners.append(owner)
    for candidate_owner in owners:
        context_info = getattr(candidate_owner, "context_info", None)
        context_get = getattr(context_info, "get", None)
        public_contract = (
            context_get("public_deliverable_contract")
            if callable(context_get)
            else None
        )
        if (
            not isinstance(public_contract, Mapping)
            or public_contract.get("schema_version") != "aworld.public-deliverables/v1"
            or public_contract.get("authority") != "public_task_advisory"
            or public_contract.get("source") != "public_task_text"
        ):
            continue
        artifacts = (
            public_contract.get("artifacts")
            if isinstance(public_contract, Mapping)
            else None
        )
        if not isinstance(artifacts, list) or len(artifacts) > 16:
            continue
        for artifact in artifacts:
            if (
                not isinstance(artifact, Mapping)
                or artifact.get("kind") != "file"
                or artifact.get("authority") != "public_task_advisory"
            ):
                continue
            path = artifact.get("path") if isinstance(artifact, Mapping) else None
            if not isinstance(path, str):
                continue
            try:
                targets.add(declared_identity(path))
            except ValueError:
                continue
    return frozenset(targets)


def declared_action_target_ids(context: Any) -> frozenset[str]:
    """Return bounded hashed targets from trusted delivery contracts.

    Raw paths remain local to the Sandbox preflight boundary.  Controllers use
    only these stable identities when admitting a converged Tool call.
    """

    return _declared_target_ids(context)


def _registered_validation_kind(
    context: Any,
    *,
    code: str | None,
    cwd: Any = None,
) -> str | None:
    if not isinstance(code, str) or not code.strip():
        return None
    contract = _completion_contract(context)
    for validation in getattr(contract, "validation_commands", ()) or ():
        argv = tuple(getattr(validation, "argv", ()) or ())
        if not argv:
            continue
        registered = (
            str(argv[-1])
            if len(argv) >= 2 and argv[-2] == "-c"
            else shlex.join(str(item) for item in argv)
        )
        if code.strip() != registered.strip() or canonical_invocation_cwd(
            context, cwd
        ) != canonical_invocation_cwd(context, getattr(validation, "cwd", None)):
            continue
        command_id = str(getattr(validation, "command_id", "") or "")
        return "registered:" + hashlib.sha256(command_id.encode("utf-8")).hexdigest()
    return None


def _action_target_paths(
    action: Any,
    *,
    terminal_receipt: Mapping[str, Any] | None = None,
    effect: str | None = None,
) -> tuple[str, ...]:
    if terminal_receipt is not None:
        terminal_effect = str(terminal_receipt.get("effect") or effect or "unknown")
        values = (
            terminal_receipt.get("write_paths")
            if terminal_effect == "mutating"
            else terminal_receipt.get("read_paths")
            if terminal_effect == "read_only"
            else (
                *(terminal_receipt.get("read_paths") or ()),
                *(terminal_receipt.get("write_paths") or ()),
            )
        ) or ()
        return tuple(value for value in values if isinstance(value, str))[:16]
    tool, operation = _semantic_tool_parts(action)
    params = _value(action, "params", {})
    params = params if isinstance(params, Mapping) else {}
    normalized_tool = tool.casefold().replace("_", "-")
    if normalized_tool in _TERMINAL_CAPABILITY_TOOLS and operation in {
        "execute",
        "run_code",
    }:
        code = params.get("code", params.get("command"))
        language = params.get("language", "shell")
        if isinstance(code, str) and language in TERMINAL_LANGUAGES:
            plan = plan_terminal_execution(code, language=language)
            selected_effect = effect or plan.effect
            return (
                plan.write_paths
                if selected_effect == "mutating"
                else plan.read_paths
                if selected_effect == "read_only"
                else (*plan.read_paths, *plan.write_paths)
            )[:16]
        return ()
    values: list[str] = []
    for key in ("path", "source", "destination", "target", "file"):
        value = params.get(key)
        if isinstance(value, str):
            values.append(value)
    return tuple(dict.fromkeys(values))[:16]


def _effective_action_cwd(
    action: Any,
    result: Any | None = None,
    *,
    context: Any | None = None,
) -> str | None:
    params = _value(action, "params", {})
    params = params if isinstance(params, Mapping) else {}
    cwd = params.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        metadata = _metadata(result) if result is not None else {}
        cwd = metadata.get("working_directory")
    if (not isinstance(cwd, str) or not cwd.strip()) and context is not None:
        cwd = getattr(context, "workspace_path", None)
    current = (
        posixpath.normpath(cwd.replace("\\", "/"))
        if isinstance(cwd, str) and cwd.strip()
        else None
    )
    code = params.get("code", params.get("command"))
    if not isinstance(code, str) or params.get("language", "shell") != "shell":
        return current
    command_cwd, command_cwd_safe = shell_command_working_directory(code)
    if not command_cwd_safe:
        return None
    if command_cwd is not None:
        normalized = posixpath.normpath(command_cwd.replace("\\", "/"))
        current = (
            normalized
            if posixpath.isabs(normalized) or current is None
            else posixpath.normpath(posixpath.join(current, normalized))
        )
    return current


def _target_ids(paths: Sequence[str], *, cwd: str | None = None) -> tuple[str, ...]:
    identities: list[str] = []
    for path in paths:
        try:
            normalized = posixpath.normpath(path.replace("\\", "/"))
            if cwd is not None and not posixpath.isabs(normalized):
                normalized = posixpath.normpath(posixpath.join(cwd, normalized))
            identity = semantic_target_sha256(normalized)
        except ValueError:
            continue
        if identity not in identities:
            identities.append(identity)
    return tuple(identities[:16])


def build_planned_action_semantic_receipt(
    *,
    context: Any,
    tool_name: str,
    arguments: Mapping[str, Any],
    delivery_intent: str,
) -> ActionSemanticReceipt:
    """Derive path-free planned semantics before raw model arguments vanish."""

    action = {"tool_name": tool_name, "action_name": "", "params": arguments}
    tool, operation = _semantic_tool_parts(action)
    action = {"tool_name": tool, "action_name": operation, "params": arguments}
    fallback = classify_tool_effect(action)
    code = arguments.get("code", arguments.get("command"))
    validation_kind = _registered_validation_kind(
        context,
        code=code if isinstance(code, str) else None,
        cwd=arguments.get("cwd"),
    )
    effect = fallback.effect
    normalized_tool = tool.casefold().replace("_", "-")
    normalized_operation = operation.casefold().replace("-", "_")
    if (
        normalized_tool in _TERMINAL_CAPABILITY_TOOLS
        and normalized_operation in {"execute", "run_code"}
        and isinstance(code, str)
    ):
        language = arguments.get("language", "shell")
        if language in TERMINAL_LANGUAGES:
            execution_plan = plan_terminal_execution(code, language=language)
            effect = (
                "unknown" if execution_plan.callback_kinds else execution_plan.effect
            )
    paths = _action_target_paths(action, effect=effect)
    target_ids = _target_ids(
        paths,
        cwd=_effective_action_cwd(action, context=context),
    )
    declared_targets = _declared_target_ids(context)
    declared = bool(declared_targets.intersection(target_ids)) if target_ids else False
    if validation_kind is not None:
        effect = "validation"
    elif delivery_intent == "validate_candidate" and effect == "read_only" and declared:
        validation_kind = "declared_artifact_read"
    return ActionSemanticReceipt(
        capability_aliases=_semantic_capability_aliases(action, effect=effect),
        effect=effect,
        target_ids=target_ids,
        validation_kind=validation_kind,
        declared_deliverable_targeted=declared,
    )


def build_preflight_action_semantic_receipt(
    *,
    context: Any,
    action: Any,
    delivery_intent: str,
) -> ActionSemanticReceipt:
    """Derive semantics from one validated, already-dispatched action."""

    tool, operation = canonical_tool_identity(action)
    params = _value(action, "params", {})
    if not tool or not operation or not isinstance(params, Mapping):
        raise ValueError("preflight action requires a canonical Tool identity")
    return build_planned_action_semantic_receipt(
        context=context,
        tool_name=f"{tool}__{operation}",
        arguments=params,
        delivery_intent=delivery_intent,
    )


def _observed_action_semantic_receipt(
    action: Any,
    result: Any,
    *,
    context: Any,
    effect: ToolEffect,
    terminal_receipt: Mapping[str, Any] | None,
) -> ActionSemanticReceipt:
    params = _value(action, "params", {})
    params = params if isinstance(params, Mapping) else {}
    code = params.get("code", params.get("command"))
    validation_kind = _registered_validation_kind(
        context,
        code=code if isinstance(code, str) else None,
        cwd=params.get("cwd"),
    )
    semantic_effect = effect.effect
    if validation_kind is not None and (
        terminal_receipt is not None or not _trusted_terminal_receipt_identity(action)
    ):
        semantic_effect = "validation"
    elif terminal_receipt is None and _trusted_terminal_receipt_identity(action):
        validation_kind = None
    paths = _action_target_paths(
        action,
        terminal_receipt=terminal_receipt,
        effect=effect.effect,
    )
    target_ids = _target_ids(
        paths,
        cwd=_effective_action_cwd(action, result, context=context),
    )
    declared_targets = _declared_target_ids(context)
    declared = bool(declared_targets.intersection(target_ids)) if target_ids else False
    if validation_kind is None and semantic_effect == "read_only" and declared:
        validation_kind = "declared_artifact_read"
    timed_out = bool(terminal_receipt.get("timed_out")) if terminal_receipt else False
    executed = bool(terminal_receipt.get("executed")) if terminal_receipt else True
    success = bool(_result_success(result) and not timed_out)
    action_call_id = _value(action, "tool_call_id")
    result_call_id = _value(result, "tool_call_id")
    call_id = result_call_id or action_call_id
    if (
        isinstance(action_call_id, str)
        and isinstance(result_call_id, str)
        and action_call_id != result_call_id
    ):
        semantic_effect = "unknown"
        call_id = None
    return ActionSemanticReceipt(
        capability_aliases=_semantic_capability_aliases(action, effect=semantic_effect),
        effect=semantic_effect,
        target_ids=target_ids,
        executed=executed,
        succeeded=success,
        timed_out=timed_out,
        validation_kind=validation_kind,
        declared_deliverable_targeted=declared,
        tool_call_id=call_id if isinstance(call_id, str) and call_id else None,
    )


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
        language = params.get("language", "shell")
        if isinstance(code, str) and language in TERMINAL_LANGUAGES:
            execution_plan = plan_terminal_execution(code, language=language)
            effect = (
                "unknown" if execution_plan.callback_kinds else execution_plan.effect
            )
            # Terminal replay is enabled only after a provider receipt binds
            # the parser decision to the actual execution environment.
            cacheable = False
    elif (
        normalized_tool in _TERMINAL_CAPABILITY_TOOLS
        and operation == "observe_artifact"
    ):
        # The Terminal provider validates the file epoch/content identity and
        # owns the bounded media sidecar. Replaying here would bypass that
        # validation, so the outer Sandbox records read-only but never caches.
        effect = "read_only"
        cacheable = False
    operation_params = dict(params)
    if (
        normalized_tool in _TERMINAL_CAPABILITY_TOOLS
        and operation == "run_code"
        and operation_params.get("language", "shell") == "shell"
    ):
        # Omitted and explicit Shell are the same versioned request contract.
        operation_params.pop("language", None)
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
    operation_hash = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                {"identity": identity, "params": operation_params},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
    )
    return ToolEffect(identity, effect, cacheable, operation_hash)


def actions_are_provably_read_only(actions: Sequence[Any]) -> bool:
    return bool(actions) and all(
        classify_tool_effect(action).effect == "read_only" for action in actions
    )


def _scope(context: Any) -> tuple[str, str, str]:
    epoch = getattr(context, "task_epoch", None)
    return (
        str(getattr(context, "task_id", "") or "").strip(),
        "" if epoch is None or isinstance(epoch, bool) else str(epoch),
        str(getattr(context, "session_id", "") or "").strip(),
    )


def _scope_is_complete(scope: tuple[str, str, str]) -> bool:
    return all(isinstance(value, str) and bool(value) for value in scope)


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
        if isinstance(result_params, Mapping)
        and isinstance(result_params.get("code"), str)
        else action_params
    )
    code = params.get("code") if isinstance(params, Mapping) else None
    if not isinstance(code, str):
        return None, True
    if receipt.get("command_sha256") != terminal_command_sha256(code):
        return None, True
    requested_language = (
        params.get("language", action_params.get("language", "shell"))
        if isinstance(params, Mapping) and isinstance(action_params, Mapping)
        else "shell"
    )
    if requested_language not in TERMINAL_LANGUAGES:
        return None, True
    if (
        receipt.get("language_contract_version") != TERMINAL_LANGUAGE_CONTRACT_VERSION
        or receipt.get("requested_language") != requested_language
        or receipt.get("effective_language") not in TERMINAL_LANGUAGES
        or receipt.get("effective_language") != requested_language
        or receipt.get("language") != receipt.get("effective_language")
    ):
        return None, True
    effect = receipt.get("effect")
    potential_effect = receipt.get("potential_effect")
    effect_source = receipt.get("effect_source")
    executed = receipt.get("executed")
    provider_cache_hit = receipt.get("cache_hit", False)
    cacheable = receipt.get("cacheable")
    generation_delta = receipt.get("workspace_generation_delta")
    if effect not in TERMINAL_EFFECTS:
        return None, True
    if potential_effect not in TERMINAL_EFFECTS:
        return None, True
    if not isinstance(effect_source, str) or not effect_source:
        return None, True
    if (
        not isinstance(executed, bool)
        or not isinstance(provider_cache_hit, bool)
        or not isinstance(cacheable, bool)
        or (executed and provider_cache_hit)
    ):
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
    if cacheable and (not (executed or provider_cache_hit) or effect != "read_only"):
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
    if provider_cache_hit and (
        effect != "read_only" or receipt.get("timed_out") is not False or exit_code != 0
    ):
        return None, True
    for key in ("read_paths", "write_paths"):
        paths = receipt.get(key)
        if (
            not isinstance(paths, list)
            or len(paths) > 16
            or any(not isinstance(path, str) or len(path) > 512 for path in paths)
        ):
            return None, True
    nested_evidence = receipt.get("nested_language_evidence", [])
    if not isinstance(nested_evidence, list) or len(nested_evidence) > 4:
        return None, True
    for item in nested_evidence:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"language", "source_sha256"}
            or item.get("language") not in TERMINAL_LANGUAGES
            or not isinstance(item.get("source_sha256"), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", item["source_sha256"]) is None
        ):
            return None, True
    read_paths = receipt["read_paths"]
    read_ranges = receipt.get("read_ranges")
    if read_ranges is None:
        # Additive v2 compatibility: derive coverage only when the same shared
        # parser reproduces the provider's complete path sequence. Never guess
        # that a legacy bounded read covered the full file.
        planned = plan_terminal_execution(code, language=requested_language)
        if tuple(read_paths) != planned.read_paths:
            return None, True
        planned_ranges = tuple(planned.read_ranges or ())
        read_ranges = (
            [item.to_dict() for item in planned_ranges]
            if len(planned_ranges) == len(read_paths)
            else [{"kind": "full"} for _ in read_paths]
        )
        receipt["read_ranges"] = read_ranges
    if (
        not isinstance(read_ranges, list)
        or len(read_ranges) != len(read_paths)
        or any(not _valid_read_coverage(item) for item in read_ranges)
    ):
        return None, True
    projection_reusable = receipt.get("read_projection_reusable", False)
    if not isinstance(projection_reusable, bool):
        return None, True
    verifier_plan = plan_terminal_execution(code, language=requested_language)
    if tuple(read_paths) == verifier_plan.read_paths:
        verifier_ranges = tuple(verifier_plan.read_ranges or ())
        expected_ranges = (
            [item.to_dict() for item in verifier_ranges]
            if len(verifier_ranges) == len(read_paths)
            else [{"kind": "full"} for _ in read_paths]
        )
        if read_ranges != expected_ranges:
            return None, True
        if projection_reusable and not verifier_plan.read_projection_reusable:
            return None, True
    elif cacheable or projection_reusable:
        return None, True
    receipt["read_projection_reusable"] = projection_reusable
    representation = receipt.get("read_representation")
    if representation is not None and (
        not isinstance(representation, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", representation) is None
    ):
        return None, True
    normalized_tool = (
        canonical_tool_identity(action)[0].strip().lower().replace("_", "-")
    )
    representation_provider = (
        "docker"
        if normalized_tool in {"docker", "docker-sandbox", "docker-sandbox-server"}
        else "terminal"
    )
    if representation is not None and not representation.startswith(
        f"{representation_provider}.run-code."
    ):
        return None, True
    observation_id = receipt.get("observation_id")
    if observation_id is not None and (
        not isinstance(observation_id, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", observation_id) is None
    ):
        return None, True
    observation_content_sha256 = receipt.get("observation_content_sha256")
    if observation_content_sha256 is not None and (
        not isinstance(observation_content_sha256, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", observation_content_sha256) is None
    ):
        return None, True
    source_checkpoint_revision = receipt.get("source_checkpoint_revision")
    if source_checkpoint_revision is not None and (
        isinstance(source_checkpoint_revision, bool)
        or not isinstance(source_checkpoint_revision, int)
        or source_checkpoint_revision < 0
    ):
        return None, True
    if projection_reusable and representation is None:
        if provider_cache_hit or observation_id is not None:
            return None, True
        # Additive v2 compatibility: retain execution semantics but disable
        # overlap facts when the legacy provider did not bind normalization.
        projection_reusable = False
        receipt["read_projection_reusable"] = False
    if provider_cache_hit and (
        observation_id is None
        or observation_content_sha256 is None
        or representation is None
        or source_checkpoint_revision is None
    ):
        return None, True
    read_path_epochs = receipt.get("read_path_epochs")
    if not isinstance(read_path_epochs, list) or len(read_path_epochs) > 16:
        return None, True
    if cacheable and read_paths and len(read_path_epochs) != len(read_paths):
        return None, True
    for epoch in read_path_epochs:
        if not isinstance(epoch, Mapping):
            return None, True
        if any(
            isinstance(epoch.get(key), bool) or not isinstance(epoch.get(key), int)
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
        authority = epoch.get("authority")
        if authority is not None and (
            not isinstance(authority, str) or len(authority) > 256
        ):
            return None, True
        fingerprint = epoch.get("fingerprint")
        if fingerprint is not None and (
            not isinstance(fingerprint, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint) is None
        ):
            return None, True
    return receipt, True


def _valid_read_coverage(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    kind = value.get("kind")
    if kind not in {
        "full",
        "line_range",
        "byte_range",
        "tail_lines",
        "tail_bytes",
        "query",
        "metadata",
    }:
        return False
    if set(value).difference({"kind", "start", "end"}):
        return False
    for key in ("start", "end"):
        item = value.get(key)
        if item is not None and (
            isinstance(item, bool) or not isinstance(item, int) or item < 0
        ):
            return False
    if kind == "line_range":
        return (
            isinstance(value.get("start"), int)
            and value["start"] >= 1
            and isinstance(value.get("end"), int)
            and value["end"] >= value["start"]
        )
    if kind == "byte_range":
        return (
            isinstance(value.get("start"), int)
            and isinstance(value.get("end"), int)
            and value["end"] >= value["start"]
        )
    if kind in {"tail_lines", "tail_bytes"}:
        return isinstance(value.get("start"), int) and value["start"] >= 1
    return value.get("start") is None and value.get("end") is None


def _epoch_is_host_authoritative(epoch: Mapping[str, Any]) -> bool:
    return epoch.get("authority") in (None, "", "host")


def _validated_read_observation_receipt(
    action: Any,
    result: Any,
) -> tuple[dict[str, Any] | None, bool]:
    tool, operation = canonical_tool_identity(action)
    normalized = tool.strip().lower().replace("_", "-")
    if normalized not in _FILESYSTEM_CAPABILITY_TOOLS or operation != "read_file":
        return None, False
    metadata = _metadata(result)
    if READ_OBSERVATION_RECEIPT_KEY not in metadata:
        return None, False
    candidate = metadata.get(READ_OBSERVATION_RECEIPT_KEY)
    if not isinstance(candidate, Mapping):
        return None, True
    receipt = dict(candidate)
    epoch = receipt.get("epoch")
    coverage = receipt.get("coverage")
    if (
        receipt.get("schema_version") != READ_OBSERVATION_SCHEMA
        or not isinstance(receipt.get("authority"), str)
        or len(receipt["authority"]) > 256
        or not isinstance(receipt.get("path"), str)
        or len(receipt["path"]) > 1024
        or not isinstance(epoch, Mapping)
        or not _valid_read_coverage(coverage)
        or not isinstance(receipt.get("coverage_complete"), bool)
        or not isinstance(receipt.get("cache_hit"), bool)
        or not isinstance(receipt.get("executed"), bool)
        or receipt["executed"] is receipt["cache_hit"]
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(receipt.get("content_sha256") or "")
        )
        is None
    ):
        return None, True
    observation_id = receipt.get("observation_id")
    if (
        observation_id is not None
        and re.fullmatch(r"sha256:[0-9a-f]{64}", str(observation_id)) is None
    ):
        return None, True
    representation = receipt.get("representation")
    if (
        not isinstance(representation, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", representation) is None
    ):
        return None, True
    action_params = _value(action, "params", {})
    requested_output = (
        action_params.get("output", "text")
        if isinstance(action_params, Mapping)
        else "text"
    )
    provider_name = (
        "docker"
        if normalized in {"docker", "docker-sandbox", "docker-sandbox-server"}
        else "filesystem"
    )
    if not representation.startswith(f"{provider_name}.read-file.{requested_output}."):
        return None, True
    source_checkpoint_revision = receipt.get("source_checkpoint_revision")
    if (
        isinstance(source_checkpoint_revision, bool)
        or not isinstance(source_checkpoint_revision, int)
        or source_checkpoint_revision < 0
    ):
        return None, True
    if receipt["cache_hit"] and observation_id is None:
        return None, True
    required_epoch_ints = (
        "link_inode",
        "link_mtime_ns",
        "mode",
        "size",
        "mtime_ns",
        "ctime_ns",
        "inode",
    )
    if any(
        isinstance(epoch.get(key), bool) or not isinstance(epoch.get(key), int)
        for key in required_epoch_ints
    ):
        return None, True
    if any(
        not isinstance(epoch.get(key), str) or len(epoch.get(key)) > 1024
        for key in ("path", "resolved_path")
    ):
        return None, True
    if receipt["path"] != epoch["path"]:
        return None, True
    epoch_authority = epoch.get("authority") or "host"
    if receipt["authority"] != epoch_authority:
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


def _context_checkpoint_revision(context: Any) -> int:
    """Return the current Context rewrite boundary, defaulting conservatively."""

    lifecycle = getattr(context, "context_lifecycle_state", None)
    revision = (
        lifecycle.get("checkpoint_revision")
        if isinstance(lifecycle, Mapping)
        else getattr(lifecycle, "checkpoint_revision", 0)
    )
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return 0
    return revision


def _bounded_replay_content(
    result: Any,
    *,
    max_bytes: int,
) -> tuple[Any | None, int | None, str | None]:
    """Copy one JSON-shaped result body when it fits the exact-replay budget."""

    try:
        content = _value(result, "content")
        serializable = to_serializable(content)
        encoded = json.dumps(
            serializable,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    except Exception:
        # Replay is an optimization. A provider-specific result type must not
        # turn an otherwise successful Tool execution into a control-plane
        # failure merely because it cannot be copied safely.
        return None, None, "content_not_serializable"
    if len(encoded) > max_bytes:
        return None, len(encoded), "content_too_large"
    try:
        retained = deepcopy(content)
    except Exception:
        return None, len(encoded), "content_not_copyable"
    return retained, len(encoded), None


def _read_epoch_matches(epoch: Mapping[str, Any]) -> bool:
    if not _epoch_is_host_authoritative(epoch):
        return False
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
    comparable = {
        "path",
        "resolved_path",
        "link_inode",
        "link_mtime_ns",
        "mode",
        "size",
        "mtime_ns",
        "ctime_ns",
        "inode",
    }
    return all(current.get(key) == epoch.get(key) for key in comparable)


def _coverage_contains(stored: Mapping[str, Any], requested: Mapping[str, Any]) -> bool:
    if stored.get("kind") in {"query", "metadata"} or requested.get("kind") in {
        "query",
        "metadata",
    }:
        return False
    if dict(stored) == dict(requested):
        return True
    stored_kind = stored.get("kind")
    requested_kind = requested.get("kind")
    if stored_kind == "full" and requested_kind in {
        "full",
        "line_range",
        "byte_range",
        "tail_lines",
        "tail_bytes",
    }:
        return True
    if stored_kind != requested_kind:
        return False
    if stored_kind in {"line_range", "byte_range"}:
        return (
            isinstance(stored.get("start"), int)
            and isinstance(stored.get("end"), int)
            and isinstance(requested.get("start"), int)
            and isinstance(requested.get("end"), int)
            and stored["start"] <= requested["start"]
            and stored["end"] >= requested["end"]
        )
    if stored_kind in {"tail_lines", "tail_bytes"}:
        return (
            isinstance(stored.get("start"), int)
            and isinstance(requested.get("start"), int)
            and stored["start"] >= requested["start"]
        )
    return False


def _requested_read_projection(
    action: Any,
    *,
    context: Any,
) -> tuple[str, dict[str, Any], str] | None:
    tool, operation = canonical_tool_identity(action)
    normalized = tool.strip().lower().replace("_", "-")
    params = _value(action, "params", {})
    if not isinstance(params, Mapping):
        return None
    if normalized in _TERMINAL_CAPABILITY_TOOLS and operation == "run_code":
        code = params.get("code")
        language = params.get("language", "shell")
        if not isinstance(code, str) or language not in TERMINAL_LANGUAGES:
            return None
        plan = plan_terminal_execution(code, language=language)
        if (
            plan.effect != "read_only"
            or not plan.read_set_complete
            or not plan.read_projection_reusable
            or len(plan.read_paths) != 1
        ):
            return None
        ranges = tuple(plan.read_ranges or ())
        coverage = ranges[0].to_dict() if len(ranges) == 1 else {"kind": "full"}
        cwd = _effective_action_cwd(action, context=context)
        path = posixpath.normpath(plan.read_paths[0].replace("\\", "/"))
        if not posixpath.isabs(path):
            if cwd is None:
                return None
            path = posixpath.normpath(posixpath.join(cwd, path))
        representation = (
            f"docker.run-code.text.{coverage['kind']}/v1"
            if normalized in {"docker", "docker-sandbox", "docker-sandbox-server"}
            else (
                "terminal.run-code."
                f"{params.get('output_format', 'structured')}.{coverage['kind']}/v1"
            )
        )
        return path, coverage, representation
    if normalized in _FILESYSTEM_CAPABILITY_TOOLS and operation == "read_file":
        if params.get("refresh") is True:
            return None
        raw_path = params.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            return None
        path = posixpath.normpath(raw_path.replace("\\", "/"))
        if not posixpath.isabs(path):
            cwd = canonical_invocation_cwd(context)
            if cwd is None:
                return None
            path = posixpath.normpath(posixpath.join(cwd, path))
        head = params.get("head")
        tail = params.get("tail")
        output = params.get("output", "text")
        if isinstance(head, bool) or isinstance(tail, bool):
            return None
        if isinstance(head, int) and isinstance(tail, int):
            coverage = {"kind": "line_range", "start": head, "end": tail}
        elif isinstance(head, int):
            coverage = {"kind": "line_range", "start": 1, "end": head}
        elif isinstance(tail, int):
            coverage = {"kind": "tail_lines", "start": tail}
        elif output == "base64":
            offset = params.get("offset", 0)
            limit = params.get("limit")
            if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
                return None
            if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
                return None
            coverage = {
                "kind": "byte_range",
                "start": offset,
                "end": offset + limit,
            }
        else:
            coverage = {"kind": "full"}
        if not _valid_read_coverage(coverage):
            return None
        provider = (
            "docker"
            if normalized in {"docker", "docker-sandbox", "docker-sandbox-server"}
            else "filesystem"
        )
        selector = (
            "range"
            if isinstance(head, int) and isinstance(tail, int)
            else "head"
            if isinstance(head, int)
            else "tail"
            if isinstance(tail, int)
            else "bytes"
            if output == "base64"
            else "full"
        )
        return path, coverage, f"{provider}.read-file.{output}.{selector}/v1"
    return None


class SandboxToolObservationRuntime:
    """Task-scoped observation cache owned by one Sandbox control plane."""

    def __init__(
        self,
        *,
        max_cache_entries: int = _MAX_CACHE_ENTRIES,
        max_replay_content_bytes: int = _MAX_EXACT_REPLAY_CONTENT_BYTES,
    ) -> None:
        self._max_cache_entries = max(1, max_cache_entries)
        if (
            isinstance(max_replay_content_bytes, bool)
            or not isinstance(max_replay_content_bytes, int)
            or max_replay_content_bytes < 1
        ):
            raise ValueError("max_replay_content_bytes must be a positive integer")
        self._max_replay_content_bytes = max_replay_content_bytes
        self._generation: dict[tuple[str, str, str], int] = {}
        self._cache: "OrderedDict[tuple[tuple[str, str, str], int, str], dict[str, Any]]" = OrderedDict()
        self._authoritative_effects: "OrderedDict[tuple[tuple[str, str, str], str], ToolEffect]" = OrderedDict()
        self._retained_read_facts: "OrderedDict[tuple[tuple[str, str, str], str, str], dict[str, Any]]" = OrderedDict()
        self._volatile_scopes: set[tuple[str, str, str]] = set()

    def _current_generation(self, context: Any) -> int:
        return self._generation.get(_scope(context), 0)

    def current_generation(self, context: Any) -> int:
        """Return the bounded workspace generation for control-plane receipts."""

        return self._current_generation(context)

    def _lookup_retained_read_fact(
        self,
        action: Any,
        *,
        context: Any,
        effect: ToolEffect,
    ) -> ActionResult | None:
        projection = _requested_read_projection(action, context=context)
        if projection is None:
            return None
        requested_path, requested_coverage, requested_representation = projection
        scope = _scope(context)
        checkpoint_revision = _context_checkpoint_revision(context)
        stale_path = False
        selected_key = None
        selected = None
        for key in reversed(self._retained_read_facts):
            fact_scope, fact_path, _ = key
            if fact_scope != scope or fact_path != requested_path:
                continue
            fact = self._retained_read_facts[key]
            if fact.get("source_checkpoint_revision") != checkpoint_revision:
                continue
            if fact.get("representation") != requested_representation:
                continue
            epoch = fact.get("epoch")
            if not isinstance(epoch, Mapping) or not _epoch_is_host_authoritative(
                epoch
            ):
                continue
            if not _read_epoch_matches(epoch):
                stale_path = True
                continue
            if fact.get("coverage_complete") is not True or not _coverage_contains(
                fact.get("coverage", {}), requested_coverage
            ):
                continue
            selected_key = key
            selected = fact
            break
        if stale_path:
            self._invalidate_read_path(scope, requested_path)
            self._generation[scope] = self._current_generation(context) + 1
            return None
        if selected is None or selected_key is None:
            return None
        self._retained_read_facts.move_to_end(selected_key)
        generation = self._current_generation(context)
        receipt = {
            "schema_version": OBSERVATION_SCHEMA,
            "canonical_tool": effect.identity,
            "effect": "read_only",
            "cache_hit": True,
            "executed": False,
            "cache_state": "retained_facts",
            "changed": False,
            "workspace_mutated": False,
            "workspace_generation": generation,
            "operation_hash": effect.operation_hash,
            "observation_id": selected["observation_id"],
            "content_sha256": selected["content_sha256"],
            "cache_validation": "host_epoch_overlap",
            "source_workspace_generation": selected["workspace_generation"],
            "source_checkpoint_revision": selected["source_checkpoint_revision"],
            "evidence_checkpoint_revision": checkpoint_revision,
            "content_rehydrated": False,
            "exact_replay_cached": False,
            "retained_fact_coverage": dict(selected["coverage"]),
            "requested_coverage": dict(requested_coverage),
            "read_representation": requested_representation,
            "scope_volatile": False,
        }
        return ActionResult(
            success=True,
            is_done=bool(selected.get("is_done", False)),
            tool_name=canonical_tool_identity(action)[0],
            action_name=canonical_tool_identity(action)[1],
            tool_call_id=_value(action, "tool_call_id"),
            content=json.dumps(
                {
                    "type": "unchanged",
                    "observationId": selected["observation_id"],
                    "contentSha256": selected["content_sha256"],
                    "coverage": requested_coverage,
                    "message": "unchanged since a scoped Sandbox observation; reuse retained facts",
                },
                ensure_ascii=False,
            ),
            keep=True,
            metadata={"sandbox_observation": receipt},
            parameter=deepcopy(_value(action, "params", {}) or {}),
        )

    def _invalidate_read_path(
        self,
        scope: tuple[str, str, str],
        path: str,
    ) -> None:
        for key in list(self._retained_read_facts):
            if key[0] == scope and key[1] == path:
                self._retained_read_facts.pop(key, None)
        for key, cached in list(self._cache.items()):
            if key[0] != scope:
                continue
            if any(
                isinstance(epoch, Mapping) and epoch.get("path") == path
                for epoch in cached.get("read_path_epochs", ())
            ):
                self._cache.pop(key, None)

    def _store_retained_read_fact(
        self,
        *,
        context: Any,
        path: str,
        coverage: Mapping[str, Any],
        epoch: Mapping[str, Any],
        observation_id: str,
        content_sha256: str,
        coverage_complete: bool,
        is_done: bool,
        representation: str,
    ) -> None:
        if (
            not _epoch_is_host_authoritative(epoch)
            or not _valid_read_coverage(coverage)
            or not coverage_complete
            or coverage.get("kind") in {"query", "metadata"}
        ):
            return
        scope = _scope(context)
        if not _scope_is_complete(scope):
            return
        key = (scope, path, observation_id)
        self._retained_read_facts[key] = {
            "epoch": deepcopy(dict(epoch)),
            "coverage": deepcopy(dict(coverage)),
            "coverage_complete": True,
            "representation": representation,
            "observation_id": observation_id,
            "content_sha256": content_sha256,
            "workspace_generation": self._current_generation(context),
            "source_checkpoint_revision": _context_checkpoint_revision(context),
            "is_done": bool(is_done),
        }
        self._retained_read_facts.move_to_end(key)
        while len(self._retained_read_facts) > self._max_cache_entries:
            self._retained_read_facts.popitem(last=False)

    def lookup(self, action: Any, *, context: Any) -> ActionResult | None:
        fallback_effect = classify_tool_effect(action)
        scope = _scope(context)
        if not _scope_is_complete(scope):
            return None
        if scope in self._volatile_scopes:
            return None
        learned_key = (scope, fallback_effect.operation_hash)
        effect = self._authoritative_effects.get(learned_key, fallback_effect)
        if not effect.cacheable:
            return self._lookup_retained_read_fact(
                action,
                context=context,
                effect=effect,
            )
        if learned_key in self._authoritative_effects:
            self._authoritative_effects.move_to_end(learned_key)
        generation = self._current_generation(context)
        key = (scope, generation, effect.operation_hash)
        cached = self._cache.get(key)
        source_generation = generation
        epoch_revalidated = False
        if cached is None:
            # A generation is a conservative ordering barrier, not proof that
            # every previously read file changed. Receipts with explicit file
            # epochs can be revalidated safely across unrelated or opaque
            # operations instead of becoming unreachable forever.
            for candidate_key in reversed(self._cache):
                candidate_scope, candidate_generation, candidate_hash = candidate_key
                if (
                    candidate_scope != scope
                    or candidate_hash != effect.operation_hash
                    or candidate_generation == generation
                ):
                    continue
                candidate = self._cache[candidate_key]
                if not candidate.get("read_path_epochs"):
                    continue
                key = candidate_key
                cached = candidate
                source_generation = candidate_generation
                epoch_revalidated = True
                break
        if cached is None:
            return self._lookup_retained_read_fact(
                action,
                context=context,
                effect=effect,
            )
        stale_epochs = [
            epoch
            for epoch in cached.get("read_path_epochs", ())
            if not _read_epoch_matches(epoch)
        ]
        if stale_epochs:
            for epoch in stale_epochs:
                path = epoch.get("path") if isinstance(epoch, Mapping) else None
                if isinstance(path, str):
                    self._invalidate_read_path(scope, path)
            self._cache.pop(key, None)
            generation += 1
            self._generation[scope] = generation
            return None
        if epoch_revalidated:
            self._cache.pop(key, None)
            key = (scope, generation, effect.operation_hash)
            self._cache[key] = cached
        self._cache.move_to_end(key)
        checkpoint_revision = _context_checkpoint_revision(context)
        evidence_retained = (
            cached.get("evidence_checkpoint_revision") == checkpoint_revision
        )
        cache_state = "retained_reference" if evidence_retained else "rehydrated"
        if not evidence_retained:
            # Returning the full body makes this observation evidence available
            # again in the active checkpoint. Later hits in the same epoch may
            # safely use a compact content-addressed reference.
            cached["evidence_checkpoint_revision"] = checkpoint_revision
        receipt = {
            "schema_version": OBSERVATION_SCHEMA,
            "canonical_tool": effect.identity,
            "effect": "read_only",
            "cache_hit": True,
            "executed": False,
            "cache_state": cache_state,
            "changed": False,
            "workspace_mutated": False,
            "workspace_generation": generation,
            "operation_hash": effect.operation_hash,
            "observation_id": cached["observation_id"],
            "content_sha256": cached["content_sha256"],
            "cache_validation": (
                "epoch_revalidated" if epoch_revalidated else "exact_generation"
            ),
            "source_workspace_generation": source_generation,
            "source_checkpoint_revision": cached["source_checkpoint_revision"],
            "evidence_checkpoint_revision": checkpoint_revision,
            "content_rehydrated": not evidence_retained,
            "exact_replay_cached": True,
            "scope_volatile": False,
        }
        return ActionResult(
            success=True,
            is_done=bool(cached.get("is_done", False)),
            tool_name=canonical_tool_identity(action)[0],
            action_name=canonical_tool_identity(action)[1],
            tool_call_id=_value(action, "tool_call_id"),
            content=(
                json.dumps(
                    {
                        "type": "unchanged",
                        "observationId": cached["observation_id"],
                        "contentSha256": cached["content_sha256"],
                        "message": "unchanged since the referenced Sandbox observation; reuse retained facts",
                    },
                    ensure_ascii=False,
                )
                if evidence_retained
                else deepcopy(cached["content"])
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
        read_receipt, read_receipt_supplied = _validated_read_observation_receipt(
            action, result
        )
        effect = fallback_effect
        if terminal_receipt is not None:
            terminal_epochs = terminal_receipt.get("read_path_epochs", ())
            checkpoint_matches = terminal_receipt.get("source_checkpoint_revision") in (
                None,
                _context_checkpoint_revision(context),
            )
            sandbox_cacheable = (
                bool(terminal_receipt["cacheable"])
                and all(
                    _epoch_is_host_authoritative(epoch) for epoch in terminal_epochs
                )
                and checkpoint_matches
            )
            effect = ToolEffect(
                identity=fallback_effect.identity,
                effect=str(terminal_receipt["effect"]),
                cacheable=sandbox_cacheable,
                operation_hash=fallback_effect.operation_hash,
            )
        elif read_receipt is not None:
            read_epoch = read_receipt["epoch"]
            checkpoint_matches = read_receipt[
                "source_checkpoint_revision"
            ] == _context_checkpoint_revision(context)
            action_params = _value(action, "params", {})
            refresh_requested = bool(
                isinstance(action_params, Mapping)
                and action_params.get("refresh") is True
            )
            effect = ToolEffect(
                identity=fallback_effect.identity,
                effect="read_only",
                cacheable=bool(
                    read_receipt["coverage_complete"]
                    and _epoch_is_host_authoritative(read_epoch)
                    and not refresh_requested
                    and checkpoint_matches
                ),
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
        elif read_receipt_supplied:
            # A malformed read receipt is never replay evidence. The declared
            # filesystem operation remains mechanically read-only.
            effect = ToolEffect(
                identity=fallback_effect.identity,
                effect="read_only",
                cacheable=False,
                operation_hash=fallback_effect.operation_hash,
            )
        scope = _scope(context)
        result_metadata = _metadata(result)
        raw_terminal_receipt = result_metadata.get(TERMINAL_EXECUTION_RECEIPT_KEY)
        raw_read_receipt = result_metadata.get(READ_OBSERVATION_RECEIPT_KEY)
        untrusted_replay_claim = bool(
            isinstance(raw_terminal_receipt, Mapping)
            and raw_terminal_receipt.get("cache_hit") is True
        ) or bool(
            isinstance(raw_read_receipt, Mapping)
            and raw_read_receipt.get("cache_hit") is True
        )
        provider_cache_hit = bool(
            (terminal_receipt or {}).get("cache_hit") is True
            or (read_receipt or {}).get("cache_hit") is True
            or untrusted_replay_claim
        )
        scope_volatile = (
            bool(
                terminal_receipt is not None
                and terminal_receipt.get("scope_volatile") is True
            )
            or result_metadata.get("background_output_detached") is True
        )
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
        if _scope_is_complete(scope) and (
            terminal_receipt is not None or read_receipt is not None
        ):
            learned_key = (scope, effect.operation_hash)
            self._authoritative_effects[learned_key] = effect
            self._authoritative_effects.move_to_end(learned_key)
            while len(self._authoritative_effects) > self._max_cache_entries:
                self._authoritative_effects.popitem(last=False)
        result_content_sha256 = _result_content_hash(result)
        provider_content_sha256 = (
            terminal_receipt.get("observation_content_sha256")
            if terminal_receipt is not None
            else read_receipt.get("content_sha256")
            if read_receipt is not None
            else None
        )
        content_sha256 = str(provider_content_sha256 or result_content_sha256)
        computed_observation_id = (
            "sha256:"
            + hashlib.sha256(
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
        )
        provider_observation_id = (
            terminal_receipt.get("observation_id")
            if terminal_receipt is not None
            else read_receipt.get("observation_id")
            if read_receipt is not None
            else None
        )
        observation_id = str(provider_observation_id or computed_observation_id)
        checkpoint_revision = _context_checkpoint_revision(context)
        replay_content = None
        replay_content_bytes = None
        replay_bypass_reason = None
        replay_candidate = (
            _scope_is_complete(scope)
            and not provider_cache_hit
            and effect.cacheable
            and success
            and scope not in self._volatile_scopes
        )
        if replay_candidate:
            (
                replay_content,
                replay_content_bytes,
                replay_bypass_reason,
            ) = _bounded_replay_content(
                result,
                max_bytes=self._max_replay_content_bytes,
            )
        replay_stored = replay_candidate and replay_bypass_reason is None
        receipt = {
            "schema_version": OBSERVATION_SCHEMA,
            "canonical_tool": effect.identity,
            "effect": effective_effect,
            "cache_hit": provider_cache_hit,
            "executed": (
                False
                if provider_cache_hit
                else bool(terminal_receipt.get("executed"))
                if terminal_receipt is not None
                else bool(read_receipt.get("executed"))
                if read_receipt is not None
                else True
            ),
            "cache_state": (
                "provider_replay_untrusted"
                if untrusted_replay_claim
                and terminal_receipt is None
                and read_receipt is None
                else "provider_replay"
                if provider_cache_hit
                else "stored"
                if replay_stored
                else "not_stored"
            ),
            "changed": False if provider_cache_hit else workspace_mutated,
            "workspace_mutated": False if provider_cache_hit else workspace_mutated,
            "workspace_generation": generation,
            "operation_hash": effect.operation_hash,
            "observation_id": observation_id,
            "content_sha256": content_sha256,
            "source_checkpoint_revision": checkpoint_revision,
            "evidence_checkpoint_revision": checkpoint_revision,
            "content_rehydrated": False,
            "exact_replay_cached": replay_stored,
            "scope_volatile": scope in self._volatile_scopes,
        }
        try:
            action_semantics = (
                None
                if provider_cache_hit
                else _observed_action_semantic_receipt(
                    action,
                    result,
                    context=context,
                    effect=effect,
                    terminal_receipt=terminal_receipt,
                )
            )
        except (TypeError, ValueError):
            # Semantic alignment is advisory before convergence.  An
            # unrepresentable receipt must never invalidate the Tool result or
            # be guessed from raw arguments downstream.
            action_semantics = None
        if action_semantics is not None:
            receipt[ACTION_SEMANTIC_RECEIPT_KEY] = action_semantics.to_dict()
        if replay_content_bytes is not None:
            receipt["replay_content_bytes"] = replay_content_bytes
        if replay_bypass_reason is not None:
            receipt["cache_bypass_reason"] = replay_bypass_reason
        if terminal_receipt is not None:
            receipt["terminal_execution_receipt"] = terminal_receipt
        if read_receipt is not None:
            receipt[READ_OBSERVATION_RECEIPT_KEY] = read_receipt
        metadata = _metadata(result)
        metadata["sandbox_observation"] = receipt
        try:
            result.metadata = metadata
        except (AttributeError, TypeError):
            if isinstance(result, dict):
                result["metadata"] = metadata
        if replay_stored:
            key = (scope, generation, effect.operation_hash)
            self._cache[key] = {
                "observation_id": observation_id,
                "content_sha256": content_sha256,
                "content": replay_content,
                "is_done": bool(_value(result, "is_done", False)),
                "source_checkpoint_revision": checkpoint_revision,
                "evidence_checkpoint_revision": checkpoint_revision,
                "read_path_epochs": deepcopy(
                    terminal_receipt.get("read_path_epochs", ())
                    if terminal_receipt is not None
                    else (read_receipt.get("epoch"),)
                    if read_receipt is not None
                    else ()
                ),
            }
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_cache_entries:
                self._cache.popitem(last=False)
        if success and not provider_cache_hit and terminal_receipt is not None:
            read_paths = terminal_receipt.get("read_paths", ())
            read_ranges = terminal_receipt.get("read_ranges", ())
            read_epochs = terminal_receipt.get("read_path_epochs", ())
            read_representation = terminal_receipt.get("read_representation")
            if len(read_paths) == len(read_ranges) == len(
                read_epochs
            ) == 1 and isinstance(read_representation, str):
                self._store_retained_read_fact(
                    context=context,
                    path=str(read_epochs[0]["path"]),
                    coverage=read_ranges[0],
                    epoch=read_epochs[0],
                    observation_id=observation_id,
                    content_sha256=content_sha256,
                    coverage_complete=bool(
                        terminal_receipt.get("cacheable")
                        and terminal_receipt.get("read_projection_reusable") is True
                        and result_metadata.get("output_truncated") is not True
                    ),
                    is_done=bool(_value(result, "is_done", False)),
                    representation=read_representation,
                )
        elif success and not provider_cache_hit and read_receipt is not None:
            self._store_retained_read_fact(
                context=context,
                path=str(read_receipt["path"]),
                coverage=read_receipt["coverage"],
                epoch=read_receipt["epoch"],
                observation_id=str(
                    read_receipt.get("observation_id") or observation_id
                ),
                content_sha256=str(read_receipt["content_sha256"]),
                coverage_complete=bool(read_receipt["coverage_complete"]),
                is_done=bool(_value(result, "is_done", False)),
                representation=str(read_receipt["representation"]),
            )
        return result


__all__ = [
    "ACTION_SEMANTIC_RECEIPT_KEY",
    "OBSERVATION_SCHEMA",
    "READ_OBSERVATION_RECEIPT_KEY",
    "READ_OBSERVATION_SCHEMA",
    "SandboxToolObservationRuntime",
    "ToolEffect",
    "actions_are_provably_read_only",
    "build_planned_action_semantic_receipt",
    "build_preflight_action_semantic_receipt",
    "canonical_tool_identity",
    "canonical_invocation_cwd",
    "classify_tool_effect",
    "declared_action_target_ids",
    "semantic_target_sha256",
]
