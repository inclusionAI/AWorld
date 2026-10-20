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
from datetime import date, datetime, timezone
import math
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
_MAX_PUBLIC_OBLIGATION_CANDIDATES = 128
_MAX_PUBLIC_OBLIGATION_TEXT = 512
_MAX_PUBLIC_ENUMERATED_ITEMS = 16
_MAX_PUBLIC_ENUMERATED_ITEM_TEXT = 128
_MAX_PUBLIC_EXPECTATION_BYTES = 4096
_MAX_VALIDATION_DIAGNOSTIC_BYTES = 16 * 1024
_VALIDATION_DIAGNOSTIC_CODE = re.compile(r"[a-z][a-z0-9_.:-]{0,127}\Z")
_DELIVERABLE_TOKEN = r"(?:`([^`\r\n]+)`|'([^'\r\n]+)'|\"([^\"\r\n]+)\"|([^\s,;:!?]+))"
_PUBLIC_DELIVERABLE_PATTERNS = (
    re.compile(
        r"(?i)\b(?:return|provide|submit)\b[^\r\n.!?]{0,160}?"
        r"\b(?:final\s+)?output(?:\s+(?:file|document|artifact))?\s+"
        r"(?:at|to|as)\s+" + _DELIVERABLE_TOKEN
    ),
    re.compile(
        r"(?i)\bin\s+" + _DELIVERABLE_TOKEN
        + r"[\s\S]{0,512}?\b(?:edit|modify|update|complete|finalize|fix|repair)\b"
    ),
    re.compile(
        r"(?i)\b(?:edit|modify|update|complete|finalize|fix|repair)\b"
        r"\s+(?:the\s+)?(?:file\s+)?" + _DELIVERABLE_TOKEN
    ),
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
_PUBLIC_NAMED_FILE_IN_DIRECTORY = re.compile(
    r"(?i)\b(?:file|document|artifact)\s+(?:named|called)\s+"
    r"(?:`(?P<backtick_file>[^`\r\n]+)`|'(?P<single_file>[^'\r\n]+)'|"
    r'\"(?P<double_file>[^\"\r\n]+)\"|(?P<plain_file>[^\s,;:!?]+))'
    r"\s+(?:in|under)\s+"
    r"(?:`(?P<backtick_dir>[^`\r\n]+)`|'(?P<single_dir>[^'\r\n]+)'|"
    r'\"(?P<double_dir>[^\"\r\n]+)\"|(?P<plain_dir>[^\s,;:!?]+))'
    r"(?:\s+(?:folder|directory))?"
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
    r"(?i)(?:exact(?:ly)?|required\s+output|"
    r"required\s+(?:json|toml|ya?ml|proto(?:buf)?)\s+"
    r"(?:format|structure|schema)|output\s+format|"
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
    r"no\s+less\s+than|under|over|exactly|within|tolerance|precision|"
    r"accuracy|size|<=|>=|≤|≥)[^\r\n.!?]{0,96}(?<![A-Za-z_])\d"
)
_NEGATIVE_CONSTRAINT = re.compile(
    r"(?i)\b(?:do\s+not|don't|must\s+not|should\s+not|never|without)\b"
)
_LIVENESS_CONSTRAINT = re.compile(
    r"(?i)\b(?:keep|leave|left|remain|stays?|continue)\b"
    r"[^\r\n.!?]{0,96}\b(?:running|background|alive|available)\b"
)
_COUNT_LIKE_THRESHOLD = re.compile(
    r"(?i)\b(?:under|over|less\s+than|more\s+than)\s+\d{1,9}\s+"
    r"(?:different\s+(?:[A-Za-z_-]+\s+)?(?:cases?|items?)|"
    r"rows?|columns?|files?|records?|events?|"
    r"tasks?)\b"
)
_PUBLIC_LIST_ITEM = re.compile(
    r"^\s*(?:[-*+]|\d+[.)])\s+(?P<item>\S(?:.*?\S)?)\s*$"
)
_PUBLIC_REQUIREMENT_PARENT = re.compile(
    r"(?i)\b(?:output|outputs|deliverable|deliverables|required|requirements?|"
    r"expected\s+output|following\s+information|create|add|must\s+contain|"
    r"should\s+contain)\b"
)
_PUBLIC_NON_AUTHORITATIVE_CONTEXT = re.compile(
    r"(?i)(?:^|\b)(?:examples?|samples?|hints?|available\s+(?:tools?|libraries)|"
    r"pre[- ]installed\s+(?:tools?|libraries)|exclusion\s+keywords?)\b"
)
_PUBLIC_INPUT_CONTEXT = re.compile(
    r"(?i)^\s*(?:the\s+)?input\s+(?:files?|schema|data|fields?|columns?|items?|"
    r"tables?|workbooks?|sheets?|notes?)\b"
)
_PUBLIC_CONDITIONAL_PREFIX = re.compile(
    r"(?i)^\s*(?:if|only\s+if|unless|provided\s+that|in\s+case|"
    r"when\s+(?:the\s+)?optional)\b"
)
_PUBLIC_CONDITIONAL_CLAUSE = re.compile(
    r"(?i)\b(?:if|unless|when)\s+(?:the\s+)?(?:optional|debug|preview|"
    r"feature|mode)\b[^.]*[.!?]?$"
)
_MARKDOWN_TABLE_SEPARATOR = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_CELL_RANGE = re.compile(r"(?<![A-Za-z0-9_])([A-Z]{1,3}\d+)\s*:\s*([A-Z]{1,3}\d+)\b")
_DATE_RANGE = re.compile(
    r"(?<!\d)(\d{4}-\d{2}-\d{2})\s+(?:to|through|until|[-–—])\s+"
    r"(\d{4}-\d{2}-\d{2})(?!\d)",
    re.IGNORECASE,
)
_NUMBER_RANGE = re.compile(
    r"(?i)\b(?:between\s+(-?\d{1,24}(?:\.\d{1,12})?)\s+and\s+"
    r"(-?\d{1,24}(?:\.\d{1,12})?)|from\s+"
    r"(-?\d{1,24}(?:\.\d{1,12})?)\s+to\s+"
    r"(-?\d{1,24}(?:\.\d{1,12})?))\b"
)
_QUANTITY_UNIT = (
    r"rows?|columns?|files?|items?|sheets?|tables?|records?|events?|tasks?|"
    r"stocks?|digits?|decimal\s+places?|ioctls?|outputs?|artifacts?"
)
_QUANTITY_CONSTRAINT = re.compile(
    rf"(?i)\b(?P<operator>exactly|at\s+least|at\s+most|no\s+more\s+than|"
    rf"no\s+less\s+than|all)\s+(?P<value>\d{{1,9}})\s+"
    rf"(?P<unit>{_QUANTITY_UNIT})\b"
)
_TOTAL_QUANTITY_CONSTRAINT = re.compile(
    rf"(?i)\b(?P<value>\d{{1,9}})\s+(?P<unit>{_QUANTITY_UNIT})\s+"
    rf"(?:in\s+)?total\b"
)
_BARE_QUANTITY_CONSTRAINT = re.compile(
    rf"(?i)\b(?P<value>\d{{1,9}})\s+"
    rf"(?:new\s+|different\s+)?(?:pivot\s+)?"
    rf"(?P<unit>{_QUANTITY_UNIT})\b"
)
_WORD_QUANTITY_CONSTRAINT = re.compile(
    rf"(?i)\b(?P<value>one|two|three|four|five|six|seven|eight|nine|ten|"
    rf"eleven|twelve|thirteen|fourteen|fifteen|sixteen)\s+"
    rf"(?:new\s+|different\s+)?(?:pivot\s+)?(?P<unit>{_QUANTITY_UNIT})\b"
)
_NUMBER_WORDS = {
    value: index
    for index, value in enumerate(
        (
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
        )
    )
}
_PER_ITEM_QUANTITY = re.compile(
    rf"(?i)\b(?:each|every)\s+(?P<per>[A-Za-z][A-Za-z_-]{{0,31}})\b"
    rf"[^\r\n.!?]{{0,96}}?\b(?:one|1)\s+(?:output\s+)?"
    rf"(?P<unit>{_QUANTITY_UNIT})\b"
)
_ONE_PER_ITEM_QUANTITY = re.compile(
    rf"(?i)\b(?:one|1)\s+(?:output\s+)?(?P<unit>{_QUANTITY_UNIT})\s+"
    rf"(?:for\s+each|per)\s+(?P<per>[A-Za-z][A-Za-z_-]{{0,31}})\b"
)
_SYMBOLIC_THRESHOLD = re.compile(
    r"(?i)(?P<metric>[A-Za-z][A-Za-z0-9 _./-]{0,63}?)\s*"
    r"(?P<operator><=|>=|<|>|≤|≥)\s*"
    r"(?P<value>-?\d{1,24}(?:\.\d{1,12})?)\s*"
    r"(?P<unit>%|ms|milliseconds?|seconds?|s|bytes?|KB|MB|GB|degrees?|°C|C)?\b"
)
_VERBAL_THRESHOLD = re.compile(
    r"(?i)(?P<metric>[A-Za-z][A-Za-z0-9 _./-]{0,63}?)\s+"
    r"(?:(?:must|should|needs?\s+to)\s+)?"
    r"(?:(?:be|stay|remain|reach)\s+)?"
    r"(?P<operator>at\s+most|at\s+least|less\s+than|smaller\s+than|"
    r"more\s+than|greater\s+than|no\s+more\s+than|no\s+less\s+than|"
    r"under|over|within)\s+"
    r"(?P<value>-?\d{1,24}(?:\.\d{1,12})?)\s*"
    r"(?P<unit>%|ms|milliseconds?|seconds?|s|bytes?|KB|MB|GB|degrees?|°C|C|"
    r"accuracy|precision|tolerance)?\b"
)
_QUALITY_PREDICATES = (
    ("syntactically_correct", re.compile(r"(?i)\bsyntactically\s+correct\b")),
    (
        "mathematically_consistent",
        re.compile(r"(?i)\b(?:mathematically\s+consistent|math\s+is\s+consistent)\b"),
    ),
    ("complete", re.compile(r"(?i)\bcomplete\b")),
    ("valid", re.compile(r"(?i)\bvalid\b")),
    ("consistent", re.compile(r"(?i)\bconsistent\b")),
)
_STRUCTURAL_REQUIREMENT_NOUN = re.compile(
    r"(?i)\b(?:nodes?|edges?|graphs?|frames?|masks?|columns?|headers?|fields?|"
    r"cells?|formulas?|rows?|keys?|sheets?|signatures?|functions?|routes?|"
    r"records?|entries|invoices?|paths?)\b"
)
_STRUCTURAL_REQUIREMENT_PREDICATE = re.compile(
    r"(?i)\b(?:must|should|required|make\s+sure|only\s+include|"
    r"same\s+(?:order|names?|format)|for\s+each|every|all|exactly|"
    r"append|return|reachable|targets?\s+must\s+exist|stores?|records?|"
    r"using|compute|replace)\b"
)
_OUTPUT_FORMAT_PREDICATE = re.compile(
    r"(?i)\b(?:only\s+write|only\s+include|format|1-based|one-based|"
    r"sorted\s+in|replace\s+each|same\s+(?:order|names?))\b"
)
_STRUCTURED_FIELD_CONTEXT = re.compile(
    r"(?i)\b(?:columns?|headers?|fields?|keys?|schema|json\s+structure|"
    r"append\s+(?:a\s+)?new\s+column)\b"
)
_QUOTED_PUBLIC_TOKEN = re.compile(
    r"`([A-Za-z_][A-Za-z0-9_.-]{0,127})`|"
    r"[\"“]([A-Za-z_][A-Za-z0-9_.-]{0,127})[\"”]"
)
_PUBLIC_OBJECTIVE_PREDICATE = re.compile(
    r"(?i)\b(?:find|calculate|compute|determine|identify|recover|analy[sz]e|"
    r"compare|count|classify|harmonize|optimi[sz]e|parse|detect|audit|"
    r"edit|update|convert|design|solve|serve)\b"
)
_PUBLIC_QUESTION = re.compile(
    r"(?i)(?:\b(?:what(?:['’]s|\s+is|\s+are)|which|how\s+many)\b"
    r"[^?]{0,480}\?|^\s*\d+[.)]\s+(?:what|which|how|list)\b)"
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


def _public_lines(value: str) -> tuple[tuple[int, int, str], ...]:
    """Return bounded lines with exact offsets into the authoritative request."""

    return tuple(
        (match.start(), match.end(), match.group(0))
        for match in re.finditer(r"(?m)^.*$", value)
    )


def _public_clauses(value: str) -> tuple[tuple[int, int, str], ...]:
    """Split prose without letting an input prefix hide a later output clause."""

    return tuple(
        (match.start(), match.end(), match.group(0))
        for match in re.finditer(r"[^\r\n;；]+", value)
        if match.group(0).strip()
    )


def _is_non_authoritative_public_text(value: str) -> bool:
    """Reject requirement-shaped data, examples, and conditional branches."""

    normalized = value.strip()
    if not normalized:
        return True
    if normalized.startswith(">") or _PUBLIC_CONDITIONAL_PREFIX.match(normalized):
        return True
    if (
        _PUBLIC_NON_AUTHORITATIVE_CONTEXT.search(normalized)
        or _PUBLIC_INPUT_CONTEXT.search(normalized)
        or _PUBLIC_CONDITIONAL_CLAUSE.search(normalized)
        or re.search(
            r"(?i)\b(?:the\s+)?(?:verifier|grader|scorer)\s+"
            r"(?:requires?|expects?|checks?|asserts?)\b",
            normalized,
        )
        or re.search(
            r"(?i)^\s*(?:the\s+)?(?:tool|api|library|command)\s+"
            r"(?:documentation|docs?|reference)\b",
            normalized,
        )
    ):
        return True
    if re.search(
        r"(?i)\b(?:literally\s+says?|quoted\s+data|treat\s+it\s+as\s+"
        r"(?:quoted\s+)?data)\b",
        normalized,
    ):
        return True
    if re.search(
        r"(?i)\b(?:input|note|log|document|text|message)\b[^\r\n]{0,96}"
        r"[\"“][^\"”]{0,256}\b(?:must|required|exactly|at\s+least|"
        r"at\s+most|do\s+not|never)\b[^\"”]{0,256}[\"”]",
        normalized,
    ):
        return True
    if re.search(
        r"(?i)[\"“][^\"”]{0,256}\bverifier\b[^\"”]{0,256}[\"”]",
        normalized,
    ):
        return True
    if re.search(
        r"(?i)[\"“][^\"”]{0,256}\b(?:output|result|report|artifact|file|"
        r"requirement)\b[^\"”]{0,128}\b(?:must|required|exactly|at\s+least|"
        r"at\s+most|do\s+not|never|accuracy|precision|rmse|f1)\b"
        r"[^\"”]{0,256}[\"”]",
        normalized,
    ):
        return True
    return False


def _non_authoritative_fenced_ranges(value: str) -> tuple[tuple[int, int], ...]:
    ranges = []
    for match in re.finditer(r"```[^\n]*\n.*?```", value, re.DOTALL):
        prefix = value[max(0, match.start() - 240) : match.start()]
        if not _authoritative_schema_prefix(prefix):
            ranges.append(match.span())
    return tuple(ranges)


def _position_in_ranges(position: int, ranges: Sequence[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in ranges)


def _has_public_requirement_anchor(value: str) -> bool:
    return bool(
        re.search(
            r"(?i)\b(?:must|should|shall|required|needs?\s+to|output|result|"
            r"report|deliverable|write|produce|generate|create|fill|contain|"
            r"include|keep|stay|remain|return)\b",
            value,
        )
    )


def _has_explicit_quantity_anchor(value: str, position: int) -> bool:
    prefix = value[max(0, position - 160) : position]
    return bool(
        re.search(
            r"(?i)(?:\b(?:output|result|report|deliverable)\b|"
            r"\b(?:must|should|shall|needs?\s+to)\b[^\r\n.!?]{0,96}$|"
            r"\b(?:generate|write|produce|return|give|keep|create|fill)\b"
            r"[^\r\n.!?]{0,96}$)",
            prefix,
        )
    )


def _authoritative_schema_prefix(value: str) -> bool:
    if _is_non_authoritative_public_text(value):
        return False
    return bool(
        re.search(
            r"(?i)(?:exact(?:ly)?\s+(?:public\s+)?interface|required\s+output|"
            r"required\s+(?:json|toml|yaml|yml|proto(?:buf)?)?\s*"
            r"(?:format|schema|structure)|"
            r"output\s+(?:format|schema|structure)|following\s+(?:format|structure)|"
            r"(?:follow(?:ing)?\s+(?:the\s+)?)?file\s+schema|must\s+contain)",
            value,
        )
    )


def _bounded_list_item(value: str) -> str:
    item = value.strip()
    code = re.match(r"^`([^`\r\n]+)`", item)
    bold = re.match(r"^\*\*([^*\r\n]+)\*\*", item)
    quoted = re.match(r"^[\"“]([^\"”\r\n]+)[\"”]", item)
    if code or bold or quoted:
        item = next(group for group in (code, bold, quoted) if group).group(1)
    else:
        item = re.split(r"\s*[:：]\s*", item, maxsplit=1)[0]
    return " ".join(item.strip().strip("`*_'\"“”").split())[
        :_MAX_PUBLIC_ENUMERATED_ITEM_TEXT
    ]


def _number_value(value: str) -> int | float | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed):
        return None
    if parsed.is_integer() and abs(parsed) <= 10**18:
        return int(parsed)
    return parsed


def _cell_coordinate(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"([A-Z]{1,3})(\d+)", value)
    if match is None:
        return None
    column = 0
    for character in match.group(1):
        column = column * 26 + (ord(character) - ord("A") + 1)
    row = int(match.group(2))
    return (row, column) if row > 0 else None


def _ordered_date_range(lower: str, upper: str) -> bool:
    try:
        return date.fromisoformat(lower) <= date.fromisoformat(upper)
    except ValueError:
        return False


def _threshold_operator(value: str) -> str:
    return {
        "<": "less_than",
        "less than": "less_than",
        "smaller than": "less_than",
        "under": "less_than",
        "<=": "less_than_or_equal",
        "≤": "less_than_or_equal",
        "at most": "less_than_or_equal",
        "no more than": "less_than_or_equal",
        ">": "greater_than",
        "more than": "greater_than",
        "greater than": "greater_than",
        "over": "greater_than",
        ">=": "greater_than_or_equal",
        "≥": "greater_than_or_equal",
        "at least": "greater_than_or_equal",
        "no less than": "greater_than_or_equal",
        "within": "within",
    }[value.casefold()]


def _threshold_metric(value: str, unit: str | None) -> str:
    compact = " ".join(value.strip(" ,;:，；：").split())
    for pattern, canonical in (
        (r"(?i)\bF1\s+score\b", "F1 score"),
        (r"(?i)\bRMSE\b", "RMSE"),
        (r"(?i)\baccuracy\b", "accuracy"),
        (r"(?i)\bprecision\b", "precision"),
        (r"(?i)\brecall\b", "recall"),
        (r"(?i)\b(?:file|artifact|output)\s+size\b", "size"),
    ):
        if re.search(pattern, compact):
            return canonical
    if unit and unit.casefold() in {"kb", "mb", "gb", "byte", "bytes"}:
        return "size"
    if unit and unit.casefold() in {"accuracy", "precision", "tolerance"}:
        return unit.casefold()
    compact = re.sub(
        r"(?i)^(?:and\s+)?(?:the\s+)?|\b(?:must|should|shall|needs?\s+to|"
        r"be|stay|remain|reach)$",
        "",
        compact,
    ).strip()
    return compact[-64:] or "value"


def _typed_numeric_thresholds(value: str) -> tuple[dict[str, object], ...]:
    results: list[dict[str, object]] = []
    seen: set[str] = set()
    statement = _bounded_public_excerpt(value)
    for pattern in (_SYMBOLIC_THRESHOLD, _VERBAL_THRESHOLD):
        for match in pattern.finditer(value):
            if re.match(
                r"(?i)\s*(?:different\s+(?:[A-Za-z_-]+\s+)?"
                r"(?:cases?|items?)|rows?|columns?|"
                r"files?|records?|events?|tasks?)\b",
                value[match.end() :],
            ):
                continue
            unit = match.group("unit") or None
            metric = _threshold_metric(match.group("metric"), unit)
            numeric_value = _number_value(match.group("value"))
            if numeric_value is None:
                continue
            expectation: dict[str, object] = {
                "statement": statement,
                "metric": metric,
                "operator": _threshold_operator(match.group("operator")),
                "value": numeric_value,
            }
            if unit:
                expectation["unit"] = unit
            identity = json.dumps(
                expectation,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            if identity not in seen:
                seen.add(identity)
                results.append(expectation)
    return tuple(results)


def _infer_public_obligation_hints_with_roles(
    request: str | None,
    *,
    workspace_path: str | os.PathLike[str],
) -> tuple[tuple[PublicObligationHint, ...], tuple[str, ...]]:
    """Conservatively derive a typed ledger from public task text only.

    The ledger is advisory task grounding.  It is never populated from a
    verifier, reward, historical failure, or solver-generated text, and it
    never becomes external scoring authority.
    """

    if not isinstance(request, str) or not request.strip():
        return (), ()
    bounded_request = request[:256_000]
    rows: list[tuple[str, str, object, str, str]] = []
    seen: set[str] = set()
    advisory_only_identities: set[str] = set()
    completion_required_default = True

    def add(kind: str, subject: str, expectation: object, excerpt: str) -> None:
        normalized_excerpt = _bounded_public_excerpt(excerpt)
        if not normalized_excerpt or not subject:
            return
        try:
            encoded_expectation = json.dumps(
                expectation,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError):
            return
        if len(encoded_expectation.encode("utf-8")) > _MAX_PUBLIC_EXPECTATION_BYTES:
            return
        identity = json.dumps(
            [kind, subject, expectation],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if identity in seen or len(rows) >= _MAX_PUBLIC_OBLIGATION_CANDIDATES:
            return
        seen.add(identity)
        row = (
            kind,
            subject[:512],
            expectation,
            normalized_excerpt,
            _public_fingerprint(normalized_excerpt),
        )
        if not completion_required_default:
            advisory_only_identities.add(
                json.dumps(
                    [row[0], row[1], row[2]],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        rows.append(row)

    def has_kind_subject(kind: str, subject: str) -> bool:
        return any(
            row_kind == kind and row_subject == subject
            for row_kind, row_subject, *_ in rows
        )

    blocked_fenced_ranges = _non_authoritative_fenced_ranges(bounded_request)
    public_lines = _public_lines(bounded_request)
    public_clauses = _public_clauses(bounded_request)

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
                    for match in re.finditer(
                        re.escape(item.display_path), bounded_request
                    )
                ),
                item.display_path,
            ),
        )

    for match in _FENCED_PUBLIC_SCHEMA.finditer(bounded_request):
        prefix = bounded_request[max(0, match.start() - 240) : match.start()]
        if (
            _EXPLICIT_SCHEMA_CONTEXT.search(prefix) is None
            or not _authoritative_schema_prefix(prefix)
        ):
            continue
        language, body = match.group(1).lower(), match.group(2)
        fields: list[tuple[str, str | None]] = []
        if language == "json":
            fields.extend(
                (name, None)
                for name in re.findall(r'"([^"\r\n]{1,128})"\s*:', body)
            )
        elif language == "toml":
            fields.extend(
                (name, None)
                for name in re.findall(
                    r"(?m)^\s*([A-Za-z_][A-Za-z0-9_.-]{0,127})\s*=",
                    body,
                )
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
                {
                    "required": True,
                    **({"declared_type": declared_type} if declared_type else {}),
                },
                excerpt,
            )

    for match in _FUNCTION_SIGNATURE.finditer(bounded_request):
        clause = _public_clause(bounded_request, match.start(), match.end())
        if _position_in_ranges(
            match.start(), blocked_fenced_ranges
        ) or _is_non_authoritative_public_text(clause):
            continue
        signature = " ".join(match.group(1).split())
        add(
            "function_signature",
            signature.split("(", 1)[0].removeprefix("async ").removeprefix("def "),
            {"signature": signature},
            clause,
        )

    code_spans = list(_COMMAND_CODE_SPAN.finditer(bounded_request))
    classified_code_spans: set[tuple[int, int]] = set()
    for match in code_spans:
        clause = _public_clause(bounded_request, match.start(), match.end())
        if _position_in_ranges(
            match.start(), blocked_fenced_ranges
        ) or _is_non_authoritative_public_text(clause):
            continue
        content = " ".join(match.group(1).split())
        signature = _CALLABLE_SIGNATURE.fullmatch(content)
        if signature is not None:
            name = signature.group(2)
            add(
                "function_signature",
                name,
                {"signature": signature.group(1)},
                clause,
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
                clause,
            )
            classified_code_spans.add(match.span())

    for match in _TYPED_IDENTIFIER.finditer(bounded_request):
        clause = _public_clause(bounded_request, match.start(), match.end())
        if (
            _position_in_ranges(match.start(), blocked_fenced_ranges)
            or _is_non_authoritative_public_text(clause)
            or re.search(
                r"(?i)\b(?:field|parameter|message|request|response|includes?|"
                r"contains?|returns?)\b",
                clause,
            )
            is None
        ):
            continue
        add(
            "structured_field",
            match.group(1),
            {"required": True, "declared_type": match.group(2).lower()},
            clause,
        )

    for match in _PORT_REQUIREMENT.finditer(bounded_request):
        clause = _public_clause(bounded_request, match.start(), match.end())
        if _position_in_ranges(
            match.start(), blocked_fenced_ranges
        ) or _is_non_authoritative_public_text(clause):
            continue
        port = int(match.group(1) or match.group(2))
        if 0 < port <= 65535:
            add(
                "service_port",
                str(port),
                {"port": port, "required": True},
                clause,
            )

    for match in code_spans:
        if match.span() in classified_code_spans:
            continue
        clause = _public_clause(bounded_request, match.start(), match.end())
        if _position_in_ranges(
            match.start(), blocked_fenced_ranges
        ) or _is_non_authoritative_public_text(clause):
            continue
        command = " ".join(match.group(1).split())
        if (
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", command)
            and Path(command).suffix.casefold()
            not in {
                ".csv",
                ".doc",
                ".docx",
                ".json",
                ".md",
                ".pdf",
                ".ppt",
                ".pptx",
                ".proto",
                ".toml",
                ".tsv",
                ".txt",
                ".yaml",
                ".yml",
            }
            and re.search(
                r"(?i)\b(?:field|key|column|sheet|property|attribute|header|member)\b",
                clause,
            )
        ):
            add(
                "structured_field",
                command,
                {"required": True},
                clause,
            )
            classified_code_spans.add(match.span())
            continue
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
        if (
            _position_in_ranges(match.start(), blocked_fenced_ranges)
            or _is_non_authoritative_public_text(excerpt)
        ):
            continue
        if (
            _THRESHOLD_CONTEXT.search(excerpt)
            and _COUNT_LIKE_THRESHOLD.search(excerpt) is None
        ):
            add(
                "numeric_threshold",
                _bounded_public_excerpt(excerpt),
                {"statement": _bounded_public_excerpt(excerpt)},
                excerpt,
            )
        if _NEGATIVE_CONSTRAINT.search(excerpt):
            add(
                "negative_constraint",
                _bounded_public_excerpt(excerpt),
                {"statement": _bounded_public_excerpt(excerpt)},
                excerpt,
            )
        if _LIVENESS_CONSTRAINT.search(excerpt):
            add(
                "process_liveness",
                "process_liveness",
                {"statement": _bounded_public_excerpt(excerpt)},
                excerpt,
            )

    # New grammar is intentionally appended after all legacy passes so existing
    # obligation ids retain their relative order whenever the old text yields
    # the same obligations.
    completion_required_default = False
    line_index = 0
    while line_index < len(public_lines):
        item_match = _PUBLIC_LIST_ITEM.match(public_lines[line_index][2])
        if item_match is None:
            line_index += 1
            continue
        group_start = line_index
        raw_items: list[str] = []
        while line_index < len(public_lines):
            current = _PUBLIC_LIST_ITEM.match(public_lines[line_index][2])
            if current is None:
                break
            item = _bounded_list_item(current.group("item"))
            if (
                item
                and item not in raw_items
                and len(raw_items) < _MAX_PUBLIC_ENUMERATED_ITEMS
            ):
                raw_items.append(item)
            line_index += 1
        if _position_in_ranges(
            public_lines[group_start][0], blocked_fenced_ranges
        ):
            continue
        parent = ""
        parent_line_index: int | None = None
        parent_index = group_start - 1
        while parent_index >= 0:
            candidate_index = parent_index
            candidate = public_lines[candidate_index][2].strip()
            parent_index -= 1
            if not candidate:
                continue
            if _PUBLIC_REQUIREMENT_PARENT.search(candidate):
                parent = candidate
                parent_line_index = candidate_index
            break
        if (
            raw_items
            and _PUBLIC_REQUIREMENT_PARENT.search(parent)
            and not _is_non_authoritative_public_text(parent)
        ):
            excerpt_start = public_lines[
                parent_line_index if parent_line_index is not None else group_start
            ][0]
            excerpt_end = public_lines[max(group_start, line_index - 1)][1]
            excerpt = bounded_request[excerpt_start:excerpt_end]
            add(
                "enumerated_set",
                _bounded_public_excerpt(parent),
                {
                    "items": raw_items[:_MAX_PUBLIC_ENUMERATED_ITEMS],
                    "required": True,
                },
                excerpt,
            )

    for index, (line_start, _line_end, line) in enumerate(public_lines[:-1]):
        if (
            "|" not in line
            or not _MARKDOWN_TABLE_SEPARATOR.match(public_lines[index + 1][2])
            or _position_in_ranges(line_start, blocked_fenced_ranges)
        ):
            continue
        parent = " ".join(
            candidate[2].strip()
            for candidate in public_lines[max(0, index - 3) : index]
            if candidate[2].strip()
        )
        if (
            not _PUBLIC_REQUIREMENT_PARENT.search(parent)
            or _is_non_authoritative_public_text(parent)
        ):
            continue
        fields = [
            " ".join(value.strip().strip("`*_\"").split())[:128]
            for value in line.strip().strip("|").split("|")[:16]
        ]
        for ordinal, field in enumerate((value for value in fields if value), start=1):
            if not has_kind_subject("structured_field", field):
                add(
                    "structured_field",
                    field,
                    {
                        "required": True,
                        "container": "table",
                        "ordinal": ordinal,
                    },
                    line,
                )

    sheet_pattern = re.compile(
        r"(?i)\b(?:workbook|output)\b[^\r\n.!?]{0,96}?\bsheet\s+"
        r"(?:named|called)\s+[`\"“]([^`\"”\r\n]{1,128})[`\"”]"
    )
    role_pattern = re.compile(r"(?i)^\s*(Rows?|Columns?|Values?)\s*[:：]\s*(\S.*?)\s*$")
    objective_count = 0
    question_items: list[str] = []
    for index, (line_start, _line_end, line) in enumerate(public_lines):
        if (
            _position_in_ranges(line_start, blocked_fenced_ranges)
            or _is_non_authoritative_public_text(line)
        ):
            continue
        sheet = sheet_pattern.search(line)
        if sheet is not None and not has_kind_subject(
            "structured_field", sheet.group(1)
        ):
            add(
                "structured_field",
                sheet.group(1),
                {"required": True, "container": "workbook", "role": "sheet"},
                line,
            )
        role = role_pattern.match(line)
        if role is None:
            continue
        context = " ".join(
            candidate[2].strip()
            for candidate in public_lines[max(0, index - 8) : index]
            if candidate[2].strip()
        )
        if (
            not re.search(r"(?i)\b(?:output|workbook|sheet|pivot|table)\b", context)
            or _is_non_authoritative_public_text(context)
        ):
            continue
        subject = " ".join(role.group(2).strip().strip("`*_\"").split())[:128]
        if subject and not has_kind_subject("structured_field", subject):
            add(
                "structured_field",
                subject,
                {
                    "required": True,
                    "container": "table",
                    "role": role.group(1).casefold()
                    if role.group(1).casefold().endswith("s")
                    else role.group(1).casefold() + "s",
                },
                line,
            )

    for line_start, _line_end, line in public_clauses:
        excerpt = line.strip()
        typed_thresholds = _typed_numeric_thresholds(excerpt)
        if (
            not excerpt
            or _position_in_ranges(line_start, blocked_fenced_ranges)
            or _is_non_authoritative_public_text(excerpt)
            or (
                not _has_public_requirement_anchor(excerpt)
                and not typed_thresholds
            )
        ):
            continue
        for threshold in typed_thresholds:
            add(
                "range_constraint",
                str(threshold.get("metric") or "value"),
                {"value_type": "number", **threshold},
                excerpt,
            )
        for match in _CELL_RANGE.finditer(excerpt):
            lower_cell = _cell_coordinate(match.group(1))
            upper_cell = _cell_coordinate(match.group(2))
            if (
                lower_cell is None
                or upper_cell is None
                or lower_cell[0] > upper_cell[0]
                or lower_cell[1] > upper_cell[1]
            ):
                continue
            add(
                "range_constraint",
                "cells",
                {
                    "value_type": "cell",
                    "lower": match.group(1),
                    "upper": match.group(2),
                    "inclusive": True,
                },
                excerpt,
            )
        for match in _DATE_RANGE.finditer(excerpt):
            if not _ordered_date_range(match.group(1), match.group(2)):
                continue
            add(
                "range_constraint",
                "dates",
                {
                    "value_type": "date",
                    "lower": match.group(1),
                    "upper": match.group(2),
                    "inclusive": True,
                },
                excerpt,
            )
        for match in _NUMBER_RANGE.finditer(excerpt):
            lower = match.group(1) or match.group(3)
            upper = match.group(2) or match.group(4)
            lower_value = _number_value(lower)
            upper_value = _number_value(upper)
            if (
                lower_value is None
                or upper_value is None
                or lower_value > upper_value
            ):
                continue
            prefix = excerpt[max(0, match.start() - 64) : match.start()]
            subject = _threshold_metric(prefix, None)
            add(
                "range_constraint",
                subject,
                {
                    "value_type": "number",
                    "lower": lower_value,
                    "upper": upper_value,
                    "inclusive": True,
                },
                excerpt,
            )

        for match in _QUANTITY_CONSTRAINT.finditer(excerpt):
            operator = match.group("operator").casefold()
            operator = {
                "at least": "at_least",
                "no less than": "at_least",
                "at most": "at_most",
                "no more than": "at_most",
                "all": "exactly",
                "exactly": "exactly",
            }[operator]
            unit = " ".join(match.group("unit").casefold().split())
            add(
                "quantity_constraint",
                unit,
                {
                    "operator": operator,
                    "value": int(match.group("value")),
                    "unit": unit,
                },
                excerpt,
            )
        for match in _TOTAL_QUANTITY_CONSTRAINT.finditer(excerpt):
            unit = " ".join(match.group("unit").casefold().split())
            add(
                "quantity_constraint",
                unit,
                {
                    "operator": "exactly",
                    "value": int(match.group("value")),
                    "unit": unit,
                },
                excerpt,
            )
        for match in _BARE_QUANTITY_CONSTRAINT.finditer(excerpt):
            prefix = excerpt[max(0, match.start() - 32) : match.start()]
            if (
                not _has_explicit_quantity_anchor(excerpt, match.start())
                or re.search(
                    r"(?i)(?:exactly|at\s+least|at\s+most|no\s+more\s+than|"
                    r"no\s+less\s+than|all|each|every)\s*$",
                    prefix,
                )
            ):
                continue
            unit = " ".join(match.group("unit").casefold().split())
            add(
                "quantity_constraint",
                unit,
                {
                    "operator": "exactly",
                    "value": int(match.group("value")),
                    "unit": unit,
                },
                excerpt,
            )
        for match in _WORD_QUANTITY_CONSTRAINT.finditer(excerpt):
            prefix = excerpt[max(0, match.start() - 96) : match.start()]
            suffix = excerpt[match.end() : match.end() + 48]
            if (
                not _has_explicit_quantity_anchor(excerpt, match.start())
                or re.search(r"(?i)\b(?:each|every)\b", prefix)
                or re.match(r"(?i)\s+(?:for\s+each|per)\b", suffix)
            ):
                continue
            unit = " ".join(match.group("unit").casefold().split())
            add(
                "quantity_constraint",
                unit,
                {
                    "operator": "exactly",
                    "value": _NUMBER_WORDS[match.group("value").casefold()],
                    "unit": unit,
                },
                excerpt,
            )
        for match in _PER_ITEM_QUANTITY.finditer(excerpt):
            unit = " ".join(match.group("unit").casefold().split())
            add(
                "quantity_constraint",
                unit,
                {
                    "operator": "per_item",
                    "value": 1,
                    "unit": unit,
                    "per": match.group("per").casefold(),
                },
                excerpt,
            )
        for match in _ONE_PER_ITEM_QUANTITY.finditer(excerpt):
            unit = " ".join(match.group("unit").casefold().split())
            add(
                "quantity_constraint",
                unit,
                {
                    "operator": "per_item",
                    "value": 1,
                    "unit": unit,
                    "per": match.group("per").casefold(),
                },
                excerpt,
            )

        specific_consistency = bool(
            re.search(
                r"(?i)\b(?:mathematically\s+consistent|"
                r"math\s+is\s+consistent)\b",
                excerpt,
            )
        )
        for predicate, pattern in _QUALITY_PREDICATES:
            if predicate == "consistent" and specific_consistency:
                continue
            quality_match = pattern.search(excerpt)
            if quality_match is None:
                continue
            quality_prefix = excerpt[
                max(0, quality_match.start() - 160) : quality_match.start()
            ]
            target_match = re.search(
                r"(?i)\b(output|result|report|artifact|file|files|plan|table|"
                r"schema|json|data)\b[^.]{0,96}\b"
                r"(?:must|should|shall|needs?\s+to|is|are|be)\b[^.]{0,64}$",
                quality_prefix,
            )
            if target_match is None:
                continue
            add(
                "quality_constraint",
                target_match.group(1).casefold(),
                {"predicate": predicate, "required": True},
                excerpt,
            )

    for index, (line_start, _line_end, line) in enumerate(public_lines):
        excerpt = line.strip()
        if (
            not excerpt
            or _position_in_ranges(line_start, blocked_fenced_ranges)
            or _is_non_authoritative_public_text(excerpt)
        ):
            continue
        nearby_context = " ".join(
            candidate[2].strip()
            for candidate in public_lines[max(0, index - 5) : index]
            if candidate[2].strip()
        )
        requirement_context = bool(
            _has_public_requirement_anchor(excerpt)
            or _PUBLIC_QUESTION.search(excerpt)
            or (
                _PUBLIC_OBJECTIVE_PREDICATE.search(excerpt)
                and re.search(
                    r"(?i)\b(?:your\s+task|task\s+is|goal\s+is|"
                    r"you\s+need\s+to|please)\b",
                    excerpt,
                )
            )
            or re.search(
                r"(?i)^\s*(?:append|add|copy|write|save|return|include|recover)\b",
                excerpt,
            )
            or _PUBLIC_REQUIREMENT_PARENT.search(nearby_context)
            or re.search(
                r"(?i)\b(?:create|add|write|save|format|structure)\b",
                nearby_context,
            )
        )
        if not requirement_context:
            continue

        structured_field_context = bool(
            _STRUCTURED_FIELD_CONTEXT.search(excerpt)
            or (
                _COMMAND_CODE_SPAN.search(excerpt)
                and _STRUCTURED_FIELD_CONTEXT.search(nearby_context)
                and re.search(
                    r"(?i)\b(?:output|results?|write|save|return|produce)\b",
                    nearby_context,
                )
            )
        )
        if structured_field_context:
            structure_text = f"{nearby_context} {excerpt}"
            container = (
                "workbook"
                if re.search(r"(?i)\b(?:workbook|sheet|excel)\b", structure_text)
                else "table"
                if re.search(r"(?i)\b(?:columns?|headers?|csv)\b", structure_text)
                else "structured_output"
            )
            for token_match in _QUOTED_PUBLIC_TOKEN.finditer(excerpt):
                field = next(
                    value for value in token_match.groups() if isinstance(value, str)
                )
                if field.casefold() in {
                    "json",
                    "csv",
                    "xlsx",
                    "data",
                    "output",
                    "input",
                } or Path(field).suffix.casefold() in {
                    ".csv",
                    ".doc",
                    ".docx",
                    ".json",
                    ".pdf",
                    ".ppt",
                    ".pptx",
                    ".toml",
                    ".tsv",
                    ".txt",
                    ".yaml",
                    ".yml",
                }:
                    continue
                if not has_kind_subject("structured_field", field):
                    add(
                        "structured_field",
                        field,
                        {"required": True, "container": container},
                        excerpt,
                    )
            for code_span in _COMMAND_CODE_SPAN.finditer(excerpt):
                candidates = [
                    item.strip()
                    for item in code_span.group(1).split(",")
                    if item.strip()
                ]
                if not 1 < len(candidates) <= 16 or any(
                    re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", item)
                    is None
                    for item in candidates
                ):
                    continue
                for field in candidates:
                    if not has_kind_subject("structured_field", field):
                        add(
                            "structured_field",
                            field,
                            {"required": True, "container": container},
                            excerpt,
                        )

        structural_noun = _STRUCTURAL_REQUIREMENT_NOUN.search(excerpt)
        if structural_noun is not None and _STRUCTURAL_REQUIREMENT_PREDICATE.search(
            excerpt
        ):
            add(
                "structural_constraint",
                structural_noun.group(0).casefold(),
                {"statement": _bounded_public_excerpt(excerpt)},
                excerpt,
            )
        if _OUTPUT_FORMAT_PREDICATE.search(excerpt):
            add(
                "format_constraint",
                "output_format",
                {"statement": _bounded_public_excerpt(excerpt)},
                excerpt,
            )
        objective_match = _PUBLIC_OBJECTIVE_PREDICATE.search(excerpt)
        question_match = _PUBLIC_QUESTION.search(excerpt)
        if (
            question_match is not None
            and excerpt not in question_items
            and len(question_items) < 8
        ):
            question_items.append(_bounded_public_excerpt(excerpt))
        if objective_count < 4 and (
            question_match is not None
            or (
                objective_match is not None
                and (
                    requirement_context
                    or re.search(
                        r"(?i)\b(?:your\s+task|task\s+is|goal\s+is|"
                        r"you\s+need\s+to|please)\b",
                        excerpt,
                    )
                    or re.match(
                        r"(?i)^\s*(?:find|calculate|compute|determine|identify|"
                        r"recover|analy[sz]e|compare|count|classify|harmonize|"
                        r"optimi[sz]e|parse|detect|audit|edit|update|convert|"
                        r"design|solve)\b",
                        excerpt,
                    )
                )
            )
        ):
            subject = (
                objective_match.group(0).casefold()
                if objective_match is not None
                else "question"
            )
            add(
                "answer_constraint",
                subject,
                {"statement": _bounded_public_excerpt(excerpt)},
                excerpt,
            )
            objective_count += 1

    if len(question_items) > 1:
        add(
            "answer_set_constraint",
            "questions",
            {"items": question_items, "required": True},
            " ".join(question_items),
        )

    selected_rows = rows[:_MAX_PUBLIC_OBLIGATIONS]
    new_kinds = {
        "answer_constraint",
        "answer_set_constraint",
        "enumerated_set",
        "format_constraint",
        "quality_constraint",
        "quantity_constraint",
        "range_constraint",
        "structural_constraint",
    }
    if (
        len(rows) > _MAX_PUBLIC_OBLIGATIONS
        and not any(row[0] in new_kinds for row in selected_rows)
    ):
        supplements = [row for row in rows[28:] if row[0] in new_kinds][:4]
        if supplements:
            selected_rows = [*rows[:28], *supplements]

    hints = tuple(
        PublicObligationHint(
            obligation_id=f"public-obligation-{index}",
            kind=kind,
            subject=subject,
            expectation=expectation,
            source_excerpt=excerpt,
            source_fingerprint=source_fingerprint,
        )
        for index, (
            kind,
            subject,
            expectation,
            excerpt,
            source_fingerprint,
        ) in enumerate(selected_rows, start=1)
    )
    completion_obligation_ids = tuple(
        hint.obligation_id
        for hint, row in zip(hints, selected_rows)
        if json.dumps(
            [row[0], row[1], row[2]],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        not in advisory_only_identities
    )
    return hints, completion_obligation_ids


def infer_public_obligation_hints(
    request: str | None,
    *,
    workspace_path: str | os.PathLike[str],
) -> tuple[PublicObligationHint, ...]:
    """Return all public obligations, including reviewer-only risk targets."""

    hints, _completion_obligation_ids = _infer_public_obligation_hints_with_roles(
        request,
        workspace_path=workspace_path,
    )
    return hints


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
    bounded_request = request[:256_000]
    blocked_fenced_ranges = _non_authoritative_fenced_ranges(bounded_request)
    discovered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pattern in _PUBLIC_DELIVERABLE_PATTERNS:
        for match in pattern.finditer(bounded_request):
            if (
                _position_in_ranges(match.start(), blocked_fenced_ranges)
                or _is_non_authoritative_public_text(
                    _public_clause(bounded_request, match.start(), match.end())
                )
                or _is_described_runtime_side_effect(request, match)
            ):
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
        for match in pattern.finditer(bounded_request):
            if (
                _position_in_ranges(match.start(), blocked_fenced_ranges)
                or _is_non_authoritative_public_text(
                    _public_clause(bounded_request, match.start(), match.end())
                )
                or _is_described_runtime_side_effect(request, match)
            ):
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
    for match in _PUBLIC_NAMED_FILE_IN_DIRECTORY.finditer(bounded_request):
        if (
            _position_in_ranges(match.start(), blocked_fenced_ranges)
            or _is_non_authoritative_public_text(
                _public_clause(bounded_request, match.start(), match.end())
            )
            or _is_described_runtime_side_effect(request, match)
        ):
            continue
        filename = next(
            (
                value.strip()
                for value in (
                    match.group("backtick_file"),
                    match.group("single_file"),
                    match.group("double_file"),
                    match.group("plain_file"),
                )
                if isinstance(value, str) and value.strip()
            ),
            "",
        )
        directory = next(
            (
                value.strip()
                for value in (
                    match.group("backtick_dir"),
                    match.group("single_dir"),
                    match.group("double_dir"),
                    match.group("plain_dir"),
                )
                if isinstance(value, str) and value.strip()
            ),
            "",
        )
        candidate = Path(filename)
        resolved_directory = _resolve_public_deliverable(
            directory,
            workspace_path=workspace_path,
            kind="directory",
        )
        if (
            resolved_directory is None
            or candidate.is_absolute()
            or candidate.name != filename
            or filename in {"", ".", ".."}
            or "." not in filename
        ):
            continue
        joined = str(Path(resolved_directory[0]) / filename)
        key = os.path.normcase(joined)
        if key in seen:
            continue
        seen.add(key)
        discovered.append((joined, joined if Path(directory).is_absolute() else filename))
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
        bounded_request
    ):
        if (
            _position_in_ranges(directory_match.start(), blocked_fenced_ranges)
            or _is_non_authoritative_public_text(
                _public_clause(
                    bounded_request,
                    directory_match.start(),
                    directory_match.end(),
                )
            )
            or _is_described_runtime_side_effect(request, directory_match)
        ):
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
    obligations, completion_obligation_ids = _infer_public_obligation_hints_with_roles(
        request,
        workspace_path=workspace_path,
    )
    if obligations:
        context.context_info["public_task_contract"] = {
            "schema_version": PUBLIC_TASK_CONTRACT_SCHEMA,
            "authority": PUBLIC_DELIVERABLE_AUTHORITY,
            "source": "public_task_text",
            "request_fingerprint": _public_fingerprint(str(request or "")),
            "completion_obligation_ids": list(completion_obligation_ids),
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
