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
import posixpath
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import bashlex


TERMINAL_EXECUTION_RECEIPT_SCHEMA = "aworld.terminal-execution-receipt/v2"
TERMINAL_EXECUTION_RECEIPT_KEY = "terminal_execution_receipt"
# v2 coverage fields are additive: ``read_ranges`` aligns one-for-one with
# ``read_paths`` and ``read_projection_reusable`` says whether stdout preserves
# that single-file window. Legacy v2 receipts are re-derived by the Sandbox
# with this same analyzer; they are never assumed reusable by default.
TERMINAL_EXECUTION_ANALYZER_VERSION = 6
TERMINAL_LANGUAGE_CONTRACT_VERSION = 1
TERMINAL_LANGUAGES = frozenset({"shell", "python"})
TERMINAL_EFFECTS = frozenset({"read_only", "mutating", "unknown"})
TERMINAL_CACHEABLE_EFFECT_SOURCES = frozenset(
    {"execution_trace", "trusted_command_contract", "trusted_docker_command_contract"}
)
_MAX_RECEIPT_PATHS = 16
_MAX_RECEIPT_PATH_CHARS = 512

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
_PYTHON_PATH_MUTATION_METHODS = frozenset(
    {
        "chmod",
        "mkdir",
        "rename",
        "replace",
        "rmdir",
        "symlink_to",
        "touch",
        "unlink",
        "write_bytes",
        "write_text",
    }
)
_PYTHON_OS_MUTATION_METHODS = frozenset(
    {"chmod", "makedirs", "mkdir", "remove", "rename", "replace", "rmdir", "unlink"}
)
_PYTHON_SHUTIL_MUTATION_METHODS = frozenset(
    {"copy", "copy2", "copyfile", "copytree", "move", "rmtree"}
)


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


def _constant_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _path_from_python_receiver(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call) or not node.args:
        return None
    function = node.func
    if isinstance(function, ast.Name) and function.id in {
        "Path",
        "PurePath",
        "PurePosixPath",
    }:
        return _constant_string(node.args[0])
    return None


def _python_effect_and_paths(
    source: str,
) -> tuple[str, tuple[str, ...], tuple[str, ...], bool]:
    try:
        tree = ast.parse(source, mode="exec")
    except (SyntaxError, ValueError, TypeError):
        return "unknown", (), (), False
    reads: list[str] = []
    writes: list[str] = []
    known_mutation = False
    read_set_complete = True
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
        elif isinstance(function, ast.Attribute):
            path = _path_from_python_receiver(function.value)
            receiver_name = (
                function.value.id if isinstance(function.value, ast.Name) else None
            )
            if path is not None and function.attr in _PYTHON_PATH_MUTATION_METHODS:
                known_mutation = True
                writes.append(path)
            elif (
                receiver_name == "os" and function.attr in _PYTHON_OS_MUTATION_METHODS
            ) or (
                receiver_name == "shutil"
                and function.attr in _PYTHON_SHUTIL_MUTATION_METHODS
            ):
                known_mutation = True
                argument_path = _constant_string(node.args[-1]) if node.args else None
                if argument_path:
                    writes.append(argument_path)
            elif function.attr == "save":
                # numpy/PIL-style save calls have filesystem semantics even
                # though their receiver may be an imported alias or object.
                known_mutation = True
                argument_path = _constant_string(node.args[0]) if node.args else None
                if argument_path:
                    writes.append(argument_path)
            elif function.attr in {"VideoCapture", "load"}:
                argument_path = _constant_string(node.args[0]) if node.args else None
                if argument_path:
                    reads.append(argument_path)
                else:
                    read_set_complete = False
            elif function.attr in {"open", "read", "read_bytes", "read_text"}:
                if path is None and function.attr == "open" and node.args:
                    path = _constant_string(node.args[0])
                if function.attr == "open" and not _python_open_is_read_only(node):
                    known_mutation = True
                    if path:
                        writes.append(path)
                elif path:
                    reads.append(path)
                else:
                    read_set_complete = False
    if len(dict.fromkeys(reads)) > _MAX_RECEIPT_PATHS:
        read_set_complete = False
    if known_mutation:
        return (
            "mutating",
            _bounded_paths(reads),
            _bounded_paths(writes),
            read_set_complete,
        )
    if python_is_provably_read_only(source):
        return "read_only", _bounded_paths(reads), (), read_set_complete
    return (
        "unknown",
        _bounded_paths(reads),
        _bounded_paths(writes),
        read_set_complete,
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
        executable = Path(remaining[0]).name.lower()
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
    if value.startswith("/dev/fd/") or any(marker in value for marker in ("$", "`")):
        return None
    return value


def _python_source_from_shell_command(node: Any, args: Sequence[str]) -> str | None:
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
    return any(marker in value for marker in ("$", "`", "*", "?", "[", "]", "\n", "\r"))


def _consume_search_options(
    args: Sequence[str],
    *,
    literal_flags: frozenset[str],
    value_options: Mapping[str, str],
    unsafe_options: frozenset[str] = frozenset(),
    allow_files_mode: bool = False,
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
                    index += 1
                    continue
                return [], [], False, False, False
            if not all(item in literal_flags for item in short_names[:value_index]):
                return [], [], False, False, False
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
            "--help",
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
    node = commands[0]
    words = [
        part.word
        for part in getattr(node, "parts", ())
        if getattr(part, "kind", None) == "word"
    ]
    executable, args = _command_words(words)
    if executable == "cat":
        return all(value == "--" or not value.startswith("-") for value in args)
    if executable in {"head", "tail"}:
        return not any(
            value
            in {
                "-q",
                "-v",
                "--quiet",
                "--silent",
                "--verbose",
            }
            for value in args
        )
    return executable == "sed"


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
    r"(?P=quote)[ \t]*\r?\n"
    r"(?P<body>.*?)"
    r"(?:\r?\n)(?P<closing_tabs>\t*)(?P=delimiter)[ \t]*(?:\r?\n)?\Z",
    re.DOTALL | re.IGNORECASE,
)


def _python_heredoc_source(source: str) -> tuple[str, bool] | None:
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
    body = match.group("body")
    literal = match.group("strip") != "-" and not (
        match.group("quote") == ""
        and any(marker in body for marker in ("$", "`", "\\"))
    )
    return body, literal


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
        effect, reads, writes, read_set_complete = _python_effect_and_paths(code)
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
        )
    nested_heredoc = _python_heredoc_source(code)
    if nested_heredoc is not None:
        nested_python, literal = nested_heredoc
        if literal:
            effect, reads, writes, read_set_complete = _python_effect_and_paths(
                nested_python
            )
        else:
            effect, reads, writes, read_set_complete = "unknown", (), (), False
        try:
            ast.parse(nested_python, mode="exec")
        except (SyntaxError, ValueError, TypeError):
            parsed = False
            effect = "unknown"
        else:
            parsed = True
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
    read_set_complete = True
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
            wrapper = Path(words[0]).name.lower() in {
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
        executable, args = _command_words(words)
        if not executable:
            continue
        if executable == "cd" and command_cwd is None:
            unknown = True
            read_set_complete = False
        if any(getattr(part, "parts", ()) for part in word_nodes[1:]):
            unknown = True
        if executable in _SHELL_MUTATION_COMMANDS:
            known_mutation = True
            writes.extend(_path_arguments(executable, args))
            continue
        if executable in _PYTHON_EXECUTABLES:
            python_source = _python_source_from_shell_command(node, args)
            if python_source is None:
                unknown = True
                continue
            (
                python_effect,
                python_reads,
                python_writes,
                python_reads_complete,
            ) = _python_effect_and_paths(python_source)
            read_entries.extend((path, _FULL_READ) for path in python_reads)
            writes.extend(python_writes)
            read_set_complete = read_set_complete and python_reads_complete
            if python_effect == "mutating":
                known_mutation = True
            elif python_effect != "read_only":
                unknown = True
            continue
        if executable == "sed" and any(
            value == "-i" or value.startswith("-i") for value in args
        ):
            known_mutation = True
            writes.extend(_path_arguments(executable, args))
            continue
        command_read_only, command_entries, command_complete = _generic_read_analysis(
            executable, args
        )
        if not command_read_only:
            unknown = True
            continue
        read_entries.extend(command_entries)
        read_set_complete = read_set_complete and command_complete

    effect = "mutating" if known_mutation else "unknown" if unknown else "read_only"
    read_paths, read_ranges, entries_complete = _bounded_read_entries(read_entries)
    read_set_complete = read_set_complete and entries_complete
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
            read_paths=read_paths,
            read_ranges=read_ranges,
        ),
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
) -> dict[str, Any]:
    """Project one terminal decision/result into bounded transport metadata."""

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
            executed
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
        "write_paths": list(plan.write_paths),
        "read_set_complete": plan.read_set_complete,
        "read_path_epochs": read_epochs,
        "workspace_generation_delta": generation_delta,
        "mutation_observed": mutation_observed,
        "scope_volatile": bool(executed and (plan.background or not capture_complete)),
        "executed": executed,
        "timed_out": bool(timed_out),
        "exit_code": exit_code,
    }
    if nested_language_evidence:
        receipt["nested_language_evidence"] = nested_language_evidence
    return receipt


__all__ = [
    "TERMINAL_EFFECTS",
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
]
