"""Separate-verifier bridge from FileX artifacts to the pinned ParseBench scorer.

The verifier owns the private rules.  It reconstructs only the official JSONL
and ``InferenceResult`` files required by the pinned scorer, invokes that
scorer one task dimension at a time, and writes Harbor-compatible rewards only
when the result is safe to publish.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from aworld.benchmarks.parsebench.adapter import (
    FileXAdapterError,
    ParseBenchTaskSource,
    validate_parsebench_artifacts,
)
from aworld.benchmarks.parsebench.contracts import (
    DATASET_REVISION,
    GROUND_TRUTH_SCHEMA_VERSION,
    PINNED_PARSEBENCH_CONTRACT,
    SCORER_REVISION,
    ParseBenchDimension,
)
from aworld.benchmarks.parsebench.scoring import (
    PARSEBENCH_DIMENSIONS,
    PARSEBENCH_REWARD_FILENAME,
    PARSEBENCH_VERIFIER_RESULT_FILENAME,
    OfficialScorerValidationError,
    ParseBenchCaseResult,
    ParseBenchDimensionResult,
    ParseBenchResultStatus,
    ParseBenchVerifierResult,
    run_official_scorer,
    validate_official_scorer,
)


DEFAULT_GROUND_TRUTH_PATH = Path("/tests/ground_truth.json")
DEFAULT_SCOPE_PATH = Path("/tests/parsebench-scope.json")
DEFAULT_RESULT_PATH = Path("/logs/artifacts/result.json")
DEFAULT_MARKDOWN_PATH = Path("/logs/artifacts/document.md")
DEFAULT_LAYOUT_PATH = Path("/logs/artifacts/layout.json")
DEFAULT_VERIFIER_OUTPUT = Path("/logs/verifier")
DEFAULT_SCORER_CHECKOUT = Path("/opt/parsebench-scorer")
DEFAULT_SCORER_PYTHON = DEFAULT_SCORER_CHECKOUT / ".venv" / "bin" / "python"

_ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "dataset_revision",
        "scorer_revision",
        "task_id",
        "source",
        "dimensions",
        "rules",
    }
)
_SOURCE_FIELDS = frozenset({"path", "runtime_path", "size", "sha256", "page"})
_RULE_FIELDS = frozenset(
    {
        "id",
        "dimension",
        "type",
        "rule",
        "page",
        "expected_markdown",
        "tags",
        "provenance",
    }
)
_PROVENANCE_FIELDS = frozenset({"source_jsonl", "source_line"})
_SCOPE_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "selection_manifest_sha256",
        "publishable",
        "non_publishable_reasons",
        "selected_execution_count",
        "runtime_image",
        "dataset_revision",
        "scorer_revision",
    }
)
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMMUTABLE_RUNTIME_IMAGE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,430}@sha256:[0-9a-f]{64}$"
)
_SOURCE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".jfif", ".docx"}
_SCOPE_SCHEMA_VERSION = "aworld-parsebench-scope/v1"
_SCOPE_REASONS = {
    "smoke_selection",
    "noncanonical_contract",
    "incomplete_official_selection",
    "mutable_runtime_image",
}
_MAX_GROUND_TRUTH_BYTES = 64 * 1024 * 1024
_MAX_SCOPE_BYTES = 64 * 1024
_MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
_FIXED_TIMESTAMP = "1970-01-01T00:00:00+00:00"


class ParseBenchVerificationError(RuntimeError):
    """Stable failure that cannot accidentally be interpreted as Reward 0."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class _GroundTruthSource:
    logical_path: str
    runtime_path: Path
    size: int
    sha256: str
    page: int | None


@dataclass(frozen=True, slots=True)
class _GroundTruthRule:
    rule_id: str
    dimension: ParseBenchDimension
    rule_type: str
    rule: dict[str, Any]
    page: int | None
    expected_markdown: str | None
    tags: tuple[str, ...]
    source_jsonl: str
    source_line: int

    def official_row(self, *, source_path: str) -> dict[str, Any]:
        return {
            "pdf": source_path,
            "category": self.dimension.value,
            "id": self.rule_id,
            "type": self.rule_type,
            "rule": self.rule,
            "page": self.page,
            "expected_markdown": self.expected_markdown,
            "tags": list(self.tags),
        }


@dataclass(frozen=True, slots=True)
class _GroundTruth:
    task_id: str
    source: _GroundTruthSource
    dimensions: tuple[ParseBenchDimension, ...]
    rules: tuple[_GroundTruthRule, ...]

    def rules_for(self, dimension: ParseBenchDimension) -> tuple[_GroundTruthRule, ...]:
        return tuple(rule for rule in self.rules if rule.dimension is dimension)


@dataclass(frozen=True, slots=True)
class _BenchmarkScope:
    kind: str
    selection_manifest_sha256: str
    publishable: bool


@dataclass(frozen=True, slots=True)
class _ArtifactSnapshot:
    result: dict[str, Any]
    layout: dict[str, Any]
    result_sha256: str
    document_sha256: str
    layout_sha256: str


@dataclass(frozen=True, slots=True)
class ParseBenchVerificationOutcome:
    """Committed verifier result plus its bounded stdout recovery line."""

    result: ParseBenchVerifierResult
    stdout_line: str
    result_path: Path
    reward_path: Path | None


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _parse_json_object(content: bytes, description: str) -> dict[str, Any]:
    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ParseBenchVerificationError(
            "invalid_verifier_input", f"{description} is not strict JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ParseBenchVerificationError(
            "invalid_verifier_input", f"{description} must be a JSON object"
        )
    return value


def _read_regular_file(path: Path, description: str, *, limit: int) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("not a regular file")
        size = path.stat().st_size
        if size <= 0 or size > limit:
            raise OSError("file size is outside the verifier limit")
        content = path.read_bytes()
    except OSError as exc:
        raise ParseBenchVerificationError(
            "invalid_verifier_input", f"{description} is not a bounded regular file"
        ) from exc
    if len(content) != size:
        raise ParseBenchVerificationError(
            "invalid_verifier_input", f"{description} changed while it was read"
        )
    return content


def _require_exact_fields(
    value: Mapping[str, Any], expected: frozenset[str], description: str
) -> None:
    if set(value) != expected:
        raise ParseBenchVerificationError(
            "ground_truth_contract_mismatch",
            f"{description} does not match the pinned ParseBench contract",
        )


def _printable_token(value: object, description: str, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ParseBenchVerificationError(
            "ground_truth_contract_mismatch", f"{description} is invalid"
        )
    return value


def _page(value: object, description: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ParseBenchVerificationError(
            "ground_truth_contract_mismatch", f"{description} is invalid"
        )
    return value


def _logical_source_path(value: object) -> str:
    path = _printable_token(value, "ground truth source path", maximum=4096)
    logical = PurePosixPath(path)
    if (
        logical.is_absolute()
        or "\\" in path
        or logical.as_posix() != path
        or len(logical.parts) < 2
        or logical.parts[0] != "docs"
        or any(part in {"", ".", ".."} for part in logical.parts)
        or logical.suffix.lower() not in _SOURCE_SUFFIXES
    ):
        raise ParseBenchVerificationError(
            "ground_truth_contract_mismatch", "ground truth source path is unsafe"
        )
    return path


def _load_ground_truth(path: Path) -> _GroundTruth:
    payload = _parse_json_object(
        _read_regular_file(
            path,
            "ParseBench ground truth",
            limit=_MAX_GROUND_TRUTH_BYTES,
        ),
        "ParseBench ground truth",
    )
    _require_exact_fields(payload, _ROOT_FIELDS, "ground truth")
    if (
        payload["schema_version"] != GROUND_TRUTH_SCHEMA_VERSION
        or payload["dataset_revision"] != DATASET_REVISION
        or payload["scorer_revision"] != SCORER_REVISION
    ):
        raise ParseBenchVerificationError(
            "ground_truth_contract_mismatch",
            "ground truth schema or pinned revisions do not match",
        )
    task_id = payload["task_id"]
    if not isinstance(task_id, str) or _TASK_ID.fullmatch(task_id) is None:
        raise ParseBenchVerificationError(
            "ground_truth_contract_mismatch", "ground truth task_id is invalid"
        )

    raw_source = payload["source"]
    if not isinstance(raw_source, Mapping):
        raise ParseBenchVerificationError(
            "ground_truth_contract_mismatch", "ground truth source is invalid"
        )
    _require_exact_fields(raw_source, _SOURCE_FIELDS, "ground truth source")
    runtime = raw_source["runtime_path"]
    size = raw_source["size"]
    digest = raw_source["sha256"]
    if (
        not isinstance(runtime, str)
        or not runtime
        or not Path(runtime).is_absolute()
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size <= 0
        or not isinstance(digest, str)
        or _SHA256.fullmatch(digest) is None
    ):
        raise ParseBenchVerificationError(
            "ground_truth_contract_mismatch", "ground truth source identity is invalid"
        )
    source = _GroundTruthSource(
        logical_path=_logical_source_path(raw_source["path"]),
        runtime_path=Path(runtime),
        size=size,
        sha256=digest,
        page=_page(raw_source["page"], "ground truth source page"),
    )

    raw_dimensions = payload["dimensions"]
    if not isinstance(raw_dimensions, list) or not raw_dimensions:
        raise ParseBenchVerificationError(
            "ground_truth_contract_mismatch", "ground truth dimensions are invalid"
        )
    try:
        dimensions = tuple(ParseBenchDimension(item) for item in raw_dimensions)
    except (TypeError, ValueError) as exc:
        raise ParseBenchVerificationError(
            "ground_truth_contract_mismatch", "ground truth dimension is unknown"
        ) from exc
    if len(set(dimensions)) != len(dimensions) or dimensions != tuple(
        dimension for dimension in PARSEBENCH_DIMENSIONS if dimension in dimensions
    ):
        raise ParseBenchVerificationError(
            "ground_truth_contract_mismatch",
            "ground truth dimensions are duplicated or out of order",
        )

    raw_rules = payload["rules"]
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ParseBenchVerificationError(
            "ground_truth_contract_mismatch", "ground truth rules are invalid"
        )
    rules: list[_GroundTruthRule] = []
    for raw_rule in raw_rules:
        if not isinstance(raw_rule, Mapping):
            raise ParseBenchVerificationError(
                "ground_truth_contract_mismatch", "ground truth rule is invalid"
            )
        _require_exact_fields(raw_rule, _RULE_FIELDS, "ground truth rule")
        try:
            dimension = ParseBenchDimension(raw_rule["dimension"])
        except (TypeError, ValueError) as exc:
            raise ParseBenchVerificationError(
                "ground_truth_contract_mismatch",
                "ground truth rule dimension is invalid",
            ) from exc
        if dimension not in dimensions:
            raise ParseBenchVerificationError(
                "ground_truth_contract_mismatch",
                "ground truth rule references an undeclared dimension",
            )
        rule_payload = raw_rule["rule"]
        if not isinstance(rule_payload, dict):
            raise ParseBenchVerificationError(
                "ground_truth_contract_mismatch", "ground truth rule payload is invalid"
            )
        rule_page = _page(raw_rule["page"], "ground truth rule page")
        if rule_page != source.page:
            raise ParseBenchVerificationError(
                "ground_truth_contract_mismatch",
                "ground truth rule page does not match its task source",
            )
        rule_type = _printable_token(
            raw_rule["type"], "ground truth rule type", maximum=128
        )
        expected_markdown = raw_rule["expected_markdown"]
        if dimension is ParseBenchDimension.TABLE:
            if (
                rule_type != "expected_markdown"
                or not isinstance(expected_markdown, str)
                or not expected_markdown
            ):
                raise ParseBenchVerificationError(
                    "ground_truth_contract_mismatch",
                    "table rule is missing expected Markdown",
                )
        elif expected_markdown is not None:
            raise ParseBenchVerificationError(
                "ground_truth_contract_mismatch",
                "non-table rule contains expected Markdown",
            )
        raw_tags = raw_rule["tags"]
        if not isinstance(raw_tags, list):
            raise ParseBenchVerificationError(
                "ground_truth_contract_mismatch", "ground truth rule tags are invalid"
            )
        tags = tuple(
            _printable_token(tag, "ground truth rule tag", maximum=128)
            for tag in raw_tags
        )
        provenance = raw_rule["provenance"]
        if not isinstance(provenance, Mapping):
            raise ParseBenchVerificationError(
                "ground_truth_contract_mismatch", "rule provenance is invalid"
            )
        _require_exact_fields(provenance, _PROVENANCE_FIELDS, "rule provenance")
        source_jsonl = provenance["source_jsonl"]
        source_line = provenance["source_line"]
        if (
            source_jsonl != PINNED_PARSEBENCH_CONTRACT.source_file_for(dimension)
            or isinstance(source_line, bool)
            or not isinstance(source_line, int)
            or source_line < 1
        ):
            raise ParseBenchVerificationError(
                "ground_truth_contract_mismatch", "rule provenance is not pinned"
            )
        rules.append(
            _GroundTruthRule(
                rule_id=_printable_token(
                    raw_rule["id"], "ground truth rule id", maximum=512
                ),
                dimension=dimension,
                rule_type=rule_type,
                rule=rule_payload,
                page=rule_page,
                expected_markdown=expected_markdown,
                tags=tags,
                source_jsonl=source_jsonl,
                source_line=source_line,
            )
        )

    dimension_order = {item: index for index, item in enumerate(PARSEBENCH_DIMENSIONS)}
    if tuple(rules) != tuple(
        sorted(
            rules,
            key=lambda rule: (
                dimension_order[rule.dimension],
                rule.source_line,
                rule.rule_id,
            ),
        )
    ):
        raise ParseBenchVerificationError(
            "ground_truth_contract_mismatch", "ground truth rules are out of order"
        )
    observed_dimensions = tuple(
        dimension
        for dimension in PARSEBENCH_DIMENSIONS
        if any(rule.dimension is dimension for rule in rules)
    )
    if observed_dimensions != dimensions:
        raise ParseBenchVerificationError(
            "ground_truth_contract_mismatch",
            "ground truth dimensions do not match its rules",
        )
    return _GroundTruth(
        task_id=task_id,
        source=source,
        dimensions=dimensions,
        rules=tuple(rules),
    )


def _load_scope(path: Path) -> _BenchmarkScope:
    payload = _parse_json_object(
        _read_regular_file(path, "ParseBench scope", limit=_MAX_SCOPE_BYTES),
        "ParseBench scope",
    )
    _require_exact_fields(payload, _SCOPE_FIELDS, "benchmark scope")
    if (
        payload["schema_version"] != _SCOPE_SCHEMA_VERSION
        or payload["dataset_revision"] != DATASET_REVISION
        or payload["scorer_revision"] != SCORER_REVISION
    ):
        raise ParseBenchVerificationError(
            "scope_contract_mismatch",
            "benchmark scope schema or revisions do not match",
        )
    kind = payload["kind"]
    selection_digest = payload["selection_manifest_sha256"]
    publishable = payload["publishable"]
    selected_count = payload["selected_execution_count"]
    if (
        kind not in {"smoke", "official-full", "custom"}
        or not isinstance(selection_digest, str)
        or _SHA256.fullmatch(selection_digest) is None
        or not isinstance(publishable, bool)
        or isinstance(selected_count, bool)
        or not isinstance(selected_count, int)
        or selected_count < 1
    ):
        raise ParseBenchVerificationError(
            "scope_contract_mismatch", "benchmark scope identity is invalid"
        )
    reasons = payload["non_publishable_reasons"]
    if (
        not isinstance(reasons, list)
        or any(
            not isinstance(reason, str) or reason not in _SCOPE_REASONS
            for reason in reasons
        )
        or len(reasons) != len(set(reasons))
    ):
        raise ParseBenchVerificationError(
            "scope_contract_mismatch", "benchmark scope reasons are invalid"
        )
    runtime_image = payload["runtime_image"]
    if (
        not isinstance(runtime_image, str)
        or not runtime_image
        or runtime_image != runtime_image.strip()
        or len(runtime_image) > 511
        or any(ord(character) < 32 for character in runtime_image)
    ):
        raise ParseBenchVerificationError(
            "scope_contract_mismatch", "benchmark scope runtime image is invalid"
        )
    if publishable:
        if (
            kind != "official-full"
            or reasons
            or selected_count != PINNED_PARSEBENCH_CONTRACT.unique_execution_count
            or _IMMUTABLE_RUNTIME_IMAGE.fullmatch(runtime_image) is None
        ):
            raise ParseBenchVerificationError(
                "scope_contract_mismatch",
                "publishable benchmark scope is not an immutable official release",
            )
    elif not reasons:
        raise ParseBenchVerificationError(
            "scope_contract_mismatch",
            "non-publishable benchmark scope must state a reason",
        )
    if kind == "smoke" and "smoke_selection" not in reasons:
        raise ParseBenchVerificationError(
            "scope_contract_mismatch", "smoke benchmark scope is inconsistent"
        )
    if kind == "custom" and "noncanonical_contract" not in reasons:
        raise ParseBenchVerificationError(
            "scope_contract_mismatch", "custom benchmark scope is inconsistent"
        )
    return _BenchmarkScope(
        kind=kind,
        selection_manifest_sha256=selection_digest,
        publishable=publishable,
    )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _validate_source_integrity(source: _GroundTruthSource) -> None:
    """Re-hash the verifier-owned source copy bound by trusted ground truth."""

    path = source.runtime_path
    try:
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != source.size
        ):
            raise OSError("source is not the expected regular file")
        digest = hashlib.sha256()
        observed_size = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                observed_size += len(chunk)
                if observed_size > source.size:
                    raise OSError("source grew while hashing")
                digest.update(chunk)
        if (
            observed_size != source.size
            or "sha256:" + digest.hexdigest() != source.sha256
            or path.stat().st_size != source.size
        ):
            raise OSError("source integrity mismatch")
    except OSError as exc:
        raise ParseBenchVerificationError(
            "source_integrity_mismatch",
            "verifier source does not match pinned ParseBench ground truth",
        ) from exc


def _snapshot_artifacts(
    *,
    ground_truth: _GroundTruth,
    result_path: Path,
    markdown_path: Path,
    layout_path: Path,
) -> _ArtifactSnapshot:
    expected_source = ParseBenchTaskSource(
        runtime_path=ground_truth.source.runtime_path,
        size=ground_truth.source.size,
        sha256=ground_truth.source.sha256,
        page=ground_truth.source.page,
    )
    try:
        validated = validate_parsebench_artifacts(
            result_path=result_path,
            markdown_path=markdown_path,
            layout_path=layout_path,
            expected_task_id=ground_truth.task_id,
            expected_source=expected_source,
        )
        result_bytes = _read_regular_file(
            result_path, "result", limit=_MAX_ARTIFACT_BYTES
        )
        document_bytes = _read_regular_file(
            markdown_path, "Markdown artifact", limit=_MAX_ARTIFACT_BYTES
        )
        layout_bytes = _read_regular_file(
            layout_path, "layout artifact", limit=_MAX_ARTIFACT_BYTES
        )
        result = _parse_json_object(result_bytes, "result")
        layout = _parse_json_object(layout_bytes, "layout artifact")
        if result != validated:
            raise ParseBenchVerificationError(
                "artifact_validation_failed",
                "ParseBench result changed during validation",
            )
        artifacts = result["artifacts"]
        if (
            _sha256(document_bytes) != artifacts["document"]["sha256"]
            or _sha256(layout_bytes) != artifacts["layout"]["sha256"]
        ):
            raise ParseBenchVerificationError(
                "artifact_validation_failed",
                "ParseBench artifact changed during validation",
            )
    except ParseBenchVerificationError:
        raise
    except (FileXAdapterError, KeyError, TypeError, OSError) as exc:
        raise ParseBenchVerificationError(
            "artifact_validation_failed", "ParseBench artifact validation failed"
        ) from exc
    return _ArtifactSnapshot(
        result=result,
        layout=layout,
        result_sha256="sha256:" + _sha256(result_bytes),
        document_sha256="sha256:" + _sha256(document_bytes),
        layout_sha256="sha256:" + _sha256(layout_bytes),
    )


def _canonical_json_bytes(value: object, *, sort_keys: bool = True) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=sort_keys,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _prepare_verifier_output(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ParseBenchVerificationError(
            "unsafe_output_path", "verifier output is not a regular directory"
        )
    for filename in (PARSEBENCH_REWARD_FILENAME, PARSEBENCH_VERIFIER_RESULT_FILENAME):
        target = path / filename
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ParseBenchVerificationError(
                "unsafe_output_path", "stale verifier output could not be removed"
            ) from exc


def _inference_group(dimension: ParseBenchDimension) -> str:
    if dimension in {
        ParseBenchDimension.TEXT_CONTENT,
        ParseBenchDimension.TEXT_FORMATTING,
    }:
        return "text"
    return dimension.value


def _write_official_inputs(
    *,
    root: Path,
    ground_truth: _GroundTruth,
    dimension: ParseBenchDimension,
    snapshot: _ArtifactSnapshot,
) -> tuple[Path, Path, Path]:
    test_cases_dir = root / "test_cases"
    output_dir = root / "outputs" / _inference_group(dimension)
    report_dir = root / "reports" / dimension.value
    test_cases_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)

    rows = [
        rule.official_row(source_path=ground_truth.source.logical_path)
        for rule in ground_truth.rules_for(dimension)
    ]
    jsonl = b"".join(_canonical_json_bytes(row) for row in rows)
    (test_cases_dir / f"{dimension.value}.jsonl").write_bytes(jsonl)

    example_id = (
        f"{_inference_group(dimension)}/"
        f"{PurePosixPath(ground_truth.source.logical_path).stem}"
    )
    output = dict(snapshot.layout)
    output["example_id"] = example_id
    inference_result = {
        "request": {
            "example_id": example_id,
            "source_file_path": ground_truth.source.logical_path,
            "product_type": "parse",
        },
        "pipeline_name": snapshot.layout["pipeline_name"],
        "product_type": "parse",
        "raw_output": {
            "result_sha256": snapshot.result_sha256,
            "document_sha256": snapshot.document_sha256,
            "layout_sha256": snapshot.layout_sha256,
        },
        "output": output,
        "started_at": _FIXED_TIMESTAMP,
        "completed_at": _FIXED_TIMESTAMP,
        "latency_in_ms": 0,
    }
    (output_dir / "filex.result.json").write_bytes(
        _canonical_json_bytes(inference_result)
    )
    return output_dir, test_cases_dir, report_dir


def _case_from_dimension_result(
    *,
    task_id: str,
    dimension: ParseBenchDimension,
    result: ParseBenchDimensionResult,
) -> ParseBenchCaseResult:
    if result.dimension is not dimension:
        return ParseBenchCaseResult.execution_failed(
            case_id=task_id,
            dimension=dimension,
            diagnostics=("official scorer returned the wrong dimension",),
        )
    if result.status is ParseBenchResultStatus.EXECUTION_FAILED:
        return ParseBenchCaseResult.execution_failed(
            case_id=task_id,
            dimension=dimension,
            diagnostics=result.errors or ("official scorer execution failed",),
        )
    if result.total_examples != 1:
        return ParseBenchCaseResult.execution_failed(
            case_id=task_id,
            dimension=dimension,
            diagnostics=("official scorer returned unexpected example cardinality",),
        )
    if result.status is ParseBenchResultStatus.MISSING:
        return ParseBenchCaseResult.missing(
            case_id=task_id,
            dimension=dimension,
            diagnostics=result.errors or ("official scorer result is missing",),
        )
    if result.status is ParseBenchResultStatus.NOT_SCORED:
        return ParseBenchCaseResult.not_scored(
            case_id=task_id,
            dimension=dimension,
            aggregate_metrics=result.aggregate_metrics,
            diagnostics=result.official_failure_details,
        )
    if result.status is not ParseBenchResultStatus.SCORED:
        return ParseBenchCaseResult.execution_failed(
            case_id=task_id,
            dimension=dimension,
            diagnostics=("official scorer returned an unsupported status",),
        )
    if result.official_failure_examples and not result.numeric_examples:
        if result.official_failure_examples != 1:
            return ParseBenchCaseResult.execution_failed(
                case_id=task_id,
                dimension=dimension,
                diagnostics=("official failure cardinality is inconsistent",),
            )
        return ParseBenchCaseResult.official_failure(
            case_id=task_id,
            dimension=dimension,
            diagnostics=result.official_failure_details,
        )
    if (
        result.numeric_examples != 1
        or result.official_failure_examples
        or result.score is None
        or not math.isfinite(result.score)
    ):
        return ParseBenchCaseResult.execution_failed(
            case_id=task_id,
            dimension=dimension,
            diagnostics=("official numeric result cardinality is inconsistent",),
        )
    return ParseBenchCaseResult.scored(
        case_id=task_id,
        dimension=dimension,
        score=result.score,
        aggregate_metrics=result.aggregate_metrics,
        diagnostics=result.official_failure_details,
    )


def _execution_failure_cases(
    ground_truth: _GroundTruth, diagnostic: str
) -> list[ParseBenchCaseResult]:
    return [
        ParseBenchCaseResult.execution_failed(
            case_id=ground_truth.task_id,
            dimension=dimension,
            diagnostics=(diagnostic,),
        )
        for dimension in ground_truth.dimensions
    ]


def _commit_verifier_result(
    *, output: Path, result: ParseBenchVerifierResult
) -> ParseBenchVerificationOutcome:
    details_path = output / PARSEBENCH_VERIFIER_RESULT_FILENAME
    reward_path: Path | None = None
    try:
        _atomic_write(details_path, _canonical_json_bytes(result.to_dict()))
        if result.publishable:
            reward_path = output / PARSEBENCH_REWARD_FILENAME
            # Insertion order is part of the Harbor contract: ``reward`` is first.
            _atomic_write(
                reward_path,
                _canonical_json_bytes(result.reward_payload(), sort_keys=False),
            )
    except (OSError, TypeError, ValueError) as exc:
        raise ParseBenchVerificationError(
            "verifier_output_failed",
            "ParseBench verifier output could not be committed",
        ) from exc
    return ParseBenchVerificationOutcome(
        result=result,
        stdout_line=result.render_stdout_sentinel(),
        result_path=details_path,
        reward_path=reward_path,
    )


def verify_parsebench_task(
    *,
    ground_truth_path: Path = DEFAULT_GROUND_TRUTH_PATH,
    scope_path: Path | None = None,
    result_path: Path = DEFAULT_RESULT_PATH,
    markdown_path: Path = DEFAULT_MARKDOWN_PATH,
    layout_path: Path = DEFAULT_LAYOUT_PATH,
    verifier_output: Path = DEFAULT_VERIFIER_OUTPUT,
    scorer_checkout: Path = DEFAULT_SCORER_CHECKOUT,
    scorer_python: Path = DEFAULT_SCORER_PYTHON,
    scorer_timeout_seconds: float = 8 * 60,
) -> ParseBenchVerificationOutcome:
    """Verify one FileX task with the exact official scorer and fail closed."""

    if (
        isinstance(scorer_timeout_seconds, bool)
        or not isinstance(scorer_timeout_seconds, (int, float))
        or not math.isfinite(float(scorer_timeout_seconds))
        or not 0 < float(scorer_timeout_seconds) <= 7_200
    ):
        raise ParseBenchVerificationError(
            "invalid_scorer_timeout", "official scorer timeout must be positive"
        )
    output = Path(verifier_output)
    _prepare_verifier_output(output)
    ground_truth_file = Path(ground_truth_path)
    scope_file = (
        ground_truth_file.with_name(DEFAULT_SCOPE_PATH.name)
        if scope_path is None
        else Path(scope_path)
    )
    scope = _load_scope(scope_file)
    ground_truth = _load_ground_truth(ground_truth_file)
    _validate_source_integrity(ground_truth.source)
    snapshot = _snapshot_artifacts(
        ground_truth=ground_truth,
        result_path=Path(result_path),
        markdown_path=Path(markdown_path),
        layout_path=Path(layout_path),
    )

    try:
        scorer_environment = validate_official_scorer(
            scorer_checkout,
            python_executable=scorer_python,
        )
    except (OfficialScorerValidationError, OSError, TypeError, ValueError):
        result = ParseBenchVerifierResult.from_case_results(
            task_id=ground_truth.task_id,
            case_results=_execution_failure_cases(
                ground_truth, "official scorer environment validation failed"
            ),
            scope_kind=scope.kind,
            selection_manifest_sha256=scope.selection_manifest_sha256,
            scope_publishable=scope.publishable,
        )
        return _commit_verifier_result(output=output, result=result)

    case_results: list[ParseBenchCaseResult] = []
    with tempfile.TemporaryDirectory(prefix="aworld-parsebench-verifier-") as temporary:
        temporary_root = Path(temporary)
        for dimension in ground_truth.dimensions:
            dimension_root = temporary_root / dimension.value
            output_dir, test_cases_dir, report_dir = _write_official_inputs(
                root=dimension_root,
                ground_truth=ground_truth,
                dimension=dimension,
                snapshot=snapshot,
            )
            try:
                dimension_result = run_official_scorer(
                    scorer_environment,
                    dimension=dimension,
                    output_dir=output_dir,
                    test_cases_dir=test_cases_dir,
                    report_dir=report_dir,
                    max_workers=1,
                    timeout_seconds=float(scorer_timeout_seconds),
                )
                case_result = _case_from_dimension_result(
                    task_id=ground_truth.task_id,
                    dimension=dimension,
                    result=dimension_result,
                )
            except Exception:
                # An exception is infrastructure failure, never an official zero.
                case_result = ParseBenchCaseResult.execution_failed(
                    case_id=ground_truth.task_id,
                    dimension=dimension,
                    diagnostics=("official scorer invocation failed",),
                )
            case_results.append(case_result)

    verifier_result = ParseBenchVerifierResult.from_case_results(
        task_id=ground_truth.task_id,
        case_results=case_results,
        scope_kind=scope.kind,
        selection_manifest_sha256=scope.selection_manifest_sha256,
        scope_publishable=scope.publishable,
    )
    return _commit_verifier_result(output=output, result=verifier_result)


__all__ = (
    "DEFAULT_GROUND_TRUTH_PATH",
    "DEFAULT_LAYOUT_PATH",
    "DEFAULT_MARKDOWN_PATH",
    "DEFAULT_RESULT_PATH",
    "DEFAULT_SCORER_CHECKOUT",
    "DEFAULT_SCORER_PYTHON",
    "DEFAULT_SCOPE_PATH",
    "DEFAULT_VERIFIER_OUTPUT",
    "ParseBenchVerificationError",
    "ParseBenchVerificationOutcome",
    "verify_parsebench_task",
)
