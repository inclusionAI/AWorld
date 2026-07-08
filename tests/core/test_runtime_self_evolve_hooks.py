from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from aworld.config.conf import AgentConfig, SelfEvolveConfig
from aworld_cli.models import AgentInfo
from aworld_cli.runtime.base import BaseCliRuntime
from aworld_cli.runtime.cli import _apply_self_evolve_config_to_swarm


class DummyRuntime(BaseCliRuntime):
    async def _load_agents(self):
        return []

    async def _create_executor(self, agent: AgentInfo):
        return None

    def _get_source_type(self) -> str:
        return "TEST"

    def _get_source_location(self) -> str:
        return "test://runtime"


def test_runtime_refresh_skill_registry_rebuilds_runtime_view_and_resets_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = DummyRuntime()
    calls = {"reset": 0, "build": []}

    class FakeRegistryView:
        source_paths = (str(tmp_path / "skills"),)

        def get_all_skills(self):
            return {"demo": object()}

    monkeypatch.setattr(
        "aworld_cli.core.skill_registry.reset_skill_registry",
        lambda: calls.__setitem__("reset", calls["reset"] + 1),
    )
    monkeypatch.setattr(
        "aworld_cli.core.runtime_skill_registry.build_runtime_skill_registry_view",
        lambda skill_paths=None, cwd=None: calls["build"].append(
            {"skill_paths": skill_paths, "cwd": cwd}
        )
        or FakeRegistryView(),
    )

    result = runtime.refresh_skill_registry(candidate=None)

    assert calls["reset"] == 1
    assert calls["build"] == [{"skill_paths": None, "cwd": Path.cwd()}]
    assert sorted(runtime._runtime_skill_registry.get_all_skills()) == ["demo"]
    assert result == {
        "status": "refreshed",
        "runtime_skill_count": 1,
        "source_paths": [str(tmp_path / "skills")],
    }


def test_cli_runtime_applies_self_evolve_config_to_swarm_agent_conf() -> None:
    root_agent = SimpleNamespace(conf=AgentConfig())
    helper_agent = SimpleNamespace(conf=AgentConfig())
    swarm = SimpleNamespace(
        communicate_agent=root_agent,
        ordered_agents=[root_agent, helper_agent],
    )
    config = SelfEvolveConfig(mode="online", apply_policy="auto_verified")

    _apply_self_evolve_config_to_swarm(swarm, config)

    assert root_agent.conf.self_evolve_config.mode == "online"
    assert root_agent.conf.self_evolve_config.apply_policy == "auto_verified"
    assert helper_agent.conf.self_evolve_config.mode == "online"
    assert helper_agent.conf.self_evolve_config.apply_policy == "auto_verified"


@pytest.mark.asyncio
async def test_runtime_start_drains_pending_self_evolve_jobs_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = DummyRuntime()
    calls = []

    async def fake_start_scheduler():
        return None

    async def fake_drain_pending_self_evolve_jobs(*, max_jobs=None):
        calls.append(max_jobs)
        return 1

    runtime._start_scheduler = fake_start_scheduler
    runtime._initialize_plugin_framework = lambda: None
    runtime._drain_pending_self_evolve_jobs = fake_drain_pending_self_evolve_jobs

    await runtime.start()

    assert calls == [None]
