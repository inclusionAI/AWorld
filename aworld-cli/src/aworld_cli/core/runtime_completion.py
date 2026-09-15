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
    r"(?:save|write|export|generate|create|produce|store|place|submit)"
    r"[^.!?\n]{0,36}?(?:\bto\b|\bat\b|\bas\b|\bunder\b|\binto\b|\bin\b)"
    r")",
    re.IGNORECASE,
)
_DIRECT_OUTPUT_CUE_RE = re.compile(
    r"(?:"
    r"(?:保存|另存|写入|输出|导出|生成|创建|存储|放置|提交)(?:为|到|至|在)?"
    r"|"
    r"(?:save|write|export|generate|create|produce|store|place|submit)"
    r"(?:\s+(?:me|us))?"
    r"(?:\s+(?:(?:the|a|an|final|resulting)\s+){0,3})?"
    r")\s*$",
    re.IGNORECASE,
)
_NEGATED_OUTPUT_CUE_RE = re.compile(
    r"(?:do\s+not|don't|never|不要|请勿|别)\s*"
    r"(?:保存|另存|写入|输出|导出|生成|创建|存储|放置|提交|"
    r"save|write|export|generate|create|produce|store|place|submit)",
    re.IGNORECASE,
)
_INSTRUCTIONAL_OUTPUT_CONTEXT_RE = re.compile(
    r"(?:"
    r"\bhow\s+(?:to|do|can|would|should)\b"
    r"|\b(?:explain|describe|tell|show|give|provide|document|demonstrate)\b"
    r"[^.!?\n]{0,48}?"
    r"(?:\bhow\s+to\b|\bcommands?\s+to\b|\binstructions?\s+(?:to|for)\b|\bsteps?\s+to\b)"
    r"|\b(?:what\s+happens\s+if|should\s+i|do\s+i\s+need\s+to)\b"
    r"|(?:如何|怎样|怎么)"
    r"|(?:解释|说明|告诉我|展示|演示|描述|给出|提供)"
    r"[^。！？\n]{0,36}?(?:如何|怎样|怎么|命令|指令|步骤|方法)"
    r")",
    re.IGNORECASE,
)
_OUTPUT_CLAUSE_BOUNDARY_RE = re.compile(
    r"(?:[。！？.!?;；]\s*|(?:,\s*)?(?:\b(?:and\s+then|then|next)\b|然后|随后|接着|再)\s*)",
    re.IGNORECASE,
)
_CAPABILITY_OR_QUESTION_CONTEXT_RE = re.compile(
    r"(?:"
    r"\b(?:can|could)\s+(?:this|that|the|it|i|we)\b"
    r"|\b(?:should|may)\s+(?:i|we)\b"
    r"|\bwould\s+(?:it|this|that)\b"
    r"|\bis\s+it\s+(?:possible|better|recommended|advisable)\b"
    r"|\b(?:recommend|advise)\b[^.!?\n]{0,48}?\b(?:i|we|whether|if)\b"
    r"|\bdoes?\s+(?:this|that|the|it|application|tool|program|system)\b"
    r"|\bwhether\b"
    r"|\b(?:verify|check|determine|test)\b[^.!?\n]{0,48}?"
    r"(?:\bwhether\b|\bif\b|\bcan\b|\bcould\b|\bdoes?\b)"
    r"|(?:是否|能否|可否|会不会)"
    r"|(?:验证|检查|确认|判断)[^。！？\n]{0,36}?(?:是否|能否|可否|会不会)"
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
    ]
    if not cue_matches:
        return None
    cue = max(cue_matches, key=lambda match: match.start())
    # Include a small leading window because the direct cue itself starts at
    # ``write``/``保存`` while its negation necessarily appears just before it.
    cue_window = prefix[max(0, cue.start() - 16) :]
    if _NEGATED_OUTPUT_CUE_RE.search(cue_window):
        return None
    # A filename used in a tutorial/capability question is not a declared
    # output artifact.  Only inspect the cue's current clause: an earlier
    # explanatory sentence must not contaminate a later direct imperative.
    leading_context = prefix[: cue.start()]
    boundaries = list(_OUTPUT_CLAUSE_BOUNDARY_RE.finditer(leading_context))
    clause_prefix = (
        leading_context[boundaries[-1].end() :]
        if boundaries
        else leading_context
    )
    if _INSTRUCTIONAL_OUTPUT_CONTEXT_RE.search(
        clause_prefix
    ) or _CAPABILITY_OR_QUESTION_CONTEXT_RE.search(clause_prefix):
        return None
    return cue


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
            prefix = line[max(0, path_match.start() - 96) : path_match.start()]
            cue_match = _last_output_cue(prefix)
            if cue_match is None:
                continue
            # Do not let a cue from a previous comma-separated clause govern an
            # input path in the next clause.
            tail = prefix[cue_match.start() :]
            if re.search(r"[。！？.!?\n]", tail):
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
