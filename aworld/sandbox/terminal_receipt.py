"""Terminal-owned execution planning and compact effect receipts.

The terminal provider is the only component that knows which language it
actually selected and which parser accepted the submitted source.  Sandbox
observation code consumes the resulting receipt instead of independently
guessing those facts from the raw ``code`` argument.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import os
import posixpath
import re
import secrets
import shlex
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

import bashlex


TERMINAL_EXECUTION_RECEIPT_SCHEMA = "aworld.terminal-execution-receipt/v2"
TERMINAL_EXECUTION_RECEIPT_KEY = "terminal_execution_receipt"
# v2 coverage fields are additive: ``read_ranges`` aligns one-for-one with
# ``read_paths`` and ``read_projection_reusable`` says whether stdout preserves
# that single-file window. Legacy v2 receipts are re-derived by the Sandbox
# with this same analyzer; they are never assumed reusable by default. Provider
# replay is authenticated by ``cache_hit=true`` plus ``executed=false``, a
# content/observation identity, representation, and checkpoint revision.
TERMINAL_EXECUTION_ANALYZER_VERSION = 9
TERMINAL_LANGUAGE_CONTRACT_VERSION = 1
TERMINAL_LANGUAGES = frozenset({"shell", "python"})
TERMINAL_EFFECTS = frozenset({"read_only", "mutating", "unknown"})
TERMINAL_CACHEABLE_EFFECT_SOURCES = frozenset(
    {"execution_trace", "trusted_command_contract", "trusted_docker_command_contract"}
)
_MAX_RECEIPT_PATHS = 16
_MAX_RECEIPT_PATH_CHARS = 512
TERMINAL_EXECUTION_AUTHORITY_ENV = (
    "AWORLD_INTERNAL_TERMINAL_EXECUTION_AUTHORITY"
)


def _terminal_process_authority() -> str:
    inherited = os.environ.get(TERMINAL_EXECUTION_AUTHORITY_ENV, "")
    if re.fullmatch(r"[0-9a-f]{64}", inherited):
        return inherited
    generated = secrets.token_hex(32)
    # Built-in stdio providers are child processes of the Sandbox. Sharing a
    # freshly generated parent authority through inherited process state lets
    # that sidecar prove locality without treating unrelated hosts/containers
    # with coincidentally equal executable metadata as local.
    os.environ[TERMINAL_EXECUTION_AUTHORITY_ENV] = generated
    return generated


_TERMINAL_PROCESS_AUTHORITY = _terminal_process_authority()

_SHELL_READ_COMMANDS = frozenset(
    {
        ":",
        "basename",
        "cat",
        "cd",
        "cmp",
        "cut",
        "dirname",
        "du",
        "echo",
        "false",
        "find",
        "git",
        "grep",
        "head",
        "ls",
        "md5sum",
        "od",
        "printf",
        "pwd",
        "readlink",
        "realpath",
        "rg",
        "sed",
        "sha1sum",
        "sha256sum",
        "sha512sum",
        "stat",
        "tail",
        "test",
        "true",
        "type",
        "wc",
        "which",
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
_PYTHON_EXECUTABLES = frozenset({"python", "python3", "py"})
_VERSIONED_PYTHON_EXECUTABLE = re.compile(r"python3(?:\.\d+)+\Z")
_NON_FILE_REDIRECT_TARGETS = frozenset(
    {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"}
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
_SAFE_PYTHON_CALLBACK_NAMES = frozenset(
    {
        "abs",
        "bool",
        "bytes",
        "float",
        "format",
        "frozenset",
        "hex",
        "int",
        "len",
        "list",
        "oct",
        "ord",
        "repr",
        "reversed",
        "round",
        "set",
        "str",
        "tuple",
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
_SAFE_PYTHON_MODULE_CALLS = {
    # These entries apply only when the receiver is bound by an exact import
    # (including ``import math as geometry``).  Keeping them module-qualified
    # avoids treating an arbitrary object's equally named method as pure.
    "math": frozenset(
        {
            "atan2",
            "cos",
            "degrees",
            "hypot",
            "sin",
            "sqrt",
        }
    ),
    "re": frozenset(
        {
            "compile",
            "escape",
            "findall",
            "finditer",
            "fullmatch",
            "match",
            "search",
            "split",
            "sub",
            "subn",
        }
    ),
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
        "cwd",
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
        "lstrip",
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
        "readline",
        "readlines",
        "read_bytes",
        "read_text",
        "release",
        "reshape",
        "resize",
        "round",
        "rstrip",
        "search",
        "sort",
        "split",
        "splitlines",
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
_PYTHON_PATH_MUTATION_METHODS = frozenset(
    {
        "chmod",
        "mkdir",
        "rmdir",
        "symlink_to",
        "touch",
        "unlink",
        "write_bytes",
        "write_text",
    }
)
_PYTHON_PATH_MOVE_METHODS = frozenset({"rename", "replace"})
_PYTHON_OS_MUTATION_METHODS = frozenset(
    {"chmod", "makedirs", "mkdir", "remove", "rmdir", "unlink"}
)
_PYTHON_OS_MOVE_METHODS = frozenset({"rename", "replace"})
_PYTHON_SHUTIL_COPY_METHODS = frozenset(
    {"copy", "copy2", "copyfile", "copytree"}
)
_PYTHON_SHUTIL_MOVE_METHODS = frozenset({"move"})
_PYTHON_SHUTIL_MUTATION_METHODS = frozenset({"rmtree"})


@dataclass(frozen=True, slots=True)
class TerminalExecutionPlan:
    """The language/effect decision made by the terminal before execution."""

    language: str
    effect: str
    cacheable: bool
    parsed: bool
    read_paths: tuple[str, ...] = ()
    write_paths: tuple[str, ...] = ()
    background: bool = False
    read_set_complete: bool = True
    nested_languages: tuple[str, ...] = ()
    nested_source_sha256: tuple[str, ...] = ()
    command_cwd: str | None = None
    command_cwd_safe: bool = True
    read_ranges: tuple["TerminalReadRange", ...] = ()
    read_projection_reusable: bool = False
    callback_kinds: tuple[str, ...] = ()
    # Literal executable tokens accepted by the same Shell parse that produced
    # this plan. Providers use these tokens to prove the concrete execution
    # context (for example, PATH resolution inside a Docker container) without
    # maintaining a second command parser.
    executable_tokens: tuple[str, ...] = ()
    executable_set_complete: bool = True
    write_set_complete: bool = True


@dataclass(frozen=True, slots=True)
class TerminalReadRange:
    """Bounded semantic coverage for one path in ``read_paths``.

    The range is descriptive evidence, not an instruction to execute.  Only
    ``full``, explicit line/byte windows, and tail windows can participate in
    overlap reuse.  ``query`` and ``metadata`` remain exact-operation facts.
    """

    kind: str
    start: int | None = None
    end: int | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"kind": self.kind}
        if self.start is not None:
            value["start"] = self.start
        if self.end is not None:
            value["end"] = self.end
        return value


_FULL_READ = TerminalReadRange("full")
_QUERY_READ = TerminalReadRange("query")
_METADATA_READ = TerminalReadRange("metadata")


def terminal_command_sha256(code: str) -> str:
    return "sha256:" + hashlib.sha256(code.encode("utf-8")).hexdigest()


def terminal_execution_context_sha256(executable: str) -> str:
    """Identify one local executable context without exposing its path.

    The identity is reproducible inside the current process and built-in stdio
    children that inherit its freshly generated authority. An independently
    launched worker, cloned container, or remote provider receives a distinct
    authority even when executable path/stat metadata happen to match. It is a
    cache binding, not a content signature or a claim that arbitrary
    executables are trusted.
    """

    candidate = Path(executable).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
        stat_result = resolved.stat()
        fields = (
            "aworld.terminal-execution-context/v1",
            _TERMINAL_PROCESS_AUTHORITY,
            str(resolved),
            str(stat_result.st_dev),
            str(stat_result.st_ino),
            str(stat_result.st_size),
            str(stat_result.st_mtime_ns),
        )
    except OSError:
        fields = (
            "aworld.terminal-execution-context/v1",
            _TERMINAL_PROCESS_AUTHORITY,
            str(candidate.resolve(strict=False)),
            "unavailable",
        )
    return "sha256:" + hashlib.sha256("\0".join(fields).encode("utf-8")).hexdigest()


def _python_open_mode_is_read_only(
    call: ast.Call, *, positional_index: int
) -> bool:
    mode: ast.AST | None = None
    if len(call.args) > positional_index:
        mode = call.args[positional_index]
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


def _python_open_is_read_only(call: ast.Call) -> bool:
    return _python_open_mode_is_read_only(call, positional_index=1)


def _python_path_open_is_read_only(call: ast.Call) -> bool:
    return _python_open_mode_is_read_only(call, positional_index=0)


def python_is_provably_read_only(source: str) -> bool:
    """Return true only for a bounded allowlist of Python read/compute code."""

    try:
        tree = ast.parse(source, mode="exec")
    except (SyntaxError, ValueError, TypeError):
        return False
    local_functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imported_modules = _python_imported_modules(tree)
    imported_bindings = _python_imported_bindings(tree)
    if not _python_trusted_bindings_are_immutable(tree, imported_bindings):
        return False
    if not _python_higher_order_callbacks_are_safe(
        tree, imported_modules, imported_bindings
    ):
        return False
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
                receiver_name = (
                    function.value.id
                    if isinstance(function.value, ast.Name)
                    else None
                )
                receiver_module = imported_modules.get(receiver_name or "")
                module_calls = _SAFE_PYTHON_MODULE_CALLS.get(
                    receiver_module or ""
                )
                if module_calls is not None:
                    if function.attr not in module_calls:
                        return False
                    continue
                if function.attr == "open":
                    if not _python_open_is_read_only(node):
                        return False
                elif function.attr not in _SAFE_PYTHON_METHOD_NAMES:
                    return False
            else:
                return False
    return True


def _constant_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _path_from_python_receiver(node: ast.AST) -> str | None:
    if not _is_python_path_receiver(node) or node.keywords:
        return None
    segments: list[str] = []
    for argument in node.args:
        segment = _constant_string(argument)
        if segment is None and _is_python_path_receiver(argument):
            segment = _path_from_python_receiver(argument)
        if segment is None:
            return None
        segments.append(segment)
    return str(PurePosixPath(*segments))


def _constant_python_path(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    return _constant_string(node) or _path_from_python_receiver(node)


def _is_python_path_receiver(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    function = node.func
    return isinstance(function, ast.Name) and function.id in {
        "Path",
        "PurePath",
        "PurePosixPath",
    }


def _is_direct_python_open_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "open"
    )


def _is_python_path_open_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "open"
        and _is_python_path_receiver(node.func.value)
    )


def _python_path_bindings(tree: ast.Module) -> dict[str, str]:
    store_counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            store_counts[node.id] = store_counts.get(node.id, 0) + 1

    candidates: dict[str, str] = {}
    for node in ast.walk(tree):
        target: ast.AST | None = None
        value: ast.AST | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and value is not None:
            path = _path_from_python_receiver(value)
            if path is not None:
                candidates[target.id] = path
    return {
        name: path
        for name, path in candidates.items()
        if store_counts.get(name) == 1
    }


def _python_file_open_is_read_only(
    node: ast.AST,
    *,
    path_bindings: Mapping[str, str] | None = None,
) -> bool | None:
    if _is_direct_python_open_call(node) and isinstance(node, ast.Call):
        return _python_open_is_read_only(node)
    if _is_python_path_open_call(node) and isinstance(node, ast.Call):
        return _python_path_open_is_read_only(node)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "open"
        and isinstance(node.func.value, ast.Name)
        and path_bindings is not None
        and node.func.value.id in path_bindings
    ):
        return _python_path_open_is_read_only(node)
    return None


def _python_imported_modules(tree: ast.Module) -> dict[str, str]:
    modules: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import):
            continue
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            modules[alias.asname or root] = root
    return modules


def _python_imported_bindings(tree: ast.Module) -> dict[str, str]:
    bindings = dict(_python_imported_modules(tree))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        root = node.module.split(".", 1)[0]
        for alias in node.names:
            if alias.name != "*":
                bindings[alias.asname or alias.name] = root
    return bindings


def _python_expression_root_name(node: ast.AST) -> str | None:
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _python_trusted_bindings_are_immutable(
    tree: ast.Module,
    imported_bindings: Mapping[str, str],
) -> bool:
    """Prove names treated as pure cannot be rebound or monkeypatched.

    Module and builtin purity is sound only while each receiver still denotes
    the value the analyzer modeled.  Python permits rebinding names and
    attributes at runtime, including from a nested function, so every trusted
    binding/shadow and every attribute store is rejected.  Ordinary container
    item assignment remains available for data-processing scripts, while
    imported or dunder-rooted subscript stores fail closed.
    """

    imported_aliases = frozenset(imported_bindings)
    trusted_builtin_names = _SAFE_PYTHON_CALL_NAMES
    if imported_aliases & trusted_builtin_names:
        return False
    local_function_names = frozenset(
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    trusted_names = (
        imported_aliases | trusted_builtin_names | local_function_names
    )
    import_binding_counts = {name: 0 for name in imported_aliases}
    local_function_counts = {name: 0 for name in local_function_names}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                binding = alias.asname or root
                if binding in trusted_builtin_names or binding in local_function_names:
                    return False
                if binding not in imported_aliases:
                    continue
                if imported_bindings.get(binding) != root:
                    return False
                import_binding_counts[binding] += 1
            continue
        if isinstance(node, ast.ImportFrom):
            root = node.module.split(".", 1)[0] if node.module else ""
            for alias in node.names:
                binding = alias.asname or alias.name
                if binding in trusted_builtin_names or binding in local_function_names:
                    return False
                if binding not in imported_aliases:
                    continue
                if imported_bindings.get(binding) != root:
                    return False
                import_binding_counts[binding] += 1
            continue
        if isinstance(node, ast.Name):
            if node.id.startswith("__") and node.id.endswith("__"):
                return False
            if isinstance(node.ctx, (ast.Store, ast.Del)) and node.id in trusted_names:
                return False
            continue
        if isinstance(node, ast.arg) and node.arg in trusted_names:
            return False
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in imported_aliases or node.name in trusted_builtin_names:
                return False
            if node.name in local_function_counts:
                local_function_counts[node.name] += 1
        if isinstance(node, ast.ClassDef) and node.name in trusted_names:
            return False
        if isinstance(node, ast.ExceptHandler):
            if isinstance(node.name, str) and node.name in trusted_names:
                return False
        if isinstance(node, ast.MatchAs):
            if isinstance(node.name, str) and node.name in trusted_names:
                return False
        if isinstance(node, ast.MatchStar):
            if isinstance(node.name, str) and node.name in trusted_names:
                return False
        if isinstance(node, ast.MatchMapping):
            if isinstance(node.rest, str) and node.rest in trusted_names:
                return False
        if isinstance(node, ast.Attribute):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                return False
            if node.attr.startswith("__") and node.attr.endswith("__"):
                return False
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and (
                _python_expression_root_name(node) in imported_aliases
                or str(_python_expression_root_name(node) or "").startswith("__")
            )
        ):
            return False

    return bool(
        all(count == 1 for count in import_binding_counts.values())
        and all(count == 1 for count in local_function_counts.values())
    )


def _python_callback_reference_is_safe(
    node: ast.AST,
    imported_bindings: Mapping[str, str],
) -> bool:
    if isinstance(node, ast.Constant) and node.value is None:
        return True
    if isinstance(node, ast.Name):
        return bool(
            node.id in _SAFE_PYTHON_CALLBACK_NAMES
            and node.id not in imported_bindings
        )
    if not isinstance(node, ast.Lambda):
        return False
    parameter_names = {argument.arg for argument in ast.walk(node.args) if isinstance(argument, ast.arg)}
    rejected_nodes = (
        ast.Attribute,
        ast.Await,
        ast.Call,
        ast.DictComp,
        ast.GeneratorExp,
        ast.ListComp,
        ast.NamedExpr,
        ast.SetComp,
        ast.Yield,
        ast.YieldFrom,
    )
    for child in ast.walk(node.body):
        if isinstance(child, rejected_nodes):
            return False
        if (
            isinstance(child, ast.Name)
            and isinstance(child.ctx, ast.Load)
            and child.id not in parameter_names
        ):
            return False
    return True


def _python_higher_order_callbacks_are_safe(
    tree: ast.Module,
    imported_modules: Mapping[str, str],
    imported_bindings: Mapping[str, str],
) -> bool:
    """Fail closed when executable values cross a callback boundary."""

    def positional(call: ast.Call, index: int) -> ast.AST | None:
        return call.args[index] if index < len(call.args) else None

    def keyword(call: ast.Call, name: str) -> ast.AST | None:
        return next(
            (value.value for value in call.keywords if value.arg == name),
            None,
        )

    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        callbacks: list[ast.AST] = []
        callback_sink = False
        function = call.func
        if isinstance(function, ast.Name):
            if function.id in {"filter", "map", "reduce", "starmap", "takewhile"}:
                callback_sink = True
                callback = positional(call, 0)
                if callback is not None:
                    callbacks.append(callback)
            elif function.id == "iter":
                if len(call.args) >= 2 or any(
                    isinstance(argument, ast.Starred) for argument in call.args
                ):
                    callback_sink = True
                    callback = positional(call, 0)
                    if callback is not None:
                        callbacks.append(callback)
            elif function.id == "groupby":
                callback_sink = True
                callback = keyword(call, "key") or positional(call, 1)
                if callback is not None:
                    callbacks.append(callback)
            elif function.id in {"max", "min", "sorted"}:
                callback_sink = True
                callback = keyword(call, "key")
                if callback is not None:
                    callbacks.append(callback)
            elif function.id == "defaultdict":
                callback_sink = True
                callback = positional(call, 0)
                if callback is not None:
                    callbacks.append(callback)
            elif function.id == "open":
                callback_sink = True
                callback = keyword(call, "opener")
                if callback is not None:
                    callbacks.append(callback)
        elif isinstance(function, ast.Attribute):
            receiver_name = (
                function.value.id if isinstance(function.value, ast.Name) else None
            )
            receiver_module = imported_modules.get(receiver_name or "")
            if receiver_module == "re" and function.attr in {"sub", "subn"}:
                callback_sink = True
                callback = positional(call, 1)
                if callback is not None and not isinstance(callback, ast.Constant):
                    callbacks.append(callback)
            elif receiver_module == "functools" and function.attr == "reduce":
                callback_sink = True
                callback = positional(call, 0)
                if callback is not None:
                    callbacks.append(callback)
            elif receiver_module == "itertools" and function.attr in {
                "starmap",
                "takewhile",
            }:
                callback_sink = True
                callback = positional(call, 0)
                if callback is not None:
                    callbacks.append(callback)
            elif receiver_module == "itertools" and function.attr == "groupby":
                callback_sink = True
                callback = keyword(call, "key") or positional(call, 1)
                if callback is not None:
                    callbacks.append(callback)
            elif function.attr == "sort":
                callback_sink = True
                callback = keyword(call, "key")
                if callback is not None:
                    callbacks.append(callback)
            elif receiver_module == "json" and function.attr in {
                "dump",
                "dumps",
                "load",
                "loads",
            }:
                callback_sink = True
                callback_names = {"cls"}
                if function.attr in {"dump", "dumps"}:
                    callback_names.add("default")
                else:
                    callback_names.update(
                        {
                            "object_hook",
                            "object_pairs_hook",
                            "parse_constant",
                            "parse_float",
                            "parse_int",
                        }
                    )
                callbacks.extend(
                    value.value
                    for value in call.keywords
                    if value.arg in callback_names
                )
            elif receiver_module == "shutil" and function.attr == "copytree":
                callback_sink = True
                callbacks.extend(
                    value.value
                    for value in call.keywords
                    if value.arg in {"copy_function", "ignore"}
                )
            elif receiver_module == "shutil" and function.attr == "rmtree":
                callback_sink = True
                callbacks.extend(
                    value.value
                    for value in call.keywords
                    if value.arg in {"onerror", "onexc"}
                )
            elif receiver_module == "toml" and function.attr in {"load", "loads"}:
                callback_sink = True
                callback = keyword(call, "decoder")
                if callback is not None:
                    callbacks.append(callback)
        if callback_sink and (
            any(isinstance(argument, ast.Starred) for argument in call.args)
            or any(value.arg is None for value in call.keywords)
        ):
            return False
        if any(
            not _python_callback_reference_is_safe(callback, imported_bindings)
            for callback in callbacks
        ):
            return False
    return True


def _python_call_argument(
    call: ast.Call, index: int, keyword_name: str
) -> ast.AST | None:
    if len(call.args) > index:
        return call.args[index]
    return next(
        (keyword.value for keyword in call.keywords if keyword.arg == keyword_name),
        None,
    )


def _python_bool_argument(
    call: ast.Call,
    index: int,
    keyword_name: str,
    *,
    default: bool,
) -> bool | None:
    value = _python_call_argument(call, index, keyword_name)
    if value is None:
        return default
    if isinstance(value, ast.Constant) and isinstance(value.value, bool):
        return value.value
    return None


def _python_handle_expression_is_known(
    node: ast.AST | None,
    *,
    handles: frozenset[str],
    read_only: bool,
    path_bindings: Mapping[str, str] | None = None,
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in handles
    mode = (
        _python_file_open_is_read_only(node, path_bindings=path_bindings)
        if node is not None
        else None
    )
    return mode is read_only


def _python_open_handle_names(
    tree: ast.Module,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return simple names bound exactly once to a builtin ``open`` call."""

    store_counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            store_counts[node.id] = store_counts.get(node.id, 0) + 1

    candidates: dict[str, bool] = {}
    path_bindings = _python_path_bindings(tree)

    def remember(target: ast.AST | None, value: ast.AST) -> None:
        read_only = _python_file_open_is_read_only(
            value, path_bindings=path_bindings
        )
        if isinstance(target, ast.Name) and read_only is not None:
            candidates[target.id] = read_only

    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                remember(item.optional_vars, item.context_expr)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            remember(node.targets[0], node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            remember(node.target, node.value)

    read_handles = frozenset(
        name
        for name, read_only in candidates.items()
        if read_only and store_counts.get(name) == 1
    )
    write_handles = frozenset(
        name
        for name, read_only in candidates.items()
        if not read_only and store_counts.get(name) == 1
    )
    return read_handles, write_handles


def _is_python_csv_writer_call(
    node: ast.AST,
    *,
    imported_modules: Mapping[str, str],
    write_handles: frozenset[str],
) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    receiver = node.func.value
    if (
        node.func.attr != "writer"
        or not isinstance(receiver, ast.Name)
        or imported_modules.get(receiver.id) != "csv"
    ):
        return False
    output = _python_call_argument(node, 0, "csvfile")
    return _python_handle_expression_is_known(
        output, handles=write_handles, read_only=False
    )


def _python_csv_writer_names(
    tree: ast.Module,
    *,
    imported_modules: Mapping[str, str],
    write_handles: frozenset[str],
) -> frozenset[str]:
    store_counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            store_counts[node.id] = store_counts.get(node.id, 0) + 1

    candidates: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and _is_python_csv_writer_call(
            node.value,
            imported_modules=imported_modules,
            write_handles=write_handles,
        ):
            candidates.add(target.id)
    return frozenset(name for name in candidates if store_counts.get(name) == 1)


def _python_effects_are_fully_modeled(tree: ast.Module) -> bool:
    """Prove that calls/imports outside known file effects are computation only."""

    local_functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imported_names: set[str] = set()
    read_handles, write_handles = _python_open_handle_names(tree)
    path_bindings = _python_path_bindings(tree)
    imported_modules = _python_imported_modules(tree)
    imported_bindings = _python_imported_bindings(tree)
    if not _python_trusted_bindings_are_immutable(tree, imported_bindings):
        return False
    if not _python_higher_order_callbacks_are_safe(
        tree, imported_modules, imported_bindings
    ):
        return False
    csv_writer_names = _python_csv_writer_names(
        tree,
        imported_modules=imported_modules,
        write_handles=write_handles,
    )
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported_names.update(
                alias.asname or alias.name.split(".", 1)[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            imported_names.update(alias.asname or alias.name for alias in node.names)

    modeled_import_roots = _SAFE_PYTHON_IMPORT_ROOTS | {"os", "shutil"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name.split(".", 1)[0] not in modeled_import_roots
                for alias in node.names
            ):
                return False
            continue
        if isinstance(node, ast.ImportFrom):
            root = node.module.split(".", 1)[0] if node.module else ""
            allowed = _SAFE_PYTHON_FROM_IMPORTS.get(root, frozenset())
            if (
                node.level
                or not node.module
                or root not in _SAFE_PYTHON_IMPORT_ROOTS
                or any(alias.name not in allowed for alias in node.names)
            ):
                return False
            continue
        if isinstance(node, (ast.ClassDef, ast.Delete)):
            return False
        if not isinstance(node, ast.Call):
            continue

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
            continue
        if not isinstance(function, ast.Attribute):
            return False

        receiver_name = (
            function.value.id if isinstance(function.value, ast.Name) else None
        )
        receiver_module = imported_modules.get(receiver_name or "")
        module_calls = _SAFE_PYTHON_MODULE_CALLS.get(receiver_module or "")
        if module_calls is not None:
            if function.attr in module_calls:
                continue
            # A recognized pure-module receiver must not fall through to the
            # broad container/string method allowlist below.  Unknown module
            # calls remain unknown even when their attribute name happens to
            # match a safe method on a different receiver type.
            return False
        if receiver_name in read_handles and function.attr in {
            "close",
            "read",
            "readline",
            "readlines",
        }:
            continue
        if receiver_name in write_handles and function.attr in {
            "close",
            "flush",
            "write",
            "writelines",
        }:
            continue
        if receiver_name in csv_writer_names and function.attr in {
            "writerow",
            "writerows",
        }:
            continue
        if receiver_module == "json":
            if function.attr in {"dumps", "loads"}:
                continue
            if function.attr == "dump" and _python_handle_expression_is_known(
                _python_call_argument(node, 1, "fp"),
                handles=write_handles,
                read_only=False,
                path_bindings=path_bindings,
            ):
                continue
            if function.attr == "load" and _python_handle_expression_is_known(
                _python_call_argument(node, 0, "fp"),
                handles=read_handles,
                read_only=True,
                path_bindings=path_bindings,
            ):
                continue
            return False
        if receiver_module == "csv" and _is_python_csv_writer_call(
            node,
            imported_modules=imported_modules,
            write_handles=write_handles,
        ):
            continue
        if function.attr in {"writerow", "writerows"} and (
            _is_python_csv_writer_call(
                function.value,
                imported_modules=imported_modules,
                write_handles=write_handles,
            )
        ):
            continue
        if receiver_module == "numpy" and function.attr == "save":
            continue
        if receiver_name == "os" and function.attr in (
            _PYTHON_OS_MUTATION_METHODS | _PYTHON_OS_MOVE_METHODS
        ):
            continue
        if receiver_name == "shutil" and function.attr in (
            _PYTHON_SHUTIL_COPY_METHODS
            | _PYTHON_SHUTIL_MOVE_METHODS
            | _PYTHON_SHUTIL_MUTATION_METHODS
        ):
            continue
        path_receiver = _is_python_path_receiver(function.value) or (
            isinstance(function.value, ast.Name)
            and function.value.id in path_bindings
        )
        if path_receiver and function.attr in (
            _PYTHON_PATH_MUTATION_METHODS | _PYTHON_PATH_MOVE_METHODS
        ):
            continue
        if path_receiver and function.attr == "open":
            continue
        if function.attr in {
            "VideoCapture",
            "load",
            "read",
            "read_bytes",
            "read_text",
        }:
            continue
        if function.attr in _SAFE_PYTHON_METHOD_NAMES:
            continue
        if function.attr in {"close", "flush", "write", "writelines"} and (
            _is_direct_python_open_call(function.value)
            or _python_file_open_is_read_only(
                function.value, path_bindings=path_bindings
            )
            is False
        ):
            continue
        return False
    return True


def _python_effect_and_paths(
    source: str,
) -> tuple[str, tuple[str, ...], tuple[str, ...], bool, bool]:
    try:
        tree = ast.parse(source, mode="exec")
    except (SyntaxError, ValueError, TypeError):
        return "unknown", (), (), False, False
    reads: list[str] = []
    writes: list[str] = []
    known_mutation = False
    read_set_complete = True
    write_set_complete = True
    effect_targets_complete = True
    read_handles, write_handles = _python_open_handle_names(tree)
    path_bindings = _python_path_bindings(tree)
    imported_modules = _python_imported_modules(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name) and function.id == "open":
            path = _constant_string(node.args[0]) if node.args else None
            if _python_open_is_read_only(node):
                if path:
                    reads.append(path)
                else:
                    read_set_complete = False
            else:
                known_mutation = True
                if path:
                    writes.append(path)
                else:
                    write_set_complete = False
                    effect_targets_complete = False
        elif isinstance(function, ast.Attribute):
            receiver_name = (
                function.value.id if isinstance(function.value, ast.Name) else None
            )
            path = _path_from_python_receiver(function.value) or path_bindings.get(
                receiver_name or ""
            )
            path_receiver = _is_python_path_receiver(function.value) or (
                receiver_name in path_bindings
            )
            receiver_module = imported_modules.get(receiver_name or "")
            if path_receiver and function.attr in _PYTHON_PATH_MUTATION_METHODS:
                known_mutation = True
                if path is not None:
                    writes.append(path)
                else:
                    write_set_complete = False
                    effect_targets_complete = False
                if function.attr == "mkdir" and _python_bool_argument(
                    node, 1, "parents", default=False
                ) is not False:
                    write_set_complete = False
                    effect_targets_complete = False
            elif path_receiver and function.attr in _PYTHON_PATH_MOVE_METHODS:
                known_mutation = True
                destination = _constant_string(node.args[0]) if node.args else None
                for mutation_path in (path, destination):
                    if mutation_path is not None:
                        writes.append(mutation_path)
                    else:
                        write_set_complete = False
                        effect_targets_complete = False
                write_set_complete = False
                effect_targets_complete = False
            elif (
                receiver_name == "os" and function.attr in _PYTHON_OS_MUTATION_METHODS
            ):
                known_mutation = True
                argument_path = _constant_string(node.args[0]) if node.args else None
                if argument_path:
                    writes.append(argument_path)
                else:
                    write_set_complete = False
                    effect_targets_complete = False
                if function.attr == "makedirs":
                    write_set_complete = False
                    effect_targets_complete = False
            elif receiver_name == "os" and function.attr in _PYTHON_OS_MOVE_METHODS:
                known_mutation = True
                for index in range(2):
                    argument_path = (
                        _constant_string(node.args[index])
                        if index < len(node.args)
                        else None
                    )
                    if argument_path:
                        writes.append(argument_path)
                    else:
                        write_set_complete = False
                        effect_targets_complete = False
                write_set_complete = False
                effect_targets_complete = False
            elif (
                receiver_name == "shutil"
                and function.attr in _PYTHON_SHUTIL_COPY_METHODS
            ):
                known_mutation = True
                source_path = _constant_string(node.args[0]) if node.args else None
                destination_path = (
                    _constant_string(node.args[1]) if len(node.args) > 1 else None
                )
                if source_path:
                    reads.append(source_path)
                else:
                    read_set_complete = False
                    effect_targets_complete = False
                if destination_path:
                    writes.append(destination_path)
                else:
                    write_set_complete = False
                    effect_targets_complete = False
                if function.attr in {"copy", "copy2", "copytree"}:
                    write_set_complete = False
                    effect_targets_complete = False
                if function.attr == "copytree":
                    read_set_complete = False
            elif (
                receiver_name == "shutil"
                and function.attr in _PYTHON_SHUTIL_MOVE_METHODS
            ):
                known_mutation = True
                for index in range(2):
                    argument_path = (
                        _constant_string(node.args[index])
                        if index < len(node.args)
                        else None
                    )
                    if argument_path:
                        writes.append(argument_path)
                    else:
                        write_set_complete = False
                        effect_targets_complete = False
                # ``move`` may rename a directory tree or derive a basename
                # beneath an existing destination directory.
                write_set_complete = False
                effect_targets_complete = False
            elif (
                receiver_name == "shutil"
                and function.attr in _PYTHON_SHUTIL_MUTATION_METHODS
            ):
                known_mutation = True
                argument_path = _constant_string(node.args[0]) if node.args else None
                if argument_path:
                    writes.append(argument_path)
                else:
                    write_set_complete = False
                    effect_targets_complete = False
                # rmtree mutates an unbounded descendant set.
                write_set_complete = False
                effect_targets_complete = False
            elif receiver_module == "json":
                if function.attr == "dump" and not _python_handle_expression_is_known(
                    _python_call_argument(node, 1, "fp"),
                    handles=write_handles,
                    read_only=False,
                    path_bindings=path_bindings,
                ):
                    effect_targets_complete = False
                elif function.attr == "load" and not (
                    _python_handle_expression_is_known(
                        _python_call_argument(node, 0, "fp"),
                        handles=read_handles,
                        read_only=True,
                        path_bindings=path_bindings,
                    )
                ):
                    read_set_complete = False
                    effect_targets_complete = False
            elif receiver_module == "numpy" and function.attr == "save":
                known_mutation = True
                output = _python_call_argument(node, 0, "file")
                output_path = _constant_python_path(output)
                if output_path is not None:
                    writes.append(
                        output_path
                        if output_path.endswith(".npy")
                        else output_path + ".npy"
                    )
                elif not _python_handle_expression_is_known(
                    output,
                    handles=write_handles,
                    read_only=False,
                    path_bindings=path_bindings,
                ):
                    write_set_complete = False
                    effect_targets_complete = False
            elif function.attr == "save":
                # An arbitrary receiver's save contract is not strong enough
                # to prove filename rewriting or additional side effects.
                known_mutation = True
                argument_path = _constant_string(node.args[0]) if node.args else None
                if argument_path:
                    writes.append(argument_path)
                write_set_complete = False
                effect_targets_complete = False
            elif function.attr in {"VideoCapture", "load"}:
                argument_path = _constant_string(node.args[0]) if node.args else None
                if argument_path:
                    reads.append(argument_path)
                else:
                    read_set_complete = False
                    effect_targets_complete = False
            elif function.attr in {
                "open",
                "read",
                "read_bytes",
                "read_text",
                "readline",
                "readlines",
            }:
                if function.attr != "open" and (
                    _python_file_open_is_read_only(
                        function.value, path_bindings=path_bindings
                    )
                    is not None
                    or receiver_name in read_handles
                    or receiver_name in write_handles
                ):
                    # The underlying open call already contributed its literal
                    # path; handle reads must not manufacture a second dynamic
                    # dependency from the receiver name.
                    continue
                if function.attr == "open":
                    read_only = (
                        _python_path_open_is_read_only(node)
                        if path_receiver
                        else None
                    )
                    if read_only is False:
                        known_mutation = True
                        if path:
                            writes.append(path)
                        else:
                            write_set_complete = False
                            effect_targets_complete = False
                    elif read_only is True and path:
                        reads.append(path)
                    else:
                        read_set_complete = False
                        effect_targets_complete = False
                elif path:
                    reads.append(path)
                else:
                    read_set_complete = False
                    effect_targets_complete = False
    if len(dict.fromkeys(reads)) > _MAX_RECEIPT_PATHS or any(
        len(path) > _MAX_RECEIPT_PATH_CHARS for path in reads
    ):
        read_set_complete = False
        if known_mutation:
            effect_targets_complete = False
    if len(dict.fromkeys(writes)) > _MAX_RECEIPT_PATHS or any(
        len(path) > _MAX_RECEIPT_PATH_CHARS for path in writes
    ):
        write_set_complete = False
    effects_fully_modeled = _python_effects_are_fully_modeled(tree)
    if known_mutation:
        return (
            "mutating" if effects_fully_modeled else "unknown",
            _bounded_paths(reads),
            _bounded_paths(writes),
            read_set_complete,
            write_set_complete
            and effect_targets_complete
            and effects_fully_modeled,
        )
    if effects_fully_modeled and python_is_provably_read_only(source):
        return "read_only", _bounded_paths(reads), (), read_set_complete, True
    return (
        "unknown",
        _bounded_paths(reads),
        _bounded_paths(writes),
        read_set_complete,
        write_set_complete
        and effect_targets_complete
        and effects_fully_modeled,
    )


def _looks_like_bare_python(source: str) -> bool:
    try:
        tree = ast.parse(source, mode="exec")
    except (SyntaxError, ValueError, TypeError):
        return False
    strong_statements = (
        ast.AnnAssign,
        ast.Assign,
        ast.AsyncFor,
        ast.AsyncFunctionDef,
        ast.AsyncWith,
        ast.AugAssign,
        ast.ClassDef,
        ast.For,
        ast.FunctionDef,
        ast.If,
        ast.Import,
        ast.ImportFrom,
        ast.Match,
        ast.Try,
        ast.While,
        ast.With,
    )
    if any(isinstance(node, strong_statements) for node in tree.body):
        return True
    return any(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and (
            isinstance(node.value.func, ast.Attribute)
            or (
                isinstance(node.value.func, ast.Name)
                and node.value.func.id in _SAFE_PYTHON_CALL_NAMES
            )
        )
        for node in tree.body
    )


def _shell_child_nodes(node: Any) -> Iterable[Any]:
    for value in vars(node).values():
        if hasattr(value, "kind"):
            yield value
        elif isinstance(value, list):
            yield from (child for child in value if hasattr(child, "kind"))


def _command_words(words: Sequence[str]) -> tuple[str, list[str]]:
    remaining = list(words)
    while remaining:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", remaining[0]):
            remaining.pop(0)
            continue
        raw_executable = remaining[0]
        executable = "source" if raw_executable == "." else Path(raw_executable).name
        if executable in {"command", "builtin"}:
            remaining.pop(0)
            continue
        if executable == "time":
            remaining.pop(0)
            while remaining and remaining[0] in {"-p", "--portability", "--"}:
                remaining.pop(0)
            continue
        if executable == "sudo":
            remaining.pop(0)
            while remaining and remaining[0].startswith("-"):
                option = remaining.pop(0)
                if (
                    option in {"-u", "-g", "-h", "-p", "-C", "-T", "-R", "-D"}
                    and remaining
                ):
                    remaining.pop(0)
            continue
        if executable == "env":
            remaining.pop(0)
            while remaining and (remaining[0].startswith("-") or "=" in remaining[0]):
                remaining.pop(0)
            continue
        return executable, remaining[1:]
    return "", []


def _literal_redirect_path(output: Any) -> str | None:
    if isinstance(output, int):
        return None
    value = getattr(output, "word", None)
    if not isinstance(value, str) or not value or value in _NON_FILE_REDIRECT_TARGETS:
        return None
    if value.startswith("/dev/fd/") or _has_dynamic_path(value):
        return None
    return value


def _shell_word_has_runtime_expansion(raw_word: str) -> bool:
    quote: str | None = None
    index = 0
    while index < len(raw_word):
        value = raw_word[index]
        if quote == "'":
            if value == "'":
                quote = None
            index += 1
            continue
        if quote == '"':
            if value == '"':
                quote = None
                index += 1
                continue
            if value == "\\":
                index += 2
                continue
            if value in {"$", "`"}:
                return True
            index += 1
            continue
        if value in {"'", '"'}:
            quote = value
            index += 1
            continue
        if value == "\\":
            index += 2
            continue
        if value in {"$", "`", "*", "?", "[", "{"}:
            return True
        if value == "~" and (index == 0 or raw_word[index - 1] in {"=", ":"}):
            return True
        index += 1
    return False


def _literal_shell_word(source: str, node: Any) -> str | None:
    position = getattr(node, "pos", None)
    if (
        not isinstance(position, tuple)
        or len(position) != 2
        or not all(isinstance(value, int) for value in position)
    ):
        return None
    raw_word = source[position[0] : position[1]]
    if getattr(node, "parts", ()) or _shell_word_has_runtime_expansion(raw_word):
        return None
    try:
        values = shlex.split(raw_word, comments=False, posix=True)
    except ValueError:
        return None
    return values[0] if len(values) == 1 else None


def _python_source_from_shell_command(node: Any, source: str) -> str | None:
    word_nodes = [
        part
        for part in getattr(node, "parts", ())
        if getattr(part, "kind", None) == "word"
    ]
    words: list[str] = []
    for word_node in word_nodes:
        value = _literal_shell_word(source, word_node)
        if value is None:
            return None
        words.append(value)
    _executable, args = _command_words(words)
    for index, value in enumerate(args):
        if value == "-c" and index + 1 < len(args):
            return args[index + 1]
        if value.startswith("-") and not value.startswith("--") and "c" in value[1:]:
            return args[index + 1] if index + 1 < len(args) else None
        if not value.startswith("-"):
            break
    return None


def _path_arguments(executable: str, args: Sequence[str]) -> tuple[str, ...]:
    """Best-effort mutation operands for commands with known write effects."""

    if executable in {
        ":",
        "basename",
        "cd",
        "dirname",
        "echo",
        "false",
        "printf",
        "pwd",
        "true",
        "type",
        "which",
    }:
        return ()
    if executable in {"du", "ls"}:
        positional = [value for value in args if not value.startswith("-")]
        return tuple(positional or (".",))
    if executable == "test":
        return tuple(
            value
            for value in args
            if not value.startswith("-") and value not in {"!", "(", ")"}
        )
    values: list[str] = []
    skip_next = False
    for value in args:
        if skip_next:
            skip_next = False
            continue
        if value in {"-A", "-B", "-C", "--max-count"}:
            skip_next = True
            continue
        if value.startswith("-") or value.isdigit() or value in {".", ".."}:
            continue
        if any(marker in value for marker in ("$", "`", "*", "?", "[", "]")):
            continue
        if executable == "sed" and re.fullmatch(r"\d+(?:,\d+)?p", value):
            continue
        values.append(value)
    return tuple(values)


@dataclass(frozen=True, slots=True)
class _MutationOperandAnalysis:
    read_paths: tuple[str, ...] = ()
    write_paths: tuple[str, ...] = ()
    read_set_complete: bool = True
    write_set_complete: bool = True


def _literal_mutation_paths(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        value
        for value in values
        if value and value != "-" and not _has_dynamic_path(value)
    )


def _mutation_paths_are_bounded_literals(values: Sequence[str]) -> bool:
    return all(
        value
        and value != "-"
        and not _has_dynamic_path(value)
        and len(value) <= _MAX_RECEIPT_PATH_CHARS
        for value in values
    )


def _mutation_positionals(
    args: Sequence[str],
    *,
    short_flags: frozenset[str] = frozenset(),
    short_value_options: frozenset[str] = frozenset(),
    long_flags: frozenset[str] = frozenset(),
    long_value_options: frozenset[str] = frozenset(),
    incomplete_short_flags: frozenset[str] = frozenset(),
    incomplete_short_value_options: frozenset[str] = frozenset(),
    incomplete_long_flags: frozenset[str] = frozenset(),
    incomplete_long_value_options: frozenset[str] = frozenset(),
) -> tuple[tuple[str, ...], bool]:
    """Parse only options whose operand arity is unambiguous.

    Unknown options fail closed immediately because the next token may be an
    option value rather than a filesystem operand. Recognized options that can
    create implicit destinations (backup/target-directory/parents modes) are
    consumed but deliberately mark the result incomplete.
    """

    positionals: list[str] = []
    complete = True
    options_enabled = True
    index = 0
    while index < len(args):
        value = args[index]
        if not options_enabled or value == "-" or not value.startswith("-"):
            positionals.append(value)
            index += 1
            continue
        if value == "--":
            options_enabled = False
            index += 1
            continue
        if value.startswith("--"):
            name, separator, _attached = value.partition("=")
            if name in long_flags or name in incomplete_long_flags:
                if separator:
                    return (), False
                if name in incomplete_long_flags:
                    complete = False
                index += 1
                continue
            if name in long_value_options or name in incomplete_long_value_options:
                if name in incomplete_long_value_options:
                    complete = False
                if not separator:
                    index += 1
                    if index >= len(args):
                        return (), False
                index += 1
                continue
            return (), False

        cluster = value[1:]
        if not cluster:
            positionals.append(value)
            index += 1
            continue
        cluster_index = 0
        while cluster_index < len(cluster):
            option = cluster[cluster_index]
            if option in short_flags or option in incomplete_short_flags:
                if option in incomplete_short_flags:
                    complete = False
                cluster_index += 1
                continue
            if (
                option in short_value_options
                or option in incomplete_short_value_options
            ):
                if option in incomplete_short_value_options:
                    complete = False
                if cluster_index + 1 == len(cluster):
                    index += 1
                    if index >= len(args):
                        return (), False
                cluster_index = len(cluster)
                continue
            return (), False
        index += 1
    return tuple(positionals), complete


def _mutation_has_exact_destination_option(args: Sequence[str]) -> bool:
    for value in args:
        if value == "--":
            return False
        if value in {"-T", "--no-target-directory"}:
            return True
    return False


def _mutation_has_recursive_option(
    executable: str, args: Sequence[str]
) -> bool:
    short_options = {"cp": frozenset("aRr"), "rm": frozenset("Rr")}.get(
        executable, frozenset()
    )
    long_options = (
        {"--archive", "--recursive"}
        if executable == "cp"
        else {"--recursive"}
    )
    for value in args:
        if value == "--":
            break
        if value in long_options:
            return True
        if value.startswith("-") and not value.startswith("--") and any(
            option in value[1:] for option in short_options
        ):
            return True
    return False


def _copy_like_mutation_operands(
    executable: str, args: Sequence[str]
) -> _MutationOperandAnalysis:
    if executable == "cp":
        operands, options_complete = _mutation_positionals(
            args,
            short_flags=frozenset("afHilLnPpRrsuvxT"),
            long_flags=frozenset(
                {
                    "--archive",
                    "--attributes-only",
                    "--dereference",
                    "--force",
                    "--interactive",
                    "--link",
                    "--no-clobber",
                    "--no-dereference",
                    "--no-target-directory",
                    "--recursive",
                    "--remove-destination",
                    "--strip-trailing-slashes",
                    "--symbolic-link",
                    "--update",
                    "--verbose",
                    "--one-file-system",
                }
            ),
            incomplete_short_flags=frozenset("b"),
            incomplete_short_value_options=frozenset("St"),
            incomplete_long_flags=frozenset({"--backup", "--parents"}),
            incomplete_long_value_options=frozenset(
                {"--suffix", "--target-directory"}
            ),
        )
    elif executable == "install":
        operands, options_complete = _mutation_positionals(
            args,
            short_flags=frozenset("cCpsTv"),
            short_value_options=frozenset("gmo"),
            long_flags=frozenset(
                {
                    "--compare",
                    "--no-target-directory",
                    "--preserve-timestamps",
                    "--strip",
                    "--verbose",
                }
            ),
            long_value_options=frozenset({"--group", "--mode", "--owner"}),
            incomplete_short_flags=frozenset("bDd"),
            incomplete_short_value_options=frozenset("St"),
            incomplete_long_flags=frozenset(
                {"--backup", "--directory"}
            ),
            incomplete_long_value_options=frozenset(
                {"--suffix", "--target-directory"}
            ),
        )
    else:
        operands, options_complete = _mutation_positionals(
            args,
            short_flags=frozenset("fHilLnPrsTv"),
            long_flags=frozenset(
                {
                    "--directory",
                    "--force",
                    "--interactive",
                    "--logical",
                    "--no-dereference",
                    "--no-target-directory",
                    "--physical",
                    "--relative",
                    "--symbolic",
                    "--verbose",
                }
            ),
            incomplete_short_flags=frozenset("b"),
            incomplete_short_value_options=frozenset("St"),
            incomplete_long_flags=frozenset({"--backup"}),
            incomplete_long_value_options=frozenset(
                {"--suffix", "--target-directory"}
            ),
        )

    sources = operands[:-1] if operands else ()
    destinations = operands[-1:] if len(operands) >= 2 else ()
    literal_sources = _literal_mutation_paths(sources)
    literal_destinations = _literal_mutation_paths(destinations)
    operands_literal = _mutation_paths_are_bounded_literals(operands)
    exact_destination = _mutation_has_exact_destination_option(args)
    recursive = executable == "cp" and _mutation_has_recursive_option(
        executable, args
    )
    return _MutationOperandAnalysis(
        read_paths=literal_sources,
        write_paths=literal_destinations,
        read_set_complete=(
            options_complete
            and operands_literal
            and len(operands) >= 2
            and not recursive
            and len(dict.fromkeys(literal_sources)) <= _MAX_RECEIPT_PATHS
        ),
        write_set_complete=(
            options_complete
            and operands_literal
            and len(operands) == 2
            and exact_destination
            and not recursive
        ),
    )


def _move_mutation_operands(args: Sequence[str]) -> _MutationOperandAnalysis:
    operands, options_complete = _mutation_positionals(
        args,
        short_flags=frozenset("finTuv"),
        long_flags=frozenset(
            {
                "--force",
                "--interactive",
                "--no-clobber",
                "--no-copy",
                "--no-target-directory",
                "--strip-trailing-slashes",
                "--update",
                "--verbose",
            }
        ),
        incomplete_short_flags=frozenset("b"),
        incomplete_short_value_options=frozenset("St"),
        incomplete_long_flags=frozenset({"--backup", "--exchange"}),
        incomplete_long_value_options=frozenset(
            {"--suffix", "--target-directory"}
        ),
    )
    literal_operands = _literal_mutation_paths(operands)
    operands_literal = _mutation_paths_are_bounded_literals(operands)
    return _MutationOperandAnalysis(
        write_paths=literal_operands,
        read_set_complete=options_complete and operands_literal,
        # A source directory moves a descendant tree, and a destination
        # directory derives a basename. Shell syntax cannot prove either away.
        write_set_complete=False,
    )


def _remove_mutation_operands(args: Sequence[str]) -> _MutationOperandAnalysis:
    operands, options_complete = _mutation_positionals(
        args,
        short_flags=frozenset("dfiIRrv"),
        long_flags=frozenset(
            {
                "--dir",
                "--force",
                "--interactive",
                "--no-preserve-root",
                "--one-file-system",
                "--preserve-root",
                "--recursive",
                "--verbose",
            }
        ),
    )
    literal_operands = _literal_mutation_paths(operands)
    operands_literal = _mutation_paths_are_bounded_literals(operands)
    recursive = _mutation_has_recursive_option("rm", args)
    return _MutationOperandAnalysis(
        write_paths=literal_operands,
        write_set_complete=(
            options_complete
            and bool(operands)
            and operands_literal
            and not recursive
        ),
    )


def _sed_has_in_place_option(args: Sequence[str]) -> bool:
    return any(
        value == "-i"
        or value.startswith("-i")
        or value == "--in-place"
        or value.startswith("--in-place=")
        for value in args
    )


def _sed_in_place_mutation_operands(
    args: Sequence[str],
) -> _MutationOperandAnalysis:
    option_complete = True
    in_place = False
    explicit_script = False
    script_files: list[str] = []
    positionals: list[str] = []
    options_enabled = True
    files_started = False
    index = 0
    while index < len(args):
        value = args[index]
        if not files_started and options_enabled and value == "--":
            options_enabled = False
            index += 1
            continue
        if (
            not files_started
            and options_enabled
            and value.startswith("-")
            and value != "-"
        ):
            if value in {"-i", "--in-place"}:
                in_place = True
            elif value.startswith("-i") or value.startswith("--in-place="):
                in_place = True
                # A backup suffix adds one derived write path per input file.
                option_complete = False
            elif value in {"-e", "--expression", "-f", "--file"}:
                index += 1
                if index >= len(args):
                    return _MutationOperandAnalysis(
                        read_set_complete=False, write_set_complete=False
                    )
                explicit_script = True
                if value in {"-f", "--file"}:
                    script_files.append(args[index])
            elif value.startswith("-e") and value != "-e":
                explicit_script = True
            elif value.startswith("-f") and value != "-f":
                explicit_script = True
                script_files.append(value[2:])
            elif value.startswith("--expression="):
                explicit_script = True
            elif value.startswith("--file="):
                explicit_script = True
                script_files.append(value.partition("=")[2])
            elif value in {
                "-E",
                "-n",
                "-r",
                "-s",
                "-u",
                "-z",
                "--null-data",
                "--quiet",
                "--regexp-extended",
                "--sandbox",
                "--separate",
                "--silent",
                "--unbuffered",
            }:
                pass
            else:
                return _MutationOperandAnalysis(
                    read_set_complete=False, write_set_complete=False
                )
            index += 1
            continue
        files_started = True
        positionals.append(value)
        index += 1

    files = tuple(positionals if explicit_script else positionals[1:])
    dependencies = (*script_files, *files)
    literal_dependencies = _literal_mutation_paths(dependencies)
    literal_files = _literal_mutation_paths(files)
    paths_literal = _mutation_paths_are_bounded_literals(dependencies)
    complete = option_complete and in_place and bool(files) and paths_literal
    return _MutationOperandAnalysis(
        read_paths=literal_dependencies,
        write_paths=literal_files,
        read_set_complete=complete,
        write_set_complete=complete,
    )


def _simple_mutation_operands(
    executable: str, args: Sequence[str]
) -> _MutationOperandAnalysis:
    if executable in {"chmod", "chown"}:
        operands, options_complete = _mutation_positionals(
            args,
            short_flags=frozenset("cfv"),
            long_flags=frozenset({"--changes", "--quiet", "--silent", "--verbose"}),
            incomplete_short_flags=frozenset("R"),
            incomplete_long_flags=frozenset({"--recursive"}),
        )
        paths = operands[1:]
    elif executable in {"mkdir", "rmdir"}:
        operands, options_complete = _mutation_positionals(
            args,
            short_flags=frozenset("v"),
            short_value_options=frozenset("m"),
            long_flags=frozenset({"--verbose"}),
            long_value_options=frozenset({"--mode"}),
            incomplete_short_flags=frozenset("p"),
            incomplete_long_flags=frozenset({"--parents"}),
        )
        paths = operands
    elif executable == "truncate":
        operands, options_complete = _mutation_positionals(
            args,
            short_flags=frozenset("co"),
            short_value_options=frozenset("s"),
            long_flags=frozenset({"--no-create"}),
            long_value_options=frozenset({"--size"}),
            incomplete_short_value_options=frozenset("r"),
            incomplete_long_value_options=frozenset({"--reference"}),
        )
        paths = operands
    elif executable == "touch":
        operands, options_complete = _mutation_positionals(
            args,
            short_flags=frozenset("achm"),
            long_flags=frozenset({"--no-create", "--no-dereference"}),
            incomplete_short_value_options=frozenset("drt"),
            incomplete_long_value_options=frozenset(
                {"--date", "--reference", "--time"}
            ),
        )
        paths = operands
    else:
        paths, options_complete = _mutation_positionals(args)

    literal_paths = _literal_mutation_paths(paths)
    paths_literal = _mutation_paths_are_bounded_literals(paths)
    return _MutationOperandAnalysis(
        write_paths=literal_paths,
        write_set_complete=(
            options_complete and bool(paths) and paths_literal
        ),
    )


def _mutation_operand_analysis(
    executable: str, args: Sequence[str]
) -> _MutationOperandAnalysis:
    if executable in {"cp", "install", "ln"}:
        return _copy_like_mutation_operands(executable, args)
    if executable == "mv":
        return _move_mutation_operands(args)
    if executable == "rm":
        return _remove_mutation_operands(args)
    return _simple_mutation_operands(executable, args)


_SEARCH_LITERAL_FLAGS = frozenset(
    {
        "-c",
        "-F",
        "-H",
        "-h",
        "-i",
        "-l",
        "-L",
        "-n",
        "-N",
        "-o",
        "-q",
        "-s",
        "-S",
        "-v",
        "-w",
        "-x",
        "--case-sensitive",
        "--column",
        "--count",
        "--count-matches",
        "--files-with-matches",
        "--files-without-match",
        "--fixed-strings",
        "--follow",
        "--heading",
        "--hidden",
        "--ignore-case",
        "--include-zero",
        "--invert-match",
        "--line-number",
        "--line-regexp",
        "--no-filename",
        "--no-heading",
        "--no-hidden",
        "--no-config",
        "--no-ignore",
        "--no-ignore-dot",
        "--no-ignore-exclude",
        "--no-ignore-files",
        "--no-ignore-global",
        "--no-ignore-messages",
        "--no-ignore-parent",
        "--no-line-number",
        "--no-messages",
        "--null",
        "--null-data",
        "--only-matching",
        "--quiet",
        "--smart-case",
        "--stats",
        "--trim",
        "--type-list",
        "--unrestricted",
        "--word-regexp",
    }
)
_RG_VALUE_OPTIONS = {
    "-A": "value",
    "-B": "value",
    "-C": "value",
    "-e": "pattern",
    "-f": "pattern_file",
    "-g": "glob",
    "-j": "value",
    "-m": "value",
    "-r": "value",
    "-t": "value",
    "-T": "value",
    "--after-context": "value",
    "--before-context": "value",
    "--color": "value",
    "--colors": "value",
    "--context": "value",
    "--context-separator": "value",
    "--encoding": "value",
    "--engine": "value",
    "--field-context-separator": "value",
    "--field-match-separator": "value",
    "--file": "pattern_file",
    "--glob": "glob",
    "--iglob": "glob",
    "--ignore-file": "dependency",
    "--max-columns": "value",
    "--max-count": "value",
    "--max-depth": "value",
    "--max-filesize": "value",
    "--path-separator": "value",
    "--regexp": "pattern",
    "--replace": "value",
    "--sort": "value",
    "--sortr": "value",
    "--threads": "value",
    "--type": "value",
    "--type-not": "value",
}
_RG_UNSAFE_OPTIONS = frozenset(
    {
        "--hostname-bin",
        "--pre",
        "--pre-glob",
        "--type-add",
        "--type-clear",
    }
)
_GREP_LITERAL_FLAGS = frozenset(
    {
        "-a",
        "-b",
        "-c",
        "-E",
        "-F",
        "-G",
        "-H",
        "-h",
        "-i",
        "-I",
        "-l",
        "-L",
        "-n",
        "-o",
        "-q",
        "-r",
        "-R",
        "-s",
        "-v",
        "-w",
        "-x",
        "-Z",
        "-z",
        "--binary-files=text",
        "--byte-offset",
        "--count",
        "--extended-regexp",
        "--files-with-matches",
        "--files-without-match",
        "--fixed-strings",
        "--ignore-case",
        "--initial-tab",
        "--invert-match",
        "--line-buffered",
        "--line-number",
        "--line-regexp",
        "--no-filename",
        "--null",
        "--null-data",
        "--only-matching",
        "--quiet",
        "--recursive",
        "--silent",
        "--text",
        "--with-filename",
        "--word-regexp",
    }
)
_GREP_VALUE_OPTIONS = {
    "-A": "value",
    "-B": "value",
    "-C": "value",
    "-e": "pattern",
    "-f": "pattern_file",
    "-m": "value",
    "--after-context": "value",
    "--before-context": "value",
    "--binary-files": "value",
    "--context": "value",
    "--exclude": "glob",
    "--exclude-dir": "glob",
    "--exclude-from": "dependency",
    "--include": "glob",
    "--label": "value",
    "--max-count": "value",
    "--regexp": "pattern",
    "--file": "pattern_file",
}


def _has_dynamic_path(value: str) -> bool:
    return any(
        marker in value
        for marker in (
            "$",
            "`",
            "*",
            "?",
            "[",
            "]",
            "{",
            "}",
            "~",
            "\n",
            "\r",
        )
    )


def _consume_search_options(
    args: Sequence[str],
    *,
    literal_flags: frozenset[str],
    value_options: Mapping[str, str],
    unsafe_options: frozenset[str] = frozenset(),
    allow_files_mode: bool = False,
    observed_literal_flags: set[str] | None = None,
) -> tuple[list[str], list[str], bool, bool, bool]:
    """Return positionals, extra dependencies, pattern flag, files mode, safe."""

    positionals: list[str] = []
    dependencies: list[str] = []
    pattern_supplied = False
    files_mode = False
    index = 0
    options = True
    while index < len(args):
        value = args[index]
        if options and value == "--":
            options = False
            index += 1
            continue
        if not options or value == "-" or not value.startswith("-"):
            positionals.append(value)
            index += 1
            continue
        name, separator, attached = value.partition("=")
        if name in unsafe_options:
            return [], [], False, False, False
        if allow_files_mode and name == "--files" and not separator:
            files_mode = True
            if observed_literal_flags is not None:
                observed_literal_flags.add(name)
            index += 1
            continue
        option_kind = value_options.get(name)
        short_attached = ""
        if option_kind is None and value.startswith("-") and not value.startswith("--"):
            # Accept clusters of boolean short options, or one value-taking
            # option with its value attached (for example ``-g*.py``/``-A3``).
            short_names = ["-" + char for char in value[1:]]
            value_index = next(
                (i for i, item in enumerate(short_names) if item in value_options),
                None,
            )
            if value_index is None:
                if all(item in literal_flags for item in short_names):
                    if observed_literal_flags is not None:
                        observed_literal_flags.update(short_names)
                    index += 1
                    continue
                return [], [], False, False, False
            if not all(item in literal_flags for item in short_names[:value_index]):
                return [], [], False, False, False
            if observed_literal_flags is not None:
                observed_literal_flags.update(short_names[:value_index])
            name = short_names[value_index]
            option_kind = value_options[name]
            short_attached = value[value_index + 2 :]
            # Anything after a value-taking option is its value, not flags.
        if option_kind is not None:
            option_value = attached if separator else short_attached
            if not option_value:
                index += 1
                if index >= len(args):
                    return [], [], False, False, False
                option_value = args[index]
            if option_kind == "pattern":
                pattern_supplied = True
            elif option_kind in {"pattern_file", "dependency"}:
                dependencies.append(option_value)
                if option_kind == "pattern_file":
                    pattern_supplied = True
            index += 1
            continue
        if value in literal_flags:
            if observed_literal_flags is not None:
                observed_literal_flags.add(value)
            index += 1
            continue
        return [], [], False, False, False
    return positionals, dependencies, pattern_supplied, files_mode, True


def _search_read_analysis(
    executable: str,
    args: Sequence[str],
) -> tuple[bool, tuple[tuple[str, TerminalReadRange], ...], bool]:
    if executable == "rg":
        positionals, dependencies, pattern_supplied, files_mode, safe = (
            _consume_search_options(
                args,
                literal_flags=_SEARCH_LITERAL_FLAGS,
                value_options=_RG_VALUE_OPTIONS,
                unsafe_options=_RG_UNSAFE_OPTIONS,
                allow_files_mode=True,
            )
        )
    else:
        positionals, dependencies, pattern_supplied, files_mode, safe = (
            _consume_search_options(
                args,
                literal_flags=_GREP_LITERAL_FLAGS,
                value_options=_GREP_VALUE_OPTIONS,
            )
        )
    if not safe:
        return False, (), False
    if files_mode:
        paths = positionals or ["."]
    else:
        if not pattern_supplied:
            if not positionals:
                return False, (), False
            positionals = positionals[1:]
        paths = positionals
    entries = [
        *((path, _FULL_READ) for path in dependencies),
        *((path, _QUERY_READ) for path in paths),
    ]
    complete = not any(path == "-" or _has_dynamic_path(path) for path, _ in entries)
    return True, tuple(entries), complete


def _head_tail_analysis(
    executable: str,
    args: Sequence[str],
) -> tuple[bool, tuple[tuple[str, TerminalReadRange], ...], bool]:
    count = 10
    unit = "lines"
    relative_count = False
    paths: list[str] = []
    index = 0
    options = True
    while index < len(args):
        value = args[index]
        if options and value == "--":
            options = False
        elif not options or value == "-" or not value.startswith("-"):
            paths.append(value)
        elif executable == "tail" and (
            value in {"-f", "-F", "--follow", "--retry", "--pid"}
            or value.startswith(("--follow=", "--pid="))
        ):
            return False, (), False
        elif value in {
            "-q",
            "-v",
            "-z",
            "--quiet",
            "--silent",
            "--verbose",
            "--zero-terminated",
        }:
            pass
        elif value in {"-n", "--lines", "-c", "--bytes"}:
            index += 1
            if index >= len(args) or not re.fullmatch(r"[+-]?\d+", args[index]):
                return False, (), False
            relative_count = args[index].startswith(("+", "-"))
            count = abs(int(args[index]))
            unit = "bytes" if value in {"-c", "--bytes"} else "lines"
        elif value.startswith(("--lines=", "--bytes=")):
            raw = value.split("=", 1)[1]
            if not re.fullmatch(r"[+-]?\d+", raw):
                return False, (), False
            relative_count = raw.startswith(("+", "-"))
            count = abs(int(raw))
            unit = "bytes" if value.startswith("--bytes=") else "lines"
        elif match := re.fullmatch(r"-(?P<unit>[nc])(?P<count>[+-]?\d+)", value):
            raw = match.group("count")
            relative_count = raw.startswith(("+", "-"))
            count = abs(int(raw))
            unit = "bytes" if match.group("unit") == "c" else "lines"
        elif re.fullmatch(r"-\d+", value):
            count = int(value[1:])
            unit = "lines"
        else:
            return False, (), False
        index += 1
    if not paths:
        # Terminal subprocess stdin is explicitly DEVNULL; pipeline stdin is
        # already represented by the upstream command's dependencies.
        return True, (), True
    kind = (
        "tail_bytes"
        if executable == "tail" and unit == "bytes"
        else "tail_lines"
        if executable == "tail"
        else "byte_range"
        if unit == "bytes"
        else "line_range"
    )
    coverage = (
        _QUERY_READ
        if relative_count or count == 0
        else TerminalReadRange(kind, count, None)
        if kind.startswith("tail_")
        else TerminalReadRange(kind, 0, count)
        if unit == "bytes"
        else TerminalReadRange(kind, 1, count)
    )
    return (
        True,
        tuple((path, coverage) for path in paths),
        not any(path == "-" or _has_dynamic_path(path) for path in paths),
    )


def _cat_analysis(
    args: Sequence[str],
) -> tuple[bool, tuple[tuple[str, TerminalReadRange], ...], bool]:
    safe = {
        "-A",
        "-b",
        "-e",
        "-E",
        "-n",
        "-s",
        "-t",
        "-T",
        "-u",
        "-v",
        "--number",
        "--number-nonblank",
        "--show-all",
        "--show-ends",
        "--show-nonprinting",
        "--show-tabs",
        "--squeeze-blank",
    }
    paths: list[str] = []
    options = True
    for value in args:
        if options and value == "--":
            options = False
        elif options and value.startswith("-") and value != "-":
            short = (
                ["-" + char for char in value[1:]] if not value.startswith("--") else []
            )
            if value not in safe and not (
                short and all(item in safe for item in short)
            ):
                return False, (), False
        else:
            paths.append(value)
    if not paths:
        return True, (), True
    return (
        True,
        tuple((path, _FULL_READ) for path in paths),
        not any(path == "-" or _has_dynamic_path(path) for path in paths),
    )


def _sed_analysis(
    args: Sequence[str],
) -> tuple[bool, tuple[tuple[str, TerminalReadRange], ...], bool]:
    # Deliberately support only the bounded, non-evaluating print form.
    if len(args) < 3 or args[0] != "-n":
        return False, (), False
    match = re.fullmatch(r"(?P<start>\d+)(?:,(?P<end>\d+))?p", args[1])
    if match is None or any(value.startswith("-") for value in args[2:]):
        return False, (), False
    start = int(match.group("start"))
    end = int(match.group("end") or start)
    if start < 1 or end < start:
        return False, (), False
    paths = list(args[2:])
    return (
        True,
        tuple((path, TerminalReadRange("line_range", start, end)) for path in paths),
        not any(_has_dynamic_path(path) for path in paths),
    )


_FIND_UNSAFE_ACTIONS = frozenset(
    {"-delete", "-exec", "-execdir", "-fls", "-fprint", "-fprintf", "-ok", "-okdir"}
)
_FIND_VALUE_PREDICATES = frozenset(
    {
        "-amin",
        "-anewer",
        "-atime",
        "-cmin",
        "-cnewer",
        "-ctime",
        "-fstype",
        "-gid",
        "-group",
        "-ilname",
        "-iname",
        "-inum",
        "-ipath",
        "-iregex",
        "-links",
        "-lname",
        "-maxdepth",
        "-mindepth",
        "-mmin",
        "-mnewer",
        "-mtime",
        "-name",
        "-newer",
        "-newerXY",
        "-path",
        "-perm",
        "-printf",
        "-regex",
        "-size",
        "-type",
        "-uid",
        "-used",
        "-user",
        "-wholename",
    }
)


def _find_analysis(
    args: Sequence[str],
) -> tuple[bool, tuple[tuple[str, TerminalReadRange], ...], bool]:
    index = 0
    while index < len(args) and args[index] in {"-H", "-L", "-P"}:
        index += 1
    paths: list[str] = []
    while index < len(args) and not args[index].startswith(("-", "!", "(")):
        paths.append(args[index])
        index += 1
    paths = paths or ["."]
    dependencies: list[str] = []
    while index < len(args):
        value = args[index]
        if value in _FIND_UNSAFE_ACTIONS or any(
            value.startswith(item + "=") for item in _FIND_UNSAFE_ACTIONS
        ):
            return False, (), False
        if value in {
            "(",
            ")",
            "!",
            "-a",
            "-and",
            "-o",
            "-or",
            "-not",
            "-empty",
            "-print",
            "-print0",
            "-ls",
            "-true",
            "-false",
            "-xdev",
            "-mount",
            "-depth",
            "-daystart",
            "-follow",
            "-ignore_readdir_race",
            "-noignore_readdir_race",
        }:
            index += 1
            continue
        if value in _FIND_VALUE_PREDICATES or re.fullmatch(r"-newer[A-Za-z]{2}", value):
            index += 1
            if index >= len(args):
                return False, (), False
            if value.startswith(("-newer", "-anewer", "-cnewer", "-mnewer")):
                dependencies.append(args[index])
            index += 1
            continue
        return False, (), False
    entries = [
        *((path, _QUERY_READ) for path in paths),
        *((path, _METADATA_READ) for path in dependencies),
    ]
    # A directory traversal's complete file dependency set is discovered only
    # at runtime, so it is read-only but never exact-replay cacheable.
    return True, tuple(entries), False


_GIT_READ_SUBCOMMANDS = frozenset(
    {
        "cat-file",
        "describe",
        "diff",
        "diff-files",
        "diff-index",
        "diff-tree",
        "grep",
        "log",
        "ls-files",
        "ls-tree",
        "name-rev",
        "rev-list",
        "rev-parse",
        "show",
        "show-ref",
        "status",
    }
)
_GIT_EXECUTING_OPTIONS = frozenset(
    {
        "--ext-diff",
        "--textconv",
        "--exec-path",
        "--config-env",
        "--paginate",
        "-p",
        "-c",
        "--filters",
        "--output",
        "--open-files-in-pager",
        "-O",
        "--help",
        "-h",
    }
)


def _git_analysis(
    args: Sequence[str],
) -> tuple[bool, tuple[tuple[str, TerminalReadRange], ...], bool]:
    if not args:
        return False, (), False
    index = 0
    while index < len(args) and args[index].startswith("-"):
        value = args[index]
        if value in _GIT_EXECUTING_OPTIONS or any(
            value.startswith(item + "=") for item in _GIT_EXECUTING_OPTIONS
        ):
            return False, (), False
        if value not in {
            "--no-pager",
            "--no-replace-objects",
            "--no-optional-locks",
            "--literal-pathspecs",
            "--glob-pathspecs",
            "--noglob-pathspecs",
            "--icase-pathspecs",
            "--version",
        }:
            return False, (), False
        index += 1
    if index >= len(args):
        # ``git --version``/``git --help`` have no workspace dependency.
        return True, (), True
    subcommand = args[index]
    if subcommand not in _GIT_READ_SUBCOMMANDS:
        return False, (), False
    remaining = args[index + 1 :]
    if any(
        value in _GIT_EXECUTING_OPTIONS
        or value.startswith(
            (
                "--ext-diff=",
                "--textconv=",
                "--filters=",
                "--output=",
                "--open-files-in-pager=",
                "-O",
            )
        )
        for value in remaining
    ):
        return False, (), False
    # Git resolves refs, index, config, attributes and worktree files.  Keep a
    # compact repository-root dependency but fail replay completeness closed.
    return True, ((".", _QUERY_READ),), False


def _generic_read_analysis(
    executable: str,
    args: Sequence[str],
) -> tuple[bool, tuple[tuple[str, TerminalReadRange], ...], bool]:
    if executable not in _SHELL_READ_COMMANDS:
        return False, (), False
    if executable in {"rg", "grep"}:
        return _search_read_analysis(executable, args)
    if executable in {"head", "tail"}:
        return _head_tail_analysis(executable, args)
    if executable == "cat":
        return _cat_analysis(args)
    if executable == "sed":
        return _sed_analysis(args)
    if executable == "find":
        return _find_analysis(args)
    if executable == "git":
        return _git_analysis(args)
    paths = _path_arguments(executable, args)
    entries = tuple(
        (
            path,
            _METADATA_READ
            if executable in {"ls", "stat", "test", "readlink", "realpath", "du"}
            else _FULL_READ,
        )
        for path in paths
    )
    return (
        True,
        entries,
        not any(_has_dynamic_path(path) for path, _ in entries),
    )


def _shell_projection_is_reusable(
    commands: Sequence[Any],
    *,
    redirects: Sequence[Any],
    read_paths: Sequence[str],
    read_ranges: Sequence[TerminalReadRange],
) -> bool:
    """Admit overlap reuse only when stdout is the selected file bytes."""

    if len(commands) != 1 or len(read_paths) != 1 or len(read_ranges) != 1:
        return False
    if read_ranges[0].kind not in {
        "full",
        "line_range",
        "byte_range",
        "tail_lines",
        "tail_bytes",
    }:
        return False
    for redirect in redirects:
        redirect_type = str(getattr(redirect, "type", "") or "")
        input_fd = getattr(redirect, "input", None)
        if redirect_type in {"&>", ">&"} or (
            redirect_type in {">", ">>", ">|", "<>"} and input_fd in (None, 1)
        ):
            return False
    node = commands[0]
    words = [
        part.word
        for part in getattr(node, "parts", ())
        if getattr(part, "kind", None) == "word"
    ]
    executable, args = _command_words(words)
    if executable == "cat":
        operands = [
            value for value in args if value != "--" and not value.startswith("-")
        ]
        input_redirects = [
            redirect
            for redirect in redirects
            if str(getattr(redirect, "type", "") or "") == "<"
        ]
        return (
            all(value == "--" or not value.startswith("-") for value in args)
            and len(operands) + len(input_redirects) == 1
        )
    if executable in {"head", "tail"}:
        safe, entries, _complete = _head_tail_analysis(executable, args)
        return (
            safe
            and len(entries) == 1
            and not any(
                value
                in {
                    "-q",
                    "-v",
                    "--quiet",
                    "--silent",
                    "--verbose",
                    "-z",
                    "--zero-terminated",
                }
                for value in args
            )
        )
    if executable == "sed":
        safe, entries, _complete = _sed_analysis(args)
        return safe and len(entries) == 1
    return False


def _rg_actual_literal_flags(args: Sequence[str]) -> frozenset[str]:
    observed: set[str] = set()
    *_ignored, safe = _consume_search_options(
        args,
        literal_flags=_SEARCH_LITERAL_FLAGS,
        value_options=_RG_VALUE_OPTIONS,
        unsafe_options=_RG_UNSAFE_OPTIONS,
        allow_files_mode=True,
        observed_literal_flags=observed,
    )
    return frozenset(observed) if safe else frozenset()


def _git_version_only(args: Sequence[str]) -> bool:
    allowed = {
        "--no-pager",
        "--no-replace-objects",
        "--no-optional-locks",
        "--literal-pathspecs",
        "--glob-pathspecs",
        "--noglob-pathspecs",
        "--icase-pathspecs",
        "--version",
    }
    return "--version" in args and all(value in allowed for value in args)


def _bounded_paths(values: Iterable[str]) -> tuple[str, ...]:
    bounded: list[str] = []
    for raw_value in values:
        value = str(raw_value)
        if not value or value in bounded:
            continue
        bounded.append(value[:_MAX_RECEIPT_PATH_CHARS])
        if len(bounded) >= _MAX_RECEIPT_PATHS:
            break
    return tuple(bounded)


def _merge_coverage(
    current: TerminalReadRange,
    incoming: TerminalReadRange,
) -> TerminalReadRange:
    if current == incoming or current.kind == "full":
        return current
    if incoming.kind == "full":
        return incoming
    if current.kind == incoming.kind == "line_range":
        if (
            current.start is not None
            and current.end is not None
            and incoming.start is not None
            and incoming.end is not None
            and incoming.start <= current.end + 1
            and current.start <= incoming.end + 1
        ):
            return TerminalReadRange(
                "line_range",
                min(current.start, incoming.start),
                max(current.end, incoming.end),
            )
    if current.kind == incoming.kind == "byte_range":
        if (
            current.start is not None
            and current.end is not None
            and incoming.start is not None
            and incoming.end is not None
            and incoming.start <= current.end
            and current.start <= incoming.end
        ):
            return TerminalReadRange(
                "byte_range",
                min(current.start, incoming.start),
                max(current.end, incoming.end),
            )
    if current.kind == incoming.kind and current.kind in {"tail_lines", "tail_bytes"}:
        return TerminalReadRange(
            current.kind,
            max(current.start or 0, incoming.start or 0),
            None,
        )
    # Disjoint/mixed projections cannot be represented as one contiguous
    # reusable window. Keep the dependency but restrict it to exact-query facts.
    return _QUERY_READ


def _bounded_read_entries(
    entries: Iterable[tuple[str, TerminalReadRange]],
) -> tuple[tuple[str, ...], tuple[TerminalReadRange, ...], bool]:
    merged: dict[str, TerminalReadRange] = {}
    complete = True
    for raw_path, coverage in entries:
        path = str(raw_path)
        if not path:
            continue
        if len(path) > _MAX_RECEIPT_PATH_CHARS:
            complete = False
            path = path[:_MAX_RECEIPT_PATH_CHARS]
        if path in merged:
            merged[path] = _merge_coverage(merged[path], coverage)
            continue
        if len(merged) >= _MAX_RECEIPT_PATHS:
            complete = False
            continue
        merged[path] = coverage
    return tuple(merged), tuple(merged.values()), complete


def _parse_shell_nodes(source: str) -> list[Any] | None:
    try:
        return list(bashlex.parse(source))
    except (
        bashlex.errors.ParsingError,
        NotImplementedError,
        RecursionError,
        AssertionError,
        TypeError,
    ):
        return None


_LEADING_STATIC_CD = re.compile(
    r"\A\s*cd\s+(?P<path>'[^']*'|\"[^\"]*\"|[^\s;&|]+)\s*(?:&&|;|\n|\Z)"
)


def _leading_shell_working_directory(
    source: str,
) -> tuple[str | None, bool, int]:
    """Model only a literal ``cd`` prefix and count consumed transitions."""

    if not isinstance(source, str):
        return None, False, 0
    current: str | None = None
    remainder = source
    consumed = 0
    while True:
        match = _LEADING_STATIC_CD.match(remainder)
        if match is None:
            break
        raw_path = match.group("path")
        if raw_path[:1] in {"'", '"'} and raw_path[-1:] == raw_path[:1]:
            raw_path = raw_path[1:-1]
        if not raw_path or any(marker in raw_path for marker in ("$", "`", "\\")):
            return current, False, consumed
        normalized = posixpath.normpath(raw_path)
        current = (
            normalized
            if posixpath.isabs(normalized) or current is None
            else posixpath.normpath(posixpath.join(current, normalized))
        )
        consumed += 1
        if match.end() == len(remainder):
            remainder = ""
            break
        remainder = remainder[match.end() :]
    return current, True, consumed


def _parsed_cd_count(roots: Sequence[Any]) -> int:
    count = 0
    seen: set[int] = set()
    pending = list(roots)
    while pending:
        node = pending.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if getattr(node, "kind", None) == "command":
            words = [
                part.word
                for part in getattr(node, "parts", ())
                if getattr(part, "kind", None) == "word"
            ]
            if _command_words(words)[0] == "cd":
                count += 1
        pending.extend(_shell_child_nodes(node))
    return count


def shell_command_working_directory(source: str) -> tuple[str | None, bool]:
    """Return cwd only when parsed ``cd`` transitions equal the modeled prefix."""

    command_cwd, prefix_safe, consumed = _leading_shell_working_directory(source)
    roots = _parse_shell_nodes(source)
    if roots is None:
        return command_cwd, False
    return command_cwd, prefix_safe and _parsed_cd_count(roots) == consumed


_PYTHON_HEREDOC = re.compile(
    r"\A[ \t]*(?P<executable>(?:/[^\s]+/)?(?:python(?:3(?:\.\d+)*)?|py))"
    r"(?:[ \t]+-)?[ \t]+<<(?P<strip>-?)[ \t]*"
    r"(?P<quote>['\"]?)(?P<delimiter>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P=quote)(?P<header_suffix>[^\r\n]*)\r?\n"
    r"(?P<body>.*?)"
    r"(?:\r?\n)(?P<closing_tabs>\t*)(?P=delimiter)[ \t]*"
    r"(?P<trailer>(?:\r?\n.*)?)\Z",
    re.DOTALL,
)
_PYTHON_HEREDOC_STDOUT_REDIRECT = re.compile(
    r"\A[ \t]*(?:1)?(?P<operator>>{1,2}|>\|)[ \t]+(?P<target>.+?)[ \t]*\Z"
)


def _literal_heredoc_path(raw: str) -> str | None:
    try:
        values = shlex.split(raw, comments=False, posix=True)
    except ValueError:
        return None
    if len(values) != 1:
        return None
    value = values[0]
    if (
        not value
        or value in _NON_FILE_REDIRECT_TARGETS
        or value.startswith("/dev/fd/")
        or _has_dynamic_path(value)
        or len(value) > _MAX_RECEIPT_PATH_CHARS
    ):
        return None
    return value


def _python_heredoc_source(
    source: str,
) -> tuple[str, bool, str, str | None, str | None] | None:
    """Return one Python heredoc body and whether its bytes are static.

    Quoted delimiters are byte-stable.  An unquoted delimiter is accepted only
    when the body contains no Shell interpolation markers; otherwise the
    executed Python source is not statically knowable and must stay unknown.
    The intentionally narrow whole-command match prevents adjacent Shell
    commands, substitutions, or pipelines from inheriting the nested result.
    """

    match = _PYTHON_HEREDOC.fullmatch(source)
    if match is None:
        return None
    header_suffix = match.group("header_suffix").strip()
    stdout_path = None
    if header_suffix:
        redirect = _PYTHON_HEREDOC_STDOUT_REDIRECT.fullmatch(header_suffix)
        if redirect is None:
            return None
        stdout_path = _literal_heredoc_path(redirect.group("target"))
        if stdout_path is None:
            return None
    trailing_read_path = None
    trailer = match.group("trailer").strip()
    if trailer:
        if stdout_path is None or "\n" in trailer or "\r" in trailer:
            return None
        try:
            trailer_words = shlex.split(trailer, comments=False, posix=True)
        except ValueError:
            return None
        if trailer_words[:1] != ["cat"]:
            return None
        trailer_paths = (
            trailer_words[2:]
            if trailer_words[1:2] == ["--"]
            else trailer_words[1:]
        )
        if len(trailer_paths) != 1 or trailer_paths[0] != stdout_path:
            return None
        trailing_read_path = stdout_path
    body = match.group("body")
    literal = match.group("strip") != "-" and not (
        match.group("quote") == ""
        and any(marker in body for marker in ("$", "`", "\\"))
    )
    return (
        body,
        literal,
        match.group("executable"),
        stdout_path,
        trailing_read_path,
    )


def plan_terminal_execution(
    code: str,
    *,
    language: str = "shell",
    shell_nodes: Sequence[Any] | None = None,
    trusted_executable_paths: Sequence[str] = (),
) -> TerminalExecutionPlan:
    """Select an execution language and classify its mechanical file effect."""

    if not isinstance(code, str) or not code.strip():
        return TerminalExecutionPlan("unknown", "unknown", False, False)
    if language not in TERMINAL_LANGUAGES:
        return TerminalExecutionPlan("unknown", "unknown", False, False)
    if language == "python":
        (
            effect,
            reads,
            writes,
            read_set_complete,
            write_set_complete,
        ) = _python_effect_and_paths(code)
        try:
            ast.parse(code, mode="exec")
        except (SyntaxError, ValueError, TypeError):
            parsed = False
        else:
            parsed = True
        read_paths, read_ranges, entries_complete = _bounded_read_entries(
            (path, _FULL_READ) for path in reads
        )
        return TerminalExecutionPlan(
            "python",
            effect,
            effect == "read_only",
            parsed,
            read_paths,
            writes,
            False,
            read_set_complete and entries_complete,
            read_ranges=read_ranges,
            write_set_complete=write_set_complete,
        )
    nested_heredoc = _python_heredoc_source(code)
    if nested_heredoc is not None:
        (
            nested_python,
            literal,
            nested_executable,
            stdout_path,
            trailing_read_path,
        ) = nested_heredoc
        if literal:
            (
                effect,
                reads,
                writes,
                read_set_complete,
                write_set_complete,
            ) = _python_effect_and_paths(nested_python)
        else:
            effect, reads, writes, read_set_complete, write_set_complete = (
                "unknown",
                (),
                (),
                False,
                False,
            )
        try:
            ast.parse(nested_python, mode="exec")
        except (SyntaxError, ValueError, TypeError):
            parsed = False
            effect = "unknown"
        else:
            parsed = True
        if stdout_path is not None:
            writes = (*writes, stdout_path)
            if effect != "unknown":
                effect = "mutating"
            else:
                write_set_complete = False
        if trailing_read_path is not None:
            reads = (*reads, trailing_read_path)
        read_paths, read_ranges, entries_complete = _bounded_read_entries(
            (path, _FULL_READ) for path in reads
        )
        return TerminalExecutionPlan(
            "shell",
            effect,
            effect == "read_only",
            parsed,
            read_paths,
            writes,
            False,
            read_set_complete and entries_complete,
            ("python",),
            (terminal_command_sha256(nested_python),),
            read_ranges=read_ranges,
            executable_tokens=(
                (nested_executable, "cat")
                if trailing_read_path is not None
                else (nested_executable,)
            ),
            write_set_complete=write_set_complete,
        )
    if _looks_like_bare_python(code):
        # run_code is a shell contract.  Recognizing Python-looking input here
        # prevents comparison operators such as ``>`` from being promoted to
        # a *known* shell redirection/mutation, but deliberately does not
        # change the language that will actually execute it.
        return TerminalExecutionPlan("shell", "unknown", False, False)

    roots = list(shell_nodes) if shell_nodes is not None else _parse_shell_nodes(code)
    if not roots:
        return TerminalExecutionPlan("shell", "unknown", False, False)
    commands: list[Any] = []
    redirects: list[Any] = []
    background_operator = False
    seen: set[int] = set()
    pending = list(roots)
    while pending:
        node = pending.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        kind = getattr(node, "kind", None)
        if kind == "command":
            commands.append(node)
        elif kind == "redirect":
            redirects.append(node)
        elif kind == "operator" and getattr(node, "op", None) == "&":
            background_operator = True
        pending.extend(_shell_child_nodes(node))

    read_entries: list[tuple[str, TerminalReadRange]] = []
    writes: list[str] = []
    known_mutation = False
    unknown = background_operator
    unknown_component = False
    read_set_complete = True
    write_set_complete = True
    callback_kinds: set[str] = set()
    executable_tokens: list[str] = []
    command_cwd, command_cwd_safe = shell_command_working_directory(code)
    if not command_cwd_safe:
        unknown = True
        read_set_complete = False
    for redirect in redirects:
        redirect_type = str(getattr(redirect, "type", "") or "")
        output = getattr(redirect, "output", None)
        path = _literal_redirect_path(output)
        if redirect_type in {">", ">>", ">|", "&>", "<>", ">&"}:
            raw_target = getattr(output, "word", None)
            non_file_target = (
                isinstance(output, int)
                or raw_target in _NON_FILE_REDIRECT_TARGETS
                or (isinstance(raw_target, str) and raw_target.startswith("/dev/fd/"))
            )
            if not non_file_target:
                known_mutation = True
            if path is not None:
                writes.append(path)
            elif not non_file_target:
                write_set_complete = False
        elif redirect_type == "<" and path is not None:
            read_entries.append((path, _FULL_READ))
        elif redirect_type == "<":
            read_set_complete = False

    for node in commands:
        if any(
            getattr(part, "kind", None) == "assignment"
            for part in getattr(node, "parts", ())
        ):
            unknown = True
        word_nodes = [
            part
            for part in getattr(node, "parts", ())
            if getattr(part, "kind", None) == "word"
        ]
        words = [part.word for part in word_nodes]
        if words:
            leading_assignments = bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]))
            wrapper = Path(words[0]).name in {
                "builtin",
                "command",
                "env",
                "sudo",
                "time",
            }
            raw_executable = words[0]
            explicitly_trusted = False
            if "/" in raw_executable:
                try:
                    resolved_executable = str(
                        Path(raw_executable).expanduser().resolve()
                    )
                except OSError:
                    resolved_executable = raw_executable
                explicitly_trusted = any(
                    resolved_executable == str(Path(value).expanduser().resolve())
                    for value in trusted_executable_paths
                )
            untrusted_explicit_path = (
                "/" in raw_executable
                and not raw_executable.startswith(("/bin/", "/usr/bin/"))
                and not explicitly_trusted
            )
            if leading_assignments or wrapper or untrusted_explicit_path:
                unknown = True
            if untrusted_explicit_path:
                unknown_component = True
        executable, args = _command_words(words)
        if not executable:
            continue
        # Wrapper-bearing commands are already classified as unknown above.
        # Keeping only the accepted command token means an execution provider
        # can bind its trust proof to the exact interpreter/binary selected by
        # this parser rather than reparsing raw source independently.
        for raw_token in words:
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", raw_token):
                continue
            executable_tokens.append(raw_token)
            break
        if executable == "rg" and "--no-config" not in _rg_actual_literal_flags(args):
            callback_kinds.add("ripgrep_config")
        elif executable == "git" and not _git_version_only(args):
            callback_kinds.add("git_config")
        if executable == "cd" and command_cwd is None:
            unknown = True
            read_set_complete = False
        if any(getattr(part, "parts", ()) for part in word_nodes[1:]):
            unknown = True
        if executable in _SHELL_MUTATION_COMMANDS:
            known_mutation = True
            mutation = _mutation_operand_analysis(executable, args)
            read_entries.extend(
                (path, _FULL_READ) for path in mutation.read_paths
            )
            writes.extend(mutation.write_paths)
            read_set_complete = (
                read_set_complete and mutation.read_set_complete
            )
            write_set_complete = (
                write_set_complete and mutation.write_set_complete
            )
            continue
        if (
            executable in _PYTHON_EXECUTABLES
            or _VERSIONED_PYTHON_EXECUTABLE.fullmatch(executable) is not None
        ):
            python_source = _python_source_from_shell_command(node, code)
            if python_source is None:
                unknown = True
                unknown_component = True
                continue
            (
                python_effect,
                python_reads,
                python_writes,
                python_reads_complete,
                python_writes_complete,
            ) = _python_effect_and_paths(python_source)
            read_entries.extend((path, _FULL_READ) for path in python_reads)
            writes.extend(python_writes)
            read_set_complete = read_set_complete and python_reads_complete
            write_set_complete = write_set_complete and python_writes_complete
            if python_effect == "mutating":
                known_mutation = True
            elif python_effect != "read_only":
                unknown = True
                unknown_component = True
            continue
        if executable == "sed" and _sed_has_in_place_option(args):
            known_mutation = True
            mutation = _sed_in_place_mutation_operands(args)
            read_entries.extend(
                (path, _FULL_READ) for path in mutation.read_paths
            )
            writes.extend(mutation.write_paths)
            read_set_complete = (
                read_set_complete and mutation.read_set_complete
            )
            write_set_complete = (
                write_set_complete and mutation.write_set_complete
            )
            continue
        command_read_only, command_entries, command_complete = _generic_read_analysis(
            executable, args
        )
        if not command_read_only:
            unknown = True
            unknown_component = True
            continue
        read_entries.extend(command_entries)
        read_set_complete = read_set_complete and command_complete

    effect = (
        "unknown"
        if unknown_component
        else "mutating"
        if known_mutation
        else "unknown"
        if unknown
        else "read_only"
    )
    read_paths, read_ranges, entries_complete = _bounded_read_entries(read_entries)
    read_set_complete = read_set_complete and entries_complete
    if (
        len(dict.fromkeys(writes)) > _MAX_RECEIPT_PATHS
        or any(len(path) > _MAX_RECEIPT_PATH_CHARS for path in writes)
        or unknown_component
        or (known_mutation and unknown)
    ):
        write_set_complete = False
    return TerminalExecutionPlan(
        "shell",
        effect,
        effect == "read_only",
        True,
        read_paths,
        _bounded_paths(writes),
        background_operator,
        read_set_complete,
        command_cwd=command_cwd,
        command_cwd_safe=command_cwd_safe,
        read_ranges=read_ranges,
        read_projection_reusable=_shell_projection_is_reusable(
            commands,
            redirects=redirects,
            read_paths=read_paths,
            read_ranges=read_ranges,
        ),
        callback_kinds=tuple(sorted(callback_kinds)),
        executable_tokens=tuple(executable_tokens[:16]),
        executable_set_complete=len(executable_tokens) <= 16,
        write_set_complete=write_set_complete,
    )


def build_terminal_execution_receipt(
    *,
    code: str,
    plan: TerminalExecutionPlan,
    executed: bool,
    exit_code: int | None,
    timed_out: bool,
    capture_complete: bool = True,
    mutation_observed: bool | None = None,
    potential_effect: str | None = None,
    effect_source: str = "parser_contract",
    read_path_epochs: Sequence[dict[str, Any]] = (),
    requested_language: str | None = None,
    cache_hit: bool = False,
    observation_id: str | None = None,
    content_sha256: str | None = None,
    representation: str | None = None,
    source_checkpoint_revision: int | None = None,
    execution_context_sha256: str | None = None,
) -> dict[str, Any]:
    """Project one terminal decision/result into bounded transport metadata."""

    if cache_hit and executed:
        raise ValueError("a terminal cache hit cannot claim fresh execution")
    if (
        observation_id is not None
        and re.fullmatch(r"sha256:[0-9a-f]{64}", observation_id) is None
    ):
        raise ValueError("observation_id must be a canonical sha256 identity")
    if (
        content_sha256 is not None
        and re.fullmatch(r"sha256:[0-9a-f]{64}", content_sha256) is None
    ):
        raise ValueError("content_sha256 must be a canonical sha256 identity")
    if representation is not None and (
        not representation
        or len(representation) > 128
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", representation) is None
    ):
        raise ValueError("representation must be a bounded stable identity")
    if source_checkpoint_revision is not None and (
        isinstance(source_checkpoint_revision, bool)
        or not isinstance(source_checkpoint_revision, int)
        or source_checkpoint_revision < 0
    ):
        raise ValueError("source_checkpoint_revision must be non-negative")
    if (
        execution_context_sha256 is not None
        and re.fullmatch(r"sha256:[0-9a-f]{64}", execution_context_sha256) is None
    ):
        raise ValueError("execution_context_sha256 must be a canonical sha256 identity")
    generation_delta = 0 if not executed or plan.effect == "read_only" else 1
    read_epochs = [dict(epoch) for epoch in read_path_epochs][:_MAX_RECEIPT_PATHS]
    read_epochs_complete = not plan.read_paths or len(read_epochs) == len(
        plan.read_paths
    )
    read_ranges = (
        plan.read_ranges
        if len(plan.read_ranges) == len(plan.read_paths)
        else tuple(_FULL_READ for _ in plan.read_paths)
    )
    nested_language_evidence = (
        [
            {"language": language, "source_sha256": source_sha256}
            for language, source_sha256 in zip(
                plan.nested_languages,
                plan.nested_source_sha256,
            )
        ][:4]
        if len(plan.nested_languages) == len(plan.nested_source_sha256)
        else []
    )
    receipt = {
        "schema_version": TERMINAL_EXECUTION_RECEIPT_SCHEMA,
        "parser_version": TERMINAL_EXECUTION_ANALYZER_VERSION,
        "language_contract_version": TERMINAL_LANGUAGE_CONTRACT_VERSION,
        "command_sha256": terminal_command_sha256(code),
        "requested_language": requested_language or plan.language,
        "effective_language": plan.language,
        # Retained as a compact compatibility alias for receipt consumers.
        "language": plan.language,
        "parsed": plan.parsed,
        "potential_effect": potential_effect or plan.effect,
        "effect": plan.effect,
        "effect_source": effect_source,
        "cacheable": bool(
            (executed or cache_hit)
            and exit_code == 0
            and plan.cacheable
            and plan.read_set_complete
            and effect_source in TERMINAL_CACHEABLE_EFFECT_SOURCES
            and read_epochs_complete
            and not plan.background
            and not timed_out
            and capture_complete
        ),
        "read_paths": list(plan.read_paths),
        "read_ranges": [coverage.to_dict() for coverage in read_ranges],
        "read_projection_reusable": bool(plan.read_projection_reusable),
        "read_representation": representation,
        "write_paths": list(plan.write_paths),
        "write_set_complete": plan.write_set_complete,
        "read_set_complete": plan.read_set_complete,
        "read_path_epochs": read_epochs,
        "workspace_generation_delta": generation_delta,
        "mutation_observed": mutation_observed,
        "scope_volatile": bool(executed and (plan.background or not capture_complete)),
        "executed": executed,
        "cache_hit": bool(cache_hit),
        "observation_id": observation_id,
        "observation_content_sha256": content_sha256,
        "source_checkpoint_revision": source_checkpoint_revision,
        "timed_out": bool(timed_out),
        "exit_code": exit_code,
    }
    if execution_context_sha256 is not None:
        receipt["execution_context_sha256"] = execution_context_sha256
    if nested_language_evidence:
        receipt["nested_language_evidence"] = nested_language_evidence
    return receipt


__all__ = [
    "TERMINAL_EFFECTS",
    "TERMINAL_EXECUTION_AUTHORITY_ENV",
    "TERMINAL_CACHEABLE_EFFECT_SOURCES",
    "TERMINAL_EXECUTION_ANALYZER_VERSION",
    "TERMINAL_LANGUAGE_CONTRACT_VERSION",
    "TERMINAL_LANGUAGES",
    "TERMINAL_EXECUTION_RECEIPT_KEY",
    "TERMINAL_EXECUTION_RECEIPT_SCHEMA",
    "TerminalExecutionPlan",
    "TerminalReadRange",
    "build_terminal_execution_receipt",
    "plan_terminal_execution",
    "python_is_provably_read_only",
    "shell_command_working_directory",
    "terminal_command_sha256",
    "terminal_execution_context_sha256",
]
