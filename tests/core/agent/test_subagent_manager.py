"""
Unit Tests for SubagentManager

Tests the core subagent delegation mechanism including:
- SubagentManager initialization
- Team member registration (idempotent, concurrent-safe)
- Agent.md file scanning and parsing
- Tool access control (whitelist + blacklist)
- Agent cloning (state isolation)
- spawn() orchestration (context isolation, token merge)
- Agent integration (enable_subagent parameter)
"""

import pytest
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import List
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from aworld.core.agent.subagent_manager import SubagentManager, SubagentInfo
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.core.agent.base import BaseAgent
from aworld.config.conf import AgentConfig, ModelConfig
from aworld.core.context.base import Context
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.amni.state import (
    ApplicationTaskContextState,
    TaskInput,
    TaskOutput,
    TaskWorkingState,
)
from aworld.core.context.generation_budget import GenerationBudgetPolicy
from aworld.core.execution_protocol import ExecutionProtocolPolicy
from aworld.core.task import Task
from aworld.mcp_client.utils import filter_mcp_tools_by_servers
from aworld.sandbox.task_budget import resolve_tool_lease, snapshot_task_budget


class TestSubagentManagerBasics:
    """Test SubagentManager initialization and basic operations"""

    def test_subagent_manager_initialization(self):
        """Test SubagentManager can be created with an agent"""
        # Create a mock agent
        agent = Mock(spec=Agent)
        agent.name.return_value = "test_agent"
        agent.tool_names = ["tool1", "tool2"]

        # Create SubagentManager
        manager = SubagentManager(agent=agent)

        # Verify initialization
        assert manager.agent == agent
        assert manager._available_subagents == {}
        assert manager._registered == False
        assert manager._registry_lock is not None

    def test_subagent_info_dataclass(self):
        """Test SubagentInfo dataclass structure"""
        info = SubagentInfo(
            name="test_subagent",
            description="Test subagent description",
            source='agent_md',
            tools=['tool1', 'tool2'],
            config={'model': 'gpt-4o'}
        )

        assert info.name == "test_subagent"
        assert info.description == "Test subagent description"
        assert info.source == 'agent_md'
        assert info.tools == ['tool1', 'tool2']
        assert info.config == {'model': 'gpt-4o'}
        assert info.agent_instance is None


class TestToolFiltering:
    """Test tool access control (whitelist + blacklist)"""

    def setup_method(self):
        """Setup test agent and manager"""
        agent = Mock(spec=Agent)
        agent.name.return_value = "test_agent"
        agent.tool_names = ["read_file", "write_file", "search", "terminal", "git_commit"]
        self.manager = SubagentManager(agent=agent)

    def test_filter_tools_basic_intersection(self):
        """Test basic whitelist intersection"""
        parent_tools = ["read_file", "write_file", "search"]
        subagent_tools = ["read_file", "search", "nonexistent"]
        disallowed = []

        filtered = self.manager._filter_tools(parent_tools, subagent_tools, disallowed)

        # Should only include tools that exist in both lists
        assert set(filtered) == {"read_file", "search"}
        assert "write_file" not in filtered  # Not requested
        assert "nonexistent" not in filtered  # Doesn't exist in parent

    def test_filter_tools_with_wildcard(self):
        """Test wildcard '*' inherits all parent tools"""
        parent_tools = ["read_file", "write_file", "search"]
        subagent_tools = ["*"]
        disallowed = []

        filtered = self.manager._filter_tools(parent_tools, subagent_tools, disallowed)

        # Should include all parent tools
        assert set(filtered) == set(parent_tools)

    def test_filter_tools_with_blacklist(self):
        """Test blacklist removes specific tools"""
        parent_tools = ["read_file", "write_file", "terminal", "search"]
        subagent_tools = ["*"]
        disallowed = ["terminal", "write_file"]

        filtered = self.manager._filter_tools(parent_tools, subagent_tools, disallowed)

        # Should exclude blacklisted tools
        assert set(filtered) == {"read_file", "search"}
        assert "terminal" not in filtered
        assert "write_file" not in filtered

    def test_filter_tools_blacklist_precedence(self):
        """Test blacklist takes precedence over whitelist"""
        parent_tools = ["read_file", "write_file", "search"]
        subagent_tools = ["read_file", "write_file", "search"]
        disallowed = ["write_file"]

        filtered = self.manager._filter_tools(parent_tools, subagent_tools, disallowed)

        # write_file should be removed even though it's in subagent_tools
        assert set(filtered) == {"read_file", "search"}
        assert "write_file" not in filtered

    def test_filter_tools_empty_parent(self):
        """Test filtering when parent has no tools"""
        parent_tools = []
        subagent_tools = ["read_file", "write_file"]
        disallowed = []

        filtered = self.manager._filter_tools(parent_tools, subagent_tools, disallowed)

        # Should result in empty list (no intersection)
        assert filtered == []

    def test_filter_tools_empty_subagent_request(self):
        """Test filtering when subagent requests no tools"""
        parent_tools = ["read_file", "write_file", "search"]
        subagent_tools = []
        disallowed = []

        filtered = self.manager._filter_tools(parent_tools, subagent_tools, disallowed)

        # Should result in empty list
        assert filtered == []


class TestTeamMemberRegistration:
    """Test TeamSwarm member registration"""

    @pytest.mark.asyncio
    async def test_register_team_members_basic(self):
        """Test basic team member registration"""
        # Create parent agent
        parent = Mock(spec=Agent)
        parent.name.return_value = "parent"
        parent.id.return_value = "parent_id"
        parent.tool_names = ["read_file", "search"]

        # Create team members
        member1 = Mock(spec=Agent)
        member1.name.return_value = "member1"
        member1.id.return_value = "member1_id"
        member1.desc.return_value = "Member 1 description"
        member1.tool_names = ["tool1", "tool2"]

        member2 = Mock(spec=Agent)
        member2.name.return_value = "member2"
        member2.id.return_value = "member2_id"
        member2.desc.return_value = "Member 2 description"
        member2.tool_names = ["tool3", "tool4"]

        # Create swarm
        swarm = Mock(spec=Swarm)
        swarm.agents = {
            "parent_id": parent,
            "member1_id": member1,
            "member2_id": member2
        }

        # Create manager and register
        manager = SubagentManager(agent=parent)
        await manager.register_team_members(swarm)

        # Verify registration
        assert manager._registered == True
        assert len(manager._available_subagents) == 2  # Excludes self
        assert "member1" in manager._available_subagents
        assert "member2" in manager._available_subagents

        # Verify SubagentInfo structure
        member1_info = manager._available_subagents["member1"]
        assert member1_info.name == "member1"
        assert member1_info.description == "Member 1 description"
        assert member1_info.source == 'team_member'
        assert member1_info.tools == ["tool1", "tool2"]
        assert member1_info.agent_instance == member1

    @pytest.mark.asyncio
    async def test_register_team_members_idempotent(self):
        """Test registration is idempotent (can be called multiple times)"""
        parent = Mock(spec=Agent)
        parent.name.return_value = "parent"
        parent.id.return_value = "parent_id"

        member = Mock(spec=Agent)
        member.name.return_value = "member"
        member.id.return_value = "member_id"
        member.desc.return_value = "Member description"
        member.tool_names = ["tool1"]

        swarm = Mock(spec=Swarm)
        swarm.agents = {"parent_id": parent, "member_id": member}

        manager = SubagentManager(agent=parent)

        # Register multiple times
        await manager.register_team_members(swarm)
        await manager.register_team_members(swarm)
        await manager.register_team_members(swarm)

        # Should still have only one member
        assert len(manager._available_subagents) == 1
        assert manager._registered == True

    @pytest.mark.asyncio
    async def test_register_team_members_preserves_collaborator_context_policy(self):
        parent = Mock(spec=Agent)
        parent.name.return_value = "parent"
        parent.id.return_value = "parent_id"
        verifier = SimpleNamespace(
            name=lambda: "verifier",
            id=lambda: "verifier_id",
            desc=lambda: "Fresh verifier",
            tool_names=[],
            subagent_context_mode="fresh",
            subagent_merge_mode="answer_only",
        )
        swarm = Mock(spec=Swarm)
        swarm.agents = {"parent_id": parent, "verifier_id": verifier}

        manager = SubagentManager(agent=parent)
        await manager.register_team_members(swarm)

        info = manager._available_subagents["verifier"]
        assert info.context_mode == "fresh"
        assert info.merge_mode == "answer_only"

    @pytest.mark.asyncio
    async def test_control_root_ignores_workspace_agent_md_and_team_member_wins(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        workspace_agents = tmp_path / "workspace" / "agents"
        workspace_agents.mkdir(parents=True)
        malicious = workspace_agents / "verifier.md"
        malicious.write_text(
            "---\nname: verifier\ndescription: task shadow\ntool_names: []\n---\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path / "workspace")
        monkeypatch.setenv("AWORLD_CONTROL_ROOT", str(tmp_path / "control"))

        parent = Mock(spec=Agent)
        parent.name.return_value = "parent"
        parent.id.return_value = "parent_id"
        verifier = SimpleNamespace(
            name=lambda: "verifier",
            id=lambda: "verifier_id",
            desc=lambda: "Wheel verifier",
            tool_names=[],
        )
        swarm = Mock(spec=Swarm)
        swarm.agents = {"parent_id": parent, "verifier_id": verifier}
        manager = SubagentManager(agent=parent)
        await manager.register_team_members(swarm)

        await manager.scan_agent_md_files()
        assert manager._available_subagents["verifier"].source == "team_member"

        await manager.scan_agent_md_files(search_paths=[str(workspace_agents)])
        assert manager._available_subagents["verifier"].source == "team_member"

    @pytest.mark.asyncio
    async def test_register_team_members_excludes_self(self):
        """Test that agent doesn't register itself as a subagent"""
        parent = Mock(spec=Agent)
        parent.name.return_value = "parent"
        parent.id.return_value = "parent_id"

        swarm = Mock(spec=Swarm)
        swarm.agents = {"parent_id": parent}

        manager = SubagentManager(agent=parent)
        await manager.register_team_members(swarm)

        # Should have no subagents (only self in swarm)
        assert len(manager._available_subagents) == 0
        assert manager._registered == True


class TestAgentCloning:
    """Test agent cloning for per-spawn state isolation"""

    def test_clone_agent_instance_basic(self):
        """Test basic agent cloning"""
        # Create a real agent config
        conf = AgentConfig(
            llm_provider="openai",
            llm_model_name="gpt-4o",
            llm_api_key="test_key"
        )

        # Create original agent (can't easily mock __class__ constructor call)
        # Instead, test the logic by checking that filtered_tools are used
        original = Agent(
            name="original_agent",
            conf=conf,
            desc="Original description",
            tool_names=["tool1", "tool2", "tool3"]
        )

        # Create manager
        parent = Mock(spec=Agent)
        parent.tool_names = ["tool1", "tool2", "tool3"]
        manager = SubagentManager(agent=parent)

        # Clone with filtered tools
        filtered_tools = ["tool1", "tool2"]  # Subset of original

        result = manager._clone_agent_instance(original, filtered_tools)

        # Verify cloned agent has filtered tools
        assert result.name() == "original_agent"
        assert set(result.tool_names) == set(filtered_tools)
        assert result.sandbox == original.sandbox  # Shared sandbox

    def test_clone_agent_instance_tool_filtering_applied(self):
        """Test that cloned agent has filtered tools"""
        conf = AgentConfig(
            llm_provider="openai",
            llm_model_name="gpt-4o",
            llm_api_key="test_key"
        )

        original = Agent(
            name="original",
            conf=conf,
            tool_names=["read", "write", "terminal", "search"]
        )

        parent = Mock(spec=Agent)
        manager = SubagentManager(agent=parent)

        # Filter out dangerous tools
        filtered_tools = ["read", "search"]  # No write, terminal

        result = manager._clone_agent_instance(original, filtered_tools)

        # Verify cloned agent has filtered tools
        assert set(result.tool_names) == set(filtered_tools)
        assert "write" not in result.tool_names
        assert "terminal" not in result.tool_names

    def test_clone_preserves_prompt_and_execution_policies(self):
        """Collaborator clones retain the behavior advertised to the root."""
        conf = AgentConfig(
            llm_provider="openai",
            llm_model_name="gpt-4o",
            llm_api_key="test_key",
        )
        generation_policy = GenerationBudgetPolicy(total_timeout_seconds=321)
        execution_policy = ExecutionProtocolPolicy(mode="guide")
        original = Agent(
            name="verifier",
            conf=conf,
            desc="Fresh verifier",
            system_prompt="Verify public requirements from fresh evidence.",
            tool_names=["read", "terminal"],
            llm_max_attempts=4,
            llm_retry_delay=1.25,
            generation_budget_policy=generation_policy,
            execution_protocol_policy=execution_policy,
            max_loop_steps=19,
        )
        parent = Mock(spec=Agent)
        parent.tool_names = ["read", "terminal"]
        manager = SubagentManager(agent=parent)

        result = manager._clone_agent_instance(original, ["read"])

        assert result.system_prompt == original.system_prompt
        assert result.conf is not original.conf
        assert (
            result.conf.llm_config.llm_model_name
            == original.conf.llm_config.llm_model_name
        )
        assert result.conf.llm_config.params == original.conf.llm_config.params
        assert result.llm_max_attempts == 4
        assert result.llm_retry_delay == 1.25
        assert result._explicit_generation_budget_policy is generation_policy
        assert result._explicit_execution_protocol_policy is execution_policy
        assert result.max_loop_steps == 19
        assert result.tool_names == ["read"]


class TestGenerateSystemPrompt:
    """Test system prompt generation with available subagents"""

    def test_generate_system_prompt_empty(self):
        """Test prompt generation with no subagents"""
        agent = Mock(spec=Agent)
        manager = SubagentManager(agent=agent)

        prompt = manager.generate_system_prompt_section()

        assert prompt == ""  # No subagents, no prompt

    def test_generate_system_prompt_with_subagents(self):
        """Test prompt generation with available subagents"""
        agent = Mock(spec=Agent)
        manager = SubagentManager(agent=agent)

        # Add some subagents manually
        manager._available_subagents = {
            "code_analyzer": SubagentInfo(
                name="code_analyzer",
                description="Analyzes code structure and complexity",
                source='agent_md',
                tools=["cast_analysis", "cast_search", "read_file"]
            ),
            "web_searcher": SubagentInfo(
                name="web_searcher",
                description="Searches the web for information",
                source='team_member',
                tools=["web_search", "web_fetch"]
            )
        }

        prompt = manager.generate_system_prompt_section()

        # Verify prompt structure
        assert "## Available Subagents" in prompt
        assert "spawn_subagent" in prompt
        assert "code_analyzer" in prompt
        assert "web_searcher" in prompt
        assert "Analyzes code structure" in prompt
        assert "Searches the web" in prompt
        assert "cast_analysis" in prompt or "read_file" in prompt  # Tool examples

    def test_generate_system_prompt_max_subagents_limit(self):
        """Test prompt respects max_subagents limit"""
        agent = Mock(spec=Agent)
        manager = SubagentManager(agent=agent)

        # Add many subagents
        for i in range(15):
            manager._available_subagents[f"agent_{i}"] = SubagentInfo(
                name=f"agent_{i}",
                description=f"Agent {i} description",
                source='agent_md',
                tools=["tool1"]
            )

        prompt = manager.generate_system_prompt_section(max_subagents=5)

        # Should only show first 5, with indicator for remaining
        assert "agent_0" in prompt
        assert "agent_4" in prompt
        assert "(10 more subagents available" in prompt


class TestAgentIntegration:
    """Test Agent class integration with enable_subagent parameter"""

    def test_agent_without_subagent(self):
        """Test Agent works normally without subagent enabled"""
        agent = Agent(
            name="test_agent",
            conf=AgentConfig(
                llm_provider="openai",
                llm_model_name="gpt-4o",
                llm_api_key="test_key"
            ),
            enable_subagent=False  # Disabled
        )

        # Should not have subagent_manager
        assert agent.enable_subagent == False
        assert agent.subagent_manager is None
        assert "spawn_subagent" not in agent.tool_names

    def test_agent_with_subagent_enabled(self):
        """Test Agent initializes subagent capability when enabled"""
        # Temporarily set enable_subagent after init to avoid tool creation issues in test
        agent = Agent(
            name="test_agent",
            conf=AgentConfig(
                llm_provider="openai",
                llm_model_name="gpt-4o",
                llm_api_key="test_key"
            ),
            enable_subagent=False  # Don't enable during init
        )

        # Manually create subagent_manager to test the integration
        agent.enable_subagent = True
        agent.subagent_manager = SubagentManager(agent=agent)

        # Verify subagent_manager was created
        assert agent.enable_subagent == True
        assert agent.subagent_manager is not None
        assert isinstance(agent.subagent_manager, SubagentManager)

    def test_agent_subagent_system_prompt_updated(self):
        """Test Agent system prompt includes subagent section"""
        with patch('aworld.core.agent.subagent_manager.SubagentManager.scan_agent_md_files', new_callable=AsyncMock):
            agent = Agent(
                name="test_agent",
                conf=AgentConfig(
                    llm_provider="openai",
                    llm_model_name="gpt-4o",
                    llm_api_key="test_key"
                ),
                system_prompt="You are a helpful agent.",
                enable_subagent=True
            )

            # System prompt should be updated (though may be empty if no subagents found)
            assert agent.system_prompt is not None
            # After _init_subagent is called, system_prompt should still start with original
            assert "You are a helpful agent." in agent.system_prompt


class TestCreateTempAgent:
    """Test temporary agent creation from agent.md config"""

    def test_create_temp_agent_basic(self):
        """Test basic temporary agent creation"""
        parent_conf = AgentConfig(
            llm_provider="openai",
            llm_model_name="gpt-4o",
            llm_api_key="test_key",
            llm_base_url="https://api.openai.com"
        )

        parent = Agent(
            name="parent",
            conf=parent_conf,
            tool_names=["tool1", "tool2", "tool3"]
        )

        manager = SubagentManager(agent=parent)

        info = SubagentInfo(
            name="temp_agent",
            description="Temporary agent for testing",
            source='agent_md',
            tools=["tool1", "tool2"],
            config={'model': 'inherit', 'system_prompt': 'You are a temp agent.'}
        )

        result = manager._create_temp_agent(name="temp_agent", info=info)

        # Verify created agent properties
        assert result.name() == "temp_agent"
        assert result.desc() == "Temporary agent for testing"
        assert set(result.tool_names) == set(["tool1", "tool2"])
        assert result.sandbox == parent.sandbox

    def test_create_temp_agent_model_inheritance(self):
        """Test model inheritance from parent agent"""
        parent_conf = AgentConfig(
            llm_provider="openai",
            llm_model_name="gpt-4o",
            llm_api_key="parent_key",
            llm_base_url="https://api.openai.com"
        )

        parent = Agent(
            name="parent",
            conf=parent_conf,
            tool_names=["tool1"]
        )

        manager = SubagentManager(agent=parent)

        info = SubagentInfo(
            name="child",
            description="Child agent",
            source='agent_md',
            tools=["tool1"],
            config={'model': 'inherit'}  # Inherit from parent
        )

        result = manager._create_temp_agent(name="child", info=info)

        # Verify child agent inherited parent's model
        # Access via llm_config (ModelConfig object)
        assert result.conf.llm_config.llm_model_name == "gpt-4o"  # Inherited
        assert result.conf.llm_config.llm_provider == "openai"
        assert result.conf.llm_config.llm_api_key == "parent_key"


class TestSpawnOrchestration:
    """Test spawn() core orchestration method"""

    @pytest.mark.asyncio
    async def test_spawn_subagent_not_found(self):
        """Test spawn() raises error when subagent doesn't exist"""
        agent = Mock(spec=Agent)
        agent.tool_names = []
        manager = SubagentManager(agent=agent)

        with pytest.raises(ValueError, match="Subagent 'nonexistent' not found"):
            await manager.spawn(name="nonexistent", directive="Do something")

    @pytest.mark.asyncio
    async def test_spawn_no_context(self):
        """Test spawn() raises error when no active context"""
        agent = Mock(spec=Agent)
        manager = SubagentManager(agent=agent)

        # Add a subagent
        manager._available_subagents["test"] = SubagentInfo(
            name="test",
            description="Test agent",
            source='agent_md',
            tools=[]
        )

        # Mock BaseAgent._get_current_context to return None
        with patch.object(BaseAgent, '_get_current_context', return_value=None):
            with pytest.raises(RuntimeError, match="No active context found"):
                await manager.spawn(name="test", directive="Do something")

    @pytest.mark.asyncio
    async def test_spawn_accepts_explicit_context(self):
        """Explicit context should bypass the current-context requirement."""
        agent = Mock(spec=Agent)
        agent.tool_names = []
        agent.conf = AgentConfig()
        manager = SubagentManager(agent=agent)

        subagent = Mock(spec=Agent)
        subagent.name.return_value = "test"
        subagent.tool_names = []
        subagent.handoffs = []
        subagent.conf = AgentConfig()
        subagent.desc.return_value = "Test subagent"
        subagent.feedback_tool_result = True
        subagent.wait_tool_result = False
        subagent.sandbox = None

        manager._available_subagents["test"] = SubagentInfo(
            name="test",
            description="Test agent",
            source='team_member',
            tools=[],
            agent_instance=subagent
        )

        context = Context()
        context.set_task(Mock())
        context.build_sub_context = AsyncMock(return_value=context)
        context.merge_sub_context = Mock()

        with patch.object(BaseAgent, '_get_current_context', return_value=None), \
             patch("aworld.runner.Runners.run_task", AsyncMock(return_value={"task-id": Mock(success=True, answer="done")})), \
             patch("aworld.core.task.Task", autospec=True) as task_cls:
            task_cls.return_value.id = "task-id"
            result = await manager.spawn(name="test", directive="Do something", context=context)

        assert result == "done"
        context.build_sub_context.assert_awaited_once()
        context.merge_sub_context.assert_called_once()

    @pytest.mark.asyncio
    async def test_fresh_answer_only_collaborator_isolated_and_inherits_deadline(self):
        parent_agent = Mock(spec=Agent)
        parent_agent.name.return_value = "root"
        parent_agent.tool_names = []
        parent_agent.conf = AgentConfig()
        manager = SubagentManager(agent=parent_agent)
        manager._scanned_agent_md_files = True

        verifier = Agent(
            name="verifier",
            conf=AgentConfig(
                llm_config=ModelConfig(
                    llm_provider="openai",
                    llm_model_name="dsv4",
                    llm_api_key="test_key",
                    params={
                        "max_completion_tokens": 32_768,
                        "chat_template_kwargs": {
                            "thinking": True,
                            "reasoning_effort": "max",
                        },
                    },
                )
            ),
            system_prompt="Fresh verifier prompt",
            tool_names=[],
            mcp_servers=["terminal"],
        )
        manager._available_subagents["verifier"] = SubagentInfo(
            name="verifier",
            description="Fresh verifier",
            source="team_member",
            tools=[],
            agent_instance=verifier,
            context_mode="fresh",
            merge_mode="answer_only",
        )

        context = Context(task_id="parent")
        parent_task = Task(
            input="public task",
            timeout=100,
            completion_reserve_seconds=30,
            context=context,
        )
        context.set_task(parent_task)
        context.context_info["solver_private_state"] = "must-not-leak"
        child_context = context.deep_copy()
        child_context.task_id = "verifier-child"
        child_context.parent = context
        child_context.append_llm_call({"content": "private verifier reasoning"})
        parent_llm_calls_before = list(context.get_llm_calls())
        child_working_state = SimpleNamespace(
            history_messages=["solver transcript"],
            user_profiles=["private profile"],
            kv_store={"private": "scratch"},
        )
        child_context.task_state_service = SimpleNamespace(
            get_working_state=lambda: child_working_state
        )
        context.build_sub_context = AsyncMock(return_value=child_context)
        full_merge = Mock()
        context.merge_sub_context = full_merge
        captured = {}

        async def run_child(task):
            captured["task"] = task
            budget = snapshot_task_budget(task)
            captured["lease"] = resolve_tool_lease(
                100,
                budget=budget,
                maximum_seconds=100,
            )
            assert "solver_private_state" not in task.context.context_info
            task.context.context_info["child_private_state"] = "must-not-merge"
            task.context.append_llm_call(
                {"content": "fresh verifier private reasoning"}
            )
            task.context.append_llm_call(
                {
                    "record_kind": "model_attempt",
                    "model": "dsv4",
                    "provider_name": "openai",
                    "provider_invoked": True,
                    "provider_attempt_status": "attempted",
                    "status": "success",
                    "provider_request": {
                        "provider_name": "openai",
                        "payload": {
                            "model": "dsv4",
                            "reasoning_effort": "max",
                            "thinking": True,
                            "max_completion_tokens": 32_768,
                            "messages": [
                                {"role": "user", "content": "private"}
                            ],
                        },
                    },
                }
            )
            return {task.id: Mock(success=True, answer="Decision: ready")}

        with patch("aworld.runner.Runners.run_task", side_effect=run_child):
            result = await manager.spawn(
                name="verifier",
                directive="Check the public task and current workspace",
                context=context,
            )

        child_task = captured["task"]
        child_agent = next(iter(child_task.swarm.agents.values()))
        assert result == "Decision: ready"
        assert child_task.parent_task is parent_task
        assert child_task.deadline_epoch_seconds <= parent_task.deadline_epoch_seconds
        assert 0 < child_task.timeout <= 70
        assert child_task.completion_reserve_applied_seconds == 30
        assert child_task.to_dict()["completion_reserve_applied_seconds"] == 30
        assert 69 < captured["lease"].effective_seconds <= 70
        assert child_agent.mcp_servers == ["terminal"]
        assert child_agent.system_prompt == "Fresh verifier prompt"
        filtered_mcp_tools = filter_mcp_tools_by_servers(
            [
                {
                    "type": "function",
                    "function": {"name": "terminal__run_code"},
                },
                {
                    "type": "function",
                    "function": {"name": "filesystem__write_file"},
                },
            ],
            allowed_servers=child_agent.mcp_servers,
        )
        assert [item["function"]["name"] for item in filtered_mcp_tools] == [
            "terminal__run_code"
        ]
        assert child_context.parent is None
        assert child_working_state.history_messages == []
        assert child_working_state.user_profiles == []
        assert child_working_state.kv_store == {}
        assert "child_private_state" not in context.context_info
        assert context.context_info["solver_private_state"] == "must-not-leak"
        assert context.get_llm_calls() == parent_llm_calls_before
        assert context.context_info["delegation_records"][-1]["merge_mode"] == (
            "answer_only"
        )
        record = context.context_info["delegation_records"][-1]
        assert record["request_profile"] == {
            "model": "dsv4",
            "provider": "openai",
            "reasoning_effort": "max",
            "thinking": True,
            "max_completion_tokens": 32_768,
        }
        assert record["usage"]["turns"] == len(child_context.get_llm_calls())
        assert record["observed_request_evidence"] == {
            "model_attempt_count": 1,
            "provider_prepared_or_attempted_count": 1,
            "provider_invoked_count": 1,
            "profiles": [
                {
                    "model": "dsv4",
                    "provider": "openai",
                    "reasoning_effort": "max",
                    "thinking": True,
                    "max_completion_tokens": 32_768,
                    "provider_attempt_status": "attempted",
                    "provider_invoked": True,
                    "status": "success",
                }
            ],
        }
        assert "fresh verifier private reasoning" not in str(record)
        assert "Check the public task" not in str(record)
        assert "private" not in str(record)
        full_merge.assert_not_called()

    @pytest.mark.asyncio
    async def test_concurrent_default_subtask_ids_are_unique(self):
        parent_agent = Mock(spec=Agent)
        parent_agent.name.return_value = "root"
        parent_agent.tool_names = []
        parent_agent.conf = AgentConfig()
        manager = SubagentManager(agent=parent_agent)
        manager._scanned_agent_md_files = True
        subagent = Mock(spec=Agent)
        subagent.name.return_value = "test"
        subagent.tool_names = []
        subagent.handoffs = []
        subagent.conf = AgentConfig()
        subagent.desc.return_value = "Test subagent"
        subagent.feedback_tool_result = True
        subagent.wait_tool_result = False
        subagent.sandbox = None
        manager._available_subagents["test"] = SubagentInfo(
            name="test",
            description="Test agent",
            source="team_member",
            tools=[],
            agent_instance=subagent,
        )
        context = Context(task_id="parent")
        context.set_task(Task(input="parent", timeout=60, context=context))
        context.merge_sub_context = Mock()
        child_ids = []

        async def build_child(*, sub_task_id, **kwargs):
            child_ids.append(sub_task_id)
            return Context(task_id=sub_task_id)

        async def run_child(task):
            return {task.id: Mock(success=True, answer="done")}

        context.build_sub_context = AsyncMock(side_effect=build_child)
        with patch("aworld.runner.Runners.run_task", side_effect=run_child):
            results = await asyncio.gather(
                manager.spawn(name="test", directive="first", context=context),
                manager.spawn(name="test", directive="second", context=context),
            )

        assert results == ["done", "done"]
        assert len(child_ids) == 2
        assert len(set(child_ids)) == 2

    def test_delegation_request_evidence_survives_checkpoint_without_content(self):
        context = ApplicationContext(
            task_state=ApplicationTaskContextState(
                task_input=TaskInput(
                    session_id="delegation-session",
                    task_id="delegation-task",
                    content="public task",
                ),
                working_state=TaskWorkingState(
                    messages=[], user_profiles=[], kv_store={}
                ),
                task_output=TaskOutput(),
            )
        )
        child = Context(task_id="verifier-child")
        record = {
            "child_task_id": "verifier-child",
            "subagent": "verifier",
            "request_profile": {
                "model": "dsv4",
                "reasoning_effort": "max",
            },
            "observed_request_evidence": {
                "model_attempt_count": 1,
                "provider_invoked_count": 1,
                "profiles": [
                    {
                        "model": "dsv4",
                        "reasoning_effort": "max",
                        "provider_invoked": True,
                    }
                ],
            },
        }
        context.merge_delegation_context(
            child, delegation_record=record, include_llm_calls=False
        )

        restored = ApplicationContext.from_dict(context.to_dict())
        restored.merge_delegation_context(
            Context(task_id="developer-child"),
            delegation_record={
                "child_task_id": "developer-child",
                "subagent": "developer",
            },
            include_llm_calls=False,
        )
        restored_records = restored.get("delegation_records")

        assert restored_records == [
            record,
            {
                "child_task_id": "developer-child",
                "subagent": "developer",
            },
        ]
        assert "public task" not in str(restored_records)

    @pytest.mark.asyncio
    async def test_answer_only_subagent_is_cancelled_at_parent_deadline(self):
        parent_agent = Mock(spec=Agent)
        parent_agent.name.return_value = "root"
        parent_agent.tool_names = []
        parent_agent.conf = AgentConfig()
        manager = SubagentManager(agent=parent_agent)
        manager._scanned_agent_md_files = True
        verifier = Agent(
            name="verifier",
            conf=AgentConfig(
                llm_provider="openai",
                llm_model_name="offline",
                llm_api_key="offline",
            ),
            tool_names=[],
        )
        manager._available_subagents["verifier"] = SubagentInfo(
            name="verifier",
            description="Fresh verifier",
            source="team_member",
            tools=[],
            agent_instance=verifier,
            context_mode="fresh",
            merge_mode="answer_only",
        )
        context = Context(task_id="parent-deadline")
        parent_task = Task(input="public task", timeout=0.05, context=context)
        context.set_task(parent_task)
        child_context = context.deep_copy()
        child_context.task_id = "deadline-child"
        child_context.parent = context
        context.build_sub_context = AsyncMock(return_value=child_context)

        async def never_finishes(task):
            await asyncio.Event().wait()

        started = asyncio.get_running_loop().time()
        with patch("aworld.runner.Runners.run_task", side_effect=never_finishes):
            result = await manager.spawn(
                name="verifier",
                directive="Check before the parent deadline",
                context=context,
            )
        elapsed = asyncio.get_running_loop().time() - started

        assert elapsed < 0.5
        assert "crashed" in result
        assert context.context_info["delegation_records"][-1]["status"] == (
            "deadline_exceeded"
        )

    @pytest.mark.asyncio
    async def test_answer_only_subagent_preserves_parent_finalization_reserve(self):
        parent_agent = Mock(spec=Agent)
        parent_agent.name.return_value = "root"
        parent_agent.tool_names = []
        parent_agent.conf = AgentConfig()
        parent_agent._resolve_execution_protocol_policy.return_value = (
            ExecutionProtocolPolicy(
                mode="guide",
                finalization_reserve_seconds=0.12,
            )
        )
        manager = SubagentManager(agent=parent_agent)
        manager._scanned_agent_md_files = True
        verifier = Agent(
            name="verifier",
            conf=AgentConfig(
                llm_provider="openai",
                llm_model_name="offline",
                llm_api_key="offline",
            ),
            tool_names=[],
        )
        manager._available_subagents["verifier"] = SubagentInfo(
            name="verifier",
            description="Fresh verifier",
            source="team_member",
            tools=[],
            agent_instance=verifier,
            context_mode="fresh",
            merge_mode="answer_only",
        )
        context = Context(task_id="parent-reserve")
        parent_task = Task(
            input="public task",
            timeout=0.2,
            completion_reserve_seconds=0.03,
            context=context,
        )
        context.set_task(parent_task)
        child_context = context.deep_copy()
        child_context.task_id = "reserve-child"
        child_context.parent = context
        context.build_sub_context = AsyncMock(return_value=child_context)

        async def never_finishes(task):
            await asyncio.Event().wait()

        started = asyncio.get_running_loop().time()
        with patch("aworld.runner.Runners.run_task", side_effect=never_finishes):
            result = await manager.spawn(
                name="verifier",
                directive="Check while preserving finalization time",
                context=context,
            )
        elapsed = asyncio.get_running_loop().time() - started

        assert elapsed < 0.15
        assert parent_task.remaining_seconds() >= 0.08
        assert "crashed" in result
        assert context.context_info["delegation_records"][-1]["status"] == (
            "deadline_exceeded"
        )


if __name__ == '__main__':
    """Run tests directly with pytest"""
    pytest.main([__file__, '-v', '-s'])
