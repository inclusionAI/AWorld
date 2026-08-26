"""Local Docker executor with a real Harbor benchmark path.

This provider is intentionally an MVP/development implementation.  It invokes
the Docker and Harbor CLIs directly, which lets a worker container use the host
Docker daemon through a mounted socket without coupling Cloud core to either
tool's Python internals.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from aworld.cloud.errors import CloudError, CloudErrorCode
from aworld.cloud.executor import (
    EventCallback,
    ExecutionResult,
    ExecutorEvent,
    ExecutorHandle,
    ExecutorInspection,
    ExecutorRequest,
    ExecutorStatus,
)
from aworld.cloud.models import (
    ATIF_SCHEMA_VERSION,
    BenchmarkOutcome,
    ExecutorId,
    FileId,
    RunFile,
    RunFileKind,
    RunMode,
    TrajectoryFormat,
    TrajectoryManifest,
    TrajectoryRole,
    utc_now,
)

_DEFAULT_BENCHMARKS = frozenset({("terminal-bench@2.0", "fix-git")})


@dataclass(frozen=True)
class LocalDockerExecutorSettings:
    """Administrator-owned policy for the local Docker MVP provider."""

    docker_command: tuple[str, ...] = ("docker",)
    harbor_command: tuple[str, ...] = ("harbor",)
    harbor_agent: str = "oracle"
    allowed_benchmarks: frozenset[tuple[str, str]] = _DEFAULT_BENCHMARKS
    environment: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.docker_command or not all(self.docker_command):
            raise ValueError("docker_command must not be empty")
        if not self.harbor_command or not all(self.harbor_command):
            raise ValueError("harbor_command must not be empty")
        if not self.harbor_agent.strip():
            raise ValueError("harbor_agent must not be empty")
        if not self.allowed_benchmarks:
            raise ValueError("allowed_benchmarks must not be empty")
        object.__setattr__(self, "docker_command", tuple(self.docker_command))
        object.__setattr__(self, "harbor_command", tuple(self.harbor_command))
        object.__setattr__(
            self,
            "allowed_benchmarks",
            frozenset(self.allowed_benchmarks),
        )


@dataclass
class _Execution:
    request: ExecutorRequest
    process: asyncio.subprocess.Process
    stdout_stream: BinaryIO
    stderr_stream: BinaryIO
    stdout_path: Path
    stderr_path: Path
    command_kind: str
    job_name: str | None = None
    container_name: str | None = None
    completion: asyncio.Task[ExecutionResult] | None = None
    cancelled: bool = False


def _manifest(
    request: ExecutorRequest,
    path: Path,
    *,
    kind: RunFileKind,
    label: str,
    trajectory: TrajectoryManifest | None = None,
) -> RunFile:
    content = path.read_bytes()
    relative_path = path.relative_to(request.output_directory)
    return RunFile(
        id=FileId(f"file-{request.run.id}-{label}"),
        run_id=request.run.id,
        kind=kind,
        relative_path=PurePosixPath(relative_path.as_posix()),
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        created_at=utc_now(),
        trajectory=trajectory,
    )


def _numeric(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _reward_from_trial(trial: object) -> float | None:
    if not isinstance(trial, dict):
        return None
    verifier = trial.get("verifier_result")
    if not isinstance(verifier, dict):
        return None
    rewards = verifier.get("rewards")
    if not isinstance(rewards, dict):
        return None
    explicit = _numeric(rewards.get("reward"))
    if explicit is not None:
        return explicit
    numeric = [_numeric(value) for value in rewards.values()]
    present = [value for value in numeric if value is not None]
    return present[0] if len(present) == 1 else None


def _read_json_object(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _harbor_result(job_directory: Path) -> tuple[dict[str, object] | None, float | None]:
    job_result = _read_json_object(job_directory / "result.json")
    if job_result is None:
        return None, None
    trials = job_result.get("trial_results")
    if isinstance(trials, list):
        for trial in trials:
            reward = _reward_from_trial(trial)
            if reward is not None:
                return job_result, reward
    for candidate in sorted(job_directory.glob("*/result.json")):
        reward = _reward_from_trial(_read_json_object(candidate))
        if reward is not None:
            return job_result, reward
    return job_result, None


class LocalDockerExecutorProvider:
    """Run queries with Docker and benchmark runs through Harbor's Docker harness."""

    def __init__(self, settings: LocalDockerExecutorSettings | None = None) -> None:
        self._settings = settings or LocalDockerExecutorSettings()
        self._executions: dict[ExecutorId, _Execution] = {}

    def _benchmark_command(
        self,
        request: ExecutorRequest,
        *,
        job_name: str,
    ) -> tuple[str, ...]:
        benchmark = request.run.benchmark
        if benchmark is None:
            raise CloudError(
                CloudErrorCode.INVALID_REQUEST,
                "benchmark metadata is required by the Harbor provider",
            )
        identity = (benchmark.dataset, benchmark.task_id)
        if identity not in self._settings.allowed_benchmarks:
            raise CloudError(
                CloudErrorCode.INVALID_REQUEST,
                "benchmark is not allowed by the local Docker provider",
                details={"dataset": benchmark.dataset, "task_id": benchmark.task_id},
            )
        if benchmark.harness not in {None, "harbor"}:
            raise CloudError(
                CloudErrorCode.INVALID_REQUEST,
                "the local Docker provider only supports the Harbor harness",
            )
        jobs_directory = request.output_directory / "harbor"
        jobs_directory.mkdir(parents=True, exist_ok=True)
        command = [
            *self._settings.harbor_command,
            "run",
            "--agent",
            self._settings.harbor_agent,
            "--dataset",
            benchmark.dataset,
            "--include-task-name",
            benchmark.task_id,
            "--job-name",
            job_name,
            "--n-attempts",
            "1",
            "--n-concurrent",
            "1",
            "--jobs-dir",
            str(jobs_directory),
            "--quiet",
            "--yes",
        ]
        if request.run.model and self._settings.harbor_agent != "oracle":
            command.extend(("--model", request.run.model))
        return tuple(command)

    def _query_command(
        self,
        request: ExecutorRequest,
        *,
        container_name: str,
    ) -> tuple[str, ...]:
        command = [
            *self._settings.docker_command,
            "run",
            "--rm",
            "--name",
            container_name,
            "--cpus",
            str(request.resources.cpus),
            "--memory",
            str(request.resources.memory_bytes),
            "--pids-limit",
            str(request.resources.pids),
            "--network",
            request.network.mode,
            "--user",
            request.runtime_user,
            "--workdir",
            str(request.workspace.workdir),
            "--volume",
            f"{request.workspace.writable_repo_path}:{request.workspace.workdir}:rw",
            "--volume",
            f"{request.workspace.codex_home_path}:/home/{request.runtime_user}/.codex:rw",
        ]
        for mount in request.workspace.mounts:
            command.extend(
                (
                    "--volume",
                    f"{mount.host_path}:{mount.container_path}:{mount.access_mode.value}",
                )
            )
        command.extend(
            (
                request.workspace.runtime_image,
                "/bin/sh",
                "-lc",
                request.run.task,
            )
        )
        return tuple(command)

    async def start(self, request: ExecutorRequest) -> ExecutorHandle:
        request.output_directory.mkdir(parents=True, exist_ok=True)
        executor_id = ExecutorId(f"local-docker-{uuid.uuid4().hex}")
        token = str(request.run.id).replace("_", "-")[-32:].lower()
        job_name = f"aworld-{token}"
        container_name: str | None = f"aworld-{token}-{uuid.uuid4().hex[:8]}"
        if request.run.mode is RunMode.BENCHMARK:
            command = self._benchmark_command(request, job_name=job_name)
            command_kind = "harbor"
            container_name = None
        else:
            assert container_name is not None
            command = self._query_command(request, container_name=container_name)
            command_kind = "docker"

        stdout_path = request.output_directory / "stdout.log"
        stderr_path = request.output_directory / "stderr.log"
        stdout_stream = stdout_path.open("wb")
        stderr_stream = stderr_path.open("wb")
        environment = os.environ.copy()
        environment.update(self._settings.environment)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=stdout_stream,
                stderr=stderr_stream,
                env=environment,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            stdout_stream.close()
            stderr_stream.close()
            raise CloudError(
                CloudErrorCode.EXECUTOR_UNAVAILABLE,
                f"could not start {command_kind} executor",
            ) from exc
        execution = _Execution(
            request=request,
            process=process,
            stdout_stream=stdout_stream,
            stderr_stream=stderr_stream,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            command_kind=command_kind,
            job_name=job_name if command_kind == "harbor" else None,
            container_name=container_name,
        )
        execution.completion = asyncio.create_task(self._complete(execution))
        self._executions[executor_id] = execution
        return ExecutorHandle(executor_id)

    async def _stop_process(self, execution: _Execution, grace_seconds: float) -> None:
        if execution.process.returncode is not None:
            return
        try:
            os.killpg(execution.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(execution.process.wait(), timeout=grace_seconds)
            return
        except asyncio.TimeoutError:
            pass
        try:
            os.killpg(execution.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await execution.process.wait()

    async def _complete(self, execution: _Execution) -> ExecutionResult:
        timed_out = False
        timeout = execution.request.resources.wall_clock_timeout.total_seconds()
        try:
            try:
                return_code = await asyncio.wait_for(
                    execution.process.wait(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                timed_out = True
                await self._stop_process(execution, 5.0)
                return_code = 124
        finally:
            execution.stdout_stream.close()
            execution.stderr_stream.close()

        files = [
            _manifest(
                execution.request,
                execution.stdout_path,
                kind=RunFileKind.STDOUT,
                label="stdout",
            ),
            _manifest(
                execution.request,
                execution.stderr_path,
                kind=RunFileKind.STDERR,
                label="stderr",
            ),
        ]
        error_code: str | None = None
        error_message: str | None = None
        outcome: BenchmarkOutcome | None = None
        result_payload: dict[str, object] = {
            "executor": execution.command_kind,
            "exit_code": return_code,
        }

        if execution.command_kind == "harbor":
            assert execution.job_name is not None
            job_directory = (
                execution.request.output_directory / "harbor" / execution.job_name
            )
            job_result, reward = _harbor_result(job_directory)
            benchmark = execution.request.run.benchmark
            result_payload.update(
                {
                    "agent": self._settings.harbor_agent,
                    "dataset": benchmark.dataset if benchmark else None,
                    "task_id": benchmark.task_id if benchmark else None,
                    "job_name": execution.job_name,
                    "job_result": job_result,
                    "reward": reward,
                }
            )
            if return_code == 0 and job_result is None:
                return_code = 1
                error_code = "harbor_result_missing"
                error_message = "Harbor completed without a readable result.json"
            elif return_code == 0 and reward is None:
                return_code = 1
                error_code = "harbor_reward_missing"
                error_message = "Harbor result did not contain a verifier reward"
            if job_result is not None:
                job_result_path = job_directory / "result.json"
                files.append(
                    _manifest(
                        execution.request,
                        job_result_path,
                        kind=RunFileKind.ARTIFACT,
                        label="harbor-result",
                    )
                )
            if reward is not None:
                outcome = BenchmarkOutcome(reward=reward, result=result_payload)

        if timed_out:
            error_code = "executor_timeout"
            error_message = "executor exceeded its wall-clock timeout"
        elif execution.cancelled:
            error_code = "cancelled"
            error_message = "executor was cancelled"
        elif return_code != 0 and error_code is None:
            error_code = f"{execution.command_kind}_exit_{return_code}"
            error_message = f"{execution.command_kind} executor exited unsuccessfully"

        result_path = execution.request.output_directory / "result.json"
        result_path.write_text(
            json.dumps(result_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        files.append(
            _manifest(
                execution.request,
                result_path,
                kind=RunFileKind.RESULT,
                label="result",
            )
        )
        if return_code == 0 and error_code is None:
            trajectory_path = self._write_trajectory(
                execution,
                result_payload=result_payload,
                reward=outcome.reward if outcome else None,
            )
            files.append(
                _manifest(
                    execution.request,
                    trajectory_path,
                    kind=RunFileKind.TRAJECTORY,
                    label="trajectory",
                    trajectory=TrajectoryManifest(
                        format=TrajectoryFormat.ATIF,
                        schema_version=ATIF_SCHEMA_VERSION,
                        role=TrajectoryRole.CANONICAL,
                    ),
                )
            )
        return ExecutionResult(
            exit_code=return_code,
            finished_at=utc_now(),
            error_code=error_code,
            error_message=error_message,
            benchmark_outcome=outcome,
            files=tuple(files),
        )

    def _write_trajectory(
        self,
        execution: _Execution,
        *,
        result_payload: Mapping[str, object],
        reward: float | None,
    ) -> Path:
        request = execution.request
        message = (
            "Harbor executed the task solution and verifier through Docker."
            if execution.command_kind == "harbor"
            else "The local Docker command completed."
        )
        payload = {
            "schema_version": ATIF_SCHEMA_VERSION,
            "session_id": str(request.run.id),
            "agent": {
                "name": (
                    f"harbor-{self._settings.harbor_agent}"
                    if execution.command_kind == "harbor"
                    else "aworld-local-docker"
                ),
                "version": "1",
            },
            "steps": [
                {"step_id": 1, "source": "user", "message": request.run.task},
                {
                    "step_id": 2,
                    "source": "agent",
                    "message": message,
                    "llm_call_count": 0,
                    "extra": dict(result_payload),
                },
            ],
            "final_metrics": {"reward": reward, "total_steps": 2},
            "extra": {"producer": "aworld-cloud-local-docker-provider"},
        }
        path = request.output_directory / "trajectory.atif.json"
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    async def wait(
        self,
        handle: ExecutorHandle,
        *,
        on_event: EventCallback,
    ) -> ExecutionResult:
        execution = self._executions.get(handle.executor_id)
        if execution is None or execution.completion is None:
            raise CloudError(
                CloudErrorCode.EXECUTOR_UNAVAILABLE,
                "local Docker executor is not available",
            )
        await on_event(
            ExecutorEvent(
                event_type=f"{execution.command_kind}.started",
                payload={"executor_id": str(handle.executor_id)},
                created_at=utc_now(),
            )
        )
        result = await asyncio.shield(execution.completion)
        await on_event(
            ExecutorEvent(
                event_type=f"{execution.command_kind}.completed",
                payload={"exit_code": result.exit_code},
                created_at=result.finished_at,
            )
        )
        return result

    async def inspect(self, executor_id: ExecutorId) -> ExecutorInspection:
        execution = self._executions.get(executor_id)
        if execution is None or execution.completion is None:
            return ExecutorInspection(status=ExecutorStatus.NOT_FOUND)
        if not execution.completion.done():
            return ExecutorInspection(
                status=ExecutorStatus.RUNNING,
                reattachable=True,
            )
        return ExecutorInspection(
            status=ExecutorStatus.EXITED,
            result=execution.completion.result(),
            reattachable=True,
        )

    async def cancel(
        self,
        executor_id: ExecutorId,
        *,
        grace_period: timedelta,
    ) -> None:
        execution = self._executions.get(executor_id)
        if execution is None:
            return
        execution.cancelled = True
        await self._stop_process(execution, grace_period.total_seconds())
        if execution.container_name:
            try:
                cleanup = await asyncio.create_subprocess_exec(
                    *self._settings.docker_command,
                    "rm",
                    "--force",
                    execution.container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await cleanup.wait()
            except OSError:
                pass


def parse_command(value: str) -> tuple[str, ...]:
    """Parse a deliberately simple executable setting without shell expansion."""

    command = tuple(part for part in value.split() if part)
    if not command:
        raise ValueError("executor command must not be empty")
    return command


def local_docker_settings_from_env(
    environment: Mapping[str, str] | None = None,
) -> LocalDockerExecutorSettings:
    """Build provider settings from process environment for composition roots."""

    values = os.environ if environment is None else environment
    dataset = values.get("AWORLD_CLOUD_BENCHMARK_DATASET", "terminal-bench@2.0")
    task_id = values.get("AWORLD_CLOUD_BENCHMARK_TASK_ID", "fix-git")
    return LocalDockerExecutorSettings(
        docker_command=parse_command(values.get("AWORLD_CLOUD_DOCKER_COMMAND", "docker")),
        harbor_command=parse_command(values.get("AWORLD_CLOUD_HARBOR_COMMAND", "harbor")),
        harbor_agent=values.get("AWORLD_CLOUD_HARBOR_AGENT", "oracle"),
        allowed_benchmarks=frozenset({(dataset, task_id)}),
    )
