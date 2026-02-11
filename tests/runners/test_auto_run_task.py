# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Unit tests for Auto Run Task functionality.

Tests cover:
- SwarmComposerAgent YAML generation
- Task YAML loading
- Agent instantiation (builtin/skill/predefined)
- Swarm building
- End-to-end workflow
"""

import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

# Set test environment variables before imports
os.environ["LLM_MODEL_NAME"] = "gpt-4-test"
os.environ["LLM_PROVIDER"] = "openai"
os.environ["LLM_API_KEY"] = "test_key"
os.environ["LLM_BASE_URL"] = "https://api.test.com/v1"


@pytest.fixture
def sample_task_yaml():
    """Sample Task YAML for testing."""
    return """
task:
  query: "Test query"
  session_id: "test_session"

agents:
  - id: orchestrator
    type: builtin
    desc: "Test orchestrator"
    system_prompt: "You are a test agent"
    config:
      llm_config:
        llm_model_name: "gpt-4-test"
        llm_provider: "openai"
        llm_api_key: "test_key"
        llm_temperature: 0.0
      use_vision: false
    mcp_servers: []

swarm:
  type: team
  root_agent: orchestrator
  agents:
    - id: orchestrator
"""


@pytest.fixture
def sample_yaml_with_skill():
    """Sample YAML with skill-type agent."""
    return """
task:
  query: "Test with skill"

agents:
  - id: orchestrator
    type: builtin
    desc: "Test orchestrator"
    system_prompt: "Test prompt"
    config:
      llm_config:
        llm_model_name: "gpt-4-test"
        llm_provider: "openai"
        llm_api_key: "test_key"
    mcp_servers: []
  
  - id: skill_agent
    type: skill
    skill_name: test_agentic_skill

swarm:
  type: handoff
  root_agent: orchestrator
  agents:
    - id: orchestrator
      next: [skill_agent]
    - id: skill_agent
"""


@pytest.fixture
def temp_skills_dir(tmp_path):
    """Create temporary skills directory with test skill."""
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "test_agentic_skill"
    skill_dir.mkdir(parents=True)
    
    skill_md = skill_dir / "skill.md"
    skill_md.write_text("""---
name: test_agentic_skill
description: Test Agentic Skill
tool_list: {"test_server": ["test_tool"]}
type: agent
---

Test skill usage content.
""")
    
    return skills_dir


class TestSwarmComposerAgent:
    """Tests for SwarmComposerAgent class."""
    
    def test_swarm_composer_agent_initialization(self):
        """Test SwarmComposerAgent can be instantiated."""
        from aworld.agents.swarm_composer_agent import SwarmComposerAgent
        from aworld.config import AgentConfig, ModelConfig
        
        swarm_composer_agent = SwarmComposerAgent(
            conf=AgentConfig(
                llm_config=ModelConfig(
                    llm_model_name="gpt-4-test",
                    llm_provider="openai",
                    llm_api_key="test_key"
                )
            ),
            max_yaml_retry=3
        )
        
        assert swarm_composer_agent.name == "SwarmComposerAgent"
        assert swarm_composer_agent.max_yaml_retry == 3
        assert swarm_composer_agent.system_prompt is not None
    
    def test_swarm_composer_agent_validates_yaml(self):
        """Test SwarmComposerAgent YAML validation."""
        from aworld.agents.swarm_composer_agent import SwarmComposerAgent
        from aworld.config import AgentConfig, ModelConfig
        
        swarm_composer_agent = SwarmComposerAgent(
            conf=AgentConfig(
                llm_config=ModelConfig(
                    llm_model_name="gpt-4-test",
                    llm_provider="openai",
                    llm_api_key="test_key"
                )
            )
        )
        
        # Valid YAML should pass
        valid_yaml = """
task:
  query: "test"

agents:
  - id: agent1
    type: builtin

swarm:
  type: team
  agents:
    - id: agent1
"""
        swarm_composer_agent._validate_task_yaml(valid_yaml)  # Should not raise
        
        # Invalid YAML should raise
        invalid_yaml = "not: valid"
        with pytest.raises(ValueError, match="must contain 'agents' section"):
            swarm_composer_agent._validate_task_yaml(invalid_yaml)


class TestTaskLoader:
    """Tests for task_loader module."""
    
    @pytest.mark.asyncio
    async def test_load_builtin_agent(self, tmp_path, sample_task_yaml):
        """Test loading builtin agent from YAML."""
        from aworld.config.task_loader import load_task_from_yaml
        
        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text(sample_task_yaml)
        
        task = await load_task_from_yaml(str(yaml_path))
        
        assert task is not None
        assert task.swarm is not None
        assert len(task.swarm.agents) >= 1
        assert "orchestrator" in task.swarm.agents
    
    @pytest.mark.asyncio
    async def test_load_skill_agent(self, tmp_path, sample_yaml_with_skill, temp_skills_dir):
        """Test loading skill-type agent from YAML."""
        from aworld.config.task_loader import load_task_from_yaml
        
        yaml_path = tmp_path / "test_skill.yaml"
        yaml_path.write_text(sample_yaml_with_skill)
        
        task = await load_task_from_yaml(
            str(yaml_path),
            skills_path=temp_skills_dir
        )
        
        assert task is not None
        assert task.swarm is not None
        assert len(task.swarm.agents) >= 2
        assert "skill_agent" in task.swarm.agents
    
    @pytest.mark.asyncio
    async def test_load_predefined_agent(self, tmp_path):
        """Test loading predefined agent from YAML."""
        from aworld.config.task_loader import load_task_from_yaml
        from aworld.agents.llm_agent import Agent
        from aworld.config import AgentConfig, ModelConfig
        
        # Create predefined agent
        predefined_agent = Agent(
            name="test_predefined",
            conf=AgentConfig(
                llm_config=ModelConfig(
                    llm_model_name="gpt-4-test",
                    llm_provider="openai",
                    llm_api_key="test_key"
                )
            )
        )
        
        yaml_content = """
task:
  query: "Test predefined"

agents:
  - id: test_predefined
    type: predefined

swarm:
  type: team
  root_agent: test_predefined
  agents:
    - id: test_predefined
"""
        
        yaml_path = tmp_path / "test_predefined.yaml"
        yaml_path.write_text(yaml_content)
        
        task = await load_task_from_yaml(
            str(yaml_path),
            available_agents={"test_predefined": predefined_agent}
        )
        
        assert task is not None
        assert "test_predefined" in task.swarm.agents
        assert task.swarm.agents["test_predefined"] is predefined_agent
    
    @pytest.mark.asyncio
    async def test_missing_agent_error(self, tmp_path):
        """Test error handling when agent is missing."""
        from aworld.config.task_loader import load_task_from_yaml
        
        yaml_content = """
task:
  query: "Test"

agents:
  - id: missing_agent
    type: predefined

swarm:
  type: team
  root_agent: missing_agent
  agents:
    - id: missing_agent
"""
        
        yaml_path = tmp_path / "test_missing.yaml"
        yaml_path.write_text(yaml_content)
        
        with pytest.raises(ValueError, match="missing agent"):
            await load_task_from_yaml(
                str(yaml_path),
                available_agents={}
            )
    
    @pytest.mark.asyncio
    async def test_missing_skill_error(self, tmp_path, temp_skills_dir):
        """Test error handling when skill is not found."""
        from aworld.config.task_loader import load_task_from_yaml
        
        yaml_content = """
task:
  query: "Test"

agents:
  - id: skill_agent
    type: skill
    skill_name: nonexistent_skill

swarm:
  type: team
  root_agent: skill_agent
  agents:
    - id: skill_agent
"""
        
        yaml_path = tmp_path / "test_missing_skill.yaml"
        yaml_path.write_text(yaml_content)
        
        with pytest.raises(ValueError, match="missing agent.*Skill.*not found"):
            await load_task_from_yaml(
                str(yaml_path),
                skills_path=temp_skills_dir
            )


class TestRunnersIntegration:
    """Integration tests for Runners interfaces."""
    
    @pytest.mark.asyncio
    async def test_plan_task_creates_yaml(self, tmp_path):
        """Test plan_task generates valid YAML."""
        from aworld.runner import Runners
        from aworld.agents.swarm_composer_agent import SwarmComposerAgent
        from aworld.config import AgentConfig, ModelConfig
        from aworld.core.common import Observation
        
        # Mock SwarmComposerAgent to return predefined YAML
        mock_yaml = """
task:
  query: "Test query"

agents:
  - id: orchestrator
    type: builtin
    config:
      llm_config:
        llm_model_name: "gpt-4"
        llm_provider: "openai"
        llm_api_key: "${LLM_API_KEY}"

swarm:
  type: team
  root_agent: orchestrator
  agents:
    - id: orchestrator
"""
        
        # Create mock SwarmComposerAgent
        swarm_composer_agent = Mock(spec=SwarmComposerAgent)
        swarm_composer_agent.plan_task = AsyncMock(return_value=mock_yaml)
        
        output_path = tmp_path / "test_plan.yaml"
        
        yaml_path = await Runners.plan_task(
            query="Test query",
            swarm_composer_agent=swarm_composer_agent,
            output_yaml=str(output_path),
            auto_save=True
        )
        
        assert yaml_path == str(output_path)
        assert output_path.exists()
        
        # Verify YAML content
        content = output_path.read_text()
        assert "task:" in content
        assert "agents:" in content
        assert "swarm:" in content
    
    @pytest.mark.asyncio
    async def test_execute_plan_runs_task(self, tmp_path, sample_task_yaml):
        """Test execute_plan creates and runs task."""
        from aworld.runner import Runners
        from aworld.core.task import TaskResponse
        
        yaml_path = tmp_path / "test_exec.yaml"
        yaml_path.write_text(sample_task_yaml)
        
        # Mock run_task to avoid actual execution
        with patch.object(Runners, 'run_task', new_callable=AsyncMock) as mock_run:
            mock_response = TaskResponse(
                task_id="test_id",
                answer="Test answer",
                status="completed"
            )
            mock_run.return_value = {"test_id": mock_response}
            
            results = await Runners.execute_plan(
                yaml_path=str(yaml_path)
            )
            
            assert results is not None
            assert "test_id" in results
            assert results["test_id"].answer == "Test answer"
            
            # Verify run_task was called
            mock_run.assert_called_once()


class TestErrorHandling:
    """Tests for error handling and retry logic."""
    
    def test_yaml_validation_duplicate_agent_id(self):
        """Test validation catches duplicate agent IDs."""
        from aworld.agents.swarm_composer_agent import SwarmComposerAgent
        from aworld.config import AgentConfig, ModelConfig
        
        swarm_composer_agent = SwarmComposerAgent(
            conf=AgentConfig(
                llm_config=ModelConfig(
                    llm_model_name="gpt-4-test",
                    llm_provider="openai",
                    llm_api_key="test_key"
                )
            )
        )
        
        invalid_yaml = """
task:
  query: "test"

agents:
  - id: agent1
    type: builtin
  - id: agent1
    type: builtin

swarm:
  type: team
  agents:
    - id: agent1
"""
        
        with pytest.raises(ValueError, match="Duplicate agent id"):
            swarm_composer_agent._validate_task_yaml(invalid_yaml)
    
    def test_yaml_validation_undefined_root_agent(self):
        """Test validation catches undefined root_agent."""
        from aworld.agents.swarm_composer_agent import SwarmComposerAgent
        from aworld.config import AgentConfig, ModelConfig
        
        swarm_composer_agent = SwarmComposerAgent(
            conf=AgentConfig(
                llm_config=ModelConfig(
                    llm_model_name="gpt-4-test",
                    llm_provider="openai",
                    llm_api_key="test_key"
                )
            )
        )
        
        invalid_yaml = """
task:
  query: "test"

agents:
  - id: agent1
    type: builtin

swarm:
  type: team
  root_agent: nonexistent_agent
  agents:
    - id: agent1
"""
        
        with pytest.raises(ValueError, match="root_agent.*not defined"):
            swarm_composer_agent._validate_task_yaml(invalid_yaml)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
