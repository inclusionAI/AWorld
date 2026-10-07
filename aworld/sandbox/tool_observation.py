"""Sandbox-owned Tool observation policy and bounded replay protection.

This module belongs to the Sandbox control plane.  Providers execute calls;
they do not own cross-Tool state, progress semantics, or transcript policy.
Only exact, provably read-only core workspace operations can be compacted.
"""

from __future__ import annotations

import ast
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
import shlex
from typing import Any, Mapping, Sequence

from aworld.core.common import ActionResult
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
_SHELL_READ_COMMANDS = frozenset(
    {
        "cat",
        "cd",
        "echo",
        "head",
        "ls",
        "pwd",
        "rg",
        "sed",
        "stat",
        "tail",
        "wc",
    }
)
_SHELL_MUTATION_COMMANDS = frozenset(
    {
        "chmod",
        "chown",
        "cp",
        "install",
        "ln",
        "mkdir",
        "mv",
        "rm",
        "rmdir",
        "touch",
        "truncate",
    }
)
_SAFE_PYTHON_IMPORT_ROOTS = frozenset(
    {
        "base64",
        "collections",
        "csv",
        "cv2",
        "datetime",
        "functools",
        "hashlib",
        "itertools",
        "json",
        "math",
        "numpy",
        "pathlib",
        "re",
        "statistics",
        "struct",
        "sys",
        "toml",
        "typing",
    }
)
_SAFE_PYTHON_CALL_NAMES = frozenset(
    {
        "abs",
        "all",
        "any",
        "bool",
        "bytearray",
        "bytes",
        "dict",
        "enumerate",
        "filter",
        "float",
        "format",
        "frozenset",
        "getattr",
        "hasattr",
        "hex",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "list",
        "map",
        "max",
        "memoryview",
        "min",
        "next",
        "oct",
        "open",
        "ord",
        "print",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "slice",
        "sorted",
        "str",
        "sum",
        "super",
        "tuple",
        "type",
        "zip",
    }
)
_UNSAFE_PYTHON_CALL_NAMES = frozenset(
    {"__import__", "breakpoint", "compile", "eval", "exec", "input"}
)
_SAFE_PYTHON_FROM_IMPORTS = {
    "collections": frozenset({"Counter", "defaultdict", "deque"}),
    "datetime": frozenset({"date", "datetime", "time", "timedelta", "timezone"}),
    "functools": frozenset({"partial", "reduce"}),
    "itertools": frozenset(
        {
            "chain",
            "combinations",
            "count",
            "groupby",
            "islice",
            "permutations",
            "product",
            "repeat",
            "starmap",
            "takewhile",
            "zip_longest",
        }
    ),
    "pathlib": frozenset({"Path", "PurePath", "PurePosixPath"}),
}
_SAFE_PYTHON_METHOD_NAMES = frozenset(
    {
        "Canny",
        "Sobel",
        "VideoCapture",
        "abs",
        "absdiff",
        "all",
        "any",
        "append",
        "argmax",
        "argmin",
        "argsort",
        "array",
        "asarray",
        "astype",
        "connectedComponentsWithStats",
        "cvtColor",
        "diff",
        "endswith",
        "exists",
        "find",
        "full",
        "get",
        "group",
        "groups",
        "is_dir",
        "is_file",
        "isOpened",
        "isnan",
        "items",
        "join",
        "keys",
        "load",
        "loads",
        "match",
        "max",
        "mean",
        "median",
        "min",
        "morphologyEx",
        "nonzero",
        "ones",
        "percentile",
        "read",
        "read_bytes",
        "read_text",
        "release",
        "reshape",
        "resize",
        "round",
        "search",
        "sort",
        "split",
        "sqrt",
        "stack",
        "startswith",
        "std",
        "strip",
        "sum",
        "tolist",
        "values",
        "var",
        "where",
    }
)


def _newlines_are_quoted(code: str) -> bool:
    quote: str | None = None
    escaped = False
    for character in code:
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote != "'":
            escaped = True
            continue
        if character in {"'", '"'}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
            continue
        if character in "\n\r" and quote is None:
            return False
    return quote is None


def _python_open_is_read_only(call: ast.Call) -> bool:
    mode: ast.AST | None = None
    if len(call.args) >= 2:
        mode = call.args[1]
    for keyword in call.keywords:
        if keyword.arg == "mode":
            mode = keyword.value
    if mode is None:
        return True
    return (
        isinstance(mode, ast.Constant)
        and isinstance(mode.value, str)
        and mode.value.startswith("r")
        and "+" not in mode.value
    )


def _python_is_provably_read_only(source: str) -> bool:
    try:
        tree = ast.parse(source, mode="exec")
    except (SyntaxError, ValueError, TypeError):
        return False
    local_functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imported_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported_names.update(
                alias.asname or alias.name.split(".", 1)[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            imported_names.update(alias.asname or alias.name for alias in node.names)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name.split(".", 1)[0] not in _SAFE_PYTHON_IMPORT_ROOTS
                for alias in node.names
            ):
                return False
        elif isinstance(node, ast.ImportFrom):
            root = node.module.split(".", 1)[0] if node.module else ""
            allowed = _SAFE_PYTHON_FROM_IMPORTS.get(root, frozenset())
            if (
                node.level
                or not node.module
                or root not in _SAFE_PYTHON_IMPORT_ROOTS
                or any(alias.name not in allowed for alias in node.names)
            ):
                return False
        elif isinstance(node, (ast.ClassDef, ast.Delete)):
            return False
        elif isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Name):
                if function.id in _UNSAFE_PYTHON_CALL_NAMES:
                    return False
                if (
                    function.id not in _SAFE_PYTHON_CALL_NAMES
                    and function.id not in local_functions
                    and function.id not in imported_names
                ):
                    return False
                if function.id == "open" and not _python_open_is_read_only(node):
                    return False
            elif isinstance(function, ast.Attribute):
                if function.attr == "open":
                    if not _python_open_is_read_only(node):
                        return False
                elif function.attr not in _SAFE_PYTHON_METHOD_NAMES:
                    return False
            else:
                return False
    return True


def _shell_python_inline_is_provably_read_only(code: str) -> bool:
    if not _newlines_are_quoted(code) or any(value in code for value in ("$", "`")):
        return False
    try:
        lexer = shlex.shlex(code, posix=True, punctuation_chars=";&|><")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False
    normalized: list[str] = []
    position = 0
    while position < len(tokens):
        token = tokens[position]
        if token in {">", ">>", "<>"}:
            if position + 1 >= len(tokens) or tokens[position + 1] != "/dev/null":
                return False
            if normalized and normalized[-1].isdigit():
                normalized.pop()
            position += 2
            continue
        if token == ">&":
            if position + 1 >= len(tokens) or not tokens[position + 1].isdigit():
                return False
            if normalized and normalized[-1].isdigit():
                normalized.pop()
            position += 2
            continue
        normalized.append(token)
        position += 1
    segments: list[list[str]] = [[]]
    separators: list[str] = []
    for token in normalized:
        if token in {"&&", "|"}:
            if not segments[-1]:
                return False
            separators.append(token)
            segments.append([])
            continue
        if token in {";", "||", "&", "<"}:
            return False
        segments[-1].append(token)
    if not segments[-1]:
        return False
    if segments[0][0] == "cd":
        if len(segments[0]) != 2 or not separators or separators[0] != "&&":
            return False
        segments = segments[1:]
        separators = separators[1:]
    if not segments or len(segments[0]) != 3 or segments[0][1] != "-c":
        return False
    executable = segments[0][0].rsplit("/", 1)[-1]
    if executable not in {"python", "python3"}:
        return False
    if not _python_is_provably_read_only(segments[0][2]):
        return False
    if any(separator != "|" for separator in separators):
        return False
    return all(
        segment
        and segment[0] in _SHELL_READ_COMMANDS
        and segment[0] != "cd"
        for segment in segments[1:]
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


def _shell_segments(code: str) -> list[list[str]] | None:
    if any(character in code for character in "$`\n\r"):
        return None
    try:
        lexer = shlex.shlex(code, posix=True, punctuation_chars=";&|><")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    if not tokens:
        return None
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token == "&&":
            segments.append([])
            continue
        if any(character in token for character in ";&|><"):
            return None
        segments[-1].append(token)
    return segments if all(segments) else None


def _shell_is_provably_read_only(code: str) -> bool:
    segments = _shell_segments(code)
    if not segments:
        return False
    for segment in segments:
        executable = segment[0]
        if executable not in _SHELL_READ_COMMANDS:
            return False
        if executable == "rg" and any(value.startswith("--pre") for value in segment[1:]):
            return False
        if executable == "sed":
            if (
                len(segment) < 3
                or segment[1] != "-n"
                or re.fullmatch(r"\d+(?:,\d+)?p", segment[2]) is None
                or any(value.startswith("-") for value in segment[3:])
            ):
                return False
    return True


def _shell_is_known_mutation(code: str) -> bool:
    try:
        lexer = shlex.shlex(code, posix=True, punctuation_chars=";&|><")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False
    for position, token in enumerate(tokens):
        if token in {">", ">>", "<>"}:
            if position + 1 >= len(tokens) or tokens[position + 1] != "/dev/null":
                return True
        elif token == ">&":
            if position + 1 >= len(tokens) or not tokens[position + 1].isdigit():
                return True
    command_start = True
    for token in tokens:
        if token in {";", "&&", "||", "|", "&"}:
            command_start = True
            continue
        if command_start:
            command_start = False
            if token in _SHELL_MUTATION_COMMANDS:
                return True
            if token == "sed" and any(value == "-i" or value.startswith("-i") for value in tokens):
                return True
    return False


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
    if tool == "filesystem":
        if operation in _FILESYSTEM_READ_ACTIONS:
            effect = "read_only"
            cacheable = params.get("refresh") is not True
        elif operation in _FILESYSTEM_MUTATION_ACTIONS:
            effect = "mutating"
    elif tool == "terminal" and operation == "run_code":
        code = params.get("code")
        if isinstance(code, str) and (
            _shell_is_provably_read_only(code)
            or _shell_python_inline_is_provably_read_only(code)
        ):
            effect = "read_only"
            cacheable = True
        elif isinstance(code, str) and _shell_is_known_mutation(code):
            effect = "mutating"
    operation_hash = "sha256:" + hashlib.sha256(
        json.dumps(
            {"identity": identity, "params": params},
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


class SandboxToolObservationRuntime:
    """Task-scoped observation cache owned by one Sandbox control plane."""

    def __init__(self, *, max_cache_entries: int = _MAX_CACHE_ENTRIES) -> None:
        self._max_cache_entries = max(1, max_cache_entries)
        self._generation: dict[tuple[str, str, str], int] = {}
        self._cache: "OrderedDict[tuple[tuple[str, str, str], int, str], dict[str, Any]]" = OrderedDict()

    def _current_generation(self, context: Any) -> int:
        return self._generation.get(_scope(context), 0)

    def current_generation(self, context: Any) -> int:
        """Return the bounded workspace generation for control-plane receipts."""

        return self._current_generation(context)

    def lookup(self, action: Any, *, context: Any) -> ActionResult | None:
        effect = classify_tool_effect(action)
        if not effect.cacheable:
            return None
        scope = _scope(context)
        generation = self._current_generation(context)
        key = (scope, generation, effect.operation_hash)
        cached = self._cache.get(key)
        if cached is None:
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
        effect = classify_tool_effect(action)
        scope = _scope(context)
        generation = self._current_generation(context)
        success = _result_success(result)
        effective_effect = effect.effect
        workspace_mutated: bool | None = None
        if effect.effect == "mutating":
            # A failed shell may have partially changed state, so every known
            # mutation invalidates prior reads while only a successful result
            # becomes positive mutation evidence.
            generation += 1
            self._generation[scope] = generation
            workspace_mutated = True if success else None
            if not success:
                effective_effect = "unknown"
        elif effect.effect == "unknown":
            # Unknown calls must execute and conservatively invalidate replay,
            # but do not claim progress merely because a command ran.
            generation += 1
            self._generation[scope] = generation
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
        }
        metadata = _metadata(result)
        metadata["sandbox_observation"] = receipt
        try:
            result.metadata = metadata
        except (AttributeError, TypeError):
            if isinstance(result, dict):
                result["metadata"] = metadata
        if effect.cacheable and success:
            key = (scope, generation, effect.operation_hash)
            self._cache[key] = {
                "observation_id": observation_id,
                "content_sha256": content_sha256,
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
