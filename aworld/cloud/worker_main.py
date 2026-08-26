"""Runnable worker composition root for the local-Docker Cloud MVP."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path

from aworld.cloud.local_docker_executor import (
    LocalDockerExecutorProvider,
    LocalDockerExecutorSettings,
    local_docker_settings_from_env,
)
from aworld.cloud.runtime import cloud_settings_from_env
from aworld.cloud.settings import CloudSettings
from aworld.cloud.sqlite_repository import SQLiteCloudRepository
from aworld.cloud.worker import CloudWorker


async def _command_ready(command: tuple[str, ...], *arguments: str) -> None:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            *arguments,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return_code = await asyncio.wait_for(process.wait(), timeout=15)
    except (OSError, asyncio.TimeoutError) as exc:
        raise RuntimeError(f"runtime dependency is unavailable: {command[0]}") from exc
    if return_code != 0:
        raise RuntimeError(f"runtime dependency check failed: {command[0]}")


async def verify_executor_ready(settings: LocalDockerExecutorSettings) -> None:
    """Fail startup unless both Harbor and its host Docker daemon are usable."""

    await _command_ready(settings.harbor_command, "--help")
    await _command_ready(
        settings.docker_command,
        "version",
        "--format",
        "{{.Server.Version}}",
    )


async def _write_health(path: Path, stop: asyncio.Event) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    while not stop.is_set():
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"ok": True, "pid": os.getpid(), "updated_at": asyncio.get_running_loop().time()}),
            encoding="utf-8",
        )
        temporary.replace(path)
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass


async def run_worker(
    settings: CloudSettings | None = None,
    provider_settings: LocalDockerExecutorSettings | None = None,
    *,
    preflight: bool = True,
) -> None:
    resolved = settings or cloud_settings_from_env()
    executor_settings = provider_settings or local_docker_settings_from_env()
    if preflight:
        await verify_executor_ready(executor_settings)
    assert resolved.database_path is not None
    repository = SQLiteCloudRepository(resolved.database_path)
    await repository.initialize()
    worker = CloudWorker(
        repository,
        LocalDockerExecutorProvider(executor_settings),
        resolved,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:
            pass
    health_path = resolved.data_root / "worker-health.json"
    health_task = asyncio.create_task(_write_health(health_path, stop))
    worker_task = asyncio.create_task(worker.run_forever())
    try:
        stop_task = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait(
            {stop_task, worker_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if worker_task in done:
            stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)
            await worker_task
        else:
            await worker.stop(graceful=True)
            await worker_task
    finally:
        stop.set()
        health_task.cancel()
        await asyncio.gather(health_task, return_exceptions=True)
        if not worker_task.done():
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
        health_path.unlink(missing_ok=True)
        await repository.close()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
