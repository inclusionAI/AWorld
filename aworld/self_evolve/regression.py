from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from aworld.self_evolve.datasets import (
    EvalCase,
    SelfEvolveDataset,
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
)
from aworld.self_evolve.ingestion.types import fingerprint_json
from aworld.self_evolve.replay import replay_dataset_fingerprint
from aworld.self_evolve.types import (
    DatasetRecipe,
    EvaluationSummary,
    GateResult,
    to_json_dict,
)


REGRESSION_SUITE_SCHEMA_VERSION = "aworld.self_evolve.regression_suite.v1"
REGRESSION_EVIDENCE_SCHEMA_VERSION = "aworld.self_evolve.regression_evidence.v1"
_SUPPORTED_SUITE_SOURCE_KINDS = {
    "trajectory_log",
    "trajectory_set",
    "jsonl",
    "batch_config",
    "challenger",
    "target_contract",
}
_SUPPORTED_FILE_SOURCE_KINDS = _SUPPORTED_SUITE_SOURCE_KINDS - {
    "challenger",
    "target_contract",
}
_MAX_TARGET_CONTRACT_CASES = 2
_MAX_TARGET_CONTRACT_SECTION_CHARS = 4_096


@dataclass(frozen=True)
class RegressionSuiteSpec:
    """Immutable identity and data-boundary contract for one regression suite."""

    suite_id: str
    source_kind: str
    source_ref: str
    source_version: str
    dataset_fingerprint: str
    split_fingerprint: str
    case_fingerprints: tuple[str, ...]
    schema_version: str = REGRESSION_SUITE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != REGRESSION_SUITE_SCHEMA_VERSION:
            raise ValueError("unsupported regression suite schema version")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", self.suite_id):
            raise ValueError(f"invalid regression suite id: {self.suite_id!r}")
        if self.source_kind not in _SUPPORTED_SUITE_SOURCE_KINDS:
            raise ValueError(
                f"unsupported regression suite source kind: {self.source_kind}"
            )
        if not self.source_ref:
            raise ValueError("regression suite source_ref is required")
        if not self.case_fingerprints:
            raise ValueError("regression suite requires at least one case")
        if len(set(self.case_fingerprints)) != len(self.case_fingerprints):
            raise ValueError("regression suite contains duplicate case content")

    def to_dict(self) -> dict[str, Any]:
        return to_json_dict(self)


@dataclass(frozen=True)
class ResolvedRegressionSuite:
    spec: RegressionSuiteSpec
    dataset: SelfEvolveDataset = field(repr=False, compare=False)


@dataclass(frozen=True)
class RegressionSuiteResult:
    spec: RegressionSuiteSpec
    baseline_summary: EvaluationSummary
    candidate_summary: EvaluationSummary
    gate_results: tuple[GateResult, ...]
    execution_id: str
    duration_ms: int
    fresh_execution: bool = True

    def __post_init__(self) -> None:
        if not self.execution_id:
            raise ValueError("regression suite result requires execution_id")
        if self.duration_ms < 0:
            raise ValueError("regression suite duration cannot be negative")
        if self.baseline_summary.variant_id != "baseline":
            raise ValueError("regression baseline summary must use baseline variant")

    @property
    def passed(self) -> bool:
        return bool(self.gate_results) and all(gate.passed for gate in self.gate_results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "baseline_summary": to_json_dict(self.baseline_summary),
            "candidate_summary": to_json_dict(self.candidate_summary),
            "gate_results": to_json_dict(self.gate_results),
            "execution_id": self.execution_id,
            "duration_ms": self.duration_ms,
            "fresh_execution": self.fresh_execution,
            "passed": self.passed,
        }


@dataclass(frozen=True)
class RegressionEvidence:
    """Approval evidence produced by executions outside candidate selection data."""

    candidate_id: str
    selection_dataset_fingerprint: str
    selection_case_fingerprints: tuple[str, ...]
    selection_backend_id: str
    regression_backend_id: str
    suite_results: tuple[RegressionSuiteResult, ...]
    evidence_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    schema_version: str = REGRESSION_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != REGRESSION_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("unsupported regression evidence schema version")
        if not self.candidate_id:
            raise ValueError("regression evidence requires candidate_id")
        if not self.evidence_id:
            raise ValueError("regression evidence requires evidence_id")
        if len({item.spec.suite_id for item in self.suite_results}) != len(
            self.suite_results
        ):
            raise ValueError("regression evidence contains duplicate suite ids")

    @property
    def data_independent(self) -> bool:
        selection_cases = set(self.selection_case_fingerprints)
        return bool(self.suite_results) and all(
            result.spec.dataset_fingerprint != self.selection_dataset_fingerprint
            and selection_cases.isdisjoint(result.spec.case_fingerprints)
            for result in self.suite_results
        )

    @property
    def execution_independent(self) -> bool:
        execution_ids = [result.execution_id for result in self.suite_results]
        return bool(execution_ids) and len(set(execution_ids)) == len(execution_ids) and all(
            result.fresh_execution for result in self.suite_results
        )

    @property
    def implementation_independent(self) -> bool:
        return bool(self.selection_backend_id and self.regression_backend_id) and (
            self.selection_backend_id != self.regression_backend_id
        )

    @property
    def passed(self) -> bool:
        return (
            self.data_independent
            and self.execution_independent
            and bool(self.suite_results)
            and all(result.passed for result in self.suite_results)
        )

    @property
    def fingerprint(self) -> str:
        payload = self.to_dict()
        payload.pop("fingerprint", None)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "evidence_id": self.evidence_id,
            "candidate_id": self.candidate_id,
            "selection_dataset_fingerprint": self.selection_dataset_fingerprint,
            "selection_case_fingerprints": list(self.selection_case_fingerprints),
            "selection_backend_id": self.selection_backend_id,
            "regression_backend_id": self.regression_backend_id,
            "data_independent": self.data_independent,
            "execution_independent": self.execution_independent,
            "implementation_independent": self.implementation_independent,
            "suite_results": [result.to_dict() for result in self.suite_results],
            "passed": self.passed,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        payload["fingerprint"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
        return payload


def evaluation_backend_identity(backend: object | None) -> str:
    if backend is None:
        return "missing"
    backend_type = type(backend)
    return f"{backend_type.__module__}.{backend_type.__qualname__}"


def regression_case_fingerprint(case: EvalCase) -> str:
    """Fingerprint task semantics without source paths or split bookkeeping."""

    payload = {
        "input": case.input,
        "expected_output": case.expected_output,
        "verification_command": case.verification_command,
    }
    encoded = json.dumps(
        to_json_dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def dataset_case_fingerprints(dataset: SelfEvolveDataset) -> tuple[str, ...]:
    return tuple(regression_case_fingerprint(case) for case in dataset.cases)


def resolve_regression_suites(
    benchmarks: Iterable[str],
    *,
    selection_dataset: SelfEvolveDataset,
    split_seed: str = "self-evolve-regression-split",
    base_dir: str | Path | None = None,
) -> tuple[ResolvedRegressionSuite, ...]:
    """Resolve configured benchmark references and enforce a disjoint data plane."""

    selection_cases = set(dataset_case_fingerprints(selection_dataset))
    selection_fingerprint = replay_dataset_fingerprint(selection_dataset)
    resolved: list[ResolvedRegressionSuite] = []
    seen_ids: set[str] = set()
    for index, raw_benchmark in enumerate(benchmarks, start=1):
        source_kind, source_path = _parse_regression_benchmark(
            raw_benchmark,
            base_dir=base_dir,
        )
        dataset = build_dataset_from_source(
            SelfEvolveEvalSourceConfig(kind=source_kind, path=str(source_path)),
            split_seed=f"{split_seed}:{index}",
        )
        if not dataset.cases:
            raise ValueError(
                f"regression benchmark {raw_benchmark!r} resolved to an empty suite"
            )
        case_fingerprints = dataset_case_fingerprints(dataset)
        overlap = selection_cases.intersection(case_fingerprints)
        dataset_fingerprint = replay_dataset_fingerprint(dataset)
        if dataset_fingerprint == selection_fingerprint or overlap:
            raise ValueError(
                "regression benchmark overlaps candidate-selection data; "
                "use an independent task suite"
            )
        source_version = _file_fingerprint(source_path)
        suite_id = _suite_id(source_path, source_kind, source_version)
        if suite_id in seen_ids:
            raise ValueError(f"duplicate regression suite: {suite_id}")
        seen_ids.add(suite_id)
        spec = RegressionSuiteSpec(
            suite_id=suite_id,
            source_kind=source_kind,
            source_ref=str(source_path),
            source_version=source_version,
            dataset_fingerprint=dataset_fingerprint,
            split_fingerprint=fingerprint_json(dataset.recipe.splits),
            case_fingerprints=case_fingerprints,
        )
        resolved.append(ResolvedRegressionSuite(spec=spec, dataset=dataset))
    return tuple(resolved)


def resolve_target_contract_regression_suite(
    *,
    target_type: str,
    target_id: str,
    target_path: str | Path | None,
    current_content: str,
    target_fingerprint: str,
    selection_dataset: SelfEvolveDataset,
) -> tuple[ResolvedRegressionSuite, ...]:
    """Build a held-back behavior suite from the immutable baseline contract.

    This fallback is intentionally limited to existing skills with explicit
    second-level behavior sections. It creates fresh, non-executing contract
    review tasks rather than reusing candidate-selection trajectories.
    """

    if target_type != "skill" or target_path is None:
        return ()
    behavior_sections = _skill_behavior_sections(current_content)
    if not behavior_sections:
        return ()
    selection_cases = set(dataset_case_fingerprints(selection_dataset))
    cases: list[EvalCase] = []
    for index, (title, baseline_contract) in enumerate(
        behavior_sections,
        start=1,
    ):
        contract_fingerprint = fingerprint_json(
            {"title": title, "baseline_contract": baseline_contract}
        )
        case = EvalCase(
            case_id=f"target-contract-{index:02d}",
            input={
                "content": (
                    f"Without using external resources, summarize the documented "
                    f"{target_id} behavior for the '{title}' section. State the "
                    "operation ordering, safety rule, or invariant that the "
                    "provided baseline contract requires a revised skill to "
                    "preserve."
                )
            },
            expected_output={
                "section": title,
                "baseline_contract": baseline_contract,
                "baseline_contract_fingerprint": contract_fingerprint,
            },
            metadata={
                "target_contract": {
                    "target_type": target_type,
                    "target_id": target_id,
                    "section": title,
                    "target_fingerprint": target_fingerprint,
                    "baseline_contract_fingerprint": contract_fingerprint,
                }
            },
            source={
                "kind": "target_contract",
                "target_type": target_type,
                "target_id": target_id,
                "section": title,
            },
        )
        if regression_case_fingerprint(case) in selection_cases:
            continue
        cases.append(case)
    if not cases:
        return ()
    case_ids = [case.case_id for case in cases]
    dataset = SelfEvolveDataset(
        cases=tuple(cases),
        recipe=DatasetRecipe(
            source={
                "kind": "target_contract",
                "target_type": target_type,
                "target_id": target_id,
                "target_fingerprint": target_fingerprint,
            },
            split_seed=f"target-contract:{target_fingerprint}",
            splits={"train": case_ids, "validation": [], "held_out": []},
            trainable_case_ids=tuple(case_ids),
            held_out_case_ids=(),
        ),
    )
    case_fingerprints = dataset_case_fingerprints(dataset)
    safe_target_id = (
        re.sub(r"[^a-zA-Z0-9._-]+", "-", target_id).strip("-._")
        or "skill"
    )
    suite = ResolvedRegressionSuite(
        spec=RegressionSuiteSpec(
            suite_id=(
                f"target-contract-{safe_target_id[:72]}-"
                f"{target_fingerprint.removeprefix('sha256:')[:12]}"
            ),
            source_kind="target_contract",
            source_ref=f"target:skill:{safe_target_id}",
            source_version=target_fingerprint,
            dataset_fingerprint=replay_dataset_fingerprint(dataset),
            split_fingerprint=fingerprint_json(dataset.recipe.splits),
            case_fingerprints=case_fingerprints,
        ),
        dataset=dataset,
    )
    return (suite,)


def _skill_behavior_sections(content: str) -> tuple[tuple[str, str], ...]:
    sections: list[tuple[str, str]] = []
    active_title: str | None = None
    active_lines: list[str] = []
    in_fence = False

    def finish_section() -> None:
        nonlocal active_title, active_lines
        if active_title is None:
            return
        body = "\n".join(active_lines).strip()
        if body:
            sections.append(
                (
                    active_title,
                    body[:_MAX_TARGET_CONTRACT_SECTION_CHARS],
                )
            )
        active_title = None
        active_lines = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_fence = not in_fence
        if not in_fence and line.startswith("## ") and not line.startswith("### "):
            finish_section()
            title = re.sub(r"\s+", " ", line[3:].strip())
            if title and title.casefold() not in {"references", "license"}:
                active_title = title
            if len(sections) >= _MAX_TARGET_CONTRACT_CASES:
                break
            continue
        if active_title is not None:
            active_lines.append(raw_line)
    if len(sections) < _MAX_TARGET_CONTRACT_CASES:
        finish_section()
    return tuple(sections[:_MAX_TARGET_CONTRACT_CASES])


def regression_execution_id(suite_id: str) -> str:
    return f"{suite_id}-{time.time_ns()}-{uuid.uuid4().hex[:12]}"


def _parse_regression_benchmark(
    value: str,
    *,
    base_dir: str | Path | None = None,
) -> tuple[str, Path]:
    raw = str(value).strip()
    if not raw:
        raise ValueError("regression benchmark cannot be empty")
    prefix, separator, remainder = raw.partition(":")
    if separator and prefix in (
        _SUPPORTED_SUITE_SOURCE_KINDS - _SUPPORTED_FILE_SOURCE_KINDS
    ):
        raise ValueError(f"unsupported regression benchmark kind: {prefix}")
    if separator and prefix in _SUPPORTED_FILE_SOURCE_KINDS:
        source_kind = prefix
        raw_path = remainder
    else:
        raw_path = raw
        suffix = Path(raw_path).suffix.casefold()
        source_kind = (
            "jsonl"
            if suffix == ".jsonl"
            else "trajectory_set"
            if suffix == ".json"
            else "trajectory_log"
        )
    requested_path = Path(raw_path).expanduser()
    if not requested_path.is_absolute() and base_dir is not None:
        requested_path = Path(base_dir) / requested_path
    if requested_path.is_symlink():
        raise ValueError(
            f"regression benchmark must be a regular file: {raw_path!r}"
        )
    path = requested_path.resolve()
    if not path.is_file():
        raise ValueError(
            f"regression benchmark must be a regular file: {raw_path!r}"
        )
    return source_kind, path


def _file_fingerprint(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _suite_id(path: Path, source_kind: str, source_version: str) -> str:
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", path.stem).strip("-._") or "suite"
    return f"{source_kind}-{stem[:72]}-{source_version.removeprefix('sha256:')[:12]}"
