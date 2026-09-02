from __future__ import annotations

import ast
import asyncio
import inspect
import json
import shutil
from dataclasses import fields, is_dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from aworld.self_evolve.controllers import (
    run_apply_transaction,
    run_challenge_execution,
    run_candidate_execution,
    run_observers,
    run_regression_execution,
    run_verified_only_apply,
)
import aworld.self_evolve.controllers.run_verified_only_apply as verified_apply_module
from aworld.self_evolve.controllers.run_apply_transaction import (
    ApplyTransactionExecution,
    ApplyTransactionPolicy,
    ApplyTransactionRequest,
    ApplyTransactionRuntime,
    execute_apply_transaction,
)
from aworld.self_evolve.controllers.run_observers import safe_emit_progress
from aworld.self_evolve.controllers.run_challenge_execution import (
    ChallengeExecution,
    ChallengeExecutionPolicy,
    ChallengeExecutionRequest,
    ChallengeExecutionRuntime,
    execute_challenge,
)
from aworld.self_evolve.controllers.run_regression_execution import (
    RegressionReplayExecution,
    RegressionReplayRequest,
    RegressionExecutionPolicy,
    RegressionExecutionRequest,
    RegressionExecutionRuntime,
    execute_independent_regression,
)
from aworld.self_evolve.controllers.run_verified_only_apply import (
    VerifiedOnlyApplyPolicy,
    VerifiedOnlyApplyRequest,
    VerifiedOnlyApplyRuntime,
    execute_verified_only_apply,
)
import aworld.self_evolve.runner as runner_module
from aworld.self_evolve.runner import SelfEvolveRunner
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SkillTextTarget
from aworld.self_evolve.concurrency import SelfEvolveExecutionTelemetry
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.regression import (
    RegressionSuiteSpec,
    ResolvedRegressionSuite,
    dataset_case_fingerprints,
)
from aworld.self_evolve.replay import replay_dataset_fingerprint
from aworld.self_evolve.types import (
    CandidateVariant,
    DatasetRecipe,
    EvaluationSummary,
    SelfEvolveTargetRef,
)


def _skill_candidate(target: SkillTextTarget) -> CandidateVariant:
    return CandidateVariant(
        candidate_id="candidate-1",
        target=target.identity,
        content="---\nname: demo\n---\n# Demo\n\nUpdated guidance.\n",
        rationale="test",
        target_fingerprint=target.fingerprint_current_content(),
    )


def _dataset(case_id: str = "case-1") -> SelfEvolveDataset:
    return SelfEvolveDataset(
        cases=(EvalCase(case_id=case_id, input=f"task-{case_id}"),),
        recipe=DatasetRecipe(
            source={"kind": "controller-test"},
            split_seed="seed",
            splits={"train": [case_id]},
            trainable_case_ids=(case_id,),
        ),
    )


def _suite(dataset: SelfEvolveDataset) -> ResolvedRegressionSuite:
    return ResolvedRegressionSuite(
        spec=RegressionSuiteSpec(
            suite_id="independent-suite",
            source_kind="jsonl",
            source_ref="independent.jsonl",
            source_version="sha256:source",
            dataset_fingerprint=replay_dataset_fingerprint(dataset),
            split_fingerprint="sha256:split",
            case_fingerprints=dataset_case_fingerprints(dataset),
        ),
        dataset=dataset,
    )


def _cancel_progress(_stage: str, _message: str) -> None:
    raise asyncio.CancelledError


def test_progress_observer_cancellation_is_never_failure_isolated() -> None:
    with pytest.raises(asyncio.CancelledError):
        safe_emit_progress(_cancel_progress, "test", "cancel")


def test_progress_observer_guard_never_names_cancellation() -> None:
    source = inspect.getsource(run_observers.safe_emit_progress)
    assert "CancelledError" not in source
    assert "except Exception" in source


@pytest.mark.asyncio
async def test_apply_progress_cancellation_precedes_target_and_model_work(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original = "---\nname: demo\n---\n# Demo\n\nOriginal guidance.\n"
    skill_path.write_text(original, encoding="utf-8")
    target = SkillTextTarget(skill_path, allow_auto_apply=True)
    evaluated: list[str] = []

    with pytest.raises(asyncio.CancelledError):
        await execute_apply_transaction(
            ApplyTransactionRequest("run-progress-cancel", target, _skill_candidate(target)),
            ApplyTransactionPolicy(),
            ApplyTransactionRuntime(
                store=FilesystemSelfEvolveStore(tmp_path),
                post_apply_evaluator=lambda item: evaluated.append(item.candidate_id),
                progress_callback=_cancel_progress,
            ),
        )

    assert skill_path.read_text(encoding="utf-8") == original
    assert evaluated == []


@pytest.mark.asyncio
async def test_challenge_progress_cancellation_precedes_backend_work(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Demo\n", encoding="utf-8")
    target = SkillTextTarget(skill_path)

    class Backend:
        calls = 0

        async def propose(self, _request: object) -> object:
            self.calls += 1
            raise AssertionError("model work must not start after cancellation")

    backend = Backend()
    with pytest.raises(asyncio.CancelledError):
        await execute_challenge(
            ChallengeExecutionRequest("run-progress-cancel", target, _skill_candidate(target)),
            ChallengeExecutionPolicy(True, 1, (_suite(_dataset()),)),
            ChallengeExecutionRuntime(
                FilesystemSelfEvolveStore(tmp_path),
                backend,  # type: ignore[arg-type]
                _cancel_progress,
            ),
        )

    assert backend.calls == 0


def _transaction_factory(
    store: FilesystemSelfEvolveStore,
    *,
    passed: bool = True,
) -> object:
    def build(target: SkillTextTarget) -> ApplyTransactionExecution:
        return ApplyTransactionExecution(
            ApplyTransactionPolicy(release_state="verified_only", published=False),
            ApplyTransactionRuntime(
                store=store,
                post_apply_evaluator=lambda item: EvaluationSummary(
                    variant_id=item.candidate_id,
                    dataset_split="post_apply",
                    metrics={"post_apply_passed": passed},
                ),
            ),
        )

    return build


@pytest.mark.asyncio
async def test_disabled_challenge_is_admitted_without_backend_or_persistence(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Demo\n", encoding="utf-8")
    target = SkillTextTarget(skill_path)

    result = await execute_challenge(
        ChallengeExecutionRequest(
            run_id="run-1",
            target=target,
            candidate=_skill_candidate(target),
        ),
        ChallengeExecutionPolicy(enabled=False, max_cases=2, regression_suites=()),
        ChallengeExecutionRuntime(
            store=FilesystemSelfEvolveStore(tmp_path),
            backend=None,
        ),
    )

    assert result.report is None
    assert result.gate.passed is True
    assert result.gate.details == {
        "enabled": False,
        "approval_authority": False,
        "admitted_count": 0,
    }


@pytest.mark.asyncio
async def test_apply_transaction_rejects_target_drift_before_journal(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Demo\n", encoding="utf-8")
    target = SkillTextTarget(skill_path, allow_auto_apply=True)
    candidate = _skill_candidate(target)
    skill_path.write_text("# Concurrent edit\n", encoding="utf-8")

    result = await execute_apply_transaction(
        ApplyTransactionRequest("run-1", target, candidate),
        ApplyTransactionPolicy(),
        ApplyTransactionRuntime(
            store=FilesystemSelfEvolveStore(tmp_path),
            post_apply_evaluator=lambda _: (_ for _ in ()).throw(
                AssertionError("post-apply evaluation must not run")
            ),
        ),
    )

    assert result.report["status"] == "rejected"
    assert result.report["backup_path"] is None
    assert result.report["metrics"]["code"] == "target_snapshot_stale"


@pytest.mark.asyncio
async def test_apply_rollback_preserves_omitted_release_keys(tmp_path: Path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original = "---\nname: demo\n---\n# Demo\n\nOriginal guidance.\n"
    skill_path.write_text(original, encoding="utf-8")
    target = SkillTextTarget(skill_path, allow_auto_apply=True)
    candidate = _skill_candidate(target)

    result = await execute_apply_transaction(
        ApplyTransactionRequest("run-rollback", target, candidate),
        ApplyTransactionPolicy(),
        ApplyTransactionRuntime(
            store=FilesystemSelfEvolveStore(tmp_path),
            post_apply_evaluator=lambda item: EvaluationSummary(
                variant_id=item.candidate_id,
                dataset_split="post_apply",
                metrics={"post_apply_passed": False},
            ),
        ),
    )

    assert result.report["status"] == "rolled_back"
    assert "release_state" not in result.report
    assert "published" not in result.report
    assert skill_path.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_regression_execution_composes_typed_disabled_challenge(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Demo\n", encoding="utf-8")
    target = SkillTextTarget(skill_path)
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="case-1", input="task"),),
        recipe=DatasetRecipe(
            source={"kind": "controller-test"},
            split_seed="seed",
            splits={"train": ["case-1"]},
            trainable_case_ids=("case-1",),
        ),
    )
    store = FilesystemSelfEvolveStore(tmp_path)
    challenge = ChallengeExecution(
        ChallengeExecutionPolicy(False, 2, ()),
        ChallengeExecutionRuntime(store, None),
    )

    result = await execute_independent_regression(
        RegressionExecutionRequest(
            run_id="run-regression",
            target=target,
            selection_dataset=dataset,
            candidate=_skill_candidate(target),
            apply_policy="proposal",
            budget_context=None,
        ),
        RegressionExecutionPolicy(False, 1, 1, ()),
        RegressionExecutionRuntime(
            store=store,
            challenge=challenge,
            regression_backend=None,
            regression_replay_backend=None,
            selection_backend=None,
            replay=None,
            task_batch_executor=object(),
            max_concurrency=1,
            execution_telemetry=SelfEvolveExecutionTelemetry(),
        ),
    )

    assert result.evidence is None
    assert result.challenge_report is None
    assert result.challenge_gate.passed is True


@pytest.mark.asyncio
async def test_verified_only_rejects_non_skill_without_building_transaction(
    tmp_path: Path,
) -> None:
    target = type(
        "Target",
        (),
        {
            "identity": SelfEvolveTargetRef(
                target_type="prompt", target_id="demo", path=None
            )
        },
    )()
    candidate = CandidateVariant(
        candidate_id="candidate-1",
        target=target.identity,
        content="updated",
        rationale="test",
    )

    result = await execute_verified_only_apply(
        VerifiedOnlyApplyRequest("run-1", target, candidate),  # type: ignore[arg-type]
        VerifiedOnlyApplyPolicy(),
        VerifiedOnlyApplyRuntime(
            store=FilesystemSelfEvolveStore(tmp_path),
            transaction_factory=lambda _: (_ for _ in ()).throw(
                AssertionError("transaction must not be built")
            ),
        ),
    )

    assert result.report["status"] == "rejected"
    assert result.report["published"] is False
    assert result.report["metrics"]["code"] == (
        "verified_only_target_type_unsupported"
    )


def _has_runner_reverse_import(module: object) -> bool:
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "aworld.self_evolve.runner" for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "aworld.self_evolve.runner":
                return True
            if node.module == "aworld.self_evolve" and any(
                alias.name == "runner" for alias in node.names
            ):
                return True
    return False


def test_milestone_2b_controllers_never_reverse_import_runner() -> None:
    for module in (
        run_apply_transaction,
        run_challenge_execution,
        run_candidate_execution,
        run_regression_execution,
        run_verified_only_apply,
    ):
        assert not _has_runner_reverse_import(module)


def test_production_regression_graph_retains_no_bound_runner(tmp_path: Path) -> None:
    class Optimizer:
        async def propose(self, _):
            raise AssertionError("not used")

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=Optimizer(),
    )
    execution = runner._regression_execution()
    visited: set[int] = set()

    def inspect_value(value: object) -> None:
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

    inspect_value(execution)


def test_production_candidate_graph_and_adapter_are_runner_free(
    tmp_path: Path,
) -> None:
    class Optimizer:
        async def propose(self, _):
            raise AssertionError("not used")

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=Optimizer(),
    )
    execution = runner._candidate_iteration_execution()
    visited: set[int] = set()

    def inspect_value(value: object) -> None:
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

    inspect_value(execution)
    source = inspect.getsource(SelfEvolveRunner._execute_iteration_candidate)
    function = ast.parse(inspect.cleandoc(source)).body[0]
    assert function.end_lineno is not None
    assert function.end_lineno <= 8


@pytest.mark.asyncio
async def test_apply_rejects_candidate_target_mismatch_before_backup(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Demo\n", encoding="utf-8")
    target = SkillTextTarget(skill_path, allow_auto_apply=True)
    candidate = CandidateVariant(
        candidate_id="candidate-1",
        target=SelfEvolveTargetRef("skill", "other", str(skill_path)),
        content="# Other\n",
        rationale="test",
        target_fingerprint=target.fingerprint_current_content(),
    )
    store = FilesystemSelfEvolveStore(tmp_path)

    result = await execute_apply_transaction(
        ApplyTransactionRequest("run-mismatch", target, candidate),
        ApplyTransactionPolicy(),
        ApplyTransactionRuntime(
            store=store,
            post_apply_evaluator=lambda _: pytest.fail("must not evaluate"),
        ),
    )

    assert result.report["metrics"]["code"] == "candidate_target_mismatch"
    assert result.report["backup_path"] is None
    assert not (store.run_path("run-mismatch") / "apply").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled_stage", ["evaluation", "refresh", "activation"])
async def test_apply_cancellation_rolls_back_and_terminalizes_journal(
    tmp_path: Path,
    cancelled_stage: str,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original = "---\nname: demo\n---\n# Demo\n\nOriginal guidance.\n"
    skill_path.write_text(original, encoding="utf-8")
    target = SkillTextTarget(skill_path, allow_auto_apply=True)
    store = FilesystemSelfEvolveStore(tmp_path)
    activated: list[str] = []

    async def evaluator(item: CandidateVariant) -> EvaluationSummary:
        if cancelled_stage == "evaluation":
            raise asyncio.CancelledError
        return EvaluationSummary(
            item.candidate_id,
            {"post_apply_passed": True},
            "post_apply",
        )

    async def refresh(item: CandidateVariant) -> object:
        if cancelled_stage == "refresh":
            raise asyncio.CancelledError
        return {"refreshed": item.candidate_id}

    async def activate(item: CandidateVariant) -> object:
        activated.append(item.candidate_id)
        if cancelled_stage == "activation":
            raise asyncio.CancelledError
        return {"activated": item.candidate_id}

    with pytest.raises(asyncio.CancelledError):
        await execute_apply_transaction(
            ApplyTransactionRequest("run-cancel", target, _skill_candidate(target)),
            ApplyTransactionPolicy(),
            ApplyTransactionRuntime(
                store=store,
                post_apply_evaluator=evaluator,
                runtime_registry_refresher=refresh,
                runtime_skill_activator=activate,
            ),
        )

    assert skill_path.read_text(encoding="utf-8") == original
    journal = json.loads(
        next((store.run_path("run-cancel") / "apply").glob("*.journal.json"))
        .read_text(encoding="utf-8")
    )
    assert journal["status"] == "rolled_back"
    assert journal["details"]["code"] == "apply_cancelled"
    if cancelled_stage == "refresh":
        assert activated == []


@pytest.mark.asyncio
async def test_registry_refresh_failure_precedes_activation_and_rolls_back(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original = "---\nname: demo\n---\n# Demo\n\nOriginal guidance.\n"
    skill_path.write_text(original, encoding="utf-8")
    target = SkillTextTarget(skill_path, allow_auto_apply=True)
    activated: list[str] = []

    def refresh(_: CandidateVariant) -> object:
        raise RuntimeError("refresh failed")

    result = await execute_apply_transaction(
        ApplyTransactionRequest("run-refresh", target, _skill_candidate(target)),
        ApplyTransactionPolicy(),
        ApplyTransactionRuntime(
            store=FilesystemSelfEvolveStore(tmp_path),
            post_apply_evaluator=lambda item: EvaluationSummary(
                item.candidate_id,
                {"post_apply_passed": True},
                "post_apply",
            ),
            runtime_registry_refresher=refresh,
            runtime_skill_activator=lambda item: activated.append(item.candidate_id),
        ),
    )

    assert result.report["status"] == "rolled_back"
    assert result.report["metrics"]["registry_refresh_passed"] is False
    assert result.report["metrics"]["cleanup_errors"] == [
        "registry_compensation:missing"
    ]
    assert activated == []
    assert skill_path.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_activation", [False, True])
async def test_apply_compensates_registry_and_partial_activation_after_rollback(
    tmp_path: Path,
    cancel_activation: bool,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original = "---\nname: demo\n---\n# Demo\n\nOriginal guidance.\n"
    skill_path.write_text(original, encoding="utf-8")
    target = SkillTextTarget(skill_path, allow_auto_apply=True)
    registry_state = {"content": original}
    activation_state = {"content": original}
    compensation_order: list[str] = []

    def refresh(_: CandidateVariant) -> object:
        registry_state["content"] = skill_path.read_text(encoding="utf-8")
        return {"refreshed": True}

    def activate(_: CandidateVariant) -> object:
        activation_state["content"] = skill_path.read_text(encoding="utf-8")
        if cancel_activation:
            raise asyncio.CancelledError
        raise RuntimeError("activation partially mutated runtime")

    def compensate_registry(_: CandidateVariant, _token: object | None) -> object:
        compensation_order.append("registry")
        registry_state["content"] = skill_path.read_text(encoding="utf-8")
        return {"restored": True}

    def compensate_activation(_: CandidateVariant, _token: object | None) -> object:
        compensation_order.append("activation")
        activation_state["content"] = skill_path.read_text(encoding="utf-8")
        return {"restored": True}

    execution = execute_apply_transaction(
        ApplyTransactionRequest("run-compensate", target, _skill_candidate(target)),
        ApplyTransactionPolicy(),
        ApplyTransactionRuntime(
            store=FilesystemSelfEvolveStore(tmp_path),
            post_apply_evaluator=lambda item: EvaluationSummary(
                item.candidate_id,
                {"post_apply_passed": True},
                "post_apply",
            ),
            runtime_registry_refresher=refresh,
            runtime_skill_activator=activate,
            runtime_registry_compensator=compensate_registry,
            runtime_skill_compensator=compensate_activation,
        ),
    )
    if cancel_activation:
        with pytest.raises(asyncio.CancelledError):
            await execution
    else:
        result = await execution
        assert result.report["status"] == "rolled_back"
        assert result.report["metrics"]["activation_error"] == (
            "activation partially mutated runtime"
        )

    assert skill_path.read_text(encoding="utf-8") == original
    assert registry_state["content"] == original
    assert activation_state["content"] == original
    assert compensation_order == ["activation", "registry"]


@pytest.mark.asyncio
async def test_apply_compensation_failure_is_diagnostic_not_primary(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original = "---\nname: demo\n---\n# Demo\n\nOriginal guidance.\n"
    skill_path.write_text(original, encoding="utf-8")
    target = SkillTextTarget(skill_path, allow_auto_apply=True)

    def activation(_: CandidateVariant) -> object:
        raise RuntimeError("primary activation failure")

    def compensation_failure(_: CandidateVariant, _token: object | None) -> object:
        raise RuntimeError("registry restore failure")

    result = await execute_apply_transaction(
        ApplyTransactionRequest("run-compensation-fail", target, _skill_candidate(target)),
        ApplyTransactionPolicy(),
        ApplyTransactionRuntime(
            store=FilesystemSelfEvolveStore(tmp_path),
            post_apply_evaluator=lambda item: EvaluationSummary(
                item.candidate_id,
                {"post_apply_passed": True},
                "post_apply",
            ),
            runtime_registry_refresher=lambda _: {"refreshed": True},
            runtime_skill_activator=activation,
            runtime_registry_compensator=compensation_failure,
            runtime_skill_compensator=lambda _candidate, _token: {"restored": True},
        ),
    )

    assert result.report["status"] == "rolled_back"
    assert result.report["metrics"]["activation_error"] == (
        "primary activation failure"
    )
    assert result.report["metrics"]["cleanup_errors"] == [
        "registry_compensation:RuntimeError:registry restore failure"
    ]
    journal = json.loads(Path(result.report["journal_path"]).read_text(encoding="utf-8"))
    assert journal["details"]["runtime_side_effects"] == {
        "registry_began": True,
        "registry_succeeded": True,
        "registry_compensated": False,
        "activation_began": True,
        "activation_succeeded": False,
        "activation_compensated": True,
    }


@pytest.mark.asyncio
async def test_apply_acceptance_journal_cancellation_preserves_primary_and_rolls_back(
    tmp_path: Path,
) -> None:
    class CancellingStore(FilesystemSelfEvolveStore):
        cancelled = False

        def update_apply_journal(self, path: Path, *, status: str, details: object) -> None:
            if status == "accepted" and not self.cancelled:
                self.cancelled = True
                raise asyncio.CancelledError
            super().update_apply_journal(path, status=status, details=details)  # type: ignore[arg-type]

    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original = "---\nname: demo\n---\n# Demo\n\nOriginal guidance.\n"
    skill_path.write_text(original, encoding="utf-8")
    target = SkillTextTarget(skill_path, allow_auto_apply=True)
    store = CancellingStore(tmp_path)
    compensated: list[tuple[str, object | None]] = []

    with pytest.raises(asyncio.CancelledError):
        await execute_apply_transaction(
            ApplyTransactionRequest("run-journal-cancel", target, _skill_candidate(target)),
            ApplyTransactionPolicy(),
            ApplyTransactionRuntime(
                store=store,
                post_apply_evaluator=lambda item: EvaluationSummary(
                    item.candidate_id,
                    {"post_apply_passed": True},
                    "post_apply",
                ),
                runtime_registry_refresher=lambda _: {"token": "registry"},
                runtime_skill_activator=lambda _: {"token": "activation"},
                runtime_skill_compensator=lambda _candidate, token: compensated.append(
                    ("activation", token)
                ),
                runtime_registry_compensator=lambda _candidate, token: compensated.append(
                    ("registry", token)
                ),
            ),
        )

    assert skill_path.read_text(encoding="utf-8") == original
    journal_path = next(
        (store.run_path("run-journal-cancel") / "apply").glob("*.journal.json")
    )
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["status"] == "rolled_back"
    assert journal["details"]["commit_stage"] == "journal_acceptance"
    assert compensated == [
        ("activation", {"token": "activation"}),
        ("registry", {"token": "registry"}),
    ]


@pytest.mark.asyncio
async def test_apply_cleanup_failures_do_not_mask_cancellation(tmp_path: Path) -> None:
    class CleanupFailingTarget(SkillTextTarget):
        def rollback(self) -> None:
            raise RuntimeError("rollback failed")

    class CleanupFailingStore(FilesystemSelfEvolveStore):
        def update_apply_journal(self, path: Path, *, status: str, details: object) -> None:
            if status == "rolled_back":
                raise RuntimeError("journal failed")
            super().update_apply_journal(path, status=status, details=details)  # type: ignore[arg-type]

    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: demo\n---\n# Demo\n\nOriginal guidance.\n",
        encoding="utf-8",
    )
    target = CleanupFailingTarget(skill_path, allow_auto_apply=True)

    async def cancel(_: CandidateVariant) -> EvaluationSummary:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError) as caught:
        await execute_apply_transaction(
            ApplyTransactionRequest("run-cleanup-fail", target, _skill_candidate(target)),
            ApplyTransactionPolicy(),
            ApplyTransactionRuntime(
                store=CleanupFailingStore(tmp_path),
                post_apply_evaluator=cancel,
            ),
        )

    notes = getattr(caught.value, "__notes__", ())
    assert any("rollback:RuntimeError:rollback failed" in note for note in notes)
    assert any("journal:RuntimeError:journal failed" in note for note in notes)


@pytest.mark.asyncio
async def test_apply_progress_observer_failure_is_non_fatal(tmp_path: Path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: demo\n---\n# Demo\n\nOriginal guidance.\n",
        encoding="utf-8",
    )
    target = SkillTextTarget(skill_path, allow_auto_apply=True)

    result = await execute_apply_transaction(
        ApplyTransactionRequest("run-progress", target, _skill_candidate(target)),
        ApplyTransactionPolicy(),
        ApplyTransactionRuntime(
            store=FilesystemSelfEvolveStore(tmp_path),
            post_apply_evaluator=lambda item: EvaluationSummary(
                item.candidate_id,
                {"post_apply_passed": False},
                "post_apply",
            ),
            progress_callback=lambda *_: (_ for _ in ()).throw(
                RuntimeError("observer failed")
            ),
        ),
    )

    assert result.report["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_challenge_progress_observer_does_not_mask_backend_failure(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Demo\n", encoding="utf-8")
    target = SkillTextTarget(skill_path)

    class Backend:
        async def propose(self, _request: object) -> object:
            raise RuntimeError("backend failed")

    result = await execute_challenge(
        ChallengeExecutionRequest("run-challenge", target, _skill_candidate(target)),
        ChallengeExecutionPolicy(True, 1, (_suite(_dataset()),)),
        ChallengeExecutionRuntime(
            FilesystemSelfEvolveStore(tmp_path),
            Backend(),  # type: ignore[arg-type]
            lambda *_: (_ for _ in ()).throw(RuntimeError("observer failed")),
        ),
    )

    assert result.gate.passed is False
    assert result.gate.details["code"] == "challenger_generation_failed"
    assert "backend failed" in result.gate.details["reason"]


class _BudgetContext:
    def __init__(self) -> None:
        self.debits = 0
        self.releases = 0

    def reserve(self, *_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(allowed=True)

    def debit(self, *_args: object, **_kwargs: object) -> None:
        self.debits += 1

    def release(self, *_args: object, **_kwargs: object) -> None:
        self.releases += 1


def _regression_runtime(
    tmp_path: Path,
    *,
    replay: RegressionReplayExecution | None = None,
    progress_callback: object | None = None,
    evaluate_pair: object | None = None,
) -> RegressionExecutionRuntime:
    class RegressionBackend:
        pass

    class SelectionBackend:
        pass

    kwargs: dict[str, object] = {}
    if evaluate_pair is not None:
        kwargs["evaluate_pair"] = evaluate_pair
    store = FilesystemSelfEvolveStore(tmp_path)
    return RegressionExecutionRuntime(
        store=store,
        challenge=ChallengeExecution(
            ChallengeExecutionPolicy(False, 1, ()),
            ChallengeExecutionRuntime(store, None),
        ),
        regression_backend=RegressionBackend(),  # type: ignore[arg-type]
        regression_replay_backend=object(),
        selection_backend=SelectionBackend(),  # type: ignore[arg-type]
        replay=replay,
        task_batch_executor=object(),
        max_concurrency=1,
        execution_telemetry=SelfEvolveExecutionTelemetry(),
        progress_callback=progress_callback,  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_regression_progress_cancellation_precedes_replay_and_model_work(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Demo\n", encoding="utf-8")
    target = SkillTextTarget(skill_path)
    evaluated: list[str] = []

    async def evaluate_pair(*_args: object, **_kwargs: object) -> object:
        evaluated.append("evaluation")
        raise AssertionError("model work must not start after cancellation")

    with pytest.raises(asyncio.CancelledError):
        await execute_independent_regression(
            RegressionExecutionRequest(
                "run-regression-progress-cancel",
                target,
                _dataset(),
                _skill_candidate(target),
                "proposal",
                None,
            ),
            RegressionExecutionPolicy(False, 1, 1, (_suite(_dataset()),)),
            _regression_runtime(
                tmp_path,
                progress_callback=_cancel_progress,
                evaluate_pair=evaluate_pair,
            ),
        )

    assert evaluated == []


@pytest.mark.asyncio
async def test_regression_replay_cancellation_settles_reservation_once(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Demo\n", encoding="utf-8")
    target = SkillTextTarget(skill_path)
    budget = _BudgetContext()

    async def replay(request: RegressionReplayRequest) -> object:
        request.lifecycle_callback("replay_started", {})
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await execute_independent_regression(
            RegressionExecutionRequest(
                "run-regression-cancel",
                target,
                _dataset(),
                _skill_candidate(target),
                "proposal",
                budget,  # type: ignore[arg-type]
            ),
            RegressionExecutionPolicy(True, 1, 1, (_suite(_dataset()),)),
            _regression_runtime(
                tmp_path,
                replay=RegressionReplayExecution(replay),  # type: ignore[arg-type]
            ),
        )

    assert budget.debits == 1
    assert budget.releases == 0


@pytest.mark.asyncio
async def test_successful_regression_persists_evidence_schema_despite_observer_failure(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Demo\n", encoding="utf-8")
    target = SkillTextTarget(skill_path)

    async def evaluate_pair(*_args: object, **_kwargs: object) -> tuple[EvaluationSummary, EvaluationSummary]:
        return (
            EvaluationSummary(
                "baseline",
                {"score": 0.5, "cost_usd": 1.0, "latency_ms": 10.0},
                "regression",
            ),
            EvaluationSummary(
                "candidate-1",
                {"score": 0.8, "cost_usd": 1.0, "latency_ms": 10.0},
                "regression",
            ),
        )

    runtime = _regression_runtime(
        tmp_path,
        progress_callback=lambda *_: (_ for _ in ()).throw(
            RuntimeError("observer failed")
        ),
        evaluate_pair=evaluate_pair,
    )
    result = await execute_independent_regression(
        RegressionExecutionRequest(
            "run-regression-success",
            target,
            _dataset(),
            _skill_candidate(target),
            "proposal",
            None,
        ),
        RegressionExecutionPolicy(False, 1, 1, (_suite(_dataset("case-2")),)),
        runtime,
    )

    assert result.evidence is not None
    payload = json.loads(
        (
            runtime.store.run_path("run-regression-success")
            / "regression"
            / "evidence"
            / "candidate-1.json"
        ).read_text(encoding="utf-8")
    )
    assert payload["schema_version"] == "aworld.self_evolve.regression_evidence.v1"
    assert payload["candidate_id"] == "candidate-1"
    assert payload["suite_results"][0]["fresh_execution"] is True
    assert payload["passed"] is True


@pytest.mark.asyncio
async def test_verified_only_source_drift_removes_shadow_and_terminalizes_journal(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: demo\n---\n# Demo\n\nOriginal guidance.\n",
        encoding="utf-8",
    )

    class DriftingTarget(SkillTextTarget):
        calls = 0

        def fingerprint_current_content(self) -> str:
            self.calls += 1
            value = super().fingerprint_current_content()
            return value if self.calls == 1 else f"{value}-drift"

    target = DriftingTarget(skill_path, allow_auto_apply=True)
    candidate = CandidateVariant(
        "candidate-1",
        target.identity,
        "---\nname: demo\n---\n# Demo\n\nUpdated guidance.\n",
        "test",
        target_fingerprint=SkillTextTarget.fingerprint_current_content(target),
    )
    store = FilesystemSelfEvolveStore(tmp_path)

    result = await execute_verified_only_apply(
        VerifiedOnlyApplyRequest("run-drift", target, candidate),
        VerifiedOnlyApplyPolicy(),
        VerifiedOnlyApplyRuntime(store, _transaction_factory(store)),  # type: ignore[arg-type]
    )

    assert result.report["status"] == "rejected"
    assert "verified_target_path" not in result.report
    assert not (store.run_path("run-drift") / "verified_targets" / "demo").exists()
    journal = json.loads(Path(result.report["journal_path"]).read_text(encoding="utf-8"))
    assert journal["status"] == "rolled_back"
    assert journal["details"]["code"] == "source_target_changed_during_verified_only"


@pytest.mark.asyncio
async def test_verified_only_partial_auxiliary_copy_is_cleaned_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Demo\n", encoding="utf-8")
    (skill_path.parent / "helper.txt").write_text("auxiliary", encoding="utf-8")
    target = SkillTextTarget(skill_path, allow_auto_apply=True)
    candidate = _skill_candidate(target)
    store = FilesystemSelfEvolveStore(tmp_path)
    real_copytree = shutil.copytree
    calls = 0

    def flaky_copytree(src: object, dst: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            Path(dst).mkdir(parents=True)
            (Path(dst) / "partial.txt").write_text("partial", encoding="utf-8")
            raise OSError("partial copy")
        return real_copytree(src, dst, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(verified_apply_module.shutil, "copytree", flaky_copytree)
    runtime = VerifiedOnlyApplyRuntime(store, _transaction_factory(store, passed=False))  # type: ignore[arg-type]
    first = await execute_verified_only_apply(
        VerifiedOnlyApplyRequest("run-copy", target, candidate),
        VerifiedOnlyApplyPolicy(),
        runtime,
    )
    package_root = store.run_path("run-copy") / "verified_targets" / "demo"

    assert first.report["metrics"]["code"] == "verified_target_materialization_failed"
    assert not package_root.exists()

    second = await execute_verified_only_apply(
        VerifiedOnlyApplyRequest("run-copy", target, candidate),
        VerifiedOnlyApplyPolicy(),
        runtime,
    )
    assert second.report["metrics"].get("code") != "verified_target_collision"
    assert calls >= 2
    assert (package_root / "helper.txt").read_text(encoding="utf-8") == "auxiliary"


def test_subclass_and_class_monkeypatch_overrides_route_through_runner_edge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Optimizer:
        async def propose(self, _request: object) -> object:
            raise AssertionError("not used")

    class CustomRunner(SelfEvolveRunner):
        async def _validate_candidate_capabilities(self, *_args: object, **_kwargs: object) -> object:
            return "capability-override"

        async def _prepare_replay_adaptation(self, *_args: object, **_kwargs: object) -> object:
            return "replay-override"

        async def _apply_auto_verified(self, *_args: object, **_kwargs: object) -> object:
            return "apply-override"

        async def _evaluate_independent_regression(self, *_args: object, **_kwargs: object) -> object:
            return "regression-override"

        async def _prepare_challenge_suites(self, *_args: object, **_kwargs: object) -> object:
            return "challenge-override"

    runner = CustomRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=Optimizer(),
    )
    candidate_execution = runner._candidate_iteration_execution()
    assert getattr(candidate_execution.runtime.capability_override, "__self__", None) is runner
    assert getattr(candidate_execution.runtime.regression, "__self__", None) is runner
    assert runner._replay_adaptation_execution().override is not None
    assert runner._challenge_execution().override is not None
    assert runner_module._runner_method_override(runner, "_apply_auto_verified") is not None

    async def class_override(*_args: object, **_kwargs: object) -> object:
        return "class-monkeypatch"

    monkeypatch.setattr(
        SelfEvolveRunner,
        "_validate_candidate_capabilities",
        class_override,
    )
    base_runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path / "base"),
        optimizer=Optimizer(),
    )
    override = base_runner._candidate_iteration_execution().runtime.capability_override
    assert override is not None
    assert getattr(override, "__self__", None) is base_runner


def test_runner_production_compensators_are_distinct_from_forward_effects(
    tmp_path: Path,
) -> None:
    class Optimizer:
        async def propose(self, _request: object) -> object:
            raise AssertionError("not used")

    def refresh(_: CandidateVariant) -> object:
        return {"refreshed": True}

    def activate(_: CandidateVariant) -> object:
        return {"activated": True}

    def restore_registry(_: CandidateVariant, _token: object | None) -> object:
        return {"restored": True}

    def restore_activation(_: CandidateVariant, _token: object | None) -> object:
        return {"restored": True}

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=Optimizer(),
        post_apply_evaluator=lambda item: EvaluationSummary(
            item.candidate_id,
            {"post_apply_passed": True},
            "post_apply",
        ),
        runtime_registry_refresher=refresh,
        runtime_skill_activator=activate,
        runtime_registry_compensator=restore_registry,
        runtime_skill_compensator=restore_activation,
    )

    runtime = runner._auto_apply_execution().runtime
    assert runtime.runtime_registry_refresher is refresh
    assert runtime.runtime_registry_compensator is restore_registry
    assert runtime.runtime_registry_compensator is not refresh
    assert runtime.runtime_skill_activator is activate
    assert runtime.runtime_skill_compensator is restore_activation
    assert runtime.runtime_skill_compensator is not activate
