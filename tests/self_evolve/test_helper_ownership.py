from __future__ import annotations

import ast
import inspect
from decimal import Decimal
from pathlib import Path

import pytest

from aworld.self_evolve import (
    cli_ingestion,
    cli_orchestration,
    cli_rerun,
    evaluation_reporting,
    failure_events,
    feedback_history,
    history_support,
    iteration_selection,
    lineage_history,
    measurement_reporting,
    population_projection,
    repair_conformance_diagnostics,
    replay_adaptation_diagnostics,
    replay_gates,
    replay_cache,
    run_history,
    run_reporting,
    run_failure_attribution,
    schema_diagnostics,
    screening_observation_history,
    target_package,
)
from aworld.self_evolve import runner
from aworld.self_evolve.controllers import (
    measurement_execution_datasets,
    retention,
    run_budget_support,
    run_apply_transaction,
    run_evaluation_execution,
    run_generation_helpers,
    run_iteration_helpers,
    run_repair_conformance,
    run_replay_adaptation,
    run_resources,
    run_terminal_lifecycle,
    screening_execution,
    screening_helpers,
)


LEAF_MODULES = (
    evaluation_reporting,
    failure_events,
    feedback_history,
    history_support,
    iteration_selection,
    lineage_history,
    measurement_reporting,
    population_projection,
    repair_conformance_diagnostics,
    replay_adaptation_diagnostics,
    replay_gates,
    replay_cache,
    run_history,
    run_reporting,
    run_failure_attribution,
    schema_diagnostics,
    screening_observation_history,
    target_package,
)


OWNER_FUNCTIONS = {
    measurement_execution_datasets: {"_partial_replay_evaluator_dataset"},
    run_generation_helpers: {"_verification_contract_fingerprint"},
    cli_ingestion: {"_load_or_build_campaign_dataset"},
    cli_orchestration: {"_empty_run_budget_report"},
    retention: {
        "_retention_controller",
        "_artifact_retention_report",
        "_finalize_run_report",
        "artifact_retention_report",
        "finalize_run_report",
    },
    run_budget_support: {
        "_judge_actual_token_usage",
        "_unique_evaluation_summaries",
        "_same_evaluation_execution",
        "_execution_usage_report",
        "configured_budget_usage",
        "backend_proves_zero_budget_usage",
    },
    evaluation_reporting: {
        "_metric_number",
        "_accumulate_score_evidence",
        "_finite_score_samples",
        "_nonnegative_numeric_count",
        "_positive_metric_count",
        "_evidence_quality_gate",
        "_summary_with_replay_evidence_metrics",
        "_replay_failure_summary",
    },
    run_failure_attribution: {
        "_candidate_materialization_failures",
        "_candidate_materialization_failure_event",
        "_candidate_materialization_failure_events",
        "_candidate_policy_filter_event",
        "_candidate_policy_filter_signature",
        "_retryable_candidate_generation_failure",
        "_optimizer_iteration_diagnostics",
        "_status_without_selected_candidate",
        "_candidate_generation_failure_events",
        "_candidate_protocol_failure_events",
        "_candidate_policy_filter_outcomes",
        "_candidate_policy_frontier_stalled_event",
        "_candidate_policy_filter_events",
        "_repair_contract_fingerprints",
        "_terminal_cause",
        "_retryable_infrastructure_details",
        "_rejection_attribution",
        "_campaign_failure_attribution",
        "_attribution_diagnostic_refs",
        "_resolved_conformance_contract_fingerprints",
    },
    target_package: {
        "_target_runtime_skill_path",
        "_replayable_user_task_dataset",
        "_target_package_inventory",
        "_target_package_sources",
        "_safe_artifact_name",
        "_stable_json_fingerprint",
    },
    repair_conformance_diagnostics: {
        "_gate_has_typed_shared_infrastructure_failure",
        "_repair_conformance_gate",
        "_repair_conformance_validation_surface_changed",
        "_failed_probe_typed_feedback",
        "_repair_probe_root_cause_code",
        "_repair_conformance_required_nonempty_operations",
        "_repair_conformance_screening_attempt",
        "_repair_conformance_failure_diagnostics",
        "_conformance_gate_blocks_population",
    },
    replay_adaptation_diagnostics: {
        "_replay_adaptation_exception_details",
        "_replay_adaptation_details",
    },
    replay_gates: {
        "_gate_has_typed_shared_measurement_failure",
        "_gate_is_replay_execution_infrastructure_failure",
        "_system_owned_repetition_failures",
        "_gate_blocks_measurement_materialization",
        "_environment_fingerprint_drift_gate",
        "_replay_confidence_gate",
        "_replay_stability_gate",
    },
    measurement_reporting: {
        "_finite_measurement_metric",
        "_optional_measurement_bool",
        "_non_negative_measurement_int",
        "_non_negative_measurement_float",
        "_budget_curve_points",
    },
    schema_diagnostics: {
        "_schema_field_contract_fingerprint",
        "_repair_contract_fingerprint",
    },
    failure_events: {"_typed_causal_feedback_event"},
    run_reporting: {
        "_replay_report",
        "_replay_capability_report",
        "_evaluator_report_paths",
        "_repair_frontier_state_report",
        "_trajectory_set_report",
        "_no_op_report",
        "_acceptance_confidence_report",
    },
    replay_cache: {"_reusable_baseline_case_count"},
    run_history: {
        "_load_candidate_variant",
        "_load_structural_edit_intent",
        "_report_matches_target",
        "_load_prior_rejected_feedback",
        "_load_prior_scheduler_state",
        "_load_prior_candidate_package_index",
        "_load_prior_rejected_semantic_lesson_fingerprints",
        "_rejected_candidate_ids_from_report",
        "_prior_report_paths",
    },
    lineage_history: {
        "_lineage_records_from_report",
        "_lazy_import_lineage_records_from_report",
        "_lineage_importable_iterations",
        "_safe_lineage_file_stem",
        "_persist_lineage_lifecycle",
        "_lineage_addressed_lesson_ids",
        "_with_release_lesson_mapping",
    },
    feedback_history: {
        "_report_matches_screening_harness",
        "_feedback_from_report",
        "_repair_feedback_from_selected_candidate",
        "_selected_candidate_judge_metrics",
        "_historical_failure_artifact_excerpts",
        "_repair_feedback_from_screening_report",
        "_stored_repair_candidate_package",
        "_lesson_feedback_from_report",
        "_lessons_path_from_report",
        "_path_is_relative_to",
        "_bounded_text",
        "_historical_feedback_metrics",
        "_retryable_infrastructure_rejection",
        "_non_authoritative_candidate_rejection",
        "_has_missing_model_profile_judge_failure",
        "_gate_has_candidate_prerequisite_failure",
        "_report_has_candidate_prerequisite_failure",
        "_report_has_shared_measurement_failure",
    },
    population_projection: {
        "_candidate_strategy_records",
        "_candidate_validation_report_for_persistence",
        "_population_report",
    },
    screening_observation_history: {
        "_control_qualification_identity",
        "_control_qualification_identity_from_request",
        "_legacy_path_sensitive_support_fingerprint",
        "_record_support_specific_control_observation",
        "_screening_control_harness_fingerprint",
        "_screening_observation_scope_fingerprint",
        "_restore_campaign_screening_case_observations",
        "_restore_historical_screening_lifecycle_observations",
        "_restore_authoritative_member_lifecycle_observations",
        "_merge_screening_variant_lifecycle_observation",
        "_merge_support_specific_lifecycle_observation",
        "_screening_control_preflight",
    },
    iteration_selection: {
        "_select_iteration_state",
        "_iteration_candidate_score",
        "_iteration_candidate_paired_delta",
        "_candidate_generation_limit",
    },
    history_support: {
        "_load_json_mapping",
        "_non_negative_int",
        "_non_negative_numeric_int",
        "_non_negative_screening_float",
    },
    run_resources: {"remaining_measurement_budget"},
}


RUNNER_COMPATIBILITY_ALIASES = {
    "_verification_contract_fingerprint",
    "_retention_controller",
    "_artifact_retention_report",
    "_finalize_run_report",
}

GENERIC_NAME_COLLISIONS = {"_bounded_text", "_non_negative_int"}

DEAD_ORIGINAL_HELPERS = {
    "_can_reuse_single_case_replay_validation",
    "_candidate_generation_failure_event",
    "_gate_results_have_candidate_prerequisite_failure",
    "_legacy_member_baseline_replay_dir",
    "_run_candidate_generation_agent",
    "_terminal_candidate_evaluation_result",
    "_typed_terminal_candidate_evaluation_result",
}

PUBLIC_RUNNER_ENTRY_POINTS = {
    "optimize_explicit_target",
    "optimize_from_cli_request",
}

ORIGINAL_RUNNER_HELPERS = {
    "_control_qualification_identity",
    "_partial_replay_evaluator_dataset",
    "_verification_contract_fingerprint",
    "_remaining_measurement_budget",
    "_configured_budget_usage",
    "_judge_actual_token_usage",
    "_unique_evaluation_summaries",
    "_same_evaluation_execution",
    "_terminal_candidate_evaluation_result",
    "_typed_terminal_candidate_evaluation_result",
    "_backend_proves_zero_budget_usage",
    "_execution_usage_report",
    "_screening_control_harness_fingerprint",
    "_accumulate_score_evidence",
    "_finite_score_samples",
    "_nonnegative_numeric_count",
    "_positive_metric_count",
    "_optimizer_iteration_diagnostics",
    "_status_without_selected_candidate",
    "_repair_conformance_validation_surface_changed",
    "_candidate_generation_failure_events",
    "_candidate_protocol_failure_events",
    "_candidate_policy_filter_outcomes",
    "_candidate_policy_frontier_stalled_event",
    "_candidate_policy_filter_events",
    "_candidate_generation_failure_event",
    "_replay_adaptation_exception_details",
    "_repair_contract_fingerprints",
    "_terminal_cause",
    "_retryable_infrastructure_details",
    "_rejection_attribution",
    "_campaign_failure_attribution",
    "_attribution_diagnostic_refs",
    "_resolved_conformance_contract_fingerprints",
    "optimize_explicit_target",
    "_load_or_build_campaign_dataset",
    "_empty_run_budget_report",
    "optimize_from_cli_request",
    "_run_candidate_generation_agent",
    "_replayable_user_task_dataset",
    "_target_package_inventory",
    "_target_package_sources",
    "_safe_artifact_name",
    "_stable_json_fingerprint",
    "_retention_controller",
    "_artifact_retention_report",
    "_finalize_run_report",
    "_replay_report",
    "_replay_capability_report",
    "_reusable_baseline_case_count",
    "_legacy_member_baseline_replay_dir",
    "_evaluator_report_paths",
    "_load_prior_rejected_feedback",
    "_load_prior_scheduler_state",
    "_repair_frontier_state_report",
    "_load_prior_candidate_package_index",
    "_load_prior_rejected_semantic_lesson_fingerprints",
    "_rejected_candidate_ids_from_report",
    "_lineage_records_from_report",
    "_lazy_import_lineage_records_from_report",
    "_lineage_importable_iterations",
    "_safe_lineage_file_stem",
    "_persist_lineage_lifecycle",
    "_lineage_addressed_lesson_ids",
    "_with_release_lesson_mapping",
    "_report_matches_screening_harness",
    "_prior_report_paths",
    "_feedback_from_report",
    "_gate_results_have_candidate_prerequisite_failure",
    "_repair_feedback_from_selected_candidate",
    "_selected_candidate_judge_metrics",
    "_historical_failure_artifact_excerpts",
    "_repair_feedback_from_screening_report",
    "_stored_repair_candidate_package",
    "_lesson_feedback_from_report",
    "_lessons_path_from_report",
    "_path_is_relative_to",
    "_bounded_text",
    "_historical_feedback_metrics",
    "_retryable_infrastructure_rejection",
    "_non_authoritative_candidate_rejection",
    "_has_missing_model_profile_judge_failure",
    "_evidence_quality_gate",
    "_summary_with_replay_evidence_metrics",
    "_replay_failure_summary",
    "_can_reuse_single_case_replay_validation",
    "_conformance_gate_blocks_population",
    "_gate_blocks_measurement_materialization",
    "_failed_probe_typed_feedback",
    "_repair_probe_root_cause_code",
    "_repair_conformance_required_nonempty_operations",
    "_repair_conformance_screening_attempt",
    "_repair_conformance_failure_diagnostics",
    "_replay_adaptation_details",
    "_environment_fingerprint_drift_gate",
    "_replay_confidence_gate",
    "_replay_stability_gate",
    "_finite_measurement_metric",
    "_optional_measurement_bool",
    "_non_negative_measurement_int",
    "_non_negative_measurement_float",
    "_budget_curve_points",
    "_trajectory_set_report",
    "_population_report",
    "_screening_observation_scope_fingerprint",
    "_restore_campaign_screening_case_observations",
    "_restore_historical_screening_lifecycle_observations",
    "_restore_authoritative_member_lifecycle_observations",
    "_merge_screening_variant_lifecycle_observation",
    "_merge_support_specific_lifecycle_observation",
    "_screening_control_preflight",
    "_no_op_report",
    "_acceptance_confidence_report",
    "_select_iteration_state",
    "_iteration_candidate_score",
    "_iteration_candidate_paired_delta",
    "_candidate_generation_limit",
}


LEGACY_HELPER_OWNER_ALIASES = {
    "_remaining_measurement_budget": (
        run_resources,
        "remaining_measurement_budget",
    ),
    "_configured_budget_usage": (run_budget_support, "configured_budget_usage"),
    "_backend_proves_zero_budget_usage": (
        run_budget_support,
        "backend_proves_zero_budget_usage",
    ),
}


CONTROLLER_PATH_OWNERS = (
    measurement_execution_datasets,
    retention,
    run_budget_support,
    run_generation_helpers,
)


def _imports(module: object) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def _top_level_functions(module: object) -> set[str]:
    return {
        node.name
        for node in ast.parse(inspect.getsource(module)).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _implementation_paths(function_names: set[str]) -> dict[str, set[Path]]:
    package_root = Path(inspect.getsourcefile(runner) or "").parent
    found = {name: set() for name in function_names}
    for path in package_root.rglob("*.py"):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in found
            ):
                found[node.name].add(path.resolve())
    return found


def test_leaf_helper_owners_do_not_import_runner_or_controllers() -> None:
    for module in LEAF_MODULES:
        imported = _imports(module)
        assert "aworld.self_evolve.runner" not in imported
        assert not any(
            name == "aworld.self_evolve.controllers"
            or name.startswith("aworld.self_evolve.controllers.")
            for name in imported
        ), module.__name__


def test_controller_path_owners_document_their_compatibility_exception() -> None:
    """Existing controller paths remain canonical for import compatibility."""

    for module in CONTROLLER_PATH_OWNERS:
        assert "aworld.self_evolve.runner" not in _imports(module), module.__name__


def test_dependency_leaf_controller_path_owners_import_no_controllers() -> None:
    """Pure owners stay leaves despite their historical controller path.

    ``run_generation_helpers`` remains a mixed controller helper cluster whose
    unrelated telemetry helpers still depend on ``run_telemetry``.  Its
    verification fingerprint owner is retained there for API compatibility,
    and the whole module is independently guarded against Runner imports above.
    """

    for module in (run_budget_support, measurement_execution_datasets, retention):
        imported = _imports(module)
        assert not any(
            name.startswith("aworld.self_evolve.controllers.") for name in imported
        ), module.__name__


def test_runner_no_longer_implements_owned_helpers() -> None:
    moved = set().union(*OWNER_FUNCTIONS.values()) - RUNNER_COMPATIBILITY_ALIASES
    assert not (_top_level_functions(runner) & (moved | DEAD_ORIGINAL_HELPERS))
    assert RUNNER_COMPATIBILITY_ALIASES <= _top_level_functions(runner)


def test_all_117_original_runner_helpers_have_an_explicit_disposition() -> None:
    declared = (
        set().union(*OWNER_FUNCTIONS.values())
        | set(LEGACY_HELPER_OWNER_ALIASES)
        | DEAD_ORIGINAL_HELPERS
        | PUBLIC_RUNNER_ENTRY_POINTS
    )
    assert len(ORIGINAL_RUNNER_HELPERS) == 117
    assert ORIGINAL_RUNNER_HELPERS - declared == set()

    owner_counts = {
        name: sum(name in names for names in OWNER_FUNCTIONS.values())
        for name in ORIGINAL_RUNNER_HELPERS
        if name not in LEGACY_HELPER_OWNER_ALIASES
        and name not in DEAD_ORIGINAL_HELPERS
        and name not in PUBLIC_RUNNER_ENTRY_POINTS
    }
    assert {name: count for name, count in owner_counts.items() if count != 1} == {}

    for _legacy_name, (owner, canonical_name) in LEGACY_HELPER_OWNER_ALIASES.items():
        assert canonical_name in _top_level_functions(owner)

    implementations = _implementation_paths(ORIGINAL_RUNNER_HELPERS)
    runner_path = Path(inspect.getsourcefile(runner) or "").resolve()
    for name in ORIGINAL_RUNNER_HELPERS:
        if name in DEAD_ORIGINAL_HELPERS or name in LEGACY_HELPER_OWNER_ALIASES:
            assert implementations[name] == set(), name
            continue
        if name in PUBLIC_RUNNER_ENTRY_POINTS:
            assert implementations[name] == {runner_path}, name
            continue

        owners = [owner for owner, names in OWNER_FUNCTIONS.items() if name in names]
        assert len(owners) == 1, name
        owner_path = Path(inspect.getsourcefile(owners[0]) or "").resolve()
        if name == "_bounded_text":
            assert owner_path in implementations[name]
            assert runner_path not in implementations[name]
            continue
        expected = {owner_path}
        if name in RUNNER_COMPATIBILITY_ALIASES:
            expected.add(runner_path)
        assert implementations[name] == expected, name


def test_helpers_have_their_declared_owner() -> None:
    for owner, expected in OWNER_FUNCTIONS.items():
        assert expected <= _top_level_functions(owner), owner.__name__


def test_helpers_have_no_runner_or_controller_duplicate_implementation() -> None:
    implementations = _implementation_paths(set().union(*OWNER_FUNCTIONS.values()))
    runner_path = Path(inspect.getsourcefile(runner) or "").resolve()
    for owner, names in OWNER_FUNCTIONS.items():
        owner_path = Path(inspect.getsourcefile(owner) or "").resolve()
        for name in names:
            if name in GENERIC_NAME_COLLISIONS:
                assert owner_path in implementations[name], name
                assert runner_path not in implementations[name], name
                continue
            expected = {owner_path}
            if name in RUNNER_COMPATIBILITY_ALIASES:
                expected.add(runner_path)
            assert implementations[name] == expected, name


def test_json_and_numeric_support_have_one_canonical_implementation() -> None:
    package_root = Path(inspect.getsourcefile(runner) or "").parent
    canonical_path = Path(inspect.getsourcefile(history_support) or "").resolve()
    found: dict[str, set[Path]] = {
        "_load_json_mapping": set(),
        "_non_negative_int": set(),
        "_non_negative_numeric_int": set(),
        "_non_negative_screening_float": set(),
    }
    for path in package_root.rglob("*.py"):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in found:
                continue
            if node.name == "_non_negative_int":
                positional = (*node.args.posonlyargs, *node.args.args)
                if len(positional) != 1 or node.args.kwonlyargs:
                    continue
            found[node.name].add(path.resolve())
    assert found == {name: {canonical_path} for name in found}


def test_old_controller_modules_do_not_duplicate_leaf_implementations() -> None:
    assert "_schema_field_contract_fingerprint" not in _top_level_functions(
        screening_execution
    )
    assert "_repair_contract_fingerprint" not in _top_level_functions(
        run_iteration_helpers
    )
    assert "_repair_conformance_failure_diagnostics" not in _top_level_functions(
        run_repair_conformance
    )
    assert "_replay_adaptation_exception_details" not in _top_level_functions(
        run_replay_adaptation
    )
    assert "_candidate_materialization_failures" not in _top_level_functions(
        run_generation_helpers
    )
    assert "_control_qualification_identity" not in _top_level_functions(
        screening_helpers
    )
    assert "_remaining_measurement_budget" not in _top_level_functions(
        run_iteration_helpers
    )


def test_runtime_controllers_import_leaf_owners_directly() -> None:
    terminal_imports = _imports(run_terminal_lifecycle)
    assert "aworld.self_evolve.run_failure_attribution" in terminal_imports
    assert "aworld.self_evolve.replay_gates" in terminal_imports
    assert "aworld.self_evolve.controllers.run_budget_support" in terminal_imports
    assert "aworld.self_evolve.target_package" in terminal_imports

    evaluation_imports = _imports(run_evaluation_execution)
    assert "aworld.self_evolve.evaluation_reporting" in evaluation_imports
    assert "aworld.self_evolve.replay_gates" in evaluation_imports

    assert "aworld.self_evolve.run_reporting" in terminal_imports
    assert "aworld.self_evolve.run_history" in terminal_imports
    assert "aworld.self_evolve.lineage_history" in terminal_imports
    assert "aworld.self_evolve.population_projection" in terminal_imports
    assert "aworld.self_evolve.iteration_selection" in terminal_imports


def test_screening_control_preflight_preserves_runner_monkeypatch_seam() -> None:
    assert (
        runner._screening_control_preflight
        is screening_observation_history._screening_control_preflight
    )


def test_legacy_helper_paths_are_direct_identity_aliases() -> None:
    assert (
        measurement_execution_datasets._control_qualification_identity
        is screening_observation_history._control_qualification_identity
    )
    assert (
        screening_helpers._candidate_validation_report_for_persistence
        is population_projection._candidate_validation_report_for_persistence
    )
    assert (
        screening_helpers._record_support_specific_control_observation
        is screening_observation_history._record_support_specific_control_observation
    )
    assert cli_rerun._load_candidate_variant is run_history._load_candidate_variant


def test_history_and_numeric_helpers_are_direct_identity_aliases() -> None:
    for module in (
        feedback_history,
        lineage_history,
        run_history,
        run_reporting,
        screening_observation_history,
    ):
        assert module._load_json_mapping is history_support._load_json_mapping
    assert screening_execution._load_json_mapping is history_support._load_json_mapping
    assert screening_execution._non_negative_int is history_support._non_negative_int
    assert screening_helpers._non_negative_int is history_support._non_negative_int
    for module in (run_budget_support, run_failure_attribution):
        assert (
            module._non_negative_int
            is history_support._non_negative_numeric_int
        )
        assert module._non_negative_int(1.9) == 1
    assert (
        screening_helpers._non_negative_screening_float
        is history_support._non_negative_screening_float
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        (Decimal("1.5"), 1.5),
        (float("nan"), 0.0),
        (float("inf"), 0.0),
        (float("-inf"), 0.0),
    ),
)
def test_population_projection_preserves_finite_numeric_contract(
    value: object,
    expected: float,
) -> None:
    report = population_projection._population_report(
        all_candidates=[],
        iteration_reports=[
            {
                "candidate_id": "candidate",
                "lifecycle_stage": "authoritative_replay",
            }
        ],
        replay_candidate_limit=1,
        screening_reports=[{"screening": {"screening_wall_seconds": value}}],
    )
    assert report is not None
    screening_execution_report = report["screening_execution"]
    assert isinstance(screening_execution_report, dict)
    assert screening_execution_report["wall_seconds"] == expected


def test_json_mapping_exception_contract_reaches_candidate_loader(
    tmp_path: Path,
) -> None:
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text("[]", encoding="utf-8")
    message = f"expected JSON object in {candidate_path}"

    for loader in (
        history_support._load_json_mapping,
        screening_execution._load_json_mapping,
        run_history._load_candidate_variant,
    ):
        with pytest.raises(ValueError) as exc_info:
            loader(candidate_path)
        assert type(exc_info.value) is ValueError
        assert str(exc_info.value) == message


def test_terminal_services_only_retains_final_persistence_seam() -> None:
    assert set(run_terminal_lifecycle.RunTerminalLifecycleServices.__annotations__) == {
        "_finalize_run_report"
    }


def test_cli_target_runtime_path_is_canonical_owner_alias() -> None:
    assert (
        cli_orchestration._target_runtime_skill_path
        is target_package._target_runtime_skill_path
    )


def test_apply_target_runtime_path_is_canonical_owner_alias_without_duplicates() -> None:
    assert (
        run_apply_transaction._runtime_skill_path
        is target_package._target_runtime_skill_path
    )
    definitions = []
    for module in (target_package, cli_orchestration, run_apply_transaction):
        tree = ast.parse(inspect.getsource(module))
        definitions.extend(
            (module.__name__, node.name)
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name
            in {
                "_target_runtime_skill_path",
                "_runtime_skill_path",
                "_target_runtime_path",
            }
        )
    assert definitions == [
        (target_package.__name__, "_target_runtime_skill_path")
    ]


def test_runner_verification_fingerprint_alias_forwards_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, object] = {}

    def owned(**kwargs: object) -> str:
        received.update(kwargs)
        return "sha256:verification"

    monkeypatch.setattr(
        runner,
        "_generation_verification_contract_fingerprint",
        owned,
    )

    assert runner._verification_contract_fingerprint(contract="value") == (
        "sha256:verification"
    )
    assert received == {
        "contract": "value",
        "verification_contract_version": runner._VERIFICATION_CONTRACT_VERSION,
    }


def test_runner_retention_controller_alias_preserves_cleanup_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = object()
    expected = object()
    received: dict[str, object] = {}

    def owned(actual_store: object, *, cleanup: object) -> object:
        received.update(store=actual_store, cleanup=cleanup)
        return expected

    monkeypatch.setattr(runner, "_owned_retention_controller", owned)

    assert runner._retention_controller(store) is expected
    assert received == {
        "store": store,
        "cleanup": runner.cleanup_self_evolve_artifacts,
    }


def test_runner_artifact_retention_alias_preserves_cleanup_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = object()
    previous = {"policy": "previous"}
    expected = {"policy": "current"}
    received: dict[str, object] = {}

    def owned(
        actual_store: object,
        run_id: str,
        *,
        previous: object,
        cleanup: object,
    ) -> dict[str, object]:
        received.update(
            store=actual_store,
            run_id=run_id,
            previous=previous,
            cleanup=cleanup,
        )
        return expected

    monkeypatch.setattr(runner, "_owned_artifact_retention_report", owned)

    assert (
        runner._artifact_retention_report(
            store,
            "run-1",
            previous=previous,
        )
        is expected
    )
    assert received == {
        "store": store,
        "run_id": "run-1",
        "previous": previous,
        "cleanup": runner.cleanup_self_evolve_artifacts,
    }


def test_runner_finalize_report_alias_preserves_cleanup_seam(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = object()
    report = {"status": "completed"}
    completed_run = object()
    previous = {"policy": "previous"}
    expected = tmp_path / "report.json"
    received: dict[str, object] = {}

    def owned(
        actual_store: object,
        run_id: str,
        *,
        report: object,
        completed_run: object,
        previous_artifact_retention: object,
        cleanup: object,
    ) -> Path:
        received.update(
            store=actual_store,
            run_id=run_id,
            report=report,
            completed_run=completed_run,
            previous_artifact_retention=previous_artifact_retention,
            cleanup=cleanup,
        )
        return expected

    monkeypatch.setattr(runner, "_owned_finalize_run_report", owned)

    assert (
        runner._finalize_run_report(
            store,
            "run-1",
            report=report,
            completed_run=completed_run,
            previous_artifact_retention=previous,
        )
        == expected
    )
    assert received == {
        "store": store,
        "run_id": "run-1",
        "report": report,
        "completed_run": completed_run,
        "previous_artifact_retention": previous,
        "cleanup": runner.cleanup_self_evolve_artifacts,
    }


def test_public_artifact_retention_compatibility_forwards_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = object()
    previous = {"policy": "previous"}
    expected = {"policy": "current"}
    received: dict[str, object] = {}

    def owned(
        actual_store: object,
        run_id: str,
        *,
        previous: object,
    ) -> dict[str, object]:
        received.update(store=actual_store, run_id=run_id, previous=previous)
        return expected

    monkeypatch.setattr(retention, "_artifact_retention_report", owned)

    assert (
        retention.artifact_retention_report(
            store,
            "run-1",
            previous=previous,
        )
        is expected
    )
    assert received == {
        "store": store,
        "run_id": "run-1",
        "previous": previous,
    }


def test_public_finalize_report_compatibility_forwards_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = object()
    report = {"status": "completed"}
    completed_run = object()
    previous = {"policy": "previous"}
    expected = tmp_path / "report.json"
    received: dict[str, object] = {}

    def owned(
        actual_store: object,
        run_id: str,
        *,
        report: object,
        completed_run: object,
        previous_artifact_retention: object,
    ) -> Path:
        received.update(
            store=actual_store,
            run_id=run_id,
            report=report,
            completed_run=completed_run,
            previous_artifact_retention=previous_artifact_retention,
        )
        return expected

    monkeypatch.setattr(retention, "_finalize_run_report", owned)

    assert (
        retention.finalize_run_report(
            store,
            "run-1",
            report=report,
            completed_run=completed_run,
            previous_artifact_retention=previous,
        )
        == expected
    )
    assert received == {
        "store": store,
        "run_id": "run-1",
        "report": report,
        "completed_run": completed_run,
        "previous_artifact_retention": previous,
    }
