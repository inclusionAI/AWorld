import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.amni.config import AgentContextConfig
from aworld.core.context.amni.processor.op.system_prompt_augment_op import SystemPromptAugmentOp
from aworld.core.context.amni.tool.context_skill_tool import CONTEXT_SKILL
from aworld.sandbox import Sandbox
from aworld_cli.core.skill_activation_resolver import SkillActivationResolver, SkillResolverRequest
from aworld_cli.core.skill_activation_resolver import ResolvedSkillSet
from aworld_cli.executors.local import LocalAgentExecutor


class _DummyContext:
    def __init__(self, task_input):
        self.task_id = task_input.task_id
        self.user_id = task_input.user_id
        self.session_id = task_input.session_id
        self.workspace_path = None
        self.context_info = {}
        self._state = {}
        self._config = SimpleNamespace(debug_mode=False)

    def get_config(self):
        return self._config

    async def init_swarm_state(self, _swarm):
        return None

    def set_state(self, key, value):
        self._state[key] = value


@pytest.mark.asyncio
async def test_local_executor_resolves_skills_from_task_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
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
                    "default_skill_names": ["filex"],
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
    assert captured["request"].default_skill_names == ("filex",)
    assert captured["request"].enabled_skill_names == ()
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
    assert agent.skill_configs is agent.conf.skill_configs
    assert CONTEXT_SKILL in agent.tool_names


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", [[], ["filex"]], ids=["agent-default", "explicit"])
async def test_resolved_filex_reaches_prompt_tools_and_sandbox(
    monkeypatch: pytest.MonkeyPatch, requested: list[str]
) -> None:
    monkeypatch.setattr(
        "aworld_cli.executors.local.PluginManager",
        lambda: SimpleNamespace(
            get_runtime_plugin_roots=lambda: [],
            list_skill_packages=lambda **kwargs: [],
        ),
    )
    monkeypatch.setattr(
        "aworld_cli.core.skill_state_manager.SkillStateManager",
        lambda: SimpleNamespace(
            disabled_skill_names=lambda: (),
            enabled_skill_names=lambda: (),
        ),
    )
    sandbox = Sandbox(
        mcp_servers=["terminal"],
        mcp_config={"mcpServers": {"terminal": {"command": "unused-terminal"}}},
    )
    agent = Agent(
        name="filex-runtime",
        conf=AgentConfig(
            skill_configs={},
            ext={"skill_resolver_inputs": {"default_skill_names": ["filex"]}},
        ),
        sandbox=sandbox,
    )
    assert not agent.skill_configs
    assert not sandbox.skill_configs
    assert CONTEXT_SKILL not in agent.tool_names

    context = ApplicationContext.create(
        session_id="filex-runtime-session",
        task_id="filex-runtime-task",
        task_content="Process the supplied input and write the requested artifacts.",
    )
    context.task_input_object.metadata["requested_skill_names"] = requested
    executor = LocalAgentExecutor(Swarm(agent))
    executor._resolve_swarm_skills(context.task_input_object)
    # A second task resolution must refresh caches without duplicating tools.
    executor._resolve_swarm_skills(context.task_input_object)

    assert agent.skill_configs is agent.conf.skill_configs
    assert sandbox.skill_configs == agent.skill_configs
    assert sandbox.mcpservers.skill_configs == agent.skill_configs
    assert agent.tool_names.count(CONTEXT_SKILL) == 1
    filex = agent.skill_configs["filex"]
    assert filex["active"] is True
    assert filex["execution_assets"]["enabled"] is True
    assert "scripts/filex.py" in filex["execution_assets"]["relative_paths"]

    await context.build_agent_state(agent)
    assert "filex" in await context.get_active_skills(agent.id())
    monkeypatch.setattr(
        context,
        "get_agent_context_config",
        lambda _: AgentContextConfig(
            enable_system_prompt_augment=True,
            enable_aworld_file=False,
            neuron_names=[],
        ),
    )
    monkeypatch.setattr(
        "aworld.core.context.amni.processor.op.system_prompt_augment_op.AgentFactory.agent_instance",
        lambda _: agent,
    )
    op = SystemPromptAugmentOp()

    async def render_neuron(*, neuron, context, namespace):
        items = await neuron.format_items(context=context, namespace=namespace)
        return await neuron.format(context=context, items=items, namespace=namespace)

    monkeypatch.setattr(op, "rerank_items", render_neuron)
    prompts = await op._process_neurons(
        context,
        SimpleNamespace(agent_id=agent.id(), namespace=agent.id()),
    )
    assert '<skill id="filex" active_status="True">' in prompts["skills"]
    assert "python3 /skills/filex/scripts/filex.py parse" in prompts["skills"]

    shell_tool = {"type": "function", "function": {"name": "shell_execute"}}
    agent.tools = [shell_tool]
    agent.tool_mapping = {"shell_execute": "terminal"}
    assert await agent._filter_tools(context) == [shell_tool]


@pytest.mark.parametrize("shared_sandbox", [True, False], ids=["shared", "separate"])
def test_skill_resolution_keeps_all_sandbox_owners_without_changing_agent_skills(
    monkeypatch: pytest.MonkeyPatch, shared_sandbox: bool
) -> None:
    resolved_by_agent = {
        "filex-owner": {
            "filex": {"name": "filex", "active": True},
            "common": {"name": "common", "active": True},
        },
        "browser-owner": {
            "common": {"name": "common", "active": False},
            "browser-use": {"name": "browser-use", "active": True},
        },
    }

    def resolve(request):
        configs = resolved_by_agent[request.agent_name]
        return ResolvedSkillSet(
            skill_configs=configs,
            active_skill_names=tuple(name for name, config in configs.items() if config["active"]),
            available_skill_names=tuple(configs),
        )

    monkeypatch.setattr(
        "aworld_cli.executors.local.SkillActivationResolver",
        lambda: SimpleNamespace(resolve=resolve),
    )
    monkeypatch.setattr(
        "aworld_cli.executors.local.PluginManager",
        lambda: SimpleNamespace(
            get_runtime_plugin_roots=lambda: [],
            list_skill_packages=lambda **kwargs: [],
        ),
    )
    monkeypatch.setattr(
        "aworld_cli.core.skill_state_manager.SkillStateManager",
        lambda: SimpleNamespace(
            disabled_skill_names=lambda: (),
            enabled_skill_names=lambda: (),
        ),
    )

    def make_sandbox():
        return Sandbox(
            mcp_servers=["terminal"],
            mcp_config={"mcpServers": {"terminal": {"command": "unused-terminal"}}},
            skill_configs={"previous-task-skill": {"active": True}},
        )

    first_sandbox = make_sandbox()
    second_sandbox = first_sandbox if shared_sandbox else make_sandbox()
    first = Agent(name="filex-owner", conf=AgentConfig(skill_configs={}), sandbox=first_sandbox)
    second = Agent(name="browser-owner", conf=AgentConfig(skill_configs={}), sandbox=second_sandbox)
    refreshes = []
    for sandbox in {id(first_sandbox): first_sandbox, id(second_sandbox): second_sandbox}.values():
        refresh = Mock(wraps=sandbox._reinitialize_mcpservers)
        monkeypatch.setattr(sandbox, "_reinitialize_mcpservers", refresh)
        refreshes.append(refresh)

    executor = LocalAgentExecutor(Swarm(first, second))
    executor._resolve_swarm_skills(SimpleNamespace(task_content="Process input", metadata={}))

    assert first.skill_configs == resolved_by_agent["filex-owner"]
    assert second.skill_configs == resolved_by_agent["browser-owner"]
    assert "browser-use" not in first.skill_configs
    assert "filex" not in second.skill_configs
    assert second.skill_configs["common"]["active"] is False
    for agent in (first, second):
        assert agent.skill_configs is agent.conf.skill_configs
    for refresh in refreshes:
        refresh.assert_called_once_with()

    if shared_sandbox:
        expected = {
            "filex": resolved_by_agent["filex-owner"]["filex"],
            "common": resolved_by_agent["filex-owner"]["common"],
            "browser-use": resolved_by_agent["browser-owner"]["browser-use"],
        }
        assert first_sandbox.skill_configs == expected
        assert first_sandbox.mcpservers.skill_configs == expected
    else:
        for agent in (first, second):
            assert agent.sandbox.skill_configs == agent.skill_configs
            assert agent.sandbox.mcpservers.skill_configs == agent.skill_configs


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
