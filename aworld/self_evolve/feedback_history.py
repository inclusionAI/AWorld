"""Historical feedback and rejection classification policy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from aworld.self_evolve.causal_admission import causal_admission_prerequisite_blocker
from aworld.self_evolve.evaluation_reporting import _metric_number
from aworld.self_evolve.feedback_diagnostics import _typed_gate_feedback_metrics
from aworld.self_evolve.history_support import _load_json_mapping
from aworld.self_evolve.sanitization import (
    sanitize_path_ref,
    sanitize_source_text,
    sanitize_text,
)
from aworld.self_evolve.types import EvaluationSummary, GateResult

_MAX_REPAIR_CANDIDATE_PACKAGE_CHARS = 64_000
_MAX_REPAIR_CANDIDATE_FILE_CHARS = 32_000
_MAX_MIXED_REPAIR_TARGET_CHARS = 32_000
_MAX_HISTORICAL_REPAIR_CANDIDATES = 8


def _positive_int_or_default(value: Any, *, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(1, int(value))


def _nonnegative_int_or_default(value: Any, *, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(0, int(value))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


def _baseline_comparison_feedback_metrics(
    *,
    baseline_summary: EvaluationSummary | None,
    candidate_summary: EvaluationSummary | None,
) -> dict[str, float]:
    if baseline_summary is None or candidate_summary is None:
        return {}
    comparison: dict[str, float] = {}
    for metric_key in (
        "score",
        "A1_groundedness",
        "A2_completeness",
        "A3_relevance",
        "A4_readability",
        "B1_tool_use",
        "B2_efficiency",
        "B3_compliance",
        "B4_robustness",
        "evidence_block_count",
        "evidence_incomplete",
        "latency_ms",
    ):
        baseline_value = _metric_number(baseline_summary.metrics, metric_key)
        candidate_value = _metric_number(candidate_summary.metrics, metric_key)
        if baseline_value is None or candidate_value is None:
            continue
        comparison[f"baseline_{metric_key}"] = baseline_value
        comparison[f"candidate_{metric_key}"] = candidate_value
        comparison[f"{metric_key}_delta"] = candidate_value - baseline_value
    return comparison


def _bounded_repair_candidate_target_content(content: str, *, has_files: bool) -> str:
    limit = (
        _MAX_MIXED_REPAIR_TARGET_CHARS
        if has_files
        else _MAX_REPAIR_CANDIDATE_PACKAGE_CHARS
    )
    return sanitize_source_text(content, max_chars=limit)


def _report_has_shared_measurement_failure(
    report: Mapping[str, Any],
) -> bool:
    if _report_has_candidate_prerequisite_failure(report):
        return False
    outcome = report.get("campaign_measurement_outcome")
    if (
        isinstance(outcome, Mapping)
        and outcome.get("continuation_available") is True
        and outcome.get("execution_status")
        in {"checkpointed", "invalid", "framework_blocked"}
    ):
        return True
    disposition = report.get("self_improvement_disposition")
    if (
        isinstance(disposition, Mapping)
        and disposition.get("kind") == "repair_measurement"
        and disposition.get("scope") == "shared_run"
    ):
        return True
    for key in ("rejection_attribution", "campaign_failure_attribution"):
        attribution = report.get(key)
        if not isinstance(attribution, Mapping):
            continue
        if (
            attribution.get("failure_class") == "measurement"
            and attribution.get("failure_owner")
            in {"framework", "infrastructure", "evaluation_harness"}
            and attribution.get("failure_scope") == "shared_run"
        ):
            return True
    return False


def _report_has_candidate_prerequisite_failure(
    report: Mapping[str, Any],
) -> bool:
    raw_gates = report.get("gate_results")
    if not isinstance(raw_gates, list):
        return False
    for raw_gate in raw_gates:
        if not isinstance(raw_gate, Mapping):
            continue
        gate_name = raw_gate.get("gate_name")
        if not isinstance(gate_name, str) or not gate_name:
            continue
        gate = GateResult(
            gate_name=gate_name,
            passed=raw_gate.get("passed") is True,
            reason=str(raw_gate.get("reason") or ""),
            details=(
                dict(raw_gate["details"])
                if isinstance(raw_gate.get("details"), Mapping)
                else None
            ),
        )
        if _gate_has_candidate_prerequisite_failure(gate):
            return True
    return False


def _gate_has_candidate_prerequisite_failure(gate: GateResult) -> bool:
    return causal_admission_prerequisite_blocker(
        gate_name=gate.gate_name,
        passed=gate.passed,
        details=gate.details,
    )


def _report_matches_screening_harness(
    report: Mapping[str, Any],
    expected_fingerprint: str | None,
) -> bool:
    """Reject stale control evidence produced by another harness identity."""

    if expected_fingerprint is None:
        return True
    preflight = report.get("screening_control_preflight")
    return bool(
        isinstance(preflight, Mapping)
        and preflight.get("harness_fingerprint") == expected_fingerprint
    )


def _feedback_from_report(
    report: Mapping[str, Any],
    *,
    report_path: Path,
) -> tuple[EvaluationSummary, ...]:
    items: list[EvaluationSummary] = []
    repair_feedback = (
        *_repair_feedback_from_selected_candidate(
            report,
            report_path=report_path,
        ),
        *_repair_feedback_from_screening_report(
            report,
            report_path=report_path,
        ),
    )
    seen_repair_candidates: set[str] = set()
    for feedback in repair_feedback:
        if feedback.variant_id in seen_repair_candidates:
            continue
        seen_repair_candidates.add(feedback.variant_id)
        items.append(feedback)
    if _report_has_shared_measurement_failure(report):
        # A broken control plane is not general candidate training data.  Keep
        # only independently attributed candidate-owned conformance/screening
        # feedback gathered before the shared stop; omit lessons and raw
        # iteration summaries whose candidate effect was never observed.
        return tuple(items)
    items.extend(_lesson_feedback_from_report(report, report_path=report_path))
    iterations = report.get("iterations")
    if isinstance(iterations, list):
        for iteration in iterations:
            if not isinstance(iteration, Mapping):
                continue
            if iteration.get("status") not in {
                "rejected",
                "accepted",
                "prerequisite",
            }:
                continue
            candidate_id = iteration.get("candidate_id")
            if not isinstance(candidate_id, str) or not candidate_id:
                continue
            metrics = _historical_feedback_metrics(iteration)
            metrics["candidate_status"] = str(iteration.get("status"))
            post_apply = (
                report.get("post_apply")
                if isinstance(report.get("post_apply"), Mapping)
                else {}
            )
            legacy_accepted_report = (
                report.get("apply_policy") is None and not post_apply
            )
            metrics["publication_completed"] = legacy_accepted_report or (
                report.get("apply_policy") == "auto_verified"
                and post_apply.get("status") == "accepted"
                and post_apply.get("release_state") == "verified"
                and post_apply.get("published") is not False
            )
            metrics["historical_apply_policy"] = report.get("apply_policy")
            metrics["historical_release_state"] = post_apply.get("release_state")
            metrics["run_id"] = report.get("run_id")
            metrics["report_path"] = str(report_path)
            items.append(
                EvaluationSummary(
                    variant_id=candidate_id,
                    metrics=metrics,
                    dataset_split="historical",
                )
            )
    return tuple(items)


def _repair_feedback_from_selected_candidate(
    report: Mapping[str, Any],
    *,
    report_path: Path,
) -> tuple[EvaluationSummary, ...]:
    candidate_id = report.get("repair_focus_candidate_id") or report.get(
        "selected_candidate_id"
    )
    raw_gates = report.get("gate_results")
    if (
        not isinstance(candidate_id, str)
        or not candidate_id
        or not isinstance(raw_gates, list)
    ):
        return ()
    package = _stored_repair_candidate_package(
        report_path=report_path,
        candidate_id=candidate_id,
    )
    if package is None:
        return ()

    judge_metrics, judge_split = _selected_candidate_judge_metrics(
        report,
        candidate_id=candidate_id,
    )
    judge_repair_gates = {
        "evidence_quality",
        "replay_evaluator_admission",
        "required_verification",
        "held_out_verification",
        "judge_only_signal",
        "global_regression_benchmark",
        "score_improvement",
        "cost_latency",
        "replay_stability",
    }

    gates: list[GateResult] = []
    for item in raw_gates:
        if not isinstance(item, Mapping) or item.get("passed") is not False:
            continue
        details = item.get("details")
        gate_name = item.get("gate_name")
        if not isinstance(gate_name, str) or not gate_name:
            continue
        candidate_repair = (
            isinstance(details, Mapping)
            and details.get("failure_class") == "candidate"
            and details.get("repairable") is True
        )
        judge_repair = bool(judge_metrics) and gate_name in judge_repair_gates
        if not candidate_repair and not judge_repair:
            continue
        bounded_details = dict(details) if isinstance(details, Mapping) else {}
        if judge_repair:
            bounded_details.setdefault("failure_class", "candidate")
            bounded_details.setdefault("repairable", True)
            bounded_details.setdefault("failure_stage", "judge_evaluation")
        failure_artifacts = _historical_failure_artifact_excerpts(
            report_path=report_path,
            artifact_root=bounded_details.get("artifact_root"),
        )
        if failure_artifacts:
            bounded_details["failure_artifacts"] = list(failure_artifacts)
        gates.append(
            GateResult(
                gate_name=gate_name,
                passed=False,
                reason=sanitize_text(item.get("reason"), max_chars=320),
                details=bounded_details,
            )
        )
    if not gates:
        return ()
    candidate_status = (
        "prerequisite"
        if any(
            isinstance(gate.details, Mapping)
            and gate.details.get("candidate_status") == "prerequisite"
            for gate in gates
        )
        else "repairable"
    )
    metrics = _typed_gate_feedback_metrics(gates)
    metrics.update(judge_metrics)
    metrics.update(
        {
            "failed_gates": [gate.gate_name for gate in gates],
            "candidate_status": candidate_status,
            "authoritative_replay_failure": candidate_status != "prerequisite",
            "run_id": report.get("run_id") or report_path.parent.name,
            "report_path": str(report_path),
            "repair_candidate_package": package,
        }
    )
    return (
        EvaluationSummary(
            variant_id=candidate_id,
            metrics=metrics,
            dataset_split=judge_split or "historical_repair",
        ),
    )


def _selected_candidate_judge_metrics(
    report: Mapping[str, Any],
    *,
    candidate_id: str,
) -> tuple[dict[str, Any], str | None]:
    """Rehydrate judge metrics onto the selected candidate repair package.

    Iteration history stores evaluated metrics separately from the candidate
    source package.  Joining them here preserves the deepest repair frontier
    when a later optimize run learns from a rejected report.
    """

    iterations = report.get("iterations")
    if not isinstance(iterations, list):
        return {}, None
    for iteration in reversed(iterations):
        if (
            not isinstance(iteration, Mapping)
            or iteration.get("candidate_id") != candidate_id
        ):
            continue
        candidate_metrics = iteration.get("candidate_metrics")
        held_out_metrics = iteration.get("held_out_metrics")
        selected_metrics: Mapping[str, Any] | None = None
        selected_split: str | None = None
        if isinstance(held_out_metrics, Mapping) and any(
            key in held_out_metrics
            for key in (
                "score",
                "A1_groundedness",
                "A2_completeness",
                "evidence_incomplete",
                "veto_triggered",
            )
        ):
            selected_metrics = held_out_metrics
            selected_split = "held_out"
        elif isinstance(candidate_metrics, Mapping) and any(
            key in candidate_metrics
            for key in (
                "score",
                "A1_groundedness",
                "A2_completeness",
                "evidence_incomplete",
                "veto_triggered",
            )
        ):
            selected_metrics = candidate_metrics
            selected_split = "validation"
        if selected_metrics is None:
            return {}, None
        metrics = dict(selected_metrics)
        failed_gates = iteration.get("failed_gates")
        if isinstance(failed_gates, list):
            metrics["failed_gates"] = [str(gate) for gate in failed_gates if str(gate)]
        return metrics, selected_split
    return {}, None


def _historical_failure_artifact_excerpts(
    *,
    report_path: Path,
    artifact_root: Any,
) -> tuple[Mapping[str, str], ...]:
    if not isinstance(artifact_root, str) or not artifact_root:
        return ()
    run_root = report_path.parent.resolve()
    try:
        root = Path(artifact_root).expanduser().resolve()
    except OSError:
        return ()
    if not _path_is_relative_to(root, run_root) or not root.is_dir():
        return ()

    excerpts: list[Mapping[str, str]] = []
    inspected = 0
    try:
        paths = root.rglob("*")
        for path in paths:
            inspected += 1
            if inspected > 512 or len(excerpts) >= 4:
                break
            if path.is_symlink() or not path.is_file():
                continue
            name = path.name.lower()
            is_diagnostic = (
                name.endswith((".stderr.txt", ".stdout.txt"))
                or name == "failure.json"
                or (
                    "diagnostic" in name
                    and path.suffix.lower() in {".json", ".txt", ".log"}
                )
            )
            if not is_diagnostic:
                continue
            try:
                with path.open("rb") as handle:
                    handle.seek(0, 2)
                    size = handle.tell()
                    handle.seek(max(0, size - 4_096))
                    tail = handle.read(4_096).decode("utf-8", errors="replace")
            except OSError:
                continue
            # Preserve the terminal exception rather than the beginning of a
            # traceback; downstream metric compaction intentionally bounds each
            # diagnostic string to roughly one prompt paragraph.
            excerpt = sanitize_text(tail[-360:], max_chars=360)
            if not excerpt:
                continue
            excerpts.append(
                {
                    "path": sanitize_path_ref(path.relative_to(run_root).as_posix()),
                    "tail": excerpt,
                }
            )
    except OSError:
        return tuple(excerpts)
    return tuple(excerpts)


def _repair_feedback_from_screening_report(
    report: Mapping[str, Any],
    *,
    report_path: Path,
) -> tuple[EvaluationSummary, ...]:
    population = report.get("population")
    if not isinstance(population, Mapping):
        return ()

    screenings: list[Mapping[str, Any]] = []
    conformance_iterations = population.get("conformance_iterations")
    if isinstance(conformance_iterations, list):
        screenings.extend(
            item for item in conformance_iterations if isinstance(item, Mapping)
        )
    conformance = population.get("conformance")
    if isinstance(conformance, Mapping):
        screenings.append(conformance)
    screening_iterations = population.get("screening_iterations")
    if isinstance(screening_iterations, list):
        screenings.extend(
            item for item in screening_iterations if isinstance(item, Mapping)
        )
    screening = population.get("screening")
    if isinstance(screening, Mapping):
        screenings.append(screening)
    if not screenings:
        return ()

    feedback: list[EvaluationSummary] = []
    seen_candidate_ids: set[str] = set()
    attempts: list[Any] = []
    for screening_item in reversed(screenings):
        screening_attempts = screening_item.get("attempts")
        if isinstance(screening_attempts, list):
            attempts.extend(reversed(screening_attempts))
    for attempt in attempts:
        if not isinstance(attempt, Mapping) or attempt.get("passed") is not False:
            continue
        candidate_id = attempt.get("candidate_id")
        details = attempt.get("details")
        if (
            not isinstance(candidate_id, str)
            or not candidate_id
            or not isinstance(details, Mapping)
            or details.get("failure_class") != "candidate"
            or details.get("repairable") is not True
            or candidate_id in seen_candidate_ids
        ):
            continue
        package = _stored_repair_candidate_package(
            report_path=report_path,
            candidate_id=candidate_id,
        )
        if package is None:
            continue
        seen_candidate_ids.add(candidate_id)
        gate_name = (
            "candidate_repair_conformance"
            if attempt.get("stage") == "conformance"
            else "candidate_replay"
        )
        gate = GateResult(
            gate_name=gate_name,
            passed=False,
            reason=sanitize_text(attempt.get("reason"), max_chars=320),
            details=details,
        )
        metrics = _typed_gate_feedback_metrics([gate])
        metrics.update(
            {
                "failed_gates": [gate_name],
                "candidate_status": "repairable",
                "run_id": report.get("run_id") or report_path.parent.name,
                "report_path": str(report_path),
                "repair_candidate_package": package,
            }
        )
        feedback.append(
            EvaluationSummary(
                variant_id=candidate_id,
                metrics=metrics,
                dataset_split="historical_repair",
            )
        )
        if len(feedback) >= _MAX_HISTORICAL_REPAIR_CANDIDATES:
            break
    return tuple(feedback)


def _stored_repair_candidate_package(
    *,
    report_path: Path,
    candidate_id: str,
) -> Mapping[str, object] | None:
    run_root = report_path.parent.resolve()
    payload: Mapping[str, Any] | None = None
    for candidate_path in (
        run_root / "candidates" / candidate_id / "candidate.json",
        run_root / "candidates" / f"{candidate_id}.json",
    ):
        try:
            resolved = candidate_path.resolve()
        except OSError:
            continue
        if not _path_is_relative_to(resolved, run_root) or not resolved.is_file():
            continue
        try:
            payload = _load_json_mapping(resolved)
        except Exception:
            continue
        break
    if payload is None:
        return None
    if payload.get("candidate_id") != candidate_id:
        return None
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        return None

    raw_content = payload.get("content")
    has_target_content = isinstance(raw_content, str) and bool(raw_content.strip())
    bounded_target_content = (
        _bounded_repair_candidate_target_content(
            raw_content,
            has_files=bool(raw_files),
        )
        if has_target_content
        else None
    )
    remaining_chars = _MAX_REPAIR_CANDIDATE_PACKAGE_CHARS
    if bounded_target_content is not None:
        remaining_chars -= len(bounded_target_content)
    files: list[dict[str, object]] = []
    for item in raw_files[:8]:
        if not isinstance(item, Mapping):
            continue
        path = item.get("path")
        operation = item.get("operation")
        if not isinstance(path, str) or not path or not isinstance(operation, str):
            continue
        file_payload: dict[str, object] = {
            "path": sanitize_text(path, max_chars=240),
            "operation": sanitize_text(operation, max_chars=40),
            "executable": item.get("executable") is True,
        }
        content = item.get("content")
        if isinstance(content, str) and remaining_chars > 0:
            content_limit = min(
                remaining_chars,
                _MAX_REPAIR_CANDIDATE_FILE_CHARS,
            )
            sanitized_content = sanitize_source_text(
                content,
                max_chars=content_limit,
                preserve_format=True,
            )
            file_payload["content"] = sanitized_content
            remaining_chars -= len(sanitized_content)
        files.append(file_payload)
    if raw_files and not files:
        return None
    if not files and not has_target_content:
        return None
    package = {
        "candidate_id": sanitize_text(candidate_id, max_chars=160),
        "rationale": sanitize_text(payload.get("rationale"), max_chars=1_000),
        "files": files,
    }
    if bounded_target_content is not None:
        package["content"] = bounded_target_content
    return package


def _lesson_feedback_from_report(
    report: Mapping[str, Any],
    *,
    report_path: Path,
) -> tuple[EvaluationSummary, ...]:
    lessons_path = _lessons_path_from_report(report, report_path=report_path)
    if lessons_path is None or not lessons_path.exists():
        return ()
    items: list[EvaluationSummary] = []
    try:
        raw_lines = lessons_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    for raw_line in raw_lines:
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        lesson_id = payload.get("lesson_id")
        if not isinstance(lesson_id, str) or not lesson_id:
            continue
        lesson_metrics = payload.get("metrics")
        metrics: dict[str, Any] = (
            dict(lesson_metrics) if isinstance(lesson_metrics, Mapping) else {}
        )
        metrics.update(
            {
                "lesson_id": lesson_id,
                "lesson_type": str(payload.get("lesson_type") or ""),
                "lesson_title": _bounded_text(payload.get("title"), max_chars=160),
                "lesson_summary": _bounded_text(payload.get("summary"), max_chars=320),
                # Additive backward compatibility: legacy lesson rows predate
                # occurrence aggregation and therefore represent one event.
                "occurrence_count": _positive_int_or_default(
                    payload.get("occurrence_count"), default=1
                ),
                "distinct_source_count": _nonnegative_int_or_default(
                    payload.get("distinct_source_count"), default=0
                ),
                "run_id": report.get("run_id"),
                "report_path": str(report_path),
            }
        )
        source_run_ids = _string_list(payload.get("source_run_ids"))
        if source_run_ids:
            metrics["source_run_ids"] = source_run_ids
        source_task_ids = _string_list(payload.get("source_task_ids"))
        if source_task_ids:
            metrics["source_task_ids"] = source_task_ids
        source_candidate_ids = _string_list(payload.get("source_candidate_ids"))
        if source_candidate_ids:
            metrics["source_candidate_ids"] = source_candidate_ids
        affected_case_ids = _string_list(payload.get("affected_case_ids"))
        if affected_case_ids:
            metrics["affected_case_ids"] = affected_case_ids
        items.append(
            EvaluationSummary(
                variant_id=lesson_id,
                metrics=metrics,
                dataset_split="lesson_memory",
            )
        )
    return tuple(items)


def _lessons_path_from_report(
    report: Mapping[str, Any],
    *,
    report_path: Path,
) -> Path | None:
    run_root = report_path.parent.resolve()
    lessons = report.get("lessons")
    raw_path: str | None = None
    if isinstance(lessons, Mapping):
        path_value = lessons.get("path")
        if isinstance(path_value, str) and path_value:
            raw_path = path_value
    candidate_path = (
        Path(raw_path)
        if raw_path is not None
        else run_root / "lessons" / "lessons.jsonl"
    )
    if not candidate_path.is_absolute():
        candidate_path = run_root / candidate_path
    try:
        resolved = candidate_path.resolve()
    except OSError:
        return None
    if not _path_is_relative_to(resolved, run_root):
        return None
    return resolved


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _bounded_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _historical_feedback_metrics(iteration: Mapping[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    baseline_metrics = iteration.get("baseline_metrics")
    candidate_metrics = iteration.get("candidate_metrics")
    if isinstance(candidate_metrics, Mapping):
        metrics.update(dict(candidate_metrics))
    if isinstance(baseline_metrics, Mapping) and isinstance(candidate_metrics, Mapping):
        metrics.update(
            _baseline_comparison_feedback_metrics(
                baseline_summary=EvaluationSummary(
                    variant_id="baseline",
                    metrics=baseline_metrics,
                    dataset_split="historical",
                ),
                candidate_summary=EvaluationSummary(
                    variant_id=str(iteration.get("candidate_id") or "candidate"),
                    metrics=candidate_metrics,
                    dataset_split="historical",
                ),
            )
        )
    held_out_metrics = iteration.get("held_out_metrics")
    if isinstance(held_out_metrics, Mapping):
        for key, value in held_out_metrics.items():
            metrics.setdefault(f"held_out_{key}", value)
    failed_gates = iteration.get("failed_gates")
    if isinstance(failed_gates, list):
        metrics["failed_gates"] = [str(gate) for gate in failed_gates if gate]
    return metrics


def _retryable_infrastructure_rejection(metrics: Mapping[str, Any]) -> bool:
    if _has_missing_model_profile_judge_failure(metrics):
        return True
    failed_gates = {str(gate) for gate in metrics.get("failed_gates", ()) if str(gate)}
    return (
        bool(failed_gates)
        and failed_gates
        <= {
            "candidate_replay",
            "replay_confidence",
        }
        and not any(
            key in metrics
            for key in (
                "score",
                "candidate_score",
                "evaluator_gate_passed",
                "judge_attempt_count",
                "A1_groundedness",
                "A2_completeness",
            )
        )
    )


def _non_authoritative_candidate_rejection(metrics: Mapping[str, Any]) -> bool:
    if _retryable_infrastructure_rejection(metrics):
        return True
    failed_gates = {str(gate) for gate in metrics.get("failed_gates", ()) if str(gate)}
    return failed_gates == {"duplicate_rejected_candidate"}


def _has_missing_model_profile_judge_failure(metrics: Mapping[str, Any]) -> bool:
    for key, value in metrics.items():
        if not str(key).endswith("judge_failures"):
            continue
        if not isinstance(value, list):
            continue
        for failure in value:
            if not isinstance(failure, Mapping):
                continue
            reason = str(failure.get("reason") or "")
            if "model profile not found or incomplete" in reason:
                return True
    return False
