"""Opt-in completion contracts for callers that explicitly request checks.

The default CLI path leaves task completion to the model. Structured checks
remain available for callers that choose observe or enforce mode.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from aworld.core.context.compiler import (
    ArtifactEvidence,
    ArtifactRequirement,
    CompletionContract,
    CompletionMode,
    SelfCheckEvidence,
    ValidationCommand,
)
from aworld.logs.util import logger


COMPLETION_MODE_ENV = "AWORLD_COMPLETION_MODE"
COMPLETION_MAX_REPAIRS_ENV = "AWORLD_COMPLETION_MAX_REPAIRS"
REQUIRED_ARTIFACTS_ENV = "AWORLD_REQUIRED_ARTIFACTS_JSON"
VALIDATION_COMMANDS_ENV = "AWORLD_VALIDATION_COMMANDS_JSON"
PUBLIC_DELIVERABLE_SCHEMA = "aworld.public-deliverables/v1"
PUBLIC_CAPABILITY_SCHEMA = "aworld.public-capabilities/v1"
PUBLIC_TASK_CONTRACT_SCHEMA = "aworld.public-task-contract/v1"
PUBLIC_DELIVERABLE_AUTHORITY = "public_task_advisory"
_MAX_PUBLIC_DELIVERABLES = 16
_MAX_PUBLIC_OBLIGATIONS = 32
_MAX_PUBLIC_OBLIGATION_TEXT = 512
_MAX_VALIDATION_DIAGNOSTIC_BYTES = 16 * 1024
_VALIDATION_DIAGNOSTIC_CODE = re.compile(r"[a-z][a-z0-9_.:-]{0,127}\Z")
_DELIVERABLE_TOKEN = r"(?:`([^`\r\n]+)`|'([^'\r\n]+)'|\"([^\"\r\n]+)\"|([^\s,;:!?]+))"
_PUBLIC_DELIVERABLE_PATTERNS = (
    re.compile(
        r"(?i)\b(?:output|result|artifact|report|deliverable)(?:\s+[a-z0-9_-]+){0,3}\s+"
        r"(?:file\s+)?(?:should|must|shall|needs?\s+to|is\s+to)?\s*(?:be\s+)?"
        r"(?:named|called|titled|saved|written|created|generated|exported|stored)"
        r"(?:\s+(?:as|to|at))?\s*[:=]?\s*" + _DELIVERABLE_TOKEN
    ),
    # Imperative task clauses often put the concrete name immediately after a
    # file/program noun rather than after "to/as/named", e.g. "Create a
    # python file /app/filter.py" or "Write a c program image.c".
    re.compile(
        r"(?i)\b(?:save|write|create|generate|export|store|produce|implement|build|make|put)\b"
        r"\s+(?:me\s+)?(?:an?\s+|the\s+)?"
        r"(?:[a-z0-9_+#.-]+\s+){0,4}?"
        r"(?:file|script|program|executable|interpreter)\s+"
        r"(?:(?:named|called|titled)\s+)?"
        + _DELIVERABLE_TOKEN
    ),
    re.compile(
        r"(?i)\b(?:write|create|generate|produce|export|store)\s+me\s+"
        + _DELIVERABLE_TOKEN
    ),
    re.compile(
        r"(?i)\b(?:write|create|generate|produce|implement|build|make)\b"
        r"[^\r\n.!?]{0,120}?\b(?:named|called|titled)\s+"
        + _DELIVERABLE_TOKEN
    ),
    re.compile(
        r"(?i)\b(?:call|name)\s+(?:your|the)\s+"
        r"(?:file|script|program|executable|interpreter)\s+"
        + _DELIVERABLE_TOKEN
    ),
    re.compile(
        r"(?i)\b(?:save|write|create|generate|export|store|produce|implement|put)\b"
        r"\s+(?:it\s+)?(?:in|into|to|at|as)?\s*(?:an?\s+|the\s+)?"
        r"(?:file\s+)?(?:(?:named|called|titled)\s+)?"
        + _DELIVERABLE_TOKEN
    ),
)
_PUBLIC_DELIVERABLE_IMPERATIVE_TO_PATTERNS = (
    re.compile(
        r"(?i)\b(?:save|write|create|generate|export|store|produce)\b"
        r"[^\r\n.!?]{0,80}?\b(?:to|as|at|named|called|titled)\s+"
        + _DELIVERABLE_TOKEN
    ),
)
_PUBLIC_DELIVERABLE_DIRECTORY_PATTERN = re.compile(
    r"(?i)\b(?:save|write|create|generate|export|store|produce)\b"
    r"[^\r\n.!?]{0,80}?\b(?:artifacts?|outputs?|files?)\b"
    r"[^\r\n.!?]{0,40}?\b(?:under|in|into|to|at)\s+"
    + _DELIVERABLE_TOKEN
)
_PUBLIC_DELIVERABLE_LIST_ITEM = re.compile(
    r"(?im)^\s*(?:\d+[.)]|[-*])\s+`([^`\r\n]+)`"
)
_PUBLIC_EXECUTABLE_PATTERN = re.compile(
    r"(?i)\b([a-z0-9][a-z0-9._+-]{0,63})\s+(?:command[- ]line\s+)?tool\b"
)
_GENERIC_TOOL_WORDS = frozenset(
    {"a", "an", "available", "command", "external", "some", "the", "validation"}
)
_FENCED_PUBLIC_SCHEMA = re.compile(
    r"```\s*(json|toml|yaml|yml|proto|protobuf)\s*\n(.*?)```",
    re.IGNORECASE | re.DOTALL,
)
_EXPLICIT_SCHEMA_CONTEXT = re.compile(
    r"(?i)(?:exact(?:ly)?|required\s+output|output\s+format|"
    r"following\s+(?:format|structure)|field\s+names?|schema|must\s+contain)"
)
_FUNCTION_SIGNATURE = re.compile(
    r"(?m)(?:^|[`\s])((?:async\s+)?def\s+[A-Za-z_]\w*\s*\([^\n]{0,512}?\)"
    r"\s*(?:->\s*[^:\n`]{1,256})?\s*:?)"
)
_CALLABLE_SIGNATURE = re.compile(
    r"^((?:async\s+)?([A-Za-z_]\w*)\s*\([^\r\n]{0,512}?\)"
    r"\s*(?:->\s*[^\r\n]{1,256})?)$"
)
_IMPORT_CONTRACT = re.compile(
    r"^from\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s+import\s+"
    r"([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)$"
)
_PORT_REQUIREMENT = re.compile(
    r"(?i)\b(?:listen(?:ing)?\s+on|bind(?:ing)?\s+to|use|using|on)\s+port\s+"
    r"([0-9]{2,5})\b|\bport\s*[:=]?\s*([0-9]{2,5})\b"
)
_TYPED_IDENTIFIER = re.compile(
    r"\b([A-Za-z_]\w*)\s*\(\s*"
    r"(string|str|int(?:eger)?|float|number|bool(?:ean)?)\s*\)",
    re.IGNORECASE,
)
_COMMAND_CODE_SPAN = re.compile(r"`([^`\r\n]{2,512})`")
_COMMAND_CONTEXT = re.compile(
    r"(?i)(?:\b(?:run|execute|invoke)\b|\bcalled\s+with\b|"
    r"\bshould\s+be\s+able\s+to\s+run\b|\bcompile(?:d)?\s+using\b)"
)
_THRESHOLD_CONTEXT = re.compile(
    r"(?i)(?:at\s+most|at\s+least|less\s+than|more\s+than|no\s+more\s+than|"
    r"no\s+less\s+than|under|over|<=|>=|≤|≥)[^\r\n.!?]{0,96}\d"
)
_NEGATIVE_CONSTRAINT = re.compile(
    r"(?i)\b(?:do\s+not|don't|must\s+not|should\s+not|never|without)\b"
)
_LIVENESS_CONSTRAINT = re.compile(
    r"(?i)\b(?:keep|leave|left|remain|stays?|continue)\b"
    r"[^\r\n.!?]{0,96}\b(?:running|background|alive|available)\b"
)


@dataclass(frozen=True)
class PublicDeliverableHint:
    """A task-text output hint, never caller or verifier authority."""

    deliverable_id: str
    path: str
    display_path: str
    kind: str = "file"
    authority: str = PUBLIC_DELIVERABLE_AUTHORITY


@dataclass(frozen=True)
class PublicExecutableHint:
    """A named executable to check early, never an instruction to run it."""

    capability_id: str
    executable: str
    authority: str = PUBLIC_DELIVERABLE_AUTHORITY


@dataclass(frozen=True)
class PublicObligationHint:
    """One bounded requirement derived only from authoritative public text."""

    obligation_id: str
    kind: str
    subject: str
    expectation: object
    source_excerpt: str
    source_fingerprint: str
    authority: str = PUBLIC_DELIVERABLE_AUTHORITY
    source: str = "public_task_text"


def _public_fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bounded_public_excerpt(value: str) -> str:
    return " ".join(value.strip().split())[:_MAX_PUBLIC_OBLIGATION_TEXT]


def _public_clause(request: str, start: int, end: int) -> str:
    left = max(
        request.rfind("\n", 0, start),
        request.rfind(".", 0, start),
        request.rfind("!", 0, start),
        request.rfind("?", 0, start),
    )
    stops = [
        position
        for marker in ("\n", ".", "!", "?")
        for position in (request.find(marker, end),)
        if position >= 0
    ]
    right = min(stops) + 1 if stops else min(len(request), end + 256)
    return request[left + 1 : right].strip()


def infer_public_obligation_hints(
    request: str | None,
    *,
    workspace_path: str | os.PathLike[str],
) -> tuple[PublicObligationHint, ...]:
    """Conservatively derive a typed ledger from public task text only.

    The ledger is advisory task grounding.  It is never populated from a
    verifier, reward, historical failure, or solver-generated text, and it
    never becomes external scoring authority.
    """

    if not isinstance(request, str) or not request.strip():
        return ()
    bounded_request = request[:256_000]
    rows: list[tuple[str, str, object, str]] = []
    seen: set[str] = set()

    def add(kind: str, subject: str, expectation: object, excerpt: str) -> None:
        normalized_excerpt = _bounded_public_excerpt(excerpt)
        if not normalized_excerpt or not subject:
            return
        identity = json.dumps(
            [kind, subject, expectation],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if identity in seen or len(rows) >= _MAX_PUBLIC_OBLIGATIONS:
            return
        seen.add(identity)
        rows.append((kind, subject[:512], expectation, normalized_excerpt))

    deliverables = infer_public_deliverable_hints(
        bounded_request, workspace_path=workspace_path
    )
    for item in deliverables:
        add(
            "deliverable_file",
            item.display_path,
            {"path": item.path, "required": True},
            next(
                (
                    _public_clause(bounded_request, match.start(), match.end())
                    for match in re.finditer(re.escape(item.display_path), bounded_request)
                ),
                item.display_path,
            ),
        )

    for match in _FENCED_PUBLIC_SCHEMA.finditer(bounded_request):
        prefix = bounded_request[max(0, match.start() - 240) : match.start()]
        if _EXPLICIT_SCHEMA_CONTEXT.search(prefix) is None:
            continue
        language, body = match.group(1).lower(), match.group(2)
        fields: list[tuple[str, str | None]] = []
        if language == "json":
            fields.extend((name, None) for name in re.findall(r'"([^"\r\n]{1,128})"\s*:', body))
        elif language == "toml":
            fields.extend(
                (name, None)
                for name in re.findall(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_.-]{0,127})\s*=", body)
            )
        elif language in {"proto", "protobuf"}:
            fields.extend(
                (name, declared_type)
                for declared_type, name in re.findall(
                    r"(?m)^\s*(?:repeated\s+)?([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*=\s*\d+\s*;",
                    body,
                )
            )
        else:
            fields.extend(
                (name, None)
                for name in re.findall(r"(?m)^\s*([A-Za-z_]\w*)\s*:", body)
            )
        excerpt = _public_clause(bounded_request, match.start(), match.end())
        for name, declared_type in fields[:16]:
            add(
                "structured_field",
                name,
                {"required": True, **({"declared_type": declared_type} if declared_type else {})},
                excerpt,
            )

    for match in _FUNCTION_SIGNATURE.finditer(bounded_request):
        signature = " ".join(match.group(1).split())
        add(
            "function_signature",
            signature.split("(", 1)[0].removeprefix("async ").removeprefix("def "),
            {"signature": signature},
            _public_clause(bounded_request, match.start(), match.end()),
        )

    code_spans = list(_COMMAND_CODE_SPAN.finditer(bounded_request))
    classified_code_spans: set[tuple[int, int]] = set()
    for match in code_spans:
        content = " ".join(match.group(1).split())
        signature = _CALLABLE_SIGNATURE.fullmatch(content)
        if signature is not None:
            name = signature.group(2)
            add(
                "function_signature",
                name,
                {"signature": signature.group(1)},
                _public_clause(bounded_request, match.start(), match.end()),
            )
            classified_code_spans.add(match.span())
            continue
        import_contract = _IMPORT_CONTRACT.fullmatch(content)
        if import_contract is not None:
            add(
                "import_contract",
                import_contract.group(1),
                {
                    "module": import_contract.group(1),
                    "names": [
                        value.strip()
                        for value in import_contract.group(2).split(",")
                    ],
                },
                _public_clause(bounded_request, match.start(), match.end()),
            )
            classified_code_spans.add(match.span())

    for match in _TYPED_IDENTIFIER.finditer(bounded_request):
        clause = _public_clause(bounded_request, match.start(), match.end())
        if re.search(r"(?i)\b(?:field|parameter|message|request|response|includes?|contains?|returns?)\b", clause) is None:
            continue
        add(
            "structured_field",
            match.group(1),
            {"required": True, "declared_type": match.group(2).lower()},
            clause,
        )

    for match in _PORT_REQUIREMENT.finditer(bounded_request):
        port = int(match.group(1) or match.group(2))
        if 0 < port <= 65535:
            add(
                "service_port",
                str(port),
                {"port": port, "required": True},
                _public_clause(bounded_request, match.start(), match.end()),
            )

    for match in code_spans:
        if match.span() in classified_code_spans:
            continue
        clause = _public_clause(bounded_request, match.start(), match.end())
        command = " ".join(match.group(1).split())
        if (
            _COMMAND_CONTEXT.search(clause) is not None
            and _NEGATIVE_CONSTRAINT.search(clause) is None
            and bool(re.search(r"\s", command))
        ):
            add(
                "public_command",
                command[:256],
                {"command": command[:512]},
                clause,
            )

    for match in re.finditer(r"[^\r\n]+", bounded_request):
        excerpt = match.group(0).strip()
        if _THRESHOLD_CONTEXT.search(excerpt):
            add("numeric_threshold", _bounded_public_excerpt(excerpt), {"statement": _bounded_public_excerpt(excerpt)}, excerpt)
        if _NEGATIVE_CONSTRAINT.search(excerpt):
            add("negative_constraint", _bounded_public_excerpt(excerpt), {"statement": _bounded_public_excerpt(excerpt)}, excerpt)
        if _LIVENESS_CONSTRAINT.search(excerpt):
            add("process_liveness", "process_liveness", {"statement": _bounded_public_excerpt(excerpt)}, excerpt)

    return tuple(
        PublicObligationHint(
            obligation_id=f"public-obligation-{index}",
            kind=kind,
            subject=subject,
            expectation=expectation,
            source_excerpt=excerpt,
            source_fingerprint=_public_fingerprint(excerpt),
        )
        for index, (kind, subject, expectation, excerpt) in enumerate(rows, start=1)
    )


def _matched_deliverable_token(match: re.Match[str]) -> str:
    return next(
        (
            value.strip()
            for value in match.groups()[-4:]
            if isinstance(value, str) and value.strip()
        ),
        "",
    )


def _is_described_runtime_side_effect(request: str, match: re.Match[str]) -> bool:
    """Reject a component's described side effect as the primary deliverable.

    Public tasks frequently mention files produced *when* a supplied program
    runs.  A delivery reserve must not tell the model to fabricate that runtime
    by-product in place of implementing the requested program. Explicit
    output declarations and imperatives directed at the Agent remain
    observable.
    """

    prefix = request[max(0, match.start() - 160) : match.start()]
    clause_start = max(
        prefix.rfind("\n"),
        prefix.rfind("."),
        prefix.rfind("!"),
        prefix.rfind("?"),
        prefix.rfind(";"),
    )
    clause_prefix = prefix[clause_start + 1 :].strip()
    # Never manufacture a delivery obligation from a prohibition or a
    # hypothetical branch.  These advisory hints may trigger required Tool
    # use near the deadline, so false positives are more harmful than misses.
    if re.search(
        r"(?i)(?:\bdo\s+not\b|\bdon['’]?t\b|\bmust\s+not\b|"
        r"\bshould\s+not\b|\bnever\b|\bwithout\b|\bavoid\b|"
        r"\bprohibit(?:ed|s)?\b)[^\r\n.!?;]{0,96}$",
        clause_prefix,
    ):
        return True
    if re.match(
        r"(?i)^(?:if|only\s+if|unless|provided\s+that|in\s+case)\b",
        clause_prefix,
    ) or re.match(r"(?i)^should\s+(?:you|the\s+agent)\b", clause_prefix):
        return True
    # Tests, examples, and supplied components often describe files they
    # create while running.  Such observations are not instructions to the
    # Agent, even when the prose uses a bare present-tense verb.
    if re.search(
        r"(?i)(?:^|\b)(?:the\s+|this\s+|an?\s+)?"
        r"(?:tests?|test\s+cases?|examples?|samples?|renderer|program|script|"
        r"component|application|binary|command|tool)\s*:?\s*"
        r"(?:(?:will|would|can|could|may|might|should|must)\b\s*)?$",
        clause_prefix,
    ):
        return True
    # A modal directed at the Agent/user is still an explicit task imperative;
    # a modal whose subject is a supplied component describes runtime behavior.
    if re.search(
        r"(?i)\b(?:you|the\s+agent)\s+"
        r"(?:will|would|can|could|may|might|should|must)\s+$",
        prefix,
    ):
        return False
    return bool(
        re.search(
        r"(?i)(?:\b(?:will|would|can|could|may|might|should|must)\s+|"
            r"\b(?:is|are|was|were)\s+(?:expected|going|required)\s+to\s+)$",
            prefix,
        )
    )


def _resolve_public_deliverable(
    value: str,
    *,
    workspace_path: str | os.PathLike[str],
    kind: str = "file",
) -> tuple[str, str] | None:
    display = value.strip().strip("`'\"").rstrip(").]}")
    if not display or len(display) > 512 or "://" in display or "\x00" in display:
        return None
    if kind == "file" and display.endswith(("/", "\\")):
        return None
    candidate = Path(display).expanduser()
    # A bare natural-language word is not a file declaration.  Keep
    # extensionless path-like values such as /app/release, but reject words
    # captured from prose such as "interpreter complete with ...".
    if (
        kind == "file"
        and "." not in candidate.name
        and "/" not in display
        and "\\" not in display
    ):
        return None
    workspace = Path(workspace_path).expanduser().resolve()
    if candidate.is_absolute():
        resolved = candidate.resolve(strict=False)
    else:
        resolved = (workspace / candidate).resolve(strict=False)
        try:
            resolved.relative_to(workspace)
        except ValueError:
            return None
    return str(resolved), display


def _immediate_directory_list_filenames(value: str) -> tuple[str, ...]:
    """Return only the contiguous Markdown list following a directory clause."""

    filenames: list[str] = []
    started = False
    preamble_lines = 0
    for line in value.splitlines():
        match = _PUBLIC_DELIVERABLE_LIST_ITEM.match(line)
        if match is not None:
            started = True
            filenames.append(match.group(1).strip())
            if len(filenames) >= _MAX_PUBLIC_DELIVERABLES:
                break
            continue
        if started:
            break
        stripped = line.strip()
        if not stripped or re.fullmatch(r"[:=-]+", stripped):
            preamble_lines += 1
            if preamble_lines <= 4:
                continue
        break
    return tuple(filenames)


def infer_public_deliverable_hints(
    request: str | None, *, workspace_path: str | os.PathLike[str]
) -> tuple[PublicDeliverableHint, ...]:
    """Conservatively extract explicitly named public output files.

    These hints are deliberately weaker than ``CompletionContract``: they do
    not run commands, validate contents, or stand in for a benchmark verifier.
    They only make an unambiguous public delivery obligation observable.
    """

    if not isinstance(request, str) or not request.strip():
        return ()
    discovered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pattern in _PUBLIC_DELIVERABLE_PATTERNS:
        for match in pattern.finditer(request[:256_000]):
            if _is_described_runtime_side_effect(request, match):
                continue
            resolved = _resolve_public_deliverable(
                _matched_deliverable_token(match),
                workspace_path=workspace_path,
            )
            if resolved is None:
                continue
            path, display = resolved
            key = os.path.normcase(path)
            if key in seen:
                continue
            seen.add(key)
            discovered.append((path, display))
            if len(discovered) >= _MAX_PUBLIC_DELIVERABLES:
                break
        if len(discovered) >= _MAX_PUBLIC_DELIVERABLES:
            break
    for pattern in _PUBLIC_DELIVERABLE_IMPERATIVE_TO_PATTERNS:
        for match in pattern.finditer(request[:256_000]):
            if _is_described_runtime_side_effect(request, match):
                continue
            resolved = _resolve_public_deliverable(
                _matched_deliverable_token(match),
                workspace_path=workspace_path,
            )
            if resolved is None:
                continue
            path, display = resolved
            key = os.path.normcase(path)
            if key in seen:
                continue
            seen.add(key)
            discovered.append((path, display))
            if len(discovered) >= _MAX_PUBLIC_DELIVERABLES:
                break
        if len(discovered) >= _MAX_PUBLIC_DELIVERABLES:
            break
    # Some task contracts name one output directory and list the required
    # filenames beneath it, for example ``Write these two artifacts under
    # `/logs/artifacts/`:`` followed by numbered ``document.md`` and
    # ``layout.json`` entries.  The ordinary verb-to-path patterns above see
    # neither filename, so delivery reserve guidance would never arm.  Join
    # only explicit Markdown list-item filenames to the explicitly declared
    # directory; do not recursively infer paths from prose or code examples.
    for directory_match in _PUBLIC_DELIVERABLE_DIRECTORY_PATTERN.finditer(
        request[:256_000]
    ):
        if _is_described_runtime_side_effect(request, directory_match):
            continue
        resolved_directory = _resolve_public_deliverable(
            _matched_deliverable_token(directory_match),
            workspace_path=workspace_path,
            kind="directory",
        )
        if resolved_directory is None:
            continue
        directory_path, _display_directory = resolved_directory
        list_region = request[directory_match.end() : directory_match.end() + 4096]
        for filename in _immediate_directory_list_filenames(list_region):
            candidate = Path(filename)
            if (
                not filename
                or len(filename) > 255
                or candidate.is_absolute()
                or candidate.name != filename
                or filename in {".", ".."}
                or "." not in filename
            ):
                continue
            joined = str(Path(directory_path) / filename)
            key = os.path.normcase(joined)
            if key in seen:
                continue
            seen.add(key)
            discovered.append((joined, filename))
            if len(discovered) >= _MAX_PUBLIC_DELIVERABLES:
                break
        if len(discovered) >= _MAX_PUBLIC_DELIVERABLES:
            break
    return tuple(
        PublicDeliverableHint(
            deliverable_id=f"public-output-{index}",
            path=path,
            display_path=display,
        )
        for index, (path, display) in enumerate(discovered, start=1)
    )


def infer_public_executable_hints(
    request: str | None,
) -> tuple[PublicExecutableHint, ...]:
    """Extract bounded executable names from explicit ``X tool`` wording."""

    if not isinstance(request, str) or not request.strip():
        return ()
    values: list[str] = []
    seen: set[str] = set()
    for match in _PUBLIC_EXECUTABLE_PATTERN.finditer(request[:256_000]):
        executable = match.group(1)
        key = executable.casefold()
        if key in _GENERIC_TOOL_WORDS or key in seen:
            continue
        seen.add(key)
        values.append(executable)
        if len(values) >= 16:
            break
    return tuple(
        PublicExecutableHint(
            capability_id=f"public-executable-{index}",
            executable=executable,
        )
        for index, executable in enumerate(values, start=1)
    )


def _publish_public_deliverable_contract(
    context, *, request: str | None, workspace_path: str | os.PathLike[str]
) -> tuple[PublicDeliverableHint, ...]:
    hints = infer_public_deliverable_hints(request, workspace_path=workspace_path)
    if hints:
        context.context_info["public_deliverable_contract"] = {
            "schema_version": PUBLIC_DELIVERABLE_SCHEMA,
            "authority": PUBLIC_DELIVERABLE_AUTHORITY,
            "source": "public_task_text",
            "artifacts": [asdict(item) for item in hints],
        }
    else:
        context.context_info.pop("public_deliverable_contract", None)
    executables = infer_public_executable_hints(request)
    if executables:
        context.context_info["public_capability_hints"] = {
            "schema_version": PUBLIC_CAPABILITY_SCHEMA,
            "authority": PUBLIC_DELIVERABLE_AUTHORITY,
            "source": "public_task_text",
            "executables": [asdict(item) for item in executables],
        }
    else:
        context.context_info.pop("public_capability_hints", None)
    obligations = infer_public_obligation_hints(
        request,
        workspace_path=workspace_path,
    )
    if obligations:
        context.context_info["public_task_contract"] = {
            "schema_version": PUBLIC_TASK_CONTRACT_SCHEMA,
            "authority": PUBLIC_DELIVERABLE_AUTHORITY,
            "source": "public_task_text",
            "request_fingerprint": _public_fingerprint(str(request or "")),
            "obligations": [asdict(item) for item in obligations],
        }
    else:
        context.context_info.pop("public_task_contract", None)
    return hints


def resolve_completion_mode(value: str | None = None) -> CompletionMode:
    """Model finalization is authoritative unless a caller opts into checks."""

    default = "off"
    raw_value = os.environ.get(COMPLETION_MODE_ENV, default) if value is None else value
    normalized = (raw_value or "off").strip().lower()
    try:
        return CompletionMode(normalized)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in CompletionMode)
        raise ValueError(f"{COMPLETION_MODE_ENV} must be one of: {allowed}") from exc


def resolve_completion_max_repairs(value: str | None = None) -> int | None:
    """Resolve an optional runtime-owned completion repair limit.

    An unset or blank value preserves the historical unbounded runtime contract.
    Callers can opt into a finite limit with any non-negative integer, including
    zero when no model-driven repair turn should be attempted.
    """

    raw_value = (
        os.environ.get(COMPLETION_MAX_REPAIRS_ENV)
        if value is None
        else value
    )
    if raw_value is None or not raw_value.strip():
        return None
    normalized = raw_value.strip()
    if not re.fullmatch(r"[0-9]+", normalized):
        raise ValueError(
            f"{COMPLETION_MAX_REPAIRS_ENV} must be a non-negative integer"
        )
    return int(normalized)


def _configured_artifact_paths(raw_value: str | None = None) -> tuple[str, ...]:
    raw_value = os.environ.get(REQUIRED_ARTIFACTS_ENV) if raw_value is None else raw_value
    if raw_value is None or not raw_value.strip():
        return ()
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{REQUIRED_ARTIFACTS_ENV} must be a JSON array") from exc
    if not isinstance(parsed, list) or any(
        not isinstance(item, str) or not item.strip() for item in parsed
    ):
        raise ValueError(f"{REQUIRED_ARTIFACTS_ENV} must be a JSON array of paths")
    return tuple(item.strip() for item in parsed)


def _configured_validation_commands() -> tuple[ValidationCommand, ...]:
    raw = os.environ.get(VALIDATION_COMMANDS_ENV)
    if not raw:
        return ()
    try:
        values = json.loads(raw)
        if not isinstance(values, list):
            raise ValueError("expected array")
        if any(not isinstance(item, dict) or not isinstance(item.get("argv"), list)
               or (item.get("cwd") is not None and not isinstance(item["cwd"], str))
               for item in values):
            raise ValueError("expected command objects with argv arrays")
        commands = tuple(ValidationCommand(**item) for item in values)
        if len({item.command_id for item in commands}) != len(commands):
            raise ValueError("duplicate command_id")
        return commands
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{VALIDATION_COMMANDS_ENV} must be an array of explicit ValidationCommand objects") from exc


def _typed_validation_diagnostic_code(value: bytes) -> str | None:
    """Return one strict content-free code from bounded JSON stdout."""

    if not value or len(value) > _MAX_VALIDATION_DIAGNOSTIC_BYTES:
        return None

    def reject_duplicates(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = item
        return result

    try:
        payload = json.loads(value.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, ValueError, TypeError):
        return None
    if (
        not isinstance(payload, dict)
        or set(payload) - {"reason", "diagnostic"}
        or not isinstance(payload.get("reason"), str)
        or _VALIDATION_DIAGNOSTIC_CODE.fullmatch(payload["reason"]) is None
    ):
        return None
    return payload["reason"]


def _completion_candidate_hash(contract: CompletionContract) -> str:
    """Bind validation evidence to current required-artifact full hashes."""

    projection = []
    for requirement in contract.required_artifacts:
        if not requirement.required:
            continue
        path = Path(requirement.path).expanduser()
        try:
            with path.open("rb") as stream:
                content_hash = "sha256:" + hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()
        except OSError:
            content_hash = None
        projection.append((requirement.requirement_id, content_hash))
    encoded = json.dumps(projection, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


async def _run_validation(command: ValidationCommand) -> tuple[int, str, str | None]:
    """Execute only caller-supplied argv; drain bounded memory, preserve real exit."""
    digest = hashlib.sha256()
    diagnostic_bytes = bytearray()
    diagnostic_overflow = False
    process = await asyncio.create_subprocess_exec(
        *command.argv, cwd=command.cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    async def drain():
        nonlocal diagnostic_overflow
        while chunk := await process.stdout.read(65536):
            digest.update(chunk)
            if not diagnostic_overflow:
                remaining = _MAX_VALIDATION_DIAGNOSTIC_BYTES - len(
                    diagnostic_bytes
                )
                if len(chunk) <= remaining:
                    diagnostic_bytes.extend(chunk)
                else:
                    diagnostic_overflow = True
                    diagnostic_bytes.clear()
    reader = asyncio.create_task(drain())
    try:
        # Descendants keeping stdout open are also part of a bounded check.
        async with asyncio.timeout(command.timeout_seconds):
            await process.wait()
            await reader
        return (
            process.returncode,
            "sha256:" + digest.hexdigest(),
            (
                None
                if diagnostic_overflow
                else _typed_validation_diagnostic_code(bytes(diagnostic_bytes))
            ),
        )
    except (TimeoutError, asyncio.CancelledError):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        if asyncio.current_task().cancelling():
            raise
        return 124, "sha256:" + digest.hexdigest(), None


def _resolve_artifact_paths(
    values: Iterable[str], *, workspace_path: str | os.PathLike[str]
) -> tuple[str, ...]:
    workspace = Path(workspace_path).expanduser().resolve()
    resolved: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = workspace / candidate
        normalized = str(candidate.resolve(strict=False))
        key = os.path.normcase(normalized)
        if key not in seen:
            seen.add(key)
            resolved.append(normalized)
    return tuple(resolved)


def build_runtime_completion_contract(
    request: str | None,
    *,
    workspace_path: str | os.PathLike[str],
    explicit_paths: Sequence[str] = (),
    validation_commands: Sequence[ValidationCommand] = (),
) -> CompletionContract | None:
    """Build an explicit artifact/command contract, or ``None`` if empty."""

    candidates = list(explicit_paths)
    paths = _resolve_artifact_paths(candidates, workspace_path=workspace_path)
    if not paths and not validation_commands:
        return None
    requirements = tuple(
        ArtifactRequirement(
            requirement_id=f"declared-output-{index}",
            path=path,
        )
        for index, path in enumerate(paths, start=1)
    )
    return CompletionContract(
        required_artifacts=requirements,
        immutable_inputs=(),
        validation_commands=tuple(replace(command, cwd=str((Path(workspace_path) / (command.cwd or ".")).resolve()))
                                  for command in validation_commands),
        max_evidence_age_seconds=None,
        required_final_evidence=("agent_final_response",),
        max_repairs=resolve_completion_max_repairs(),
    )


async def resolve_runtime_completion_evidence(
    context,
    contract: CompletionContract,
) -> None:
    """Collect fresh files and opt-in caller validation, never inferred commands."""

    candidate_hash = _completion_candidate_hash(contract)
    for command in contract.validation_commands:
        try:
            exit_code, output_hash, diagnostic_code = await _run_validation(command)
        except (OSError, ValueError):
            exit_code, output_hash, diagnostic_code = 127, None, None
        context.record_completion_self_check(SelfCheckEvidence(
            command_id=command.command_id, exit_code=exit_code,
            output_hash=output_hash, observed_at=datetime.now(timezone.utc),
            diagnostic_code=diagnostic_code,
            candidate_hash=candidate_hash,
        ))
    _record_runtime_artifacts(context, contract)


def _record_runtime_artifacts(context, contract):
    observed_at = datetime.now(timezone.utc)
    for requirement in contract.required_artifacts:
        path = Path(requirement.path).expanduser()
        try:
            exists = path.exists()
        except OSError:
            exists = False
        content_hash = None
        if exists:
            try:
                with path.open("rb") as stream:
                    content_hash = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
            except OSError:
                exists = False
        context.record_completion_artifact(
            ArtifactEvidence(
                requirement_id=requirement.requirement_id,
                exists=exists,
                content_hash=content_hash,
                observed_at=observed_at,
            )
        )


def configure_runtime_completion(
    context,
    *,
    request: str | None,
    workspace_path: str | os.PathLike[str],
) -> CompletionContract | None:
    """Bind only caller-selected artifact and validation requirements."""
    _publish_public_deliverable_contract(
        context, request=request, workspace_path=workspace_path
    )
    mode = resolve_completion_mode()
    existing = getattr(context, "completion_contract", None)
    explicit_mode = (os.environ.get(COMPLETION_MODE_ENV) or "").strip().lower()
    previous_enforcement = context.context_info.get(
        "completion_enforcement_explicit"
    )
    context.context_info["completion_enforcement_explicit"] = (
        previous_enforcement
        if isinstance(previous_enforcement, bool)
        else bool(
            explicit_mode == CompletionMode.ENFORCE.value
            or (
                existing is not None
                and getattr(context, "completion_mode", None)
                is CompletionMode.ENFORCE
            )
        )
    )
    if mode is CompletionMode.OFF and existing is None:
        context.context_info["runtime_completion_contract"] = {
            "mode": "off", "requested_mode": "off", "source": "model_final_response",
        }
        return None
    explicit_paths = _configured_artifact_paths()
    artifact_field_provided = bool((os.environ.get(REQUIRED_ARTIFACTS_ENV) or "").strip())
    validation_commands = _configured_validation_commands()
    if existing is not None:
        logger.info("Keeping the completion contract already installed by the caller")
        return existing

    # Only caller-supplied structure can activate this generic contract path.
    if artifact_field_provided or validation_commands:
        contract = build_runtime_completion_contract(
            request, workspace_path=workspace_path, explicit_paths=explicit_paths,
            validation_commands=validation_commands,
        )
        if contract is None:
            contract = CompletionContract(
                (),
                (),
                (),
                None,
                ("agent_final_response",),
                max_repairs=resolve_completion_max_repairs(),
            )
        context.configure_completion_contract(contract, mode=mode,
                                              evidence_resolver=resolve_runtime_completion_evidence)
        context.context_info["runtime_completion_contract"] = {
            "mode": mode.value, "requested_mode": mode.value, "source": "explicit_structured",
            "required_artifacts": [item.path for item in contract.required_artifacts],
            "provided_fields": ["outputs"] if artifact_field_provided else [],
            "max_repairs": contract.max_repairs,
        }
        return contract

    context.context_info["runtime_completion_contract"] = {
        "mode": "off",
        "requested_mode": mode.value,
        "source": "no_explicit_contract",
        "required_artifacts": [],
        "max_repairs": resolve_completion_max_repairs(),
    }
    return None


def configure_goal_completion(context, *, verification_commands: Sequence[str], workspace_path) -> CompletionContract | None:
    """Bind explicit user goal verification commands; no model text is executed.

    Goal CLI commands are shell strings by contract. pipefail prevents a failing
    validation hidden behind a successful log formatter from claiming success.
    """
    if not verification_commands:
        return getattr(context, "completion_contract", None)
    context.context_info["completion_enforcement_explicit"] = True
    if any(not isinstance(command, str) or not command.strip() for command in verification_commands):
        raise ValueError("goal verification commands must be non-empty strings")
    shell = shutil.which("bash")
    if shell is None:
        raise ValueError("explicit shell validation requires bash with pipefail")
    previous = getattr(context, "completion_contract", None)
    previous_resolver = getattr(context, "_completion_evidence_resolver", None)
    metadata = context.context_info.get("runtime_completion_contract", {})
    owns_previous = previous is getattr(context, "_goal_completion_owned_contract", None)
    owned_ids = set(metadata.get("validation_command_ids", ())) if owns_previous else set()
    if owns_previous:
        previous_resolver = getattr(context, "_goal_completion_base_resolver", None)
    base_resolver_contract = getattr(context, "_goal_completion_base_contract", None) if owns_previous else previous
    base_checks = tuple(c for c in (previous.validation_commands if previous else ())
                        if c.command_id not in owned_ids)
    used_ids = {c.command_id for c in base_checks}
    used_ids.update(previous.required_self_check_ids if previous else ())
    checks = []
    for index, command in enumerate(verification_commands, 1):
        base_id = f"goal-verify-{index}"
        command_id = base_id
        suffix = 1
        while command_id in used_ids:
            command_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(command_id)
        checks.append(ValidationCommand(
            command_id=command_id,
            argv=(shell, "-o", "pipefail", "-c", command),
            cwd=str(Path(workspace_path).expanduser().resolve()),
        ))
    checks = tuple(checks)
    contract = CompletionContract(
        required_artifacts=previous.required_artifacts if previous else (),
        immutable_inputs=previous.immutable_inputs if previous else (),
        validation_commands=base_checks + checks,
        max_evidence_age_seconds=previous.max_evidence_age_seconds if previous else None,
        required_final_evidence=tuple(dict.fromkeys((previous.required_final_evidence if previous else ()) + ("agent_final_response",))),
        max_repairs=(
            previous.max_repairs
            if previous
            else resolve_completion_max_repairs()
        ),
        required_self_check_ids=previous.required_self_check_ids if previous else (),
    )
    resolver = resolve_runtime_completion_evidence
    if previous_resolver is not None and previous_resolver is not resolve_runtime_completion_evidence:
        async def resolver(target, configured):
            await previous_resolver(target, base_resolver_contract)
            # The caller resolver owns the original checks. Only execute this
            # goal's added commands here; rerunning caller commands could mutate
            # outputs twice and overwrite their authoritative evidence.
            await resolve_runtime_completion_evidence(target, replace(
                configured, validation_commands=checks, required_artifacts=(),
            ))
    context.configure_completion_contract(contract, mode=CompletionMode.ENFORCE, evidence_resolver=resolver)
    context.context_info["runtime_completion_contract"] = {
        "mode": "enforce", "requested_mode": "enforce", "source": "explicit_goal_verification",
        "required_artifacts": [item.path for item in contract.required_artifacts],
        "validation_command_ids": [item.command_id for item in checks],
        "max_repairs": contract.max_repairs,
    }
    context._goal_completion_owned_contract = contract
    context._goal_completion_base_contract = base_resolver_contract
    context._goal_completion_base_resolver = previous_resolver
    return contract


__all__ = [
    "COMPLETION_MAX_REPAIRS_ENV",
    "COMPLETION_MODE_ENV",
    "PUBLIC_CAPABILITY_SCHEMA",
    "PUBLIC_DELIVERABLE_SCHEMA",
    "REQUIRED_ARTIFACTS_ENV",
    "VALIDATION_COMMANDS_ENV",
    "PublicDeliverableHint",
    "PublicExecutableHint",
    "build_runtime_completion_contract",
    "configure_goal_completion",
    "configure_runtime_completion",
    "infer_public_deliverable_hints",
    "infer_public_executable_hints",
    "resolve_completion_max_repairs",
    "resolve_completion_mode",
    "resolve_runtime_completion_evidence",
]
