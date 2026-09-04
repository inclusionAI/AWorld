from __future__ import annotations

import ast
import inspect
from dataclasses import fields, is_dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from aworld.self_evolve.budget import CandidateAttemptKey, CandidateAttemptStage
from aworld.self_evolve.controllers import (
    run_capability_validation,
    run_repair_conformance,
    run_replay_adaptation,
)
from aworld.self_evolve.controllers.run_capability_validation import (
    CapabilityValidationPolicy,
    CapabilityValidationRequest,
    CapabilityValidationRuntime,
    validate_candidate_capabilities,
)
from aworld.self_evolve.controllers.run_repair_conformance import (
    RepairConformancePopulationRequest,
    RepairConformancePopulationRuntime,
    RepairConformancePreflightRequest,
    RepairConformancePreflightResult,
    RepairConformancePreflightRuntime,
    preflight_candidate_repair_conformance,
    validate_repair_conformance_population,
)
from aworld.self_evolve.controllers.run_replay_adaptation import (
    BaselineReuseProvenanceRequest,
    BaselineReuseProvenanceRuntime,
    ReplayAdaptationExecution,
    ReplayAdaptationRequest,
    ReplayAdaptationResult,
    ReplayAdaptationRuntime,
    ReplayAdaptationState,
    baseline_reuse_provenance,
    execute_replay_adaptation,
)
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.optimizers.base import OptimizerResult
from aworld.self_evolve.repair_conformance import (
    RepairConformanceContract,
    RepairConformanceResult,
)
from aworld.self_evolve.replay_adaptation import ReplayAdaptationCompiler
from aworld.self_evolve.replay import ReplayServiceProtocolError
from aworld.self_evolve.replay_capability import ReplayCapabilityError
from aworld.self_evolve.runner import SelfEvolveRunner
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.types import (
    CandidateVariant,
    DatasetRecipe,
    GateResult,
    SelfEvolveTargetRef,
)


def _dataset() -> SelfEvolveDataset:
    return SelfEvolveDataset(
        cases=(EvalCase(case_id="case-1", input="task"),),
        recipe=DatasetRecipe(
            source={"kind": "controller-test"},
            split_seed="seed",
            splits={"train": ["case-1"]},
            trainable_case_ids=("case-1",),
        ),
    )


def _candidate() -> CandidateVariant:
    return CandidateVariant(
        candidate_id="candidate-1",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="test",
    )


def _adaptation_execution(
    tmp_path: Path,
    *,
    override=None,
) -> ReplayAdaptationExecution:
    return ReplayAdaptationExecution(
        runtime=ReplayAdaptationRuntime(
            store=FilesystemSelfEvolveStore(tmp_path),
            compiler=ReplayAdaptationCompiler(),
        ),
        state=ReplayAdaptationState(),
        override=override,
    )


def _preflight_runtime(
    tmp_path: Path,
    *,
    adaptation: ReplayAdaptationExecution | None = None,
) -> RepairConformancePreflightRuntime:
    return RepairConformancePreflightRuntime(
        store=FilesystemSelfEvolveStore(tmp_path),
        replay_adaptation=adaptation or _adaptation_execution(tmp_path),
        create_candidate_skill_overlay=lambda **_: (_ for _ in ()).throw(
            AssertionError("overlay must not run")
        ),
    )


def _imported_module_names(source: str) -> set[str]:
    imported_modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.add(node.module)
            imported_modules.update(
                f"{node.module}.{alias.name}"
                for alias in node.names
                if alias.name != "*"
            )
    return imported_modules


def _has_runner_reverse_import(source: str) -> bool:
    return any(
        name == "aworld.self_evolve.runner"
        or name.startswith("aworld.self_evolve.runner.")
        for name in _imported_module_names(source)
    )


def test_replay_adaptation_state_cleans_only_run_owned_entries() -> None:
    state = ReplayAdaptationState()
    first = ("run-1", "dataset", "capability")
    second = ("run-2", "dataset", "capability")
    state.adaptation_cache[first] = (None, SimpleNamespace())  # type: ignore[assignment]
    state.adaptation_cache[second] = (None, SimpleNamespace())  # type: ignore[assignment]
    state.dataset_preflight_cache["dataset"] = SimpleNamespace()  # type: ignore[assignment]
    state.environment_fingerprints.update({"run-1": "one", "run-2": "two"})

    state.cleanup_run("run-1")

    assert first not in state.adaptation_cache
    assert second in state.adaptation_cache
    assert state.environment_fingerprints == {"run-2": "two"}
    assert "dataset" in state.dataset_preflight_cache


@pytest.mark.asyncio
async def test_conformance_preflight_returns_typed_missing_path_gate(
    tmp_path: Path,
) -> None:
    request = RepairConformancePreflightRequest(
        run_id="run-1",
        target=SimpleNamespace(identity=SimpleNamespace(path=None)),
        dataset=_dataset(),
        candidate=_candidate(),
        contract=RepairConformanceContract(
            focus_candidate_id="parent",
            failure_codes=("failed",),
            interaction_progress=0,
            base_file_fingerprints={},
            required_branch_paths=(),
            base_branch_fingerprints={},
        ),
    )
    runtime = _preflight_runtime(tmp_path)

    result = await preflight_candidate_repair_conformance(request, runtime)

    assert result.gate.passed is False
    assert result.gate.details["code"] == "repair_target_path_missing"


@pytest.mark.asyncio
async def test_conformance_population_preserves_non_applicable_population(
    tmp_path: Path,
) -> None:
    candidate = _candidate()
    result = await validate_repair_conformance_population(
        RepairConformancePopulationRequest(
            run_id="run-1",
            target=SimpleNamespace(),
            dataset=_dataset(),
            candidates=(candidate,),
            capability_requirements=(),
            repair_conformance_contracts={},
        ),
        RepairConformancePopulationRuntime(
            progress_callback=None,
            preflight_runtime=_preflight_runtime(tmp_path),
            emit_progress=lambda *_: None,
        ),
    )

    assert result.candidates == (candidate,)
    assert result.report is None


@pytest.mark.asyncio
async def test_capability_validation_preserves_disabled_list_compatibility(
    tmp_path: Path,
) -> None:
    result = await validate_candidate_capabilities(
        CapabilityValidationRequest(
            run_id="run-1",
            target=SimpleNamespace(identity=SimpleNamespace(path=None)),
            dataset=_dataset(),
            candidate=_candidate(),
            requirements=(),
        ),
        CapabilityValidationPolicy(replay_enabled=False),
        CapabilityValidationRuntime(
            store=FilesystemSelfEvolveStore(tmp_path),
            replay_adaptation=_adaptation_execution(tmp_path),
            create_candidate_skill_overlay=lambda **_: (_ for _ in ()).throw(
                AssertionError("overlay must not run")
            ),
        ),
    )

    assert result.as_list() == []


def test_replay_adaptation_execution_caches_identity_and_builds_baseline_provenance(
    tmp_path: Path,
) -> None:
    execution = _adaptation_execution(tmp_path)
    request = ReplayAdaptationRequest(
        run_id="run-cache",
        dataset=_dataset(),
        emit_progress=False,
    )

    first = execute_replay_adaptation(request, execution)
    second = execute_replay_adaptation(request, execution)

    assert first.gate.passed is True
    assert second.bundle is first.bundle
    assert len(execution.state.adaptation_cache) == 1
    provenance = baseline_reuse_provenance(
        BaselineReuseProvenanceRequest(
            run_id="run-cache",
            target=SimpleNamespace(
                fingerprint_current_content=lambda: "sha256:baseline"
            ),
            dataset=_dataset(),
        ),
        BaselineReuseProvenanceRuntime(replay_adaptation=execution),
    ).provenance
    assert provenance["baseline_skill_fingerprint"] == "sha256:baseline"
    assert provenance["adaptation_fingerprint"] == (first.bundle.adaptation_fingerprint)


def test_replay_adaptation_runtime_default_attributes_schema_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid_capability(_):
        raise ReplayCapabilityError(
            "invalid compiler schema",
            code="schema_field_validation_failed",
            details={
                "schema_field_constraints": [
                    {
                        "schema_layer": "compile_result",
                        "field_path": "services[*].transport",
                        "rule": "enum",
                    }
                ]
            },
        )

    monkeypatch.setattr(
        run_replay_adaptation,
        "discover_replay_capability",
        invalid_capability,
    )
    result = execute_replay_adaptation(
        ReplayAdaptationRequest(
            run_id="run-schema",
            dataset=_dataset(),
            capability_skill_root=tmp_path,
            emit_progress=False,
        ),
        _adaptation_execution(tmp_path),
    )

    event = result.gate.details["failure_event"]
    assert event["contract_fingerprint"].startswith("schema-fields:sha256:")


@pytest.mark.asyncio
async def test_applicable_population_composes_typed_preflight_directly(
    tmp_path: Path,
) -> None:
    candidate = _candidate()
    contract = RepairConformanceContract(
        focus_candidate_id="parent",
        failure_codes=("failed",),
        interaction_progress=0,
        base_file_fingerprints={},
        required_branch_paths=(),
        base_branch_fingerprints={},
    )
    result = await validate_repair_conformance_population(
        RepairConformancePopulationRequest(
            run_id="run-applicable",
            target=SimpleNamespace(identity=SimpleNamespace(path=None)),
            dataset=_dataset(),
            candidates=(candidate,),
            capability_requirements=(),
            repair_conformance_contracts={candidate.candidate_id: contract},
        ),
        RepairConformancePopulationRuntime(
            progress_callback=None,
            preflight_runtime=_preflight_runtime(tmp_path),
            emit_progress=lambda *_: None,
            evaluate_candidate_source_conformance=lambda *_: (
                RepairConformanceResult(
                    passed=True,
                    code="source_conformance_passed",
                    reason="passed",
                    details={},
                )
            ),
        ),
    )

    assert result.candidates == ()
    assert result.report is not None
    assert result.report["attempts"][0]["details"]["code"] == (
        "repair_target_path_missing"
    )


@pytest.mark.asyncio
async def test_population_typed_override_records_attempt_transitions(
    tmp_path: Path,
) -> None:
    candidate = _candidate()
    contract = RepairConformanceContract(
        focus_candidate_id="parent",
        failure_codes=("failed",),
        interaction_progress=0,
        base_file_fingerprints={},
        required_branch_paths=(),
        base_branch_fingerprints={},
    )
    attempt_key = CandidateAttemptKey(run_id="run-attempt", iteration=1, slot=1)

    class Tracker:
        emitted: list[CandidateAttemptStage] = []

        def last_stage(self, _):
            return CandidateAttemptStage.LOCAL_GATES

        def emit(self, _, stage, **__):
            self.emitted.append(stage)

        def terminal(self, _):
            return False

    class TypedPreflight:
        async def __call__(self, request):
            assert isinstance(request, RepairConformancePreflightRequest)
            return RepairConformancePreflightResult(
                GateResult(
                    gate_name="candidate_repair_conformance",
                    passed=True,
                    reason="passed",
                    details={"code": "repair_conformance_passed"},
                )
            )

    tracker = Tracker()
    result = await validate_repair_conformance_population(
        RepairConformancePopulationRequest(
            run_id="run-attempt",
            target=SimpleNamespace(),
            dataset=_dataset(),
            candidates=(candidate,),
            capability_requirements=(),
            repair_conformance_contracts={candidate.candidate_id: contract},
            attempt_tracker=tracker,  # type: ignore[arg-type]
            attempt_keys={candidate.candidate_id: attempt_key},
        ),
        RepairConformancePopulationRuntime(
            progress_callback=None,
            preflight_runtime=_preflight_runtime(tmp_path),
            preflight_override=TypedPreflight(),
            emit_progress=lambda *_: None,
            evaluate_candidate_source_conformance=lambda *_: (
                RepairConformanceResult(True, "passed", "passed", {})
            ),
        ),
    )

    assert result.candidates == (candidate,)
    assert tracker.emitted == [
        CandidateAttemptStage.ADAPTATION,
        CandidateAttemptStage.CONFORMANCE,
    ]


@pytest.mark.asyncio
async def test_preflight_settles_conformance_budget_after_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Demo\n", encoding="utf-8")
    candidate = CandidateVariant(
        candidate_id="candidate-budget",
        target=SelfEvolveTargetRef(
            target_type="skill", target_id="demo", path=str(skill_path)
        ),
        content="# Demo changed\n",
        rationale="test",
    )
    capability = SimpleNamespace(capability_id="capability", services=())
    group = SimpleNamespace(
        fingerprint="sha256:group",
        operation="query",
        requirement_id="requirement",
        case_ids=("case-1",),
    )
    plan = SimpleNamespace(groups=(group,), to_dict=lambda: {"groups": [{}]})
    monkeypatch.setattr(
        run_repair_conformance,
        "build_repair_conformance_probe_plan",
        lambda **_: plan,
    )
    monkeypatch.setattr(
        run_repair_conformance,
        "project_replay_capability_for_probe_group",
        lambda *_: capability,
    )

    class Budget:
        reserved = 0
        debited = 0

        def reserve(self, *_, **__):
            self.reserved += 1
            return SimpleNamespace(allowed=True)

        def debit(self, *_, **__):
            self.debited += 1

    async def preflight(*_, **__):
        return None

    adaptation = _adaptation_execution(
        tmp_path,
        override=lambda _: ReplayAdaptationResult(
            SimpleNamespace(replay_capability=capability),
            GateResult("replay_adaptation", True, "passed"),
        ),
    )
    budget = Budget()
    result = await preflight_candidate_repair_conformance(
        RepairConformancePreflightRequest(
            run_id="run-budget",
            target=SimpleNamespace(
                identity=SimpleNamespace(path=skill_path),
                baseline_skill_roots=(),
            ),
            dataset=_dataset(),
            candidate=candidate,
            contract=RepairConformanceContract(
                focus_candidate_id="parent",
                failure_codes=("failed",),
                interaction_progress=0,
                base_file_fingerprints={},
                required_branch_paths=(),
                base_branch_fingerprints={},
            ),
            budget_context=budget,  # type: ignore[arg-type]
        ),
        RepairConformancePreflightRuntime(
            store=FilesystemSelfEvolveStore(tmp_path),
            replay_adaptation=adaptation,
            create_candidate_skill_overlay=lambda **_: SimpleNamespace(
                candidate_skill_path=skill_path
            ),
            evaluate_compiled_probe_conformance=lambda *_args, **_kwargs: (
                RepairConformanceResult(True, "passed", "passed", {})
            ),
            replay_capability_fixture_leaf_values=lambda _: {},
            replay_capability_fixture_response_leaf_values=lambda _: {},
            frozen_replay_fixture_shape_fingerprints=lambda _: {},
            preflight_frozen_replay_capability=preflight,
        ),
    )

    assert result.gate.passed is True
    assert budget.reserved == 1
    assert budget.debited == 1


@pytest.mark.asyncio
async def test_preflight_projects_http_route_failure_into_typed_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Demo\n", encoding="utf-8")
    candidate = CandidateVariant(
        candidate_id="candidate-route",
        target=SelfEvolveTargetRef(
            target_type="skill", target_id="demo", path=str(skill_path)
        ),
        content="# Demo changed\n",
        rationale="test",
    )
    capability = SimpleNamespace(capability_id="capability", services=())
    groups = tuple(
        SimpleNamespace(
            fingerprint=f"sha256:group-{index}",
            operation="query",
            requirement_id=f"requirement-{index}",
            case_ids=(f"case-{index}",),
        )
        for index in (1, 2)
    )
    plan = SimpleNamespace(groups=groups, to_dict=lambda: {"groups": [{}, {}]})
    monkeypatch.setattr(
        run_repair_conformance,
        "build_repair_conformance_probe_plan",
        lambda **_: plan,
    )
    monkeypatch.setattr(
        run_repair_conformance,
        "project_replay_capability_for_probe_group",
        lambda *_: capability,
    )

    probe_paths = iter(("/abs/2605.11182", "/lotte/status/2056754091817361670"))

    async def preflight(*_, **__) -> None:
        probe_path = next(probe_paths)
        raise ReplayServiceProtocolError(
            "HTTP replay probe returned status 404; expected 2xx",
            code="replay_service_http_status_mismatch",
            details={
                "probe_phase": "protocol_probe",
                "probe_kind": "http",
                "probe_path": probe_path,
                "observed_http_status": 404,
                "required_http_status_class": "2xx",
                "service_id": "browser-runtime",
                "transport": "skill_runtime",
                "runtime_route_constraints": [
                    {
                        "schema_version": (
                            "aworld.self_evolve.runtime_route_constraint.v1"
                        ),
                        "constraint_kind": "framework_bound_task_entry_route",
                        "transport": "skill_runtime",
                        "probe_kind": "http",
                        "path_source": "requirement_identifier_path",
                        "required_status_class": "2xx",
                        "routing_behavior": "serve_framework_bound_path",
                    }
                ],
            },
        )

    adaptation = _adaptation_execution(
        tmp_path,
        override=lambda _: ReplayAdaptationResult(
            SimpleNamespace(replay_capability=capability),
            GateResult("replay_adaptation", True, "passed"),
        ),
    )
    result = await preflight_candidate_repair_conformance(
        RepairConformancePreflightRequest(
            run_id="run-route",
            target=SimpleNamespace(
                identity=SimpleNamespace(path=skill_path),
                baseline_skill_roots=(),
            ),
            dataset=_dataset(),
            candidate=candidate,
            contract=RepairConformanceContract(
                focus_candidate_id="parent",
                failure_codes=("failed",),
                interaction_progress=0,
                base_file_fingerprints={},
                required_branch_paths=("replay/runtime.py",),
                base_branch_fingerprints={},
                runtime_paths=("replay/runtime.py",),
            ),
        ),
        RepairConformancePreflightRuntime(
            store=FilesystemSelfEvolveStore(tmp_path),
            replay_adaptation=adaptation,
            create_candidate_skill_overlay=lambda **_: SimpleNamespace(
                candidate_skill_path=skill_path
            ),
            evaluate_compiled_probe_conformance=lambda *_args, **_kwargs: (
                RepairConformanceResult(True, "passed", "passed", {})
            ),
            replay_capability_fixture_leaf_values=lambda _: {},
            replay_capability_fixture_response_leaf_values=lambda _: {},
            frozen_replay_fixture_shape_fingerprints=lambda _: {},
            preflight_frozen_replay_capability=preflight,
        ),
    )

    assert result.gate.passed is False
    assert {
        item["probe_path"] for item in result.gate.details["diagnostics"]
    } == {"/abs/2605.11182", "/lotte/status/2056754091817361670"}
    assert result.gate.details["diagnostics"][0]["observed_http_status"] == 404
    assert result.gate.details["repair_conformance"][
        "runtime_route_constraints"
    ][0]["routing_behavior"] == "serve_framework_bound_path"
    assert len(result.gate.details["causal_failure_events"]) == 1
    event = result.gate.details["causal_failure_events"][0]
    assert event["occurrence_count"] == 2
    assert event["contract_identity_digest"]
    assert event["requirement_identity_digest"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shared", "failure_class"),
    ((False, "candidate"), (True, "infrastructure")),
)
async def test_capability_compile_failure_preserves_candidate_vs_shared_cause(
    tmp_path: Path,
    shared: bool,
    failure_class: str,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Demo\n", encoding="utf-8")
    candidate = CandidateVariant(
        candidate_id="candidate-capability",
        target=SelfEvolveTargetRef(
            target_type="skill", target_id="demo", path=str(skill_path)
        ),
        content="# Demo changed\n",
        rationale="test",
    )

    def adaptation(request):
        details = {
            "failure_owner": "infrastructure" if shared else "candidate",
            "failure_scope": "shared_run" if shared else "candidate",
            "failure_source": "native",
            "code": "compile_failed",
        }
        return ReplayAdaptationResult(
            None,
            GateResult("replay_adaptation", False, "failed", details=details),
        )

    result = await validate_candidate_capabilities(
        CapabilityValidationRequest(
            run_id="run-capability",
            target=SimpleNamespace(
                identity=SimpleNamespace(path=skill_path),
                baseline_skill_roots=(),
            ),
            dataset=_dataset(),
            candidate=candidate,
            requirements=(SimpleNamespace(),),  # type: ignore[arg-type]
        ),
        CapabilityValidationPolicy(replay_enabled=True),
        CapabilityValidationRuntime(
            store=FilesystemSelfEvolveStore(tmp_path),
            replay_adaptation=_adaptation_execution(tmp_path, override=adaptation),
            create_candidate_skill_overlay=lambda **_: SimpleNamespace(
                candidate_skill_path=skill_path
            ),
            validate_applicable_capabilities=lambda **_: (
                SimpleNamespace(capability_type="replay", passed=True, diagnostics=()),
            ),
        ),
    )

    assert result.gates[0].passed is False
    assert result.gates[0].details["failure_class"] == failure_class


@pytest.mark.asyncio
async def test_capability_startup_timeout_remains_shared_and_retains_diagnostics(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Demo\n", encoding="utf-8")
    candidate = CandidateVariant(
        candidate_id="candidate-startup-timeout",
        target=SelfEvolveTargetRef(
            target_type="skill", target_id="demo", path=str(skill_path)
        ),
        content="# Demo changed\n",
        rationale="test",
    )
    capability = SimpleNamespace(capability_id="capability-1", fingerprint="fp")
    adaptation_calls = 0

    def adaptation(_request):
        nonlocal adaptation_calls
        adaptation_calls += 1
        if adaptation_calls == 1:
            return ReplayAdaptationResult(
                None,
                GateResult("replay_adaptation", False, "baseline unavailable"),
            )
        return ReplayAdaptationResult(
            SimpleNamespace(replay_capability=capability),
            GateResult("replay_adaptation", True, "candidate compiled"),
        )

    async def preflight(_capability, *, artifact_dir):
        artifact_root = Path(artifact_dir)
        service_root = artifact_root / "replay_services" / "service-1"
        diagnostic_root = artifact_root / "diagnostics" / "service-1"
        service_root.mkdir(parents=True)
        diagnostic_root.mkdir(parents=True)
        (service_root / "stdout.txt").write_text("starting", encoding="utf-8")
        (service_root / "stderr.txt").write_text("waiting", encoding="utf-8")
        (diagnostic_root / "launch.json").write_text("{}", encoding="utf-8")
        raise TimeoutError("readiness timeout")

    result = await validate_candidate_capabilities(
        CapabilityValidationRequest(
            run_id="run-startup-timeout",
            target=SimpleNamespace(
                identity=SimpleNamespace(path=skill_path),
                baseline_skill_roots=(),
            ),
            dataset=_dataset(),
            candidate=candidate,
            requirements=(SimpleNamespace(),),  # type: ignore[arg-type]
        ),
        CapabilityValidationPolicy(replay_enabled=True),
        CapabilityValidationRuntime(
            store=FilesystemSelfEvolveStore(tmp_path),
            replay_adaptation=_adaptation_execution(tmp_path, override=adaptation),
            create_candidate_skill_overlay=lambda **_: SimpleNamespace(
                candidate_skill_path=skill_path
            ),
            validate_applicable_capabilities=lambda **_: (
                SimpleNamespace(capability_type="replay", passed=True, diagnostics=()),
            ),
            preflight_frozen_replay_capability=preflight,
            replay_service_start_failure_details=lambda *_args, **_kwargs: {
                "code": "replay_service_startup_timeout",
                "outcome": "infrastructure_failure",
                "repairable": True,
                "diagnostics": {"phase": "startup"},
            },
        ),
    )

    gate = result.gates[0]
    assert gate.passed is False
    assert gate.details["failure_class"] == "infrastructure"
    assert gate.details["code"] == "replay_service_startup_timeout"
    event = gate.details["failure_event"]
    assert event["owner"] == "infrastructure"
    assert event["scope"] == "shared_run"
    assert {Path(ref).name for ref in gate.details["diagnostic_refs"]} == {
        "launch.json",
        "stderr.txt",
        "stdout.txt",
    }


def test_phase_controllers_never_reverse_import_runner() -> None:
    for module in (
        run_replay_adaptation,
        run_repair_conformance,
        run_capability_validation,
    ):
        assert not _has_runner_reverse_import(inspect.getsource(module))


@pytest.mark.parametrize(
    "source",
    (
        "import aworld.self_evolve.runner\n",
        "from aworld.self_evolve.runner import SelfEvolveRunner\n",
        "from aworld.self_evolve import runner\n",
        "from aworld.self_evolve import runner as runner_module\n",
    ),
)
def test_reverse_import_guard_rejects_equivalent_import_forms(source: str) -> None:
    assert _has_runner_reverse_import(source)


def test_production_phase_runtime_wiring_retains_no_bound_runner(
    tmp_path: Path,
) -> None:
    class Optimizer:
        async def propose(self, _):
            return OptimizerResult(candidates=())

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=Optimizer(),
    )
    runtimes = (
        runner._replay_adaptation_execution(),
        runner._repair_conformance_preflight_runtime(),
        runner._repair_conformance_population_runtime(),
        runner._capability_validation_runtime(),
        BaselineReuseProvenanceRuntime(
            replay_adaptation=runner._replay_adaptation_execution()
        ),
    )
    visited: set[int] = set()

    def inspect_value(value) -> None:
        if id(value) in visited:
            return
        visited.add(id(value))
        if callable(value):
            assert not isinstance(getattr(value, "__self__", None), SelfEvolveRunner)
        if is_dataclass(value) and not isinstance(value, type):
            for item in fields(value):
                inspect_value(getattr(value, item.name))
        elif isinstance(value, (tuple, list, set, frozenset)):
            for item in value:
                inspect_value(item)
        elif isinstance(value, dict):
            for item in value.values():
                inspect_value(item)

    for runtime in runtimes:
        inspect_value(runtime)


def test_runner_phase_methods_remain_typed_adapters() -> None:
    limits = {
        "_prepare_replay_adaptation": 35,
        "_baseline_reuse_provenance": 35,
        "_validate_candidate_repair_conformance_population": 35,
        "_preflight_candidate_repair_conformance": 45,
        "_validate_candidate_capabilities": 35,
    }
    for method_name, limit in limits.items():
        source = inspect.getsource(getattr(SelfEvolveRunner, method_name))
        function = ast.parse(inspect.cleandoc(source)).body[0]
        assert function.end_lineno is not None
        assert function.end_lineno <= limit


def test_runner_no_longer_owns_phase_diagnostic_helpers() -> None:
    tree = ast.parse(
        inspect.getsource(__import__("aworld.self_evolve.runner", fromlist=["*"]))
    )
    owned = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not owned.intersection(
        {
            "_failed_probe_typed_feedback",
            "_repair_probe_root_cause_code",
            "_repair_conformance_validation_surface_changed",
            "_replay_adaptation_exception_details",
            "_environment_fingerprint_drift_gate",
        }
    )
