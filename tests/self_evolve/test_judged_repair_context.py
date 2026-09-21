from copy import deepcopy
import json

import pytest

from aworld.self_evolve.optimizers.base import (
    CandidateSemanticValidationError,
    OptimizerRequest,
)
from aworld.self_evolve.optimizers.llm_mutator import (
    _build_judged_repair_prompt,
    _validate_judged_repair_surface,
)
from aworld.self_evolve.types import SelfEvolveTargetRef


def _request() -> OptimizerRequest:
    return OptimizerRequest(
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        current_content="# Demo\n\nBaseline workflow.\n",
        target_fingerprint="sha256:baseline",
        trace_packs=(),
    )


def _historical_runtime_feedback() -> dict:
    return {
        "variant_id": "previous-runtime",
        "dataset_split": "historical_repair",
        "feedback_compacted": True,
        "failed_gates": ["candidate_repair_conformance"],
        "metrics": {"candidate_status": "repairable", "repairable": True},
        "candidate_validation_diagnostics": [
            {
                "code": "failed_gate",
                "details": {
                    "code": "protocol_version_invalid",
                    "stage": "capability_preflight",
                    "schema_field_constraints": [{"required_action": "old_action"}],
                },
            }
        ],
        "repair_plan": {
            "issues": ["active_typed_schema_violation"],
            "actions": ["emit_every_selector_match:protocol.version"],
            "acceptance_criteria": ["no_active_violation:protocol.version"],
        },
        "required_behaviors": ["implement_old_runtime_repair"],
        "replay_counterexamples": [
            {
                "failure_code": "protocol_version_invalid",
                "stage": "capability_preflight",
                "semantic_key": "replay-failure-old",
                "occurrence_count": 4,
                "required_transition": "satisfy_candidate_capability_preflight",
            }
        ],
        "recovery_trace": {
            "schema_version": "aworld.self_evolve.recovery_trace.public.v1",
            "recovered_member_count": 2,
            "candidate_success_rate": 0.5,
            "guidance": ["repair_unrecovered_members_without_regressing_recovered_members"],
        },
        "constraint_recovery_trace": {
            "schema_version": "aworld.self_evolve.constraint_recovery_trace.public.v1",
            "recovered_constraint_count": 1,
            "constraints": [{"constraint_identity": "sha256:" + "a" * 64, "status": "recovered"}],
            "guidance": ["switch_implementation_for_repeated_constraint_failure"],
        },
    }


def _judged_payload() -> dict:
    focus = {
        "variant_id": "current-parent",
        "dataset_split": "held_out",
        "metrics": {"score": 72.0},
        "failed_gates": ["evidence_quality"],
        "evidence": {"issues": ["Qualify conclusions beyond the retrieved evidence."]},
        "evidence_repair_constraints": [{"required_action": "support_or_omit"}],
        "repair_plan": {"actions": ["support_or_omit"]},
    }
    support = deepcopy(focus)
    support["dataset_split"] = "validation"
    support["evidence"]["issues"] = ["Reconcile the existing evidence manifest."]
    return {
        "repair_focus": {
            **focus,
            "repair_candidate_package": {"content": "# Demo\nUse bounded evidence.\n", "files": []},
        },
        "repair_support": support,
        "validation_feedback": [focus, support, _historical_runtime_feedback()],
    }


def test_judged_prompt_preserves_current_quality_and_summarizes_runtime_history():
    payload = _judged_payload()
    original = deepcopy(payload)

    prompt = _build_judged_repair_prompt(_request(), payload)
    projected = json.loads(prompt.split("\n", 1)[1])

    assert payload == original
    for key in ("repair_focus", "repair_support"):
        assert projected[key] == original[key]
    assert projected["validation_feedback"][:2] == original["validation_feedback"][:2]
    historical = projected["validation_feedback"][2]
    assert historical["variant_id"] == "previous-runtime"
    assert historical["dataset_split"] == "historical_repair"
    summary = historical["historical_runtime_summary"]
    assert summary["status"] == "history_only_runtime_frozen"
    assert summary["failed_gates"] == ["candidate_repair_conformance"]
    assert any(item.get("code") == "protocol_version_invalid" for item in summary["diagnostics"])
    assert summary["counterexamples"][0]["occurrence_count"] == 4
    assert summary["recovery_trace"]["recovered_member_count"] == 2
    assert summary["constraint_recovery_trace"]["constraints"][0]["status"] == "recovered"
    encoded = json.dumps(historical)
    for stale_action in (
        "repairable", "active_typed_schema_violation", "emit_every_selector_match",
        "required_transition", "schema_field_constraints", "guidance", "old_action",
    ):
        assert stale_action not in encoded
    assert len(encoded) < len(json.dumps(original["validation_feedback"][2]))


@pytest.mark.parametrize("split", ["validation", "held_out", "historical_repair"])
def test_judged_prompt_keeps_quality_feedback_even_when_it_has_runtime_history(split):
    payload = _judged_payload()
    quality = _historical_runtime_feedback()
    quality["dataset_split"] = split
    quality["failed_gates"].append("evidence_quality")
    quality["metrics"]["score"] = 70.0
    quality["evidence"] = {"issues": ["Preserve this current quality diagnosis."]}
    payload["validation_feedback"].append(quality)

    projected = json.loads(_build_judged_repair_prompt(_request(), payload).split("\n", 1)[1])

    assert projected["validation_feedback"][-1] == quality


@pytest.mark.parametrize(
    ("failed_gate", "growth"),
    [("score_improvement", 8), ("evidence_quality", 8),
     ("cost_latency_regression", 0), ("global_regression_benchmark", 0)],
)
def test_judged_surface_diagnostic_reports_the_existing_total_boundary(failed_gate, growth):
    request = _request()
    parent = request.current_content + " ".join(f"parent{i}" for i in range(71))
    focus = {"failed_gates": [failed_gate], "repair_candidate_package": {"content": parent}}
    at_limit = parent + " " + " ".join(f"extra{i}" for i in range(growth))

    _validate_judged_repair_surface(request, repair_focus=focus, candidate_content=at_limit)
    with pytest.raises(CandidateSemanticValidationError) as exc_info:
        _validate_judged_repair_surface(
            request, repair_focus=focus, candidate_content=at_limit + " overflow"
        )

    details = exc_info.value.details
    assert exc_info.value.code == "judged_repair_scope_expanded"
    assert details["parent_added_token_surface"] == 71
    assert details["allowed_added_token_growth"] == growth
    assert details["maximum_added_token_surface"] == 71 + growth
    assert details["candidate_added_token_surface"] == details["maximum_added_token_surface"] + 1
