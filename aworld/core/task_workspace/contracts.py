"""Compile literal public delivery requirements with their exact provenance.

No model-authored plan, task name, or hidden verifier input is consulted.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

_FENCED_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_URL_RE = re.compile(r"https?://[^\s`\"'<>]+", re.IGNORECASE)
_PATH_RE = re.compile(
    r"(?:"
    r"(?:~/|/|\.\.?/)[^\s`\"'>)，,，。！？；：、”’》」】]+"
    r"|"
    # A concrete filename such as ``report.xlsx`` or ``out/result.json``.
    # Requiring a suffix deliberately excludes ambiguous words/directories.
    r"(?:[A-Za-z0-9][A-Za-z0-9._-]*/)*"
    r"[A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z0-9][A-Za-z0-9._-]*"
    r")"
)
_OUTPUT_CUE_RE = re.compile(
    r"(?:"
    r"(?:保存|另存|写入|输出|导出|生成|创建|存储|放置|提交)"
    r"[^。！？\n]{0,36}?(?:到|至|为|在|路径(?:是|为)?|目录(?:是|为)?)"
    r"|"
    r"\b(?:save|write|export|generate|create|produce|store|place|submit)\b"
    r"[^.!?\n]{0,36}?(?:\bto\b|\bat\b|\bas\b|\bunder\b|\binto\b|\bin\b)"
    r")",
    re.IGNORECASE,
)
_DIRECT_OUTPUT_CUE_RE = re.compile(
    r"(?:"
    r"(?:保存|另存|写入|输出|导出|生成|创建|存储|放置|提交)(?:为|到|至|在)?"
    r"|"
    r"\b(?:save|write|export|generate|create|produce|store|place|submit)\b"
    r"(?:\s+(?:me|us))?"
    r"(?:\s+(?:(?:the|a|an|final|resulting)\s+){0,3})?"
    r")\s*$",
    re.IGNORECASE,
)
_OUTPUT_VERB_RE = re.compile(
    r"(?:保存|另存|写入|输出|导出|生成|创建|存储|放置|提交)"
    r"|\b(?:save|write|export|generate|create|produce|store|place|submit)\b",
    re.IGNORECASE,
)
_CONDITIONAL_CONTEXT_RE = re.compile(
    r"(?:"
    r"\b(?:if|unless|otherwise|in\s+case)\b"
    r"|\b(?:when|where)\s+(?:needed|required|appropriate)\b"
    r"|\bas\s+needed\b"
    r"|(?:如果|若(?:是|需|有)?|否则|视情况)"
    r")",
    re.IGNORECASE,
)
_NON_DIRECT_ACTION_CONTEXT_RE = re.compile(
    r"(?:"
    r"\b(?:do\s+not|don't|not|never|no\s+need\s+to|without)\b"
    r"|\b(?:for\s+example|e\.g\.|such\s+as)\b"
    r"|\b(?:you|we|i)\s+(?:may|might|should|could)\b"
    r"|\b(?:recommend|advise|suggest)\b"
    r"|\b(?:how\s+to|commands?\s+to|instructions?\s+(?:to|for)|steps?\s+to)\b"
    r"|(?:不要|请勿|别|无需|不必|例如|举例|建议|是否|能否|可否|会不会|"
    r"应该|应不应该|要不要|可以)"
    r"|(?:如何|怎样|怎么|命令|指令|步骤|方法)"
    r")",
    re.IGNORECASE,
)
_TARGET_PREPOSITION_RE = re.compile(
    r"^\s*(?:to|as|into|at|under|in)\b", re.IGNORECASE
)
_TARGET_PREPOSITION_ANY_RE = re.compile(
    r"\b(?:to|as|into|at|under|in)\b", re.IGNORECASE
)
_ALTERNATIVE_RE = re.compile(
    r"\b(?:either|or)\b|(?:或者|或是|二选一|或)", re.IGNORECASE
)
_SENTENCE_BOUNDARY_RE = re.compile(
    r"(?:[!?。！？;；]\s*|\.\s+(?=[A-Z\u3400-\u9fff]))"
)
_SEQUENCE_BOUNDARY_RE = re.compile(
    r"(?:,\s*)?(?:\b(?:and\s+then|then|next)\b|然后|随后|接着)\s*",
    re.IGNORECASE,
)
_DIRECT_REQUEST_ACTION_RE = re.compile(
    r"^\s*(?:please\s+)?(?:can|could|would|will)\s+you\s+(?:please\s+)?"
    r"(?:carefully\s+|directly\s+|first\s+|now\s+|also\s+)*"
    r"(?:analy[sz]e|inspect|read|open|review|process|convert|transform|extract|"
    r"calculate|compute|clean|merge|build|save|write|export|generate|create|"
    r"produce|store|place|submit)\b",
    re.IGNORECASE,
)
_IMPERATIVE_ROOT_RE = re.compile(
    r"^\s*(?:(?:[-*]|\d+[.)])\s*)?(?:please\s+)?"
    r"(?:carefully\s+|directly\s+|first\s+|now\s+|also\s+)*"
    r"(?:analy[sz]e|inspect|read|open|review|process|convert|transform|extract|"
    r"calculate|compute|clean|merge|build|save|write|export|generate|create|"
    r"produce|store|place|submit)\b"
    r"|^\s*(?:请\s*)?(?:仔细|直接|先|再)?\s*"
    r"(?:读取|打开|检查|分析|处理|转换|提取|计算|清理|合并|保存|另存|写入|"
    r"输出|导出|生成|创建|存储|放置|提交|把|将)",
    re.IGNORECASE,
)
_ROOT_OUTPUT_PREFIX_RE = re.compile(
    r"^\s*(?:(?:[-*]|\d+[.)])\s*)?(?:please\s+)?"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?"
    r"(?:carefully\s+|directly\s+|first\s+|now\s+|also\s+)*$",
    re.IGNORECASE,
)
_SEQUENCED_OUTPUT_PREFIX_RE = re.compile(
    r"(?:\b(?:and\s+then|then|next)\b|然后|随后|接着)\s*$", re.IGNORECASE
)
_COORDINATED_OUTPUT_PREFIX_RE = re.compile(
    r"(?:\band\b|并)\s*(?:please\s+)?$", re.IGNORECASE
)
_CHINESE_DIRECT_OUTPUT_PREFIX_RE = re.compile(
    r"^\s*(?:请\s*)?(?:(?:仔细|直接|先|再)\s*)?"
    r"(?:(?:把|将)[^,，;；:：\"'`“”‘’《》「」【】]{1,48})?\s*$"
)
_CHINESE_COMPOUND_OUTPUT_PREFIX_RE = re.compile(
    r"^\s*请\s*(?:读取|打开|检查|分析|处理|转换|提取|计算|清理|合并)"
    r"[^;；:：\"'`“”‘’《》「」【】]{0,64}(?:并将|并把)"
    r"[^;；:：\"'`“”‘’《》「」【】]{0,48}$"
)
_META_OUTPUT_SUFFIX_RE = re.compile(
    r"^\s*(?:[:：]|(?:这|该)(?:句|句话|短语|命令|指令|示例)|"
    r"(?:的|这个|该)?(?:行为|含义|利弊|方案)|"
    r"(?:the|this)\s+(?:sentence|phrase|command|instruction|example)\b)",
    re.IGNORECASE,
)
_META_ACTION_CONTEXT_RE = re.compile(
    r"(?:"
    r"\b(?:whether|why)\b"
    r"|\b(?:this|the|a)\s+(?:request|instruction|claim|hypothetical)\b"
    r"|\b(?:documentation|text|statement)\s+(?:saying|stating|that)\b"
    r"|(?:这个|该)(?:请求|指令|说法|假设)"
    r"|(?:是否|为什么)"
    r")",
    re.IGNORECASE,
)


def _spans_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _looks_like_concrete_path(value: str) -> bool:
    if not value or any(marker in value for marker in ("*", "?", "[", "]", "{", "}")):
        return False
    if value.startswith(("/", "./", "../", "~/")):
        return True
    # Bare relative paths are accepted only when the final component is an
    # unambiguous filename.  The output-cue check below supplies the semantic
    # evidence that this is a target rather than an input mention.
    return bool(Path(value).suffix) and ":" not in value


def _last_output_cue(prefix: str) -> re.Match[str] | None:
    cue_matches = [
        *list(_OUTPUT_CUE_RE.finditer(prefix)),
        *list(_DIRECT_OUTPUT_CUE_RE.finditer(prefix)),
        *list(_OUTPUT_VERB_RE.finditer(prefix)),
    ]
    if not cue_matches:
        return None
    cue = max(cue_matches, key=lambda match: match.start())
    return cue


def _is_user_directed_output_clause(prefix: str, cue: re.Match[str]) -> bool:
    """Accept only high-confidence imperative or second-person requests."""

    sentence_boundaries = list(_SENTENCE_BOUNDARY_RE.finditer(prefix))
    sentence_prefix = (
        prefix[sentence_boundaries[-1].end() :]
        if sentence_boundaries
        else prefix
    )
    sentence_cue_start = cue.start() - (len(prefix) - len(sentence_prefix))
    if sentence_cue_start < 0:
        return False
    before_cue = sentence_prefix[:sentence_cue_start]
    if _CONDITIONAL_CONTEXT_RE.search(before_cue):
        return False

    sequence_boundaries = list(_SEQUENCE_BOUNDARY_RE.finditer(before_cue))
    action_prefix = (
        sentence_prefix[sequence_boundaries[-1].end() :]
        if sequence_boundaries
        else sentence_prefix
    )
    if _NON_DIRECT_ACTION_CONTEXT_RE.search(action_prefix):
        return False
    if _META_ACTION_CONTEXT_RE.search(action_prefix):
        return False

    cue_prefix = sentence_prefix[:sentence_cue_start]
    if _ROOT_OUTPUT_PREFIX_RE.fullmatch(cue_prefix):
        return True
    if re.search(r"[\u3400-\u9fff]", cue_prefix) and (
        _CHINESE_DIRECT_OUTPUT_PREFIX_RE.fullmatch(cue_prefix)
    ):
        return True
    if _SEQUENCED_OUTPUT_PREFIX_RE.search(cue_prefix):
        return True
    if _COORDINATED_OUTPUT_PREFIX_RE.search(cue_prefix) and (
        _DIRECT_REQUEST_ACTION_RE.search(sentence_prefix)
        or _IMPERATIVE_ROOT_RE.search(sentence_prefix)
    ):
        return True
    return _CHINESE_COMPOUND_OUTPUT_PREFIX_RE.fullmatch(cue_prefix) is not None


def _candidate_sentence(line: str, span: tuple[int, int]) -> tuple[str, str]:
    """Return sentence text and the text after one path within that sentence."""

    boundaries = list(_SENTENCE_BOUNDARY_RE.finditer(line))
    start = max(
        (boundary.end() for boundary in boundaries if boundary.end() <= span[0]),
        default=0,
    )
    end = min(
        (boundary.start() for boundary in boundaries if boundary.start() >= span[1]),
        default=len(line),
    )
    return line[start:end], line[span[1] : end]


def _candidate_is_governed_target(
    line: str, path_match: re.Match[str], cue: re.Match[str]
) -> bool:
    normalized_path = path_match.group(0).rstrip(".,:;!?")
    normalized_span = (path_match.start(), path_match.start() + len(normalized_path))
    sentence, suffix = _candidate_sentence(line, normalized_span)
    if _ALTERNATIVE_RE.search(sentence):
        return False
    if any(
        mark in sentence
        for mark in ('"', "`", "“", "”", "‘", "’", "《", "》", "「", "」", "【", "】")
    ):
        return False
    if _META_OUTPUT_SUFFIX_RE.search(suffix):
        return False
    # A conditional written after the filename still makes artifact creation
    # optional, so inference must fail closed.
    if _CONDITIONAL_CONTEXT_RE.search(suffix):
        return False
    # In "save source.csv to output.csv", the first path is an object/source,
    # not the governed destination.  The later path is evaluated separately.
    later_paths = list(_PATH_RE.finditer(suffix))
    if later_paths:
        before_last_later_path = suffix[: later_paths[-1].start()]
        if _TARGET_PREPOSITION_ANY_RE.search(before_last_later_path):
            return False
    governed_prefix = line[cue.end() : path_match.start()]
    prior_paths = list(_PATH_RE.finditer(governed_prefix))
    if prior_paths:
        after_prior_path = governed_prefix[prior_paths[-1].end() :]
        if _TARGET_PREPOSITION_RE.search(after_prior_path) is None:
            return False
    return True


def infer_declared_output_paths(request: str | None) -> tuple[str, ...]:
    """Extract concrete paths governed by an explicit output-writing cue.

    Matching is line-local and the cue must precede the path.  This intentionally
    favors false negatives over blocking a task on an input or merely mentioned
    path.
    """

    natural_text = _FENCED_CODE_BLOCK_RE.sub(" ", request or "")
    candidates: list[str] = []
    seen: set[str] = set()
    for raw_line in natural_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        url_spans = [match.span() for match in _URL_RE.finditer(line)]
        for path_match in _PATH_RE.finditer(line):
            if any(_spans_overlap(path_match.span(), span) for span in url_spans):
                continue
            candidate = path_match.group(0).rstrip(".,:;!?")
            if not _looks_like_concrete_path(candidate):
                continue
            prefix = line[: path_match.start()]
            cue_match = _last_output_cue(prefix)
            if cue_match is None or not _is_user_directed_output_clause(
                prefix, cue_match
            ):
                continue
            if not _candidate_is_governed_target(line, path_match, cue_match):
                continue
            key = candidate.casefold()
            if key not in seen:
                seen.add(key)
                candidates.append(candidate)
    return tuple(candidates)




DELIVERY_SCHEMA = "aworld.delivery-contract/v1"
_INPUT_VERB_RE = re.compile(r"\b(?:read|load|open|inspect|analy[sz]e|parse|consume|use)\b|读取|加载|分析|解析|使用", re.I)
_TRANSFER_VERB_RE = re.compile(r"\b(?:convert|transform|copy)\b|转换|复制", re.I)
_DECLARATIVE_OUTPUT_RE = re.compile(
    r"^\s*(?:the\s+|your\s+)?(?:final\s+)?(?:output|result|deliverable)(?:\s+(?:file|artifact))?"
    r"\s+(?:must|shall|should)\s+(?:be\s+)?(?:(?:written|saved|stored|placed|available)\s+)?(?:to|at|in)?\s*$"
    r"|^\s*(?:最终)?(?:输出|结果|交付)(?:文件|产物)?(?:必须|应当)(?:保存|写入|放置)?(?:到|在|为)\s*$", re.I,
)
_INPUT_LABEL_RE = re.compile(r"\b(?:input|source)(?:\s+(?:file|data|path))?\s*[:=]?\s*$|(?:输入|源)(?:文件|数据|路径)?\s*[:：]?\s*$", re.I)
_IMMUTABLE_PREFIX_RE = re.compile(r"\b(?:do\s+not|don't|never)\s+(?:modify|change|overwrite|delete|edit)\s*$|(?:不要|不得|禁止|请勿)(?:修改|改变|覆盖|删除)\s*$", re.I)
_IMMUTABLE_SUFFIX_RE = re.compile(r"^\s*(?:must\s+)?(?:remain|stay|be\s+kept)\s+(?:unchanged|unmodified)|^\s*(?:保持不变|不得修改|不能修改)", re.I)
_KEEP_PREFIX_RE = re.compile(r"\b(?:keep|leave|preserve)\s*$|(?:保持|保留)\s*$", re.I)
_KEEP_SUFFIX_RE = re.compile(r"^\s*(?:unchanged|unmodified|intact)\b|^\s*(?:不变|原样)", re.I)
_FORBIDDEN_OUTPUT_PREFIX_RE = re.compile(
    r"\b(?:do\s+not|don't|never)\s+(?:write|create|save|export|generate|produce|submit)(?:\s+(?:to|a|the|file|output|result))*\s*$"
    r"|(?:不要|不得|禁止|请勿)(?:写入|创建|保存|导出|生成|提交)(?:到)?\s*$", re.I,
)
_SIZE_RE = re.compile(
    r"(?P<op>at\s+most|no\s+more\s+than|at\s+least|no\s+less\s+than|less\s+than|under|more\s+than|"
    r"maximum(?:\s+size)?(?:\s+of)?|minimum(?:\s+size)?(?:\s+of)?|<=|>=|≤|≥|<|>|不超过|小于等于|不小于|至少|最多|小于|大于)"
    r"\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>GiB|MiB|KiB|GB|MB|KB|bytes?|B|字节)(?![A-Za-z])", re.I,
)


class _PathMention:
    def __init__(self, text: str, start: int, end: int):
        self.text, self._start, self._end = text, start, end

    def group(self, index=0):
        return self.text

    def start(self):
        return self._start

    def end(self):
        return self._end


def _mentions(clause: str):
    """Recognize quoted literal paths while preserving original character offsets."""
    masked = list(clause)
    mentions = []
    occupied = []
    for match in re.finditer(r"([`\"])([^`\"\n]+)\1", clause):
        text = match.group(2)
        if not _looks_like_concrete_path(text):
            continue
        start, end = match.span(2)
        mentions.append(_PathMention(text, start, end))
        occupied.append(match.span())
        masked[match.start()] = masked[match.end() - 1] = " "
    urls = [match.span() for match in _URL_RE.finditer(clause)]
    for match in _PATH_RE.finditer(clause):
        if any(_spans_overlap(match.span(), span) for span in (*occupied, *urls)):
            continue
        value = match.group().rstrip(".,:;!?)。！？")
        if value:
            mentions.append(_PathMention(value, match.start(), match.start() + len(value)))
    return sorted(mentions, key=lambda item: item.start()), "".join(masked)


def _clauses(request: str):
    # Mask fenced examples without moving the source spans.
    text = _FENCED_CODE_BLOCK_RE.sub(lambda match: re.sub(r"[^\n]", " ", match.group()), request)
    offset = 0
    for line in text.splitlines(keepends=True):
        previous = 0
        for boundary in (*list(_SENTENCE_BOUNDARY_RE.finditer(line)), None):
            end = boundary.start() if boundary else len(line)
            segment = line[previous:end]
            start = previous + len(segment) - len(segment.lstrip())
            stop = end - (len(segment) - len(segment.rstrip()))
            if stop > start:
                yield offset + start, offset + stop, line[start:stop]
            if boundary:
                previous = boundary.end()
        offset += len(line)


def _source(request: str, start: int, end: int, kind="public_requirement") -> dict:
    return {"kind": kind, "quote": request[start:end], "start": start, "end": end}


def _resolve_path(value: str, workspace: Path) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError("delivery paths must be non-empty strings")
    path = Path(value).expanduser()
    return os.path.abspath(workspace / path if not path.is_absolute() else path)


def _stable_id(prefix: str, value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return prefix + hashlib.sha256(raw).hexdigest()[:20]


def _check(path: str, kind: str, source: dict, **values) -> dict:
    definition = {"kind": kind, "path": path, **values}
    return {"id": _stable_id("delivery-check-", definition), **definition, "source": deepcopy(source)}


def _basic_checks(path: str, source: dict, clause: str, *, allow_sizes=True) -> list[dict]:
    result = [_check(path, "exists", source), _check(path, "regular_file", source)]
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        result.append(_check(path, "json", source))
    elif suffix == ".csv":
        result.append(_check(path, "csv", source))
    elif suffix == ".py":
        result.append(_check(path, "source_syntax", source, language="python"))
    if re.search(r"\bnon[- ]?empty\b|非空|不能为空", clause, re.I):
        result.append(_check(path, "nonempty", source))
    if allow_sizes:
        bounds = {}
        for match in _SIZE_RE.finditer(clause):
            multiplier = {"b": 1, "byte": 1, "bytes": 1, "字节": 1, "kb": 1000,
                          "mb": 1000**2, "gb": 1000**3, "kib": 1024, "mib": 1024**2, "gib": 1024**3}[match["unit"].lower()]
            number = Decimal(match["value"]) * multiplier
            operator = re.sub(r"\s+", " ", match["op"].lower())
            lower = operator.startswith("minimum") or operator in {"at least", "no less than", ">=", "≥", ">", "more than", "不小于", "至少", "大于"}
            strict = operator in {"<", ">", "less than", "under", "more than", "小于", "大于"}
            amount = int(number.to_integral_value(rounding=ROUND_CEILING)) if lower else int(number)
            if strict:
                amount = int(number) + 1 if lower else int(number.to_integral_value(rounding=ROUND_CEILING)) - 1
            key = "min_bytes" if lower else "max_bytes"
            bounds[key] = (max if lower else min)(bounds.get(key, amount), amount)
        if bounds:
            result.append(_check(path, "file_size", source, **bounds))
    return result


def _explicit_contract(request: str, workspace: Path, explicit: Mapping[str, Any]) -> dict:
    if set(explicit) - {"outputs", "inputs", "checks", "policy", "unresolved", "coverage_status", "schema_version", "source_hash"}:
        raise ValueError("unsupported explicit delivery contract field")
    outputs, inputs = [], []
    for index, item in enumerate(explicit.get("outputs", ())):
        item = {"path": item} if isinstance(item, str) else dict(item)
        path = _resolve_path(item["path"], workspace)
        source = {"kind": "caller_contract", "quote": None, "start": None, "end": None}
        checks = item.get("checks")
        if checks is None:
            checks = _basic_checks(path, source, "")
        else:
            prepared = []
            for definition in checks:
                definition = dict(definition)
                for key in ("path", "input", "same_rows_as"):
                    if definition.get(key) is not None:
                        definition[key] = _resolve_path(definition[key], workspace)
                kind = definition.pop("kind")
                definition.pop("source", None)
                check_path = definition.pop("path", path)
                check_id = definition.pop("id", None)
                check = _check(check_path, kind, source, **definition)
                if check_id is not None:
                    check["id"] = check_id
                prepared.append(check)
            checks = prepared
        outputs.append({"id": item.get("id") or _stable_id("delivery-output-", path), "path": path,
                        "public_path": item["path"], "source": source, "checks": checks})
    for item in explicit.get("inputs", ()):
        item = {"path": item} if isinstance(item, str) else dict(item)
        if "immutable" in item and not isinstance(item["immutable"], bool):
            raise ValueError("explicit immutable must be a boolean")
        path = _resolve_path(item["path"], workspace)
        inputs.append({"id": item.get("id") or _stable_id("delivery-input-", path),
                       "path": path, "public_path": item["path"],
                       "immutable": item.get("immutable") is True,
                       "source": {"kind": "caller_contract", "quote": None, "start": None, "end": None}})
    top_checks = deepcopy(explicit.get("checks", []))
    policy = deepcopy(explicit.get("policy", {}))
    if not isinstance(top_checks, list) or any(not isinstance(check, dict) for check in top_checks):
        raise ValueError("explicit checks must be an array of check objects")
    if not isinstance(policy, dict) or set(policy) - {"mandatory_checks", "hard_constraints", "objective"}:
        raise ValueError("unsupported explicit selection policy")
    constraints = policy.get("hard_constraints", [])
    if not isinstance(constraints, list) or any(not isinstance(item, dict) for item in constraints):
        raise ValueError("hard_constraints must be an array of objects")
    for constraint in constraints:
        if constraint.get("artifact") is not None:
            constraint["artifact"] = _resolve_path(constraint["artifact"], workspace)
    json.dumps(policy, allow_nan=False)
    for check in top_checks:
        if not isinstance(check.get("kind"), str) or not check["kind"]:
            raise ValueError("explicit check kind is required")
        check["source"] = {"kind": "caller_contract", "quote": None, "start": None, "end": None}
        for key in ("path", "input", "same_rows_as"):
            if check.get(key) is not None:
                check[key] = _resolve_path(check[key], workspace)
        check.setdefault("id", _stable_id("delivery-check-", check))
    for label, items in (("output", outputs), ("input", inputs),
                         ("check", [check for item in outputs for check in item["checks"]] + top_checks)):
        ids = [item.get("id") for item in items]
        if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value)
               for value in ids):
            raise ValueError(f"explicit {label} IDs must be stable strings")
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate explicit {label} ID")
        if label == "check" and "workbench.delivery" in ids:
            raise ValueError("workbench.delivery is reserved for executed workspace evidence")
    return {"outputs": outputs, "inputs": inputs, "checks": top_checks, "policy": policy,
            "unresolved": [], "excluded": [], "coverage_status": "explicit"}


def derive_delivery_contract(request: str, *, workspace_path, explicit: Mapping[str, Any] | None = None) -> dict:
    """Compile public literal requirements; ambiguous clauses remain uncovered.

    ``explicit`` is a trusted caller entry point, never an agent declaration.
    Coverage describes recognized delivery clauses, not proof of task semantics.
    """
    if not isinstance(request, str):
        raise TypeError("request must be text")
    workspace = Path(os.path.abspath(Path(workspace_path).expanduser()))
    envelope = {"schema_version": DELIVERY_SCHEMA, "source_hash": "sha256:" + hashlib.sha256(request.encode()).hexdigest()}
    if explicit is not None:
        if not isinstance(explicit, Mapping):
            raise TypeError("explicit contract must be an object")
        return {**envelope, **_explicit_contract(request, workspace, explicit)}
    outputs, inputs, unresolved, excluded, forbidden = {}, {}, [], [], {}
    for start, end, clause in _clauses(request):
        source = _source(request, start, end)
        mentions, masked = _mentions(clause)
        output_mentions = []
        if not mentions:
            cue = _last_output_cue(clause)
            if (cue and _is_user_directed_output_clause(clause, cue)
                    and (cue.group().lower() in {"save", "export", "保存", "导出"}
                         or re.search(r"\b(?:file|directory|folder)\b|文件|目录|文件夹", clause, re.I))):
                unresolved.append({**source, "reason": "delivery_target_not_literal"})
        for mention in mentions:
            prefix, suffix = masked[:mention.start()], masked[mention.end():]
            path = _resolve_path(mention.text, workspace)
            meta_clause = bool(_META_ACTION_CONTEXT_RE.search(clause) or re.search(
                r"\b(?:for example|such as|explain|quote|repeat|translate)\b|例如|举例|解释|翻译", clause, re.I
            ))
            conditional_clause = bool(_CONDITIONAL_CONTEXT_RE.search(clause))
            if _FORBIDDEN_OUTPUT_PREFIX_RE.search(prefix) and not meta_clause and not conditional_clause:
                forbidden[path] = source
                excluded.append({**source, "reason": "negated_output"})
            immutable = bool(not meta_clause and not conditional_clause and (
                _IMMUTABLE_PREFIX_RE.search(prefix) or _IMMUTABLE_SUFFIX_RE.search(suffix)
                or (_KEEP_PREFIX_RE.search(prefix) and _KEEP_SUFFIX_RE.search(suffix))))
            cue = _last_output_cue(prefix)
            input_cues = list(_INPUT_VERB_RE.finditer(prefix))
            direct_input = bool(input_cues and (cue is None or input_cues[-1].start() > cue.start()))
            source_operand = bool((cue or _TRANSFER_VERB_RE.search(prefix))
                                  and _TARGET_PREPOSITION_ANY_RE.search(suffix) and _PATH_RE.search(suffix))
            declared_output = bool(_DECLARATIVE_OUTPUT_RE.fullmatch(prefix))
            if cue is None:
                transfer = list(_TRANSFER_VERB_RE.finditer(prefix))
                if transfer and _TARGET_PREPOSITION_ANY_RE.search(prefix[transfer[-1].end():]):
                    cue = transfer[-1]
            direct = declared_output or bool(cue and _is_user_directed_output_clause(prefix, cue))
            if immutable or ((direct_input or _INPUT_LABEL_RE.search(prefix) or source_operand)
                             and not _NON_DIRECT_ACTION_CONTEXT_RE.search(prefix)
                             and not _CONDITIONAL_CONTEXT_RE.search(clause)):
                if _looks_like_concrete_path(mention.text):
                    previous = inputs.get(path)
                    inputs[path] = {"id": _stable_id("delivery-input-", path), "path": path,
                                    "public_path": mention.text, "immutable": immutable or bool(previous and previous["immutable"]),
                                    "source": source}
            if not direct:
                continue
            if _ALTERNATIVE_RE.search(clause):
                unresolved.append({**source, "reason": "alternative_output_paths"})
                continue
            if _CONDITIONAL_CONTEXT_RE.search(clause):
                excluded.append({**source, "reason": "conditional_output"})
                continue
            if not _looks_like_concrete_path(mention.text):
                unresolved.append({**source, "reason": "nonliteral_output_path"})
                continue
            governed = declared_output or _candidate_is_governed_target(masked, mention, cue)
            if not governed and cue and not source_operand:
                prior = [item for item in mentions if cue.end() <= item.start() < mention.start()]
                if prior:
                    connector = masked[prior[-1].end():mention.start()]
                    governed = re.fullmatch(r"\s*(?:,|and|和|及|、)\s*", connector, re.I) is not None
            if governed and not direct_input:
                output_mentions.append((mention, path))
        for mention, path in output_mentions:
            if mention.text.endswith("/") or re.search(r"\b(?:directory|folder)\b|目录|文件夹", clause, re.I):
                unresolved.append({**source, "reason": "directory_delivery_unsupported", "path": path})
                continue
            if not Path(path).suffix and re.search(r"^\s*(?:please\s+)?(?:create|创建)", clause, re.I) and not re.search(r"\bfile\b|文件", clause, re.I):
                unresolved.append({**source, "reason": "output_kind_ambiguous", "path": path})
                continue
            checks = _basic_checks(path, source, clause, allow_sizes=len(output_mentions) == 1)
            if len(output_mentions) > 1 and _SIZE_RE.search(clause):
                unresolved.append({**source, "reason": "size_constraint_target_ambiguous"})
            previous = outputs.get(path)
            merged = {item["id"]: item for item in (previous["checks"] if previous else [])}
            merged.update({item["id"]: item for item in checks})
            outputs[path] = {"id": _stable_id("delivery-output-", path), "path": path,
                             "public_path": mention.text, "source": source, "checks": list(merged.values())}
    for path, item in list(outputs.items()):
        sizes = [check for check in item["checks"] if check["kind"] == "file_size"]
        minimum = max((check.get("min_bytes", 0) for check in sizes), default=0)
        maximum = min((check.get("max_bytes", float("inf")) for check in sizes), default=float("inf"))
        conflict = "immutable_input_is_output" if inputs.get(path, {}).get("immutable") else (
            "conflicting_output_obligations" if path in forbidden else (
                "conflicting_size_constraints" if minimum > maximum else None))
        if conflict:
            unresolved.append({**item["source"], "reason": conflict, "path": path})
            del outputs[path]
    unique_unresolved = list({json.dumps(item, sort_keys=True): item for item in unresolved}.values())
    return {**envelope, "outputs": list(outputs.values()), "inputs": list(inputs.values()), "checks": [], "policy": {},
            "unresolved": unique_unresolved, "excluded": excluded,
            "coverage_status": "partial" if unique_unresolved else "literal_requirements" if outputs else "not_applicable"}
