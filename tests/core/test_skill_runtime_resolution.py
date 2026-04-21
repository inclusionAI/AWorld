import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig
from aworld.core.agent.swarm import Swarm
from aworld_cli.core.skill_activation_resolver import ResolvedSkillSet
from aworld_cli.executors.local import LocalAgentExecutor


class _DummyContext:
    def __init__(self, task_input):
        self.task_id = task_input.task_id
        self.user_id = task_input.user_id
        self.session_id = task_input.session_id
        self.workspace_path = None
        self._config = SimpleNamespace(debug_mode=False)

    def get_config(self):
        return self._config

    async def init_swarm_state(self, _swarm):
        return None


@pytest.mark.asyncio
async def test_local_executor_resolves_skills_from_task_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = {}
    (tmp_path / "runtime-plugin").mkdir()
    (tmp_path / "installed-skill").mkdir()
    (tmp_path / "agent-plugin").mkdir()

    class DummyResolver:
        def resolve(self, request):
            captured["request"] = request
            return ResolvedSkillSet(
                skill_configs={
                    "browser-use": {
                        "name": "browser-use",
                        "active": True,
                        "skill_path": "/tmp/browser/SKILL.md",
                    }
                },
                active_skill_names=("browser-use",),
                available_skill_names=("browser-use",),
            )

    class DummyPluginManager:
        def get_runtime_plugin_roots(self):
            return [tmp_path / "runtime-plugin"]

        def list_skill_packages(self, include_disabled: bool = False):
            return [
                {
                    "path": str(tmp_path / "installed-skill"),
                    "metadata": {"scope": "global"},
                }
            ]

    async def _fake_from_input(task_input, workspace=None, context_config=None):
        return _DummyContext(task_input)

    async def _fake_create_workspace(_session_id):
        return tmp_path / "workspace"

    monkeypatch.setattr("aworld_cli.executors.local.SkillActivationResolver", DummyResolver)
    monkeypatch.setattr("aworld_cli.executors.local.PluginManager", DummyPluginManager)
    monkeypatch.setattr(
        "aworld_cli.executors.local.ApplicationContext.from_input",
        _fake_from_input,
    )

    agent = Agent(
        name="developer",
        conf=AgentConfig(
            skill_configs={},
            ext={
                "skill_resolver_inputs": {
                    "plugin_roots": [str(tmp_path / "agent-plugin")],
                    "compatibility_sources": [str(tmp_path / "compat-skills")],
                    "compatibility_skill_patterns": ["browser-use"],
                }
            },
        ),
    )
    executor = LocalAgentExecutor(Swarm(agent))
    monkeypatch.setattr(executor, "_create_workspace", _fake_create_workspace)

    await executor._build_task(
        "open docs in browser",
        session_id="session-1",
        task_id="task-1",
        requested_skill_names=["browser-use"],
    )

    assert captured["request"].requested_skill_names == ("browser-use",)
    assert captured["request"].task_text == "open docs in browser"
    assert captured["request"].compatibility_sources == (
        str(tmp_path / "compat-skills"),
    )
    assert captured["request"].plugin_roots == (
        (tmp_path / "runtime-plugin"),
        (tmp_path / "installed-skill"),
        (tmp_path / "agent-plugin"),
    )
    assert agent.conf.skill_configs == {
        "browser-use": {
            "name": "browser-use",
            "active": True,
            "skill_path": "/tmp/browser/SKILL.md",
        }
    }
