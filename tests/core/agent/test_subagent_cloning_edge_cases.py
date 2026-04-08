"""
Unit Tests for SubagentManager Edge Cases - Agent Cloning

Tests edge cases in agent cloning including:
- Custom Agent subclasses with non-standard constructors
- Fallback to copy-based cloning when constructor fails
- Safe attribute access with getattr
"""

import pytest
from unittest.mock import Mock, patch
from aworld.core.agent.subagent_manager import SubagentManager
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig


class CustomAgent(Agent):
    """Custom agent with additional required positional arguments"""

    def __init__(self, name: str, required_arg: str, conf: AgentConfig = None, **kwargs):
        """Constructor with non-standard signature"""
        super().__init__(name=name, conf=conf, **kwargs)
        self.required_arg = required_arg


class TestAgentCloningEdgeCases:
    """Test edge cases in agent cloning"""

    def test_clone_custom_agent_with_nonstandard_constructor(self):
        """Test cloning falls back gracefully for custom agents"""
        # Create a custom agent with non-standard constructor
        conf = AgentConfig(
            llm_provider="openai",
            llm_model_name="gpt-4o",
            llm_api_key="test_key"
        )

        custom_agent = CustomAgent(
            name="custom",
            required_arg="special_value",
            conf=conf,
            tool_names=["tool1", "tool2", "tool3"]
        )

        # Create manager
        parent = Mock(spec=Agent)
        parent.tool_names = ["tool1", "tool2", "tool3"]
        manager = SubagentManager(agent=parent)

        # Clone with filtered tools
        filtered_tools = ["tool1", "tool2"]

        # Should fall back to copy-based cloning (logs warning)
        with patch('aworld.core.agent.subagent_manager.logger') as mock_logger:
            result = manager._clone_agent_instance(custom_agent, filtered_tools)

            # Verify fallback warning was logged
            assert mock_logger.warning.called
            warning_msg = mock_logger.warning.call_args[0][0]
            assert "Constructor-based cloning failed" in warning_msg
            assert "Falling back to copy-based cloning" in warning_msg

        # Verify cloned agent has correct properties
        assert result.name() == "custom"
        assert result.required_arg == "special_value"  # Custom attribute preserved
        assert set(result.tool_names) == set(filtered_tools)  # Tools filtered

        # Verify mutable state was reset
        assert result.trajectory == [] or result.trajectory is None
        assert result._finished == True

    def test_clone_agent_missing_handoffs_attribute(self):
        """Test cloning agent without handoffs attribute"""
        conf = AgentConfig(
            llm_provider="openai",
            llm_model_name="gpt-4o",
            llm_api_key="test_key"
        )

        original = Agent(
            name="original",
            conf=conf,
            tool_names=["tool1", "tool2"]
        )

        # Remove handoffs attribute to simulate edge case
        if hasattr(original, 'handoffs'):
            delattr(original, 'handoffs')

        parent = Mock(spec=Agent)
        parent.tool_names = ["tool1", "tool2"]
        manager = SubagentManager(agent=parent)

        # Should handle missing handoffs gracefully
        result = manager._clone_agent_instance(original, ["tool1"])

        # Should successfully clone despite missing attribute
        assert result.name() == "original"
        assert set(result.tool_names) == {"tool1"}

    def test_clone_agent_with_none_attributes(self):
        """Test cloning agent with None values for optional attributes"""
        conf = AgentConfig(
            llm_provider="openai",
            llm_model_name="gpt-4o"
        )

        original = Agent(
            name="minimal",
            conf=conf,
            tool_names=["tool1"],
            mcp_servers=None,  # Explicitly None
            black_tool_actions=None
        )

        parent = Mock(spec=Agent)
        parent.tool_names = ["tool1"]
        manager = SubagentManager(agent=parent)

        # Should handle None attributes gracefully
        result = manager._clone_agent_instance(original, ["tool1"])

        assert result.name() == "minimal"
        assert result.tool_names == ["tool1"]

    def test_clone_preserves_sandbox_reference(self):
        """Test that sandbox is shared (not copied) between original and clone"""
        conf = AgentConfig(
            llm_provider="openai",
            llm_model_name="gpt-4o"
        )

        # Create a mock sandbox instance
        sandbox = Mock()
        sandbox.sandbox_id = "test_sandbox"

        original = Agent(
            name="with_sandbox",
            conf=conf,
            tool_names=["tool1"],
            sandbox=sandbox
        )

        parent = Mock(spec=Agent)
        parent.tool_names = ["tool1"]
        manager = SubagentManager(agent=parent)

        result = manager._clone_agent_instance(original, ["tool1"])

        # Verify sandbox is the same object (shared, not copied)
        assert result.sandbox is sandbox
        assert id(result.sandbox) == id(original.sandbox)


if __name__ == '__main__':
    """Run tests directly with pytest"""
    pytest.main([__file__, '-v', '-s'])
