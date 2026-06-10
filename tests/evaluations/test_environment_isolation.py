from __future__ import annotations

import pytest

from aworld.evaluations.runtime_composition import (
    EnvironmentIsolatedRuntimeHarness,
    EnvironmentSnapshot,
    RetryRuntimeHarness,
    RolloutState,
)
from aworld.evaluations.substrate import (
    EvalCaseDef,
    EvalSuiteDef,
    EvaluationFlowDef,
    TrialPolicyDef,
    run_evaluation_flow,
)


def test_environment_snapshot_excludes_live_handles():
    snapshot = EnvironmentSnapshot(
        environment_id="env-1",
        trial_id="case-1::trial-1",
        metadata={"workspace": "/tmp/demo", "client": object()},
    )

    assert snapshot.to_dict() == {
        "environment_id": "env-1",
        "trial_id": "case-1::trial-1",
        "metadata": {"workspace": "/tmp/demo"},
    }


@pytest.mark.asyncio
async def test_environment_isolated_harness_resets_and_cleans_up():
    events = []

    class RecordingFixture:
        async def reset(self, *, case, target):
            events.append(("reset", case.case_id))
            return EnvironmentSnapshot(
                environment_id="env-1",
                trial_id=case.input["_trial"]["trial_id"],
                metadata={"workspace": "/tmp/demo", "client": object()},
            )

        async def cleanup(self, *, snapshot, case, target, state):
            events.append(("cleanup", snapshot.environment_id, state.status))
            return EnvironmentSnapshot(
                environment_id=snapshot.environment_id,
                trial_id=snapshot.trial_id,
                metadata={"cleaned": True},
            )

    class InspectingHarness:
        async def run_rollout(self, *, case, target):
            assert case.input["_environment"]["environment_id"] == "env-1"
            assert case.metadata["_environment"]["trial_id"] == "case-1::trial-1"
            assert target["_environment"]["metadata"]["workspace"] == "/tmp/demo"
            return RolloutState(case_id=case.case_id, status="success", answer="ok")

    harness = EnvironmentIsolatedRuntimeHarness(
        base_harness=InspectingHarness(),
        fixture=RecordingFixture(),
    )
    case = EvalCaseDef(
        case_id="case-1::trial-1",
        input={"_trial": {"trial_id": "case-1::trial-1"}},
    )

    state = await harness.run_rollout(case=case, target={})

    assert events == [("reset", "case-1::trial-1"), ("cleanup", "env-1", "success")]
    assert state.metadata["environment"]["environment_id"] == "env-1"
    assert state.metadata["environment_cleanup"]["metadata"]["cleaned"] is True


@pytest.mark.asyncio
async def test_environment_isolation_resets_once_per_trial():
    class CountingFixture:
        def __init__(self):
            self.resets = []
            self.cleanups = []

        async def reset(self, *, case, target):
            trial_id = case.input["_trial"]["trial_id"]
            self.resets.append(trial_id)
            return EnvironmentSnapshot(
                environment_id=f"env-{len(self.resets)}",
                trial_id=trial_id,
                metadata={"trial_id": trial_id},
            )

        async def cleanup(self, *, snapshot, case, target, state):
            self.cleanups.append(snapshot.trial_id)
            return EnvironmentSnapshot(
                environment_id=snapshot.environment_id,
                trial_id=snapshot.trial_id,
                metadata={"cleaned": True},
            )

    class EnvironmentAwareHarness:
        async def run_rollout(self, *, case, target):
            environment_id = case.input["_environment"]["environment_id"]
            return RolloutState(
                case_id=case.case_id,
                status="success",
                answer=environment_id,
            )

    async def fake_judge(case_input, target):
        return {"score": 1.0}

    fixture = CountingFixture()
    suite = EvalSuiteDef(
        suite_id="environment-trial-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        runtime_harness=EnvironmentIsolatedRuntimeHarness(
            base_harness=EnvironmentAwareHarness(),
            fixture=fixture,
        ),
        judge=fake_judge,
        trial_policy=TrialPolicyDef(num_trials=2),
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(target={"kind": "inline", "value": {"target_path": "demo"}}, suite=suite)
    )

    assert fixture.resets == ["case-1::trial-1", "case-1::trial-2"]
    assert fixture.cleanups == ["case-1::trial-1", "case-1::trial-2"]
    assert report["results"][0]["metadata"]["environment"]["environment_id"] == "env-1"
    assert report["results"][1]["metadata"]["environment"]["environment_id"] == "env-2"


@pytest.mark.asyncio
async def test_retry_inside_environment_isolation_does_not_increase_reset_count():
    class CountingFixture:
        def __init__(self):
            self.reset_count = 0

        async def reset(self, *, case, target):
            self.reset_count += 1
            return EnvironmentSnapshot(environment_id=f"env-{self.reset_count}")

        async def cleanup(self, *, snapshot, case, target, state):
            return None

    class FlakyHarness:
        def __init__(self):
            self.calls = 0

        async def run_rollout(self, *, case, target):
            self.calls += 1
            if self.calls % 2 == 1:
                return RolloutState(case_id=case.case_id, status="failed", answer="failed-attempt")
            return RolloutState(case_id=case.case_id, status="success", answer="passed-trial")

    async def fake_judge(case_input, target):
        return {"score": 1.0 if target.get("answer") == "passed-trial" else 0.0}

    fixture = CountingFixture()
    suite = EvalSuiteDef(
        suite_id="environment-retry-suite",
        cases=[EvalCaseDef(case_id="case-1", input={"query": "hello"})],
        runtime_harness=EnvironmentIsolatedRuntimeHarness(
            base_harness=RetryRuntimeHarness(base_harness=FlakyHarness(), max_attempts=2),
            fixture=fixture,
        ),
        judge=fake_judge,
        trial_policy=TrialPolicyDef(num_trials=2),
    )

    report = await run_evaluation_flow(
        EvaluationFlowDef(target={"kind": "inline", "value": {"target_path": "demo"}}, suite=suite)
    )

    assert fixture.reset_count == 2
    assert len(report["results"][0]["artifacts"]["attempts"]) == 2
    assert len(report["results"][1]["artifacts"]["attempts"]) == 2


@pytest.mark.asyncio
async def test_environment_cleanup_runs_when_rollout_raises():
    events = []

    class RecordingFixture:
        async def reset(self, *, case, target):
            events.append("reset")
            return EnvironmentSnapshot(environment_id="env-1")

        async def cleanup(self, *, snapshot, case, target, state):
            events.append(("cleanup", state.status))
            return None

    class RaisingHarness:
        async def run_rollout(self, *, case, target):
            raise RuntimeError("rollout boom")

    harness = EnvironmentIsolatedRuntimeHarness(
        base_harness=RaisingHarness(),
        fixture=RecordingFixture(),
    )

    with pytest.raises(RuntimeError, match="rollout boom"):
        await harness.run_rollout(case=EvalCaseDef(case_id="case-1", input={}), target={})

    assert events == ["reset", ("cleanup", "failed")]


@pytest.mark.asyncio
async def test_reset_failure_prevents_rollout_execution():
    class FailingResetFixture:
        async def reset(self, *, case, target):
            raise RuntimeError("reset boom")

        async def cleanup(self, *, snapshot, case, target, state):
            raise AssertionError("cleanup should not run when reset fails")

    class UnexpectedHarness:
        async def run_rollout(self, *, case, target):
            raise AssertionError("rollout should not run when reset fails")

    harness = EnvironmentIsolatedRuntimeHarness(
        base_harness=UnexpectedHarness(),
        fixture=FailingResetFixture(),
    )

    with pytest.raises(RuntimeError, match="reset boom"):
        await harness.run_rollout(case=EvalCaseDef(case_id="case-1", input={}), target={})


@pytest.mark.asyncio
async def test_cleanup_failure_during_rollout_error_preserves_rollout_error():
    class FailingCleanupFixture:
        async def reset(self, *, case, target):
            return EnvironmentSnapshot(environment_id="env-1")

        async def cleanup(self, *, snapshot, case, target, state):
            raise RuntimeError("cleanup boom")

    class RaisingHarness:
        async def run_rollout(self, *, case, target):
            raise RuntimeError("rollout boom")

    harness = EnvironmentIsolatedRuntimeHarness(
        base_harness=RaisingHarness(),
        fixture=FailingCleanupFixture(),
    )

    with pytest.raises(RuntimeError, match="rollout boom"):
        await harness.run_rollout(case=EvalCaseDef(case_id="case-1", input={}), target={})


@pytest.mark.asyncio
async def test_cleanup_failure_after_success_marks_rollout_failed():
    class FailingCleanupFixture:
        async def reset(self, *, case, target):
            return EnvironmentSnapshot(environment_id="env-1")

        async def cleanup(self, *, snapshot, case, target, state):
            raise RuntimeError("cleanup boom")

    class PassingHarness:
        async def run_rollout(self, *, case, target):
            return RolloutState(case_id=case.case_id, status="success", answer="ok")

    harness = EnvironmentIsolatedRuntimeHarness(
        base_harness=PassingHarness(),
        fixture=FailingCleanupFixture(),
    )

    state = await harness.run_rollout(case=EvalCaseDef(case_id="case-1", input={}), target={})

    assert state.status == "failed"
    assert state.error == {
        "type": "RuntimeError",
        "message": "cleanup boom",
        "phase": "environment_cleanup",
    }
    assert state.metadata["environment_cleanup_error"]["message"] == "cleanup boom"
