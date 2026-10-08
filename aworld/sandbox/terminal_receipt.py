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
from typing import Any, Iterable, Sequence

import bashlex


TERMINAL_EXECUTION_RECEIPT_SCHEMA = "aworld.terminal-execution-receipt/v2"
TERMINAL_EXECUTION_RECEIPT_KEY = "terminal_execution_receipt"
TERMINAL_EXECUTION_ANALYZER_VERSION = 5
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
                receiver_name == "os"
                and function.attr in _PYTHON_OS_MUTATION_METHODS
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
                if option in {"-u", "-g", "-h", "-p", "-C", "-T", "-R", "-D"} and remaining:
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
    if executable == "rg":
        positional = [value for value in args if not value.startswith("-")]
        return tuple(positional[1:] or (".",))
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


def _shell_read_command_is_provable(
    executable: str,
    args: Sequence[str],
) -> bool:
    if executable not in _SHELL_READ_COMMANDS:
        return False
    if executable == "rg" and any(
        value.startswith("--pre") or value.startswith("--hostname-bin")
        for value in args
    ):
        return False
    if executable == "rg" and (
        not args or any(value.startswith("-") for value in args)
    ):
        return False
    if executable == "sed":
        return (
            len(args) >= 3
            and args[0] == "-n"
            and re.fullmatch(r"\d+(?:,\d+)?p", args[1]) is not None
            and all(not value.startswith("-") for value in args[2:])
        )
    return True


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


def _parse_shell_nodes(source: str) -> list[Any] | None:
    try:
        return list(bashlex.parse(source))
    except (bashlex.errors.ParsingError, NotImplementedError, RecursionError, AssertionError, TypeError):
        return None


_LEADING_STATIC_CD = re.compile(
    r"\A\s*cd\s+(?P<path>'[^']*'|\"[^\"]*\"|[^\s;&|]+)\s*(?:&&|;|\n|\Z)"
)
_SHELL_CD_COMMAND = re.compile(r"(?:\A|&&|[;|(){}\n])\s*cd(?:\s|\Z)")


def shell_command_working_directory(source: str) -> tuple[str | None, bool]:
    """Return a leading literal Shell cwd and whether every ``cd`` is resolved."""

    if not isinstance(source, str):
        return None, False
    current: str | None = None
    remainder = source
    while True:
        match = _LEADING_STATIC_CD.match(remainder)
        if match is None:
            break
        raw_path = match.group("path")
        if raw_path[:1] in {"'", '"'} and raw_path[-1:] == raw_path[:1]:
            raw_path = raw_path[1:-1]
        if not raw_path or any(marker in raw_path for marker in ("$", "`", "\\")):
            return current, False
        normalized = posixpath.normpath(raw_path)
        current = (
            normalized
            if posixpath.isabs(normalized) or current is None
            else posixpath.normpath(posixpath.join(current, normalized))
        )
        if match.end() == len(remainder):
            remainder = ""
            break
        remainder = remainder[match.end() :]
    if _SHELL_CD_COMMAND.search(remainder):
        return current, False
    return current, True


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
        return TerminalExecutionPlan(
            "python",
            effect,
            effect == "read_only",
            parsed,
            reads,
            writes,
            False,
            read_set_complete,
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
        return TerminalExecutionPlan(
            "shell",
            effect,
            effect == "read_only",
            parsed,
            reads,
            writes,
            False,
            read_set_complete,
            ("python",),
            (terminal_command_sha256(nested_python),),
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

    reads: list[str] = []
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
                or (
                    isinstance(raw_target, str)
                    and raw_target.startswith("/dev/fd/")
                )
            )
            if not non_file_target:
                known_mutation = True
            if path is not None:
                writes.append(path)
        elif redirect_type == "<" and path is not None:
            reads.append(path)
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
            leading_assignments = bool(
                re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0])
            )
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
                    resolved_executable = str(Path(raw_executable).expanduser().resolve())
                except OSError:
                    resolved_executable = raw_executable
                explicitly_trusted = any(
                    resolved_executable
                    == str(Path(value).expanduser().resolve())
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
            reads.extend(python_reads)
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
        if not _shell_read_command_is_provable(executable, args):
            unknown = True
            continue
        path_arguments = (
            args[1:]
            if executable == "rg" and args
            else args
        )
        if any(
            any(marker in value for marker in ("*", "?", "[", "]"))
            for value in path_arguments
        ):
            read_set_complete = False
        reads.extend(_path_arguments(executable, args))

    effect = "mutating" if known_mutation else "unknown" if unknown else "read_only"
    if len(dict.fromkeys(reads)) > _MAX_RECEIPT_PATHS:
        read_set_complete = False
    return TerminalExecutionPlan(
        "shell",
        effect,
        effect == "read_only",
        True,
        _bounded_paths(reads),
        _bounded_paths(writes),
        background_operator,
        read_set_complete,
        command_cwd=command_cwd,
        command_cwd_safe=command_cwd_safe,
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

    generation_delta = (
        0 if not executed or plan.effect == "read_only" else 1
    )
    read_epochs = [dict(epoch) for epoch in read_path_epochs][:_MAX_RECEIPT_PATHS]
    read_epochs_complete = not plan.read_paths or len(read_epochs) == len(plan.read_paths)
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
    "build_terminal_execution_receipt",
    "plan_terminal_execution",
    "python_is_provably_read_only",
    "shell_command_working_directory",
    "terminal_command_sha256",
]
