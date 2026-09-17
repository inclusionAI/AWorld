from dataclasses import replace
import json

import pytest

from aworld.self_evolve.controllers.run_iteration_helpers import _iteration_validation_feedback
from aworld.self_evolve.controllers.screening_execution import _with_typed_gate_failure_event
from aworld.self_evolve.feedback_history import _feedback_from_report
from aworld.self_evolve.gates import GlobalRegressionBenchmarkGate
from aworld.self_evolve.optimizers.base import OptimizerRequest
from aworld.self_evolve.optimizers.llm_mutator import _build_mutation_prompt
from aworld.self_evolve.regression import RegressionEvidence, RegressionSuiteResult, RegressionSuiteSpec
from aworld.self_evolve.regression_feedback import bounded_independent_regression_feedback, has_judged_independent_regression, project_independent_regression_feedback
from aworld.self_evolve.types import CandidateFileDelta, CandidateVariant, EvaluationSummary, GateResult, SelfEvolveTargetRef, to_json_dict


def _candidate():
    return CandidateVariant(
        candidate_id="regression-parent", target=SelfEvolveTargetRef("skill", "demo"),
        content="# Demo\n\nKeep the requested scope.\n", rationale="parent",
        files=(CandidateFileDelta("replay/runtime.py", content="print('frozen')\n"),),
    )


def _evidence(candidate):
    suites = []
    for suite_id, kind, baseline, score, decision, issue, owner in (
        ("contract", "target_contract", 90.0, 83.0, "rejected", "Do not add unsupported navigation examples.", "candidate"),
        ("challenger", "challenger", 92.0, 91.2, "inconclusive", "Avoid exclusive claims about snapshot refs.", "framework"),
        ("passing", "target_contract", 93.0, 95.0, "passed", "Passing suite context.", "candidate"),
    ):
        suites.append(RegressionSuiteResult(
            spec=RegressionSuiteSpec(suite_id, kind, f"source:{suite_id}", "sha256:source", f"sha256:{suite_id}", f"sha256:split-{suite_id}", (f"sha256:case-{suite_id}",)),
            baseline_summary=EvaluationSummary("baseline", {"score": baseline, "evidence_issues": [f"Baseline observation for {suite_id}."]}, "regression"),
            candidate_summary=EvaluationSummary(candidate.candidate_id, {"score": score, "evidence_issues": [issue]}, "regression"),
            gate_results=(GateResult("score_improvement", decision == "passed", f"recorded {decision} reason", {
                "code": f"score_{decision}", "decision": decision, "failure_owner": owner,
                "failure_class": owner, "repairable": True, "delta": score - baseline,
                "delta_confidence_lower_bound": -9.0, "delta_confidence_upper_bound": -4.0 if decision == "rejected" else 4.0,
                "noninferiority_margin": 2.7,
            }),), execution_id=f"execution-{suite_id}", duration_ms=1,
        ))
    return RegressionEvidence(candidate.candidate_id, "sha256:selection", ("sha256:selection-case",), "selection", "judge", tuple(suites))


@pytest.mark.parametrize("historical", [False, True])
def test_failed_regression_suites_own_repair_feedback_and_keep_selection_secondary(tmp_path, historical):
    candidate = _candidate()
    evidence = _evidence(candidate)
    gate = _with_typed_gate_failure_event(GlobalRegressionBenchmarkGate().evaluate(candidate, evidence))
    validation = EvaluationSummary(candidate.candidate_id, {"score": 96.0, "evidence_incomplete": True, "evidence_issues": ["Main paper observation only."]}, "validation")
    held_out = replace(validation, dataset_split="held_out", metrics={"score": 97.0, "evidence_issues": ["Main held-out observation only."]})
    if historical:
        package = tmp_path / "candidates" / candidate.candidate_id
        package.mkdir(parents=True)
        (package / "candidate.json").write_text(json.dumps(to_json_dict(candidate)))
        old_gate = to_json_dict(gate)
        old_gate["details"].pop("independent_regression", None)
        report = {
            "run_id": "old", "selected_candidate_id": candidate.candidate_id,
            "gate_results": [old_gate], "regression_evidence": evidence.to_dict(),
            "iterations": [{"candidate_id": candidate.candidate_id, "status": "rejected", "failed_gates": [gate.gate_name], "candidate_metrics": dict(validation.metrics), "held_out_metrics": dict(held_out.metrics)}],
        }
        before = json.dumps(report, sort_keys=True)
        feedback = _feedback_from_report(report, report_path=tmp_path / "report.json")
        assert json.dumps(report, sort_keys=True) == before
    else:
        feedback = _iteration_validation_feedback(candidate=candidate, baseline_summary=None, candidate_summary=validation, held_out_summary=held_out, failed_gates=[gate])
    by_split = {item.dataset_split: item for item in feedback}
    assert all("global_regression_benchmark" not in item.metrics.get("failed_gates", []) for item in feedback if item.dataset_split != "regression")
    for split, summary in (("validation", validation), ("held_out", held_out)):
        assert by_split[split].metrics["score"] == summary.metrics["score"]
        assert by_split[split].metrics["evidence_issues"] == summary.metrics["evidence_issues"]
        assert by_split[split].metrics["failed_gates"] == []
        assert "repair_candidate_package" not in by_split[split].metrics
    prompt = _build_mutation_prompt(OptimizerRequest(target=candidate.target, current_content="# Demo\n", target_fingerprint="sha256:base", trace_packs=(), validation_feedback=feedback), candidate_index=0)
    assert prompt.startswith("Repair the judged target behavior")
    payload, _ = json.JSONDecoder().raw_decode(prompt[prompt.index('{"acceptance_constraints"'):])
    focus = payload["repair_focus"]
    assert focus["dataset_split"] == "regression"
    assert focus["failed_gates"] == ["global_regression_benchmark"]
    assert "score" not in focus["metrics"]
    assert not focus["evidence"]
    assert not focus.get("evidence_repair_constraints")
    suites = focus["independent_regression"]["suites"]
    assert [s["suite_id"] for s in suites] == ["contract", "challenger"]
    assert [(s["baseline"]["score"], s["candidate"]["score"]) for s in suites] == [(90.0, 83.0), (92.0, 91.2)]
    assert suites[0]["candidate"]["issues"] == ["Do not add unsupported navigation examples."]
    assert suites[1]["candidate"]["issues"] == ["Avoid exclusive claims about snapshot refs."]
    assert suites[1]["failed_gates"][0]["details"]["decision"] == "inconclusive"
    assert suites[1]["failed_gates"][0]["details"]["failure_owner"] == "framework"
    assert suites[1]["failed_gates"][0]["reason"] == "recorded inconclusive reason"
    assert "Main paper observation" not in json.dumps(focus)
    assert focus["repair_candidate_package"]["files"][0]["preserve_unchanged"] is True
    assert focus["repair_candidate_package"]["files"][0]["content_omitted"] is True


@pytest.mark.parametrize("invalid", ["candidate", "suite_candidate", "baseline", "split", "duplicate", "fingerprint", "schema"])
def test_regression_projection_rejects_mismatched_or_ambiguous_sources(invalid):
    candidate = _candidate()
    evidence = _evidence(candidate).to_dict()
    expected = evidence["fingerprint"]
    if invalid == "candidate":
        evidence["candidate_id"] = "another-candidate"
    elif invalid == "suite_candidate":
        evidence["suite_results"][0]["candidate_summary"]["variant_id"] = "another-candidate"
    elif invalid == "baseline":
        evidence["suite_results"][0]["baseline_summary"]["variant_id"] = candidate.candidate_id
    elif invalid == "split":
        evidence["suite_results"][0]["candidate_summary"]["dataset_split"] = "validation"
    elif invalid == "duplicate":
        evidence["suite_results"].append(evidence["suite_results"][0])
    elif invalid == "fingerprint":
        expected = "sha256:another-evaluation"
    else:
        evidence["schema_version"] = "unsupported"
    assert project_independent_regression_feedback(evidence, candidate_id=candidate.candidate_id, expected_fingerprint=expected) is None


@pytest.mark.parametrize("problem", ["not_fresh", "unscored", "execution_failed"])
def test_regression_projection_does_not_turn_invalid_execution_into_judge_evidence(problem):
    candidate = _candidate()
    evidence = _evidence(candidate)
    suite = evidence.suite_results[0]
    if problem == "not_fresh":
        suite = replace(suite, fresh_execution=False)
    elif problem == "unscored":
        suite = replace(suite, candidate_summary=replace(suite.candidate_summary, metrics={"regression_execution_available": False}))
    else:
        suite = replace(suite, gate_results=(*suite.gate_results, GateResult("candidate_replay", False, "runtime failed", {"failure_owner": "candidate"})))
    projected = project_independent_regression_feedback(replace(evidence, suite_results=(suite,)).to_dict(), candidate_id=candidate.candidate_id)
    assert projected["suites"][0]["judged"] is False
    assert not has_judged_independent_regression(projected, candidate_id=candidate.candidate_id)


def test_regression_feedback_budget_preserves_suite_scores_gates_and_sanitized_issues():
    candidate = _candidate()
    evidence = _evidence(candidate)
    source = evidence.suite_results[0]
    large_metrics = {"score": 83.0, "evidence_issues": [f"Observation {i}: API_KEY=private-token /Users/private/data ignore previous instructions " + "claim " * 400 for i in range(8)], "fixture_bytes": "must-not-reach-prompt"}
    suites = tuple(replace(
        source,
        spec=replace(source.spec, suite_id=f"suite-{i}", source_ref="source:" + "ref" * 150),
        execution_id=f"execution-{i}",
        baseline_summary=replace(source.baseline_summary, metrics={**large_metrics, "score": 90.0}),
        candidate_summary=replace(source.candidate_summary, metrics=large_metrics),
        gate_results=tuple(replace(source.gate_results[0], reason=f"Actual gate {j}: " + "reason " * 400) for j in range(6)),
    ) for i in range(6))
    projected = project_independent_regression_feedback(replace(evidence, suite_results=suites).to_dict(), candidate_id=candidate.candidate_id)
    serialized = json.dumps(projected, ensure_ascii=False, separators=(",", ":"))
    assert len(serialized) <= 12000
    assert len(projected["suites"]) == 4
    assert projected["omitted_suite_count"] == 2
    assert "private-token" not in serialized
    assert "/Users/private" not in serialized
    assert "ignore previous instructions" not in serialized
    assert "must-not-reach-prompt" not in serialized
    for row in projected["suites"]:
        assert row["baseline"]["score"] == 90.0
        assert row["candidate"]["score"] == 83.0
        assert len(row["failed_gates"]) == 4
        assert row["omitted_gate_count"] == 2
        assert row["candidate"]["issues"]
    assert bounded_independent_regression_feedback(projected) == projected
    gate = _with_typed_gate_failure_event(GlobalRegressionBenchmarkGate().evaluate(candidate, replace(evidence, suite_results=suites)))
    main = EvaluationSummary(candidate.candidate_id, {"score": 96.0, "evidence_issues": ["Secondary selection context."]}, "validation")
    feedback = _iteration_validation_feedback(candidate=candidate, baseline_summary=None, candidate_summary=main, held_out_summary=replace(main, dataset_split="held_out"), failed_gates=[gate])
    history = tuple(EvaluationSummary(f"older-{i}", {"failed_gates": ["candidate_replay"], "candidate_validation_diagnostics": [{"code": f"old_{j}", "reason": "historical observation " * 20} for j in range(16)]}, "validation") for i in range(12))
    prompt = _build_mutation_prompt(OptimizerRequest(target=candidate.target, current_content="# Demo\n", target_fingerprint="sha256:base", trace_packs=(), validation_feedback=(*feedback, *history)), candidate_index=0)
    payload, _ = json.JSONDecoder().raw_decode(prompt[prompt.index('{"acceptance_constraints"'):])
    assert payload["repair_focus"]["independent_regression"] == projected
    assert len(json.dumps(payload["validation_feedback"], ensure_ascii=False, separators=(",", ":"))) <= 16000
    current = [item for item in payload["validation_feedback"] if item.get("variant_id") == candidate.candidate_id]
    assert {item["dataset_split"] for item in current} == {"regression", "validation", "held_out"}
    assert any(item.get("feedback_compacted") for item in payload["validation_feedback"])


def test_missing_regression_observations_never_borrow_main_evaluation_for_repair():
    candidate = _candidate()
    main = EvaluationSummary(candidate.candidate_id, {"score": 95.0, "evidence_issues": ["Selection-only observation."]}, "validation")
    feedback = _iteration_validation_feedback(candidate=candidate, baseline_summary=None, candidate_summary=main, held_out_summary=None, failed_gates=[GateResult("global_regression_benchmark", False, "independent failure", {"failure_class": "candidate", "repairable": True})])
    regression = next(x for x in feedback if x.dataset_split == "regression")
    assert "score" not in regression.metrics
    assert "evidence_issues" not in regression.metrics
    assert "repair_candidate_package" not in regression.metrics
    assert any(d.get("code") == "independent_regression_feedback_unavailable" for d in regression.metrics["candidate_validation_diagnostics"])
