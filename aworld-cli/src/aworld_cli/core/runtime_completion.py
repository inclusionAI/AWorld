"""Runtime-owned completion contracts for direct execution tasks.

The contract is deliberately narrow: it recognizes only explicitly declared
output paths.  It never guesses a target from a task id, an input path, a URL,
or a benchmark-specific name.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import signal
import shutil
import os
import re
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Sequence

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
INFER_ARTIFACTS_ENV = "AWORLD_INFER_REQUIRED_ARTIFACTS"
REQUIRED_ARTIFACTS_ENV = "AWORLD_REQUIRED_ARTIFACTS_JSON"
VALIDATION_COMMANDS_ENV = "AWORLD_VALIDATION_COMMANDS_JSON"

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

    default = "enforce" if (os.environ.get(REQUIRED_ARTIFACTS_ENV) or os.environ.get(VALIDATION_COMMANDS_ENV)) else "off"
    raw_value = os.environ.get(COMPLETION_MODE_ENV, default) if value is None else value
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


async def _run_validation(command: ValidationCommand) -> tuple[int, str]:
    """Execute only caller-supplied argv; drain bounded memory, preserve real exit."""
    digest = hashlib.sha256()
    process = await asyncio.create_subprocess_exec(
        *command.argv, cwd=command.cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    async def drain():
        while chunk := await process.stdout.read(65536):
            digest.update(chunk)
    reader = asyncio.create_task(drain())
    try:
        # Descendants keeping stdout open are also part of a bounded check.
        async with asyncio.timeout(command.timeout_seconds):
            await process.wait()
            await reader
        return process.returncode, "sha256:" + digest.hexdigest()
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
        return 124, "sha256:" + digest.hexdigest()


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
    validation_commands: Sequence[ValidationCommand] = (),
) -> CompletionContract | None:
    """Build an artifact-existence contract, or ``None`` if no target exists."""

    candidates = list(explicit_paths)
    if infer_paths:
        candidates.extend(infer_declared_output_paths(request))
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
        max_evidence_age_seconds=None if validation_commands else 30,
        required_final_evidence=("agent_final_response",),
        max_repairs=1,
    )


async def resolve_runtime_completion_evidence(
    context,
    contract: CompletionContract,
) -> None:
    """Collect fresh files and opt-in caller validation, never inferred commands."""

    for command in contract.validation_commands:
        try:
            exit_code, output_hash = await _run_validation(command)
        except (OSError, ValueError):
            exit_code, output_hash = 127, None
        context.record_completion_self_check(SelfCheckEvidence(
            command_id=command.command_id, exit_code=exit_code,
            output_hash=output_hash, observed_at=datetime.now(timezone.utc),
        ))
    observed_at = datetime.now(timezone.utc)
    for requirement in contract.required_artifacts:
        path = Path(requirement.path).expanduser()
        try:
            exists = path.exists()
        except OSError:
            exists = False
        content_hash = None
        if exists and requirement.expected_hash:
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
    """Install the opt-in generic completion contract on one execution Context."""

    mode = resolve_completion_mode()
    if mode is CompletionMode.OFF:
        return None
    if getattr(context, "completion_contract", None) is not None:
        logger.info("Keeping the completion contract already installed by the caller")
        return context.completion_contract

    explicit_paths = _configured_artifact_paths()
    validation_commands = _configured_validation_commands()
    inferred_only = not explicit_paths and not validation_commands
    contract = build_runtime_completion_contract(
        request,
        workspace_path=workspace_path,
        explicit_paths=explicit_paths,
        validation_commands=validation_commands,
        # A structured contract is authoritative.  Do not silently add natural
        # language guesses to it, because one ambiguous inferred path could
        # otherwise turn a successful task into a typed task failure.
        infer_paths=(
            inferred_only
            and _truthy_env(os.environ.get(INFER_ARTIFACTS_ENV))
        ),
    )
    if contract is None:
        logger.info(
            "Completion-contract mode is %s but the task declares no output path",
            mode.value,
        )
        return None
    effective_mode = (
        CompletionMode.OBSERVE
        if inferred_only and mode is CompletionMode.ENFORCE
        else mode
    )
    context.configure_completion_contract(
        contract,
        mode=effective_mode,
        evidence_resolver=resolve_runtime_completion_evidence,
    )
    context.context_info["runtime_completion_contract"] = {
        "mode": effective_mode.value,
        "requested_mode": mode.value,
        "source": "inferred_advisory" if inferred_only else "explicit_structured",
        "required_artifacts": [item.path for item in contract.required_artifacts],
    }
    return contract


def configure_goal_completion(context, *, verification_commands: Sequence[str], workspace_path) -> CompletionContract | None:
    """Bind explicit user goal verification commands; no model text is executed.

    Goal CLI commands are shell strings by contract. pipefail prevents a failing
    validation hidden behind a successful log formatter from claiming success.
    """
    if not verification_commands:
        return getattr(context, "completion_contract", None)
    if any(not isinstance(command, str) or not command.strip() for command in verification_commands):
        raise ValueError("goal verification commands must be non-empty strings")
    shell = shutil.which("bash")
    if shell is None:
        raise ValueError("explicit shell validation requires bash with pipefail")
    previous = getattr(context, "completion_contract", None)
    previous_resolver = getattr(context, "_completion_evidence_resolver", None)
    metadata = context.context_info.get("runtime_completion_contract", {})
    owned_ids = set(metadata.get("validation_command_ids", ())) if metadata.get("source") == "explicit_goal_verification" else set()
    base_checks = tuple(c for c in (previous.validation_commands if previous else ())
                        if c.command_id not in owned_ids)
    used_ids = {c.command_id for c in base_checks}
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
        max_evidence_age_seconds=None,
        required_final_evidence=tuple(dict.fromkeys((previous.required_final_evidence if previous else ()) + ("agent_final_response",))),
        max_repairs=previous.max_repairs if previous else 1,
    )
    resolver = resolve_runtime_completion_evidence
    if previous_resolver is not None and previous_resolver is not resolve_runtime_completion_evidence:
        async def resolver(target, configured):
            await previous_resolver(target, previous)
            await resolve_runtime_completion_evidence(target, configured)
    context.configure_completion_contract(contract, mode=CompletionMode.ENFORCE, evidence_resolver=resolver)
    context.context_info["runtime_completion_contract"] = {
        "mode": "enforce", "requested_mode": "enforce", "source": "explicit_goal_verification",
        "required_artifacts": [item.path for item in contract.required_artifacts],
        "validation_command_ids": [item.command_id for item in checks],
    }
    return contract


__all__ = [
    "COMPLETION_MODE_ENV",
    "INFER_ARTIFACTS_ENV",
    "REQUIRED_ARTIFACTS_ENV",
    "VALIDATION_COMMANDS_ENV",
    "build_runtime_completion_contract",
    "configure_runtime_completion",
    "configure_goal_completion",
    "infer_declared_output_paths",
    "resolve_completion_mode",
    "resolve_runtime_completion_evidence",
]
