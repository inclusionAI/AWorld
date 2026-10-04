import asyncio
from types import SimpleNamespace

import pytest

from aworld.agents.llm_agent import (
    Agent,
    LlmOutputParser,
    ToolCallBatchParseError,
    ToolCallParseIssueCode,
)
from aworld.config import AgentConfig, ModelConfig
from aworld.core.context.generation_budget import GenerationBudgetPolicy
from aworld.models.model_response import Function, ModelResponse, ToolCall
from aworld_cli.builtin_agents.smllc.optional_agents import _common
from aworld_cli.builtin_agents.smllc.optional_agents.developer.developer import (
    build_developer_swarm,
)
from aworld_cli.builtin_agents.smllc.optional_agents.evaluator.evaluator import (
    build_evaluator_swarm,
)
from aworld_cli.builtin_agents.smllc.optional_agents.verifier.verifier import (
    build_verifier_swarm,
)


COLLABORATOR_BUILDERS = (
    ("developer", build_developer_swarm),
    ("evaluator", build_evaluator_swarm),
    ("verifier", build_verifier_swarm),
)


def test_verifier_inherits_model_generation_budget_and_shared_sandbox() -> None:
    parent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name="test-model",
            llm_provider="openai",
            llm_api_key="offline",
            llm_base_url="http://localhost/v1",
            llm_temperature=0.7,
            max_model_len=131072,
            params={
                "max_completion_tokens": 32000,
                "chat_template_kwargs": {
                    "thinking": True,
                    "reasoning_effort": "max",
                },
            },
        ),
        use_vision=True,
        skill_configs={"root-only-skill": {"active": True}},
    )
    generation_budget = GenerationBudgetPolicy(
        total_timeout_seconds=600,
        stream_idle_timeout_seconds=180,
        active_tool_free_timeout_seconds=360,
        action_repair_timeout_seconds=90,
    )
    shared_sandbox = SimpleNamespace(
        mcp_servers=["filesystem", "terminal"],
        mcp_config={},
    )

    swarm = build_verifier_swarm(
        sandbox=shared_sandbox,
        agent_config=parent_config,
        generation_budget_policy=generation_budget,
        generation_budget_explicit_fields=("generation_budget_mode",),
        max_loop_steps=27,
        llm_max_attempts=4,
        llm_retry_delay=1.5,
    )
    verifier = next(iter(swarm.agents.values()))

    assert verifier.name() == "verifier"
    assert verifier.sandbox is shared_sandbox
    assert verifier.conf.llm_config.llm_model_name == "test-model"
    assert verifier.conf.llm_config.llm_provider == "openai"
    assert verifier.conf.llm_config.max_model_len == 131072
    assert verifier.conf.llm_config.params == parent_config.llm_config.params
    assert verifier.conf.llm_config.params is not parent_config.llm_config.params
    assert verifier.conf.skill_configs == {}
    assert verifier._explicit_generation_budget_policy is generation_budget
    assert verifier._generation_budget_explicit_fields == {"generation_budget_mode"}
    assert verifier.max_loop_steps == 27
    assert verifier.llm_max_attempts == 4
    assert verifier.llm_retry_delay == 1.5
    assert verifier.enable_subagent is False
    assert verifier.subagent_context_mode == "fresh"
    assert verifier.subagent_merge_mode == "answer_only"
    assert verifier.mcp_servers == ["filesystem"]
    assert "hidden grader" in verifier.system_prompt
    assert "Decision: `ready`, `repair`, or `uncertain`" in verifier.system_prompt
    assert not any("CAST" in name for name in verifier.tool_names)


def test_default_collaborators_inherit_root_execution_profile() -> None:
    parent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name="dsv4-test-route",
            llm_provider="openai",
            llm_api_key="offline",
            llm_base_url="http://localhost/v1",
            llm_temperature=0.25,
            max_model_len=131072,
            params={
                "max_completion_tokens": 48000,
                "chat_template_kwargs": {
                    "thinking": True,
                    "reasoning_effort": "max",
                },
            },
        ),
        use_vision=True,
        skill_configs={"root-only-skill": {"active": True}},
    )
    generation_budget = GenerationBudgetPolicy(
        total_timeout_seconds=900,
        stream_idle_timeout_seconds=180,
        active_tool_free_timeout_seconds=None,
        action_repair_timeout_seconds=None,
    )
    shared_sandbox = SimpleNamespace(
        mcp_servers=["filesystem", "terminal"],
        mcp_config={},
    )

    for expected_name, builder in COLLABORATOR_BUILDERS:
        swarm = builder(
            sandbox=shared_sandbox,
            agent_config=parent_config,
            generation_budget_policy=generation_budget,
            generation_budget_explicit_fields=("generation_budget_mode",),
            max_loop_steps=41,
            llm_max_attempts=5,
            llm_retry_delay=1.25,
        )
        collaborator = next(iter(swarm.agents.values()))

        assert collaborator.name() == expected_name
        assert collaborator.sandbox is shared_sandbox
        assert collaborator.conf.llm_config.llm_model_name == "dsv4-test-route"
        assert collaborator.conf.llm_config.llm_provider == "openai"
        assert collaborator.conf.llm_config.llm_base_url == "http://localhost/v1"
        assert collaborator.conf.llm_config.llm_temperature == 0.25
        assert collaborator.conf.llm_config.max_model_len == 131072
        assert collaborator.conf.llm_config.params == parent_config.llm_config.params
        assert (
            collaborator.conf.llm_config.params is not parent_config.llm_config.params
        )
        assert collaborator.conf.skill_configs == {}
        assert collaborator._explicit_generation_budget_policy is generation_budget
        assert collaborator._generation_budget_explicit_fields == {
            "generation_budget_mode"
        }
        assert collaborator.max_loop_steps == 41
        assert collaborator.llm_max_attempts == 5
        assert collaborator.llm_retry_delay == 1.25
        assert collaborator.enable_subagent is False
        assert collaborator.subagent_context_mode == "fresh"
        assert collaborator.subagent_merge_mode == "answer_only"
        assert collaborator.mcp_servers
        assert "public task" in collaborator.system_prompt.lower()
        assert "hidden reward" in collaborator.system_prompt.lower()

        if expected_name == "developer":
            assert "cast tools may help" in collaborator.system_prompt.lower()
            assert "never required" in collaborator.system_prompt.lower()
        if expected_name == "evaluator":
            assert "no special wording" in collaborator.system_prompt.lower()
            assert "CAST_ANALYSIS" not in collaborator.tool_names
            assert "CAST_SEARCH" not in collaborator.tool_names


def test_developer_and_evaluator_remain_available_without_cast(monkeypatch) -> None:
    monkeypatch.setattr(_common, "is_tool_by_name", lambda _name: False)
    parent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name="caller-model",
            params={"chat_template_kwargs": {"reasoning_effort": "max"}},
        ),
        skill_configs={},
    )
    shared_sandbox = SimpleNamespace(
        mcp_servers=["filesystem", "terminal"],
        mcp_config={},
    )

    for expected_name, builder in COLLABORATOR_BUILDERS[:2]:
        collaborator = next(
            iter(
                builder(
                    sandbox=shared_sandbox,
                    agent_config=parent_config,
                ).agents.values()
            )
        )

        assert collaborator.name() == expected_name
        assert collaborator.tool_names == []
        if expected_name == "developer":
            assert collaborator.mcp_servers == ["filesystem", "terminal"]
        else:
            assert collaborator.mcp_servers == ["filesystem"]


def _tool_schema(name: str) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_reviewer_schema_uses_fail_closed_filesystem_action_allowlist(
    monkeypatch,
) -> None:
    filesystem_actions = (
        "read_file",
        "read_media_file",
        "list_directory",
        "list_allowed_directories",
        "search_content",
        "search_files",
        "parse_file",
        "write_file",
        "future_workspace_mutation",
    )

    async def seed_mcp_schemas(agent, _context) -> None:
        agent.tools = [_tool_schema(name) for name in filesystem_actions]
        agent.tool_mapping = {
            name: f"filesystem__{name}" for name in filesystem_actions
        }

    monkeypatch.setattr(Agent, "async_desc_transform", seed_mcp_schemas)
    config = AgentConfig(
        llm_config=ModelConfig(llm_model_name="schema-test"),
        skill_configs={},
    )
    shared_sandbox = SimpleNamespace(
        mcp_servers=["filesystem", "terminal"],
        mcp_config={},
    )

    for builder in (build_evaluator_swarm, build_verifier_swarm):
        reviewer = next(
            iter(
                builder(
                    sandbox=shared_sandbox,
                    agent_config=config,
                ).agents.values()
            )
        )
        asyncio.run(reviewer.async_desc_transform(None))
        schema_names = {schema["function"]["name"] for schema in reviewer.tools}

        assert schema_names == {
            "read_file",
            "read_media_file",
            "list_directory",
            "list_allowed_directories",
            "search_content",
            "search_files",
        }
        assert "parse_file" not in reviewer.tool_mapping
        assert "future_workspace_mutation" not in reviewer.tool_mapping

    developer = next(
        iter(
            build_developer_swarm(
                sandbox=shared_sandbox,
                agent_config=config,
            ).agents.values()
        )
    )
    asyncio.run(developer.async_desc_transform(None))
    developer_schema_names = {schema["function"]["name"] for schema in developer.tools}
    assert developer_schema_names == set(filesystem_actions)


@pytest.mark.asyncio
@pytest.mark.parametrize("builder", [build_evaluator_swarm, build_verifier_swarm])
async def test_reviewer_rejects_explicit_mcp_calls_outside_live_surface(
    monkeypatch, builder
) -> None:
    filesystem_actions = (
        "read_file",
        "write_file",
        "parse_file",
    )

    async def seed_mcp_schemas(agent, _context) -> None:
        agent.tools = [_tool_schema(name) for name in filesystem_actions]
        agent.tool_mapping = {
            name: f"filesystem__{name}" for name in filesystem_actions
        }

    monkeypatch.setattr(Agent, "async_desc_transform", seed_mcp_schemas)
    reviewer = next(
        iter(
            builder(
                sandbox=SimpleNamespace(
                    mcp_servers=["filesystem", "terminal"],
                    mcp_config={},
                ),
                agent_config=AgentConfig(
                    llm_config=ModelConfig(llm_model_name="schema-test"),
                    skill_configs={},
                ),
            ).agents.values()
        )
    )
    await reviewer.async_desc_transform(None)

    for function_name in (
        "mcp__filesystem__write_file",
        "mcp__filesystem__parse_file",
        "mcp__terminal__execute_command",
    ):
        response = ModelResponse(
            id=f"response-{function_name}",
            model="schema-test",
            tool_calls=[
                ToolCall(
                    id=f"call-{function_name}",
                    function=Function(name=function_name, arguments="{}"),
                )
            ],
        )
        with pytest.raises(ToolCallBatchParseError) as exc_info:
            await LlmOutputParser().parse(
                response,
                agent_id=reviewer.id(),
                agent=reviewer,
            )
        assert exc_info.value.issues[0].code is (
            ToolCallParseIssueCode.TOOL_NOT_IN_LIVE_SURFACE
        )

    allowed = await LlmOutputParser().parse(
        ModelResponse(
            id="response-read",
            model="schema-test",
            tool_calls=[
                ToolCall(
                    id="call-read",
                    function=Function(
                        name="mcp__filesystem__read_file",
                        arguments='{"path": "README.md"}',
                    ),
                )
            ],
        ),
        agent_id=reviewer.id(),
        agent=reviewer,
    )
    assert allowed.actions[0].tool_name == "mcp"
    assert allowed.actions[0].action_name == "filesystem__read_file"
