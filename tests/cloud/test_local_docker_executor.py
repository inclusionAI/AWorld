from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path, PurePosixPath

import pytest

from aworld.cloud.errors import CloudError, CloudErrorCode
from aworld.cloud.executor import ExecutorEvent, ExecutorRequest
from aworld.cloud.local_docker_executor import (
    LocalDockerExecutorProvider,
    LocalDockerExecutorSettings,
)
from aworld.cloud.models import (
    BenchmarkMetadata,
    Run,
    RunFileKind,
    RunId,
    RunMode,
    RunState,
    TrajectoryRole,
    Workspace,
    WorkspaceId,
    WorkspaceState,
    utc_now,
)
from aworld.cloud.settings import NetworkPolicy, ResourceLimits


def _request(tmp_path: Path, *, task_id: str = "fix-git") -> ExecutorRequest:
    now = utc_now()
    workspace = Workspace(
        id=WorkspaceId("workspace-1"),
        name="benchmark",
        profile_name="local-docker",
        state=WorkspaceState.BUSY,
        revision=1,
        runtime_image="aworld-cloud:local",
        writable_repo_path=tmp_path / "workspace",
        codex_home_path=tmp_path / "codex-home",
        workdir=PurePosixPath("/workspace/aworld"),
        created_at=now,
        updated_at=now,
    )
    run = Run(
        id=RunId("run-1"),
        workspace_id=workspace.id,
        state=RunState.STARTING,
        revision=1,
        attempt=1,
        task="Repair the broken Git repository and satisfy the verifier.",
        created_at=now,
        mode=RunMode.BENCHMARK,
        benchmark=BenchmarkMetadata(
            dataset="terminal-bench@2.0",
            task_id=task_id,
            harness="harbor",
        ),
    )
    return ExecutorRequest(
        workspace=workspace,
        run=run,
        output_directory=tmp_path / "runs" / str(run.id),
        runtime_user="root",
        resources=ResourceLimits(wall_clock_timeout=timedelta(seconds=10)),
        network=NetworkPolicy(mode="bridge"),
    )


def _query_request(tmp_path: Path) -> ExecutorRequest:
    benchmark_request = _request(tmp_path)
    run = benchmark_request.run
    return ExecutorRequest(
        workspace=benchmark_request.workspace,
        run=Run(
            id=run.id,
            workspace_id=run.workspace_id,
            state=run.state,
            revision=run.revision,
            attempt=run.attempt,
            task="printf query-ok",
            created_at=run.created_at,
            mode=RunMode.QUERY,
        ),
        output_directory=benchmark_request.output_directory,
        runtime_user=benchmark_request.runtime_user,
        resources=benchmark_request.resources,
        network=benchmark_request.network,
    )


@pytest.mark.asyncio
async def test_harbor_benchmark_produces_result_logs_and_one_canonical_atif(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "fake_harbor.py"
    executable.write_text(
        """
import json
import sys
from pathlib import Path

args = sys.argv[1:]
assert args[0] == "run"
jobs_dir = Path(args[args.index("--jobs-dir") + 1])
job_name = args[args.index("--job-name") + 1]
job_dir = jobs_dir / job_name
trial_dir = job_dir / "fix-git__oracle__1"
trial_dir.mkdir(parents=True)
trial = {
    "task_name": "fix-git",
    "trial_name": "fix-git__oracle__1",
    "verifier_result": {"rewards": {"reward": 1}},
}
(trial_dir / "result.json").write_text(json.dumps(trial))
(job_dir / "result.json").write_text(json.dumps({"trial_results": [trial]}))
print("real harbor stdout")
print("real harbor stderr", file=sys.stderr)
""".strip()
        + "\n",
        encoding="utf-8",
    )
    provider = LocalDockerExecutorProvider(
        LocalDockerExecutorSettings(
            harbor_command=(sys.executable, str(executable)),
        )
    )
    request = _request(tmp_path)
    handle = await provider.start(request)
    events: list[ExecutorEvent] = []

    async def receive(event: ExecutorEvent) -> None:
        events.append(event)

    result = await provider.wait(handle, on_event=receive)

    assert result.exit_code == 0
    assert result.error_code is None
    assert result.benchmark_outcome is not None
    assert result.benchmark_outcome.reward == 1.0
    assert [event.event_type for event in events] == [
        "harbor.started",
        "harbor.completed",
    ]
    assert {run_file.kind for run_file in result.files} >= {
        RunFileKind.STDOUT,
        RunFileKind.STDERR,
        RunFileKind.RESULT,
        RunFileKind.ARTIFACT,
        RunFileKind.TRAJECTORY,
    }
    canonical = [
        run_file
        for run_file in result.files
        if run_file.trajectory is not None
        and run_file.trajectory.role is TrajectoryRole.CANONICAL
    ]
    assert len(canonical) == 1
    trajectory = json.loads(
        (request.output_directory / canonical[0].relative_path).read_text()
    )
    assert trajectory["schema_version"] == "ATIF-v1.7"
    assert trajectory["final_metrics"]["reward"] == 1.0
    assert (request.output_directory / "stdout.log").read_text().strip() == (
        "real harbor stdout"
    )


@pytest.mark.asyncio
async def test_harbor_benchmark_rejects_task_outside_admin_allowlist(
    tmp_path: Path,
) -> None:
    provider = LocalDockerExecutorProvider()

    with pytest.raises(CloudError) as raised:
        await provider.start(_request(tmp_path, task_id="not-allowed"))

    assert raised.value.code is CloudErrorCode.INVALID_REQUEST


@pytest.mark.asyncio
async def test_local_docker_query_produces_one_canonical_atif(tmp_path: Path) -> None:
    executable = tmp_path / "controlled_docker.py"
    executable.write_text(
        "import sys\nprint('docker query', ' '.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    provider = LocalDockerExecutorProvider(
        LocalDockerExecutorSettings(
            docker_command=(sys.executable, str(executable)),
        )
    )
    request = _query_request(tmp_path)
    handle = await provider.start(request)

    async def receive(event: ExecutorEvent) -> None:
        del event

    result = await provider.wait(handle, on_event=receive)

    assert result.exit_code == 0
    assert result.benchmark_outcome is None
    canonical = [
        run_file
        for run_file in result.files
        if run_file.trajectory is not None
        and run_file.trajectory.role is TrajectoryRole.CANONICAL
    ]
    assert len(canonical) == 1
    trajectory = json.loads(
        (request.output_directory / canonical[0].relative_path).read_text()
    )
    assert trajectory["steps"][0]["message"] == "printf query-ok"
    assert trajectory["extra"]["producer"] == (
        "aworld-cloud-local-docker-provider"
    )
