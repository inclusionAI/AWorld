from __future__ import annotations

from aworld.self_evolve.population import (
    CandidateStrategy,
    PopulationBudget,
    rank_candidate_strategies,
)


def test_rank_candidate_strategies_is_deterministic_and_budgeted() -> None:
    risky = CandidateStrategy(
        strategy_id="strategy-risky",
        candidate_family="broad-rewrite",
        intended_behavior_delta="rewrite the entire workflow",
        lessons_addressed=("lesson-a", "lesson-b"),
        success_behaviors_preserved=("baseline-path",),
        expected_metric_impact=0.8,
        evidence_confidence=0.8,
        risk=0.9,
        complexity=0.9,
        patch_operation_count=6,
        runtime_instruction_diff_chars=900,
        severity_coverage=0.9,
    )
    targeted = CandidateStrategy(
        strategy_id="strategy-targeted",
        candidate_family="targeted-delta",
        intended_behavior_delta="preserve baseline and add bounded evidence check",
        lessons_addressed=("lesson-a",),
        success_behaviors_preserved=("baseline-path", "source-links"),
        expected_metric_impact=0.6,
        evidence_confidence=0.9,
        risk=0.1,
        complexity=0.1,
        patch_operation_count=1,
        runtime_instruction_diff_chars=120,
        severity_coverage=0.7,
    )
    weak = CandidateStrategy(
        strategy_id="strategy-weak",
        candidate_family="tiny",
        intended_behavior_delta="minor wording change",
        lessons_addressed=(),
        success_behaviors_preserved=("baseline-path",),
        expected_metric_impact=0.1,
        evidence_confidence=0.5,
        risk=0.1,
        complexity=0.1,
        patch_operation_count=1,
        runtime_instruction_diff_chars=80,
        severity_coverage=0.0,
    )

    ranked = rank_candidate_strategies(
        (risky, targeted, weak),
        budget=PopulationBudget(max_replay_candidates=2),
    )

    assert [item.strategy.strategy_id for item in ranked] == [
        "strategy-targeted",
        "strategy-risky",
        "strategy-weak",
    ]
    assert [item.replay_selected for item in ranked] == [True, True, False]
    assert ranked[2].not_replayed_reason == "not_replayed_due_to_budget"


def test_rank_candidate_strategies_marks_noop_when_all_scores_are_below_threshold() -> None:
    weak = CandidateStrategy(
        strategy_id="strategy-weak",
        candidate_family="tiny",
        intended_behavior_delta="minor wording change",
        expected_metric_impact=0.05,
        evidence_confidence=0.2,
        risk=0.6,
        complexity=0.5,
    )

    ranked = rank_candidate_strategies(
        (weak,),
        budget=PopulationBudget(max_replay_candidates=2, no_op_threshold=0.25),
    )

    assert len(ranked) == 1
    assert ranked[0].replay_selected is False
    assert ranked[0].not_replayed_reason == "below_no_op_threshold"
