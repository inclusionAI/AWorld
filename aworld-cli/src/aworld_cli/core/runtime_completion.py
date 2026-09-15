"""Runtime-owned completion contracts for direct execution tasks.

The contract is deliberately narrow: it recognizes only explicitly declared
output paths.  It never guesses a target from a task id, an input path, a URL,
or a benchmark-specific name.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from aworld.core.context.compiler import (
    ArtifactEvidence,
    ArtifactRequirement,
    CompletionContract,
    CompletionMode,
)
from aworld.logs.util import logger


COMPLETION_MODE_ENV = "AWORLD_COMPLETION_MODE"
INFER_ARTIFACTS_ENV = "AWORLD_INFER_REQUIRED_ARTIFACTS"
REQUIRED_ARTIFACTS_ENV = "AWORLD_REQUIRED_ARTIFACTS_JSON"

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


def _truthy_env(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def resolve_completion_mode(value: str | None = None) -> CompletionMode:
    """Resolve the opt-in completion mode from a value or the environment."""

    raw_value = os.environ.get(COMPLETION_MODE_ENV, "off") if value is None else value
    normalized = (raw_value or "off").strip().lower()
    try:
        return CompletionMode(normalized)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in CompletionMode)
        raise ValueError(f"{COMPLETION_MODE_ENV} must be one of: {allowed}") from exc


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
    infer_paths: bool = False,
) -> CompletionContract | None:
    """Build an artifact-existence contract, or ``None`` if no target exists."""

    candidates = list(explicit_paths)
    if infer_paths:
        candidates.extend(infer_declared_output_paths(request))
    paths = _resolve_artifact_paths(candidates, workspace_path=workspace_path)
    if not paths:
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
        validation_commands=(),
        max_evidence_age_seconds=30,
        required_final_evidence=("agent_final_response",),
        max_repairs=1,
    )


async def resolve_runtime_completion_evidence(
    context,
    contract: CompletionContract,
) -> None:
    """Record fresh filesystem evidence without executing arbitrary commands."""

    observed_at = datetime.now(timezone.utc)
    for requirement in contract.required_artifacts:
        path = Path(requirement.path).expanduser()
        try:
            exists = path.exists()
        except OSError:
            exists = False
        context.record_completion_artifact(
            ArtifactEvidence(
                requirement_id=requirement.requirement_id,
                exists=exists,
                content_hash=None,
                observed_at=observed_at,
            )
        )


def configure_runtime_completion(
    context,
    *,
    request: str | None,
    workspace_path: str | os.PathLike[str],
) -> CompletionContract | None:
    """Install the opt-in generic completion contract on one execution Context."""

    mode = resolve_completion_mode()
    if mode is CompletionMode.OFF:
        return None
    if getattr(context, "completion_contract", None) is not None:
        logger.info("Keeping the completion contract already installed by the caller")
        return context.completion_contract

    contract = build_runtime_completion_contract(
        request,
        workspace_path=workspace_path,
        explicit_paths=_configured_artifact_paths(),
        infer_paths=_truthy_env(os.environ.get(INFER_ARTIFACTS_ENV)),
    )
    if contract is None:
        logger.info(
            "Completion-contract mode is %s but the task declares no output path",
            mode.value,
        )
        return None
    context.configure_completion_contract(
        contract,
        mode=mode,
        evidence_resolver=resolve_runtime_completion_evidence,
    )
    context.context_info["runtime_completion_contract"] = {
        "mode": mode.value,
        "source": "explicit_or_high_confidence_output_paths",
        "required_artifacts": [item.path for item in contract.required_artifacts],
    }
    return contract


__all__ = [
    "COMPLETION_MODE_ENV",
    "INFER_ARTIFACTS_ENV",
    "REQUIRED_ARTIFACTS_ENV",
    "build_runtime_completion_contract",
    "configure_runtime_completion",
    "infer_declared_output_paths",
    "resolve_completion_mode",
    "resolve_runtime_completion_evidence",
]
