"""Task-generic normalization for shell and tool execution evidence.

Transport success is not always semantic success.  This module keeps the
normalization deliberately conservative: it trusts structured status first and
only recognizes high-specificity textual failure signatures.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping, Sequence

from aworld.core.common import ActionModel, ActionResult


TOOL_EVIDENCE_NORMALIZATION_ENV = "AWORLD_TOOL_EVIDENCE_NORMALIZATION"

_PIPELINE_RE = re.compile(r"(?<!\|)\|(?![|&])")
_TRACEBACK_RE = re.compile(
    r"Traceback \(most recent call last\):[\s\S]{0,32768}"
    r"(?:^|\n)[A-Za-z_][\w.]*?(?:Error|Exception):\s*\S",
    re.MULTILINE,
)
_SHELL_FAILURE_RE = re.compile(
    r"(?:^|\n)(?:[^\n]*:\s*)?(?:command not found|permission denied)(?:\s|$)",
    re.IGNORECASE,
)
_SHELL_TOOL_MARKERS = ("shell", "terminal", "bash")
_SHELL_ACTIONS = frozenset({"exec", "execute", "execute_command", "run", "run_command"})
_DEFAULT_BASH_TOOL_NAMES = frozenset({"terminal", "terminal_tool"})
_PIPEFAIL_SHELLS = frozenset({"bash", "zsh", "ksh"})


def tool_evidence_normalization_enabled() -> bool:
    """Return the default-on canary switch without accepting ambiguous values."""
    raw = os.environ.get(TOOL_EVIDENCE_NORMALIZATION_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _shell_action(action: ActionModel) -> bool:
    tool_name = str(action.tool_name or "").lower()
    action_name = str(action.action_name or "").lower()
    return any(marker in tool_name for marker in _SHELL_TOOL_MARKERS) and (
        action_name in _SHELL_ACTIONS or not action_name
    )


def _pipefail_compatible(action: ActionModel) -> bool:
    params = action.params if isinstance(action.params, Mapping) else {}
    configured_shell = params.get("shell") or params.get("executable")
    if isinstance(configured_shell, str) and configured_shell.strip():
        return os.path.basename(configured_shell.strip()) in _PIPEFAIL_SHELLS
    return str(action.tool_name or "").lower() in _DEFAULT_BASH_TOOL_NAMES


def enforce_pipeline_failure_semantics(actions: Sequence[ActionModel]) -> None:
    """Enable ``pipefail`` for recognizable shell pipelines in place.

    Only the conventional ``command`` parameter of an explicitly shell-like
    tool is changed.  Other code-bearing tools and already protected commands
    are left untouched.
    """
    if not tool_evidence_normalization_enabled():
        return
    for action in actions:
        params = action.params
        if (
            not isinstance(params, dict)
            or not _shell_action(action)
            or not _pipefail_compatible(action)
        ):
            continue
        command = params.get("command")
        if (
            not isinstance(command, str)
            or not _PIPELINE_RE.search(command)
            or "pipefail" in command
        ):
            continue
        params["command"] = f"set -o pipefail; {command}"


def _mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped.startswith("{"):
        return None
    try:
        parsed = json.loads(stripped)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _bounded_text(value: Any) -> str:
    if isinstance(value, str):
        return value[:32768]
    if isinstance(value, (bytes, bytearray)):
        return bytes(value[:32768]).decode("utf-8", errors="replace")
    if isinstance(value, Mapping):
        parts = [
            item
            for key in ("stderr", "stdout", "output", "message", "error")
            for item in (value.get(key),)
            if isinstance(item, str)
        ]
        return "\n".join(parts)[:32768]
    return ""


def _structured_failure(result: ActionResult) -> str | None:
    metadata = result.metadata if isinstance(result.metadata, Mapping) else {}
    content = _mapping(result.content) or {}
    nested_metadata = (
        content.get("metadata") if isinstance(content.get("metadata"), Mapping) else {}
    )
    for source in (metadata, content, nested_metadata):
        for key in ("return_code", "exit_code"):
            value = source.get(key)
            if isinstance(value, str) and value.strip().lstrip("-").isdigit():
                value = int(value)
            if isinstance(value, int) and not isinstance(value, bool) and value != 0:
                return "nonzero_exit"
        pipeline = source.get("pipeline_status") or source.get("pipeline_statuses")
        if isinstance(pipeline, (list, tuple)) and any(
            isinstance(item, int) and not isinstance(item, bool) and item != 0
            for item in pipeline
        ):
            return "pipeline_component_failed"
        if source.get("success") is False:
            return "structured_success_false"
        if source.get("error"):
            return "structured_error"
    return None


def semantic_failure_code(
    result: ActionResult, *, allow_text_signatures: bool = False
) -> str | None:
    """Return a stable failure code, avoiding generic stderr heuristics."""
    if result.error or result.success is False:
        return _structured_failure(result) or "action_result_failed"
    structured = _structured_failure(result)
    if structured:
        return structured
    if not allow_text_signatures:
        return None
    metadata = result.metadata if isinstance(result.metadata, Mapping) else {}
    content = _mapping(result.content)
    text = "\n".join(
        part
        for part in (
            _bounded_text(metadata.get("stderr")),
            _bounded_text(content.get("stderr")) if content else "",
        )
        if part
    )[-8192:]
    if _TRACEBACK_RE.search(text):
        return "python_traceback"
    if _SHELL_FAILURE_RE.search(text):
        return "shell_diagnostic_failure"
    return None


def normalize_action_result_evidence(
    result: ActionResult, *, action: ActionModel | None = None
) -> str | None:
    """Project recognized semantic failure onto the authoritative result."""
    if not tool_evidence_normalization_enabled():
        return None
    code = semantic_failure_code(
        result,
        allow_text_signatures=bool(action is not None and _shell_action(action)),
    )
    if code is None:
        return None
    metadata = dict(result.metadata or {})
    metadata["semantic_failure"] = {
        "schema_version": "aworld.tool-semantic-failure/v1",
        "code": code,
    }
    result.metadata = metadata
    result.success = False
    if not result.error:
        result.error = f"semantic_failure:{code}"
    return code


def normalize_observation_tool_evidence(
    actions: Sequence[ActionModel], observation: Any
) -> tuple[str, ...]:
    results = getattr(observation, "action_result", None)
    if not isinstance(results, list):
        return ()
    codes = []
    for action, result in zip(actions, results):
        if isinstance(result, ActionResult):
            code = normalize_action_result_evidence(result, action=action)
            if code:
                codes.append(code)
    return tuple(codes)


__all__ = [
    "TOOL_EVIDENCE_NORMALIZATION_ENV",
    "enforce_pipeline_failure_semantics",
    "normalize_action_result_evidence",
    "normalize_observation_tool_evidence",
    "semantic_failure_code",
    "tool_evidence_normalization_enabled",
]
