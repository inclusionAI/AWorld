import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig
from aworld.core.agent.swarm import Swarm
from aworld_cli.core.skill_activation_resolver import SkillActivationResolver, SkillResolverRequest
from aworld_cli.core.skill_activation_resolver import ResolvedSkillSet
from aworld_cli.executors.local import LocalAgentExecutor
from aworld_cli.runtime.cli import _apply_runtime_skill_paths_to_swarm


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


def test_runtime_marks_only_explicit_self_evolve_candidate_paths() -> None:
    candidate_path = "/tmp/self-evolve-candidate"
    ordinary_path = "/tmp/ordinary-skill"
    agent = Agent(
        name="developer",
        conf=AgentConfig(skill_configs={}, ext={}),
    )

    _apply_runtime_skill_paths_to_swarm(
        Swarm(agent),
        (candidate_path, ordinary_path),
        (candidate_path,),
    )

    resolver_inputs = agent.conf.ext["skill_resolver_inputs"]
    assert resolver_inputs["compatibility_sources"] == [
        candidate_path,
        ordinary_path,
    ]
    assert resolver_inputs["isolated_candidate_sources"] == [candidate_path]


@pytest.mark.asyncio
async def test_local_executor_resolves_skills_from_task_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = {}
    (tmp_path / "runtime-plugin").mkdir()
    (tmp_path / "installed-skill").mkdir()
    (tmp_path / "agent-plugin").mkdir()
    candidate_skill_root = tmp_path / "candidate-skills" / "browser-use"
    candidate_skill_root.mkdir(parents=True)

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
                activation_evidence=(
                    {
                        "skill_name": "browser-use",
                        "canonical_skill_file": str(
                            candidate_skill_root / "SKILL.md"
                        ),
                        "canonical_skill_root": str(candidate_skill_root),
                        "package_fingerprint": "sha256:candidate",
                        "source": "aworld_cli_skill_activation_resolver",
                    },
                ),
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
                    "isolated_candidate_sources": [
                        str(tmp_path / "candidate-skills")
                    ],
                }
            },
        ),
    )
    executor = LocalAgentExecutor(
        Swarm(agent),
        runtime_skill_paths=[str(tmp_path / "candidate-skills")],
        isolated_candidate_skill_paths=[str(tmp_path / "candidate-skills")],
    )
    monkeypatch.setattr(executor, "_create_workspace", _fake_create_workspace)

    task = await executor._build_task(
        "open docs in browser",
        session_id="session-1",
        task_id="task-1",
        requested_skill_names=["browser-use"],
    )

    assert captured["request"].requested_skill_names == ("browser-use",)
    assert captured["request"].task_text == "open docs in browser"
    assert captured["request"].compatibility_sources == (
        str(tmp_path / "compat-skills"),
        str(tmp_path / "candidate-skills"),
    )
    assert captured["request"].isolated_candidate_sources == (
        str(tmp_path / "candidate-skills"),
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
    assert task._aworld_cli_skill_activation_evidence[0][
        "canonical_skill_root"
    ] == str(candidate_skill_root)


@pytest.mark.asyncio
async def test_local_executor_fails_closed_when_isolated_skill_is_unattested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    candidate_root = tmp_path / "candidate-skills"
    candidate_root.mkdir()

    class DummyResolver:
        def resolve(self, request):
            return ResolvedSkillSet(
                skill_configs={},
                active_skill_names=(),
                available_skill_names=(),
                activation_evidence=(),
            )

    class DummyPluginManager:
        def get_runtime_plugin_roots(self):
            return []

        def list_skill_packages(self, include_disabled: bool = False):
            return []

    async def _fake_create_workspace(_session_id):
        return tmp_path / "workspace"

    monkeypatch.setattr(
        "aworld_cli.executors.local.SkillActivationResolver", DummyResolver
    )
    monkeypatch.setattr(
        "aworld_cli.executors.local.PluginManager", DummyPluginManager
    )
    agent = Agent(
        name="developer",
        # Simulate a task-time Agent replacement/reset that loses the
        # compatibility metadata previously attached to conf.ext.
        conf=AgentConfig(skill_configs={}, ext={}),
    )
    executor = LocalAgentExecutor(
        Swarm(agent),
        runtime_skill_paths=[str(candidate_root)],
        isolated_candidate_skill_paths=[str(candidate_root)],
    )
    monkeypatch.setattr(executor, "_create_workspace", _fake_create_workspace)

    with pytest.raises(
        RuntimeError,
        match="isolated candidate skill did not produce activation evidence",
    ):
        await executor._build_task(
            "use candidate",
            session_id="session-1",
            task_id="task-1",
            requested_skill_names=["browser-use"],
        )


def test_resolver_builds_skill_configs_from_framework_registry(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "browser-use"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: Browser automation\nentrypoint: scripts/run.sh\n---\n\n# Usage\nUse browser tools.\n",
        encoding="utf-8",
    )
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.sh").write_text("echo browser\n", encoding="utf-8")

    request = SkillResolverRequest(
        plugin_roots=(),
        runtime_scope="session",
        agent_name="Aworld",
        compatibility_sources=(str(tmp_path / "skills"),),
    )

    resolved = SkillActivationResolver().resolve(request)

    assert "browser-use" in resolved.skill_configs
    assert resolved.skill_configs["browser-use"]["description"] == "Browser automation"
    assert resolved.skill_configs["browser-use"]["asset_root"] == str(skill_dir.resolve())
    assert resolved.skill_configs["browser-use"]["execution_assets"]["enabled"] is True
    assert resolved.skill_configs["browser-use"]["execution_assets"]["relative_paths"] == ["scripts/run.sh"]
    assert resolved.skill_configs["browser-use"]["execution_assets"]["entrypoint"] == "scripts/run.sh"


def test_resolver_builds_skill_configs_from_nested_metadata_entrypoint(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "browser-use"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        (
            "---\n"
            "description: Browser automation\n"
            "metadata:\n"
            "  entrypoint: scripts/index.ts\n"
            "---\n\n"
            "# Usage\nUse browser tools.\n"
        ),
        encoding="utf-8",
    )
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "index.ts").write_text("console.log('browser');\n", encoding="utf-8")

    request = SkillResolverRequest(
        plugin_roots=(),
        runtime_scope="session",
        agent_name="Aworld",
        compatibility_sources=(str(tmp_path / "skills"),),
    )

    resolved = SkillActivationResolver().resolve(request)

    assert resolved.skill_configs["browser-use"]["execution_assets"]["entrypoint"] == "scripts/index.ts"
