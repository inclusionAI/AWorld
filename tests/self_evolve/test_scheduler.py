from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from aworld.config.conf import SelfEvolveConfig
from aworld.core.task import TaskResponse
from aworld.runners.event_runner import TaskEventRunner
from aworld.self_evolve.scheduler import (
    SelfEvolveRunContext,
    SelfEvolveJobWorker,
    SelfEvolveSchedulerPolicy,
    SelfEvolveScheduler,
)


def _trajectory() -> tuple[dict, ...]:
    return (
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        },
    )


def test_scheduler_declines_when_mode_is_not_shadow_or_online(tmp_path) -> None:
    scheduler = SelfEvolveScheduler(workspace_root=tmp_path)
    context = SelfEvolveRunContext(
        agent_id="agent",
        task_id="task-1",
        workspace_root=str(tmp_path),
        trajectory=_trajectory(),
        self_evolve_config=SelfEvolveConfig(mode="offline"),
    )

    result = scheduler.enqueue(context)

    assert result.accepted is False
    assert result.reason == "self-evolve mode is not eligible for post-run enqueue"
    assert result.job_path is None


def test_scheduler_persists_pending_job_for_shadow_mode_before_returning(tmp_path) -> None:
    scheduler = SelfEvolveScheduler(workspace_root=tmp_path)
    context = SelfEvolveRunContext(
        agent_id="agent",
        task_id="task-1",
        workspace_root=str(tmp_path),
        trajectory=_trajectory(),
        self_evolve_config=SelfEvolveConfig(mode="shadow"),
        source_hints={"target": "skill:demo"},
    )

    result = scheduler.enqueue(context)

    assert result.accepted is True
    assert result.job_path is not None
    saved = json.loads(result.job_path.read_text(encoding="utf-8"))
    assert saved["status"] == "pending"
    assert saved["agent_id"] == "agent"
    assert saved["task_id"] == "task-1"
    assert saved["self_evolve_config"]["mode"] == "shadow"
    assert saved["trajectory"][0]["state"]["input"]["content"] == "Fix guidance."
    assert saved["source_hints"] == {"target": "skill:demo"}


def test_scheduler_best_effort_enqueue_failure_does_not_raise(tmp_path) -> None:
    def fail_writer(path, payload):
        raise OSError("disk unavailable")

    scheduler = SelfEvolveScheduler(workspace_root=tmp_path, write_job=fail_writer)
    context = SelfEvolveRunContext(
        agent_id="agent",
        task_id="task-1",
        workspace_root=str(tmp_path),
        trajectory=_trajectory(),
        self_evolve_config=SelfEvolveConfig(mode="shadow"),
    )

    result = scheduler.enqueue(context)

    assert result.accepted is False
    assert result.reason == "enqueue failed: disk unavailable"
    assert result.job_path is None


def test_event_runner_enqueues_self_evolve_after_response(
    monkeypatch,
    tmp_path,
) -> None:
    calls = {}

    class CapturingScheduler:
        def __init__(self, *, workspace_root):
            calls["workspace_root"] = workspace_root

        def enqueue(self, context):
            calls["context"] = context
            return SimpleNamespace(accepted=True, reason="queued")

    monkeypatch.setattr(
        "aworld.runners.event_runner.SelfEvolveScheduler",
        CapturingScheduler,
        raising=False,
    )

    runner = TaskEventRunner.__new__(TaskEventRunner)
    runner.task = SimpleNamespace(
        id="task-1",
        is_sub_task=False,
        agent=SimpleNamespace(
            id=lambda: "agent-1",
            conf=SimpleNamespace(self_evolve_config=SelfEvolveConfig(mode="shadow")),
        ),
    )
    runner.context = SimpleNamespace(
        workspace_path=str(tmp_path),
        session_id="session-1",
    )
    response = TaskResponse(
        id="task-1",
        trajectory=list(_trajectory()),
        llm_calls=[{"model": "judge"}],
    )

    result = runner._enqueue_self_evolve_after_response(response)

    assert result is response
    assert calls["workspace_root"] == str(tmp_path)
    context = calls["context"]
    assert context.agent_id == "agent-1"
    assert context.task_id == "task-1"
    assert context.workspace_root == str(tmp_path)
    assert context.trajectory == _trajectory()
    assert context.self_evolve_config.mode == "shadow"
    assert context.source_hints["session_id"] == "session-1"
    assert context.source_hints["llm_calls"] == [{"model": "judge"}]


def test_event_runner_self_evolve_enqueue_failure_does_not_replace_response(
    monkeypatch,
    tmp_path,
) -> None:
    class FailingScheduler:
        def __init__(self, *, workspace_root):
            pass

        def enqueue(self, context):
            raise RuntimeError("queue unavailable")

    monkeypatch.setattr(
        "aworld.runners.event_runner.SelfEvolveScheduler",
        FailingScheduler,
        raising=False,
    )

    runner = TaskEventRunner.__new__(TaskEventRunner)
    runner.task = SimpleNamespace(
        id="task-1",
        is_sub_task=False,
        agent=SimpleNamespace(
            id=lambda: "agent-1",
            conf=SimpleNamespace(self_evolve_config=SelfEvolveConfig(mode="shadow")),
        ),
    )
    runner.context = SimpleNamespace(workspace_path=str(tmp_path), session_id="session-1")
    response = TaskResponse(id="task-1", trajectory=list(_trajectory()))

    result = runner._enqueue_self_evolve_after_response(response)

    assert result is response


def test_scheduler_policy_rejects_duplicate_pending_and_cooldown(tmp_path) -> None:
    pending_path = tmp_path / ".aworld" / "self_evolve" / "jobs" / "existing.json"
    pending_path.parent.mkdir(parents=True)
    pending_path.write_text(json.dumps({"status": "pending", "task_id": "task-1"}), encoding="utf-8")
    scheduler = SelfEvolveScheduler(
        workspace_root=tmp_path,
        policy=SelfEvolveSchedulerPolicy(cooldown_seconds=60),
        now=lambda: 100.0,
    )
    context = SelfEvolveRunContext(
        agent_id="agent",
        task_id="task-1",
        workspace_root=str(tmp_path),
        trajectory=_trajectory(),
        self_evolve_config=SelfEvolveConfig(mode="shadow"),
    )

    duplicate = scheduler.enqueue(context)
    assert duplicate.accepted is False
    assert duplicate.reason == "duplicate pending self-evolve job exists"

    pending_path.unlink()
    first = scheduler.enqueue(context)
    assert first.accepted is True
    assert first.job_path is not None
    saved_first = json.loads(first.job_path.read_text(encoding="utf-8"))
    saved_first["status"] = "succeeded"
    first.job_path.write_text(json.dumps(saved_first), encoding="utf-8")
    cooldown = scheduler.enqueue(
        SelfEvolveRunContext(
            agent_id="agent",
            task_id="task-2",
            workspace_root=str(tmp_path),
            trajectory=_trajectory(),
            self_evolve_config=SelfEvolveConfig(mode="shadow", replay_enabled=False),
        )
    )
    assert cooldown.accepted is False
    assert cooldown.reason == "self-evolve target is in cooldown"


def test_scheduler_retries_transient_write_failure_before_returning(tmp_path) -> None:
    attempts = {"count": 0}

    def flaky_writer(path, payload):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise OSError("transient")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    scheduler = SelfEvolveScheduler(
        workspace_root=tmp_path,
        write_job=flaky_writer,
        policy=SelfEvolveSchedulerPolicy(max_enqueue_retries=1),
    )
    result = scheduler.enqueue(
        SelfEvolveRunContext(
            agent_id="agent",
            task_id="task-1",
            workspace_root=str(tmp_path),
            trajectory=_trajectory(),
            self_evolve_config=SelfEvolveConfig(mode="shadow", replay_enabled=False),
        )
    )

    assert result.accepted is True
    assert attempts["count"] == 2


def test_job_worker_marks_failure_without_raising_to_main_path(tmp_path) -> None:
    scheduler = SelfEvolveScheduler(workspace_root=tmp_path)
    result = scheduler.enqueue(
        SelfEvolveRunContext(
            agent_id="agent",
            task_id="task-worker",
            workspace_root=str(tmp_path),
            trajectory=_trajectory(),
            self_evolve_config=SelfEvolveConfig(mode="shadow", replay_enabled=False),
        )
    )
    assert result.job_path is not None

    worker = SelfEvolveJobWorker(
        workspace_root=tmp_path,
        run_job=lambda payload: (_ for _ in ()).throw(RuntimeError("worker failed")),
    )

    drained = worker.drain_pending_jobs()

    assert drained == 1
    saved = json.loads(result.job_path.read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["failure"]["message"] == "worker failed"


def test_job_worker_default_run_job_drains_pending_job_through_framework(tmp_path) -> None:
    skill_path = tmp_path / "aworld-skills" / "agent-browser" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: agent-browser\n---\n# Browser Automation\n",
        encoding="utf-8",
    )
    scheduler = SelfEvolveScheduler(workspace_root=tmp_path)
    result = scheduler.enqueue(
        SelfEvolveRunContext(
            agent_id="agent",
            task_id="browser-job",
            workspace_root=str(tmp_path),
            trajectory=(
                {
                    "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                    "state": {
                        "input": {
                            "content": "I am logged in but you see a logged-out browser."
                        }
                    },
                    "action": {"content": "I will inspect login traces."},
                    "reward": {"status": "failed"},
                },
            ),
            self_evolve_config=SelfEvolveConfig(mode="shadow", replay_enabled=False),
        )
    )
    assert result.job_path is not None

    worker = SelfEvolveJobWorker(workspace_root=tmp_path)

    drained = worker.drain_pending_jobs()

    assert drained == 1
    saved = json.loads(result.job_path.read_text(encoding="utf-8"))
    assert saved["status"] == "succeeded"
    reports = sorted((tmp_path / ".aworld" / "self_evolve").glob("cli-*/report.json"))
    assert reports
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["target"]["target_id"] == "agent-browser"


def test_online_job_worker_rejects_auto_verified_skill_candidate_on_replay_failure(
    monkeypatch,
    tmp_path,
) -> None:
    class FakeReplayBackend:
        async def replay_candidate(self, request, *, candidate, dataset):
            from aworld.self_evolve.replay import CandidateReplayResult, ReplayVariantResult

            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[{"action": {"content": "old"}}],
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="failed",
                    trajectory=[],
                    failure={"reason": "fake replay failure"},
                ),
            )

    monkeypatch.setattr(
        "aworld.self_evolve.runner.AWorldCliCandidateReplayBackend",
        FakeReplayBackend,
    )

    skill_path = tmp_path / "aworld-skills" / "workflow-helper" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original = (
        "---\n"
        "name: workflow-helper\n"
        "description: Use for workflow helper task recovery.\n"
        "---\n"
        "# Workflow Helper\n"
    )
    skill_path.write_text(original, encoding="utf-8")
    scheduler = SelfEvolveScheduler(workspace_root=tmp_path)
    result = scheduler.enqueue(
        SelfEvolveRunContext(
            agent_id="agent",
            task_id="workflow-online-job",
            workspace_root=str(tmp_path),
            trajectory=(
                {
                    "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                    "state": {
                        "input": {
                            "content": (
                                "Use workflow-helper to recover the failed task "
                                "handoff."
                            )
                        }
                    },
                    "action": {
                        "content": "workflow-helper repeated the same handoff step.",
                        "tool_calls": [],
                    },
                    "reward": {"status": "failed"},
                },
            ),
            self_evolve_config=SelfEvolveConfig(
                mode="online",
                apply_policy="auto_verified",
                min_eval_cases=0,
            ),
        )
    )
    assert result.job_path is not None

    worker = SelfEvolveJobWorker(workspace_root=tmp_path)

    drained = worker.drain_pending_jobs()

    assert drained == 1
    saved = json.loads(result.job_path.read_text(encoding="utf-8"))
    assert saved["status"] == "succeeded"
    assert skill_path.read_text(encoding="utf-8") == original

    reports = sorted((tmp_path / ".aworld" / "self_evolve").glob("cli-*/report.json"))
    assert reports
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["status"] == "rejected"
    assert report["apply_policy"] == "auto_verified"
    assert "post_apply" not in report
    assert any(
        gate["gate_name"] == "candidate_replay"
        and gate["passed"] is False
        for gate in report["gate_results"]
    )


def test_job_worker_passes_configured_judge_to_framework_job(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_optimize_from_cli_request(**kwargs):
        captured.update(kwargs)
        return {"status": "succeeded"}

    monkeypatch.setattr(
        "aworld.self_evolve.runner.optimize_from_cli_request",
        fake_optimize_from_cli_request,
    )
    judge_agent = tmp_path / "agent.md"
    judge_agent.write_text("---\nname: trajectory-judge\n---\nJudge trajectory quality.\n", encoding="utf-8")
    scheduler = SelfEvolveScheduler(workspace_root=tmp_path)
    result = scheduler.enqueue(
        SelfEvolveRunContext(
            agent_id="agent",
            task_id="judge-config-job",
            workspace_root=str(tmp_path),
            trajectory=_trajectory(),
            self_evolve_config=SelfEvolveConfig(
                mode="online",
                apply_policy="auto_verified",
                judge_config={"mode": "agent_md", "agent_path": str(judge_agent)},
            ),
        )
    )
    assert result.job_path is not None

    drained = SelfEvolveJobWorker(workspace_root=tmp_path).drain_pending_jobs()

    assert drained == 1
    assert captured["agent"] == "agent"
    assert captured["judge_config"].mode == "agent_md"
    assert captured["judge_config"].agent_path == str(judge_agent)
    assert captured["replay_enabled"] is True
    assert captured["replay_timeout_seconds"] == 600
    assert captured["replay_max_steps"] == 1
    assert captured["replay_candidate_limit"] == 1


def test_job_worker_persists_framework_result_and_replay_diagnostics(tmp_path) -> None:
    scheduler = SelfEvolveScheduler(workspace_root=tmp_path)
    result = scheduler.enqueue(
        SelfEvolveRunContext(
            agent_id="agent",
            task_id="diagnostic-job",
            workspace_root=str(tmp_path),
            trajectory=_trajectory(),
            self_evolve_config=SelfEvolveConfig(mode="shadow", replay_enabled=False),
        )
    )
    assert result.job_path is not None

    def run_job(payload):
        return {
            "status": "rejected",
            "run_id": "run-1",
            "report_path": ".aworld/self_evolve/run-1/report.json",
            "replay_path": ".aworld/self_evolve/run-1/replay/cand-1",
            "gate_results": [
                {
                    "gate_name": "candidate_replay",
                    "passed": False,
                    "reason": "candidate replay failed",
                }
            ],
        }

    drained = SelfEvolveJobWorker(workspace_root=tmp_path, run_job=run_job).drain_pending_jobs()

    assert drained == 1
    saved = json.loads(result.job_path.read_text(encoding="utf-8"))
    assert saved["status"] == "succeeded"
    assert saved["framework_result"]["status"] == "rejected"
    assert saved["replay_diagnostics"]["replay_path"] == ".aworld/self_evolve/run-1/replay/cand-1"
    assert saved["replay_diagnostics"]["failed_gates"][0]["gate_name"] == "candidate_replay"


def test_job_worker_recovers_interrupted_apply_before_draining(tmp_path) -> None:
    from aworld.self_evolve.store import FilesystemSelfEvolveStore
    from aworld.self_evolve.types import CandidateVariant, SelfEvolveRun, SelfEvolveTargetRef

    target_path = tmp_path / "skills" / "demo" / "SKILL.md"
    target_path.parent.mkdir(parents=True)
    original = "---\nname: demo\n---\n# Demo\n\nOriginal.\n"
    target_path.write_text(original, encoding="utf-8")
    target = SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(target_path))
    candidate = CandidateVariant(
        candidate_id="cand-1",
        target=target,
        content="---\nname: demo\n---\n# Demo\n\nCandidate.\n",
        rationale="candidate",
    )
    store = FilesystemSelfEvolveStore(workspace_root=tmp_path)
    store.create_run(SelfEvolveRun(run_id="run-interrupted", target=target))
    _backup_path, journal_path = store.write_apply_backup(
        "run-interrupted",
        candidate=candidate,
        original_content=original,
        target_path=str(target_path),
    )
    store.update_apply_journal(journal_path, status="applying")
    target_path.write_text(candidate.content, encoding="utf-8")

    worker = SelfEvolveJobWorker(workspace_root=tmp_path)

    recovered = worker.recover_interrupted_applies()

    assert recovered == 1
    assert target_path.read_text(encoding="utf-8") == original


def test_job_worker_respects_max_jobs_resource_limit(tmp_path) -> None:
    scheduler = SelfEvolveScheduler(
        workspace_root=tmp_path,
        policy=SelfEvolveSchedulerPolicy(allow_duplicate_pending=True),
    )
    for task_id in ("task-1", "task-2"):
        result = scheduler.enqueue(
            SelfEvolveRunContext(
                agent_id="agent",
                task_id=task_id,
                workspace_root=str(tmp_path),
                trajectory=_trajectory(),
                self_evolve_config=SelfEvolveConfig(mode="shadow", replay_enabled=False),
            )
        )
        assert result.accepted is True

    drained = SelfEvolveJobWorker(
        workspace_root=tmp_path,
        run_job=lambda payload: {"status": "succeeded"},
    ).drain_pending_jobs(max_jobs=1)

    assert drained == 1
    jobs = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((tmp_path / ".aworld" / "self_evolve" / "jobs").glob("*.json"))
    ]
    assert sum(1 for job in jobs if job["status"] == "pending") == 1
    assert sum(1 for job in jobs if job["status"] == "succeeded") == 1


def test_async_drain_entrypoint_offloads_sync_worker(monkeypatch, tmp_path) -> None:
    from aworld.self_evolve.scheduler import drain_pending_self_evolve_jobs_async

    calls = {}

    def fake_drain_pending_self_evolve_jobs(*, workspace_root, max_jobs=None):
        calls["workspace_root"] = workspace_root
        calls["max_jobs"] = max_jobs
        return 3

    monkeypatch.setattr(
        "aworld.self_evolve.scheduler.drain_pending_self_evolve_jobs",
        fake_drain_pending_self_evolve_jobs,
    )

    drained = asyncio.run(
        drain_pending_self_evolve_jobs_async(workspace_root=tmp_path, max_jobs=2)
    )

    assert drained == 3
    assert calls == {"workspace_root": tmp_path, "max_jobs": 2}
