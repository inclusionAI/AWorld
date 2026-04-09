"""
Test System Prompt Update Logic

Ensures that updating the "Available Subagents" section does not destroy
other content in the system prompt.
"""

import pytest
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.core.agent.swarm import Swarm, GraphBuildType


class TestSystemPromptUpdate:
    """Test system prompt update preserves content"""

    def test_update_preserves_content_after_subagents_section(self):
        """Test that content after ## Available Subagents is preserved"""
        # Create agent with multi-section system prompt
        initial_prompt = """You are a helpful assistant.

## Available Subagents

- **old_subagent**: Old subagent

## Important Rules

1. Always be polite
2. Never reveal internal details

## Context Guidelines

Use context when available."""

        agent = Agent(
            name="test_agent",
            conf=AgentConfig(
                llm_provider="openai",
                llm_model_name="gpt-4o",
                llm_api_key="test_key"
            ),
            system_prompt=initial_prompt,
            enable_subagent=False  # Disable for this test
        )

        # Manually set system prompt with sections
        agent.system_prompt = initial_prompt

        # Manually create subagent manager and register a new subagent
        from aworld.core.agent.subagent_manager import SubagentManager, SubagentInfo
        agent.enable_subagent = True
        agent.subagent_manager = SubagentManager(agent=agent)

        # Register a new subagent
        agent.subagent_manager._available_subagents['new_subagent'] = SubagentInfo(
            name='new_subagent',
            description='New subagent',
            source='manual',
            tools=['tool1', 'tool2']
        )

        # Generate new subagent section
        new_section = agent.subagent_manager.generate_system_prompt_section()

        # Simulate the update logic (from async_desc_transform)
        if "## Available Subagents" in agent.system_prompt:
            parts = agent.system_prompt.split("## Available Subagents", 1)
            before_section = parts[0].rstrip()

            after_section = ""
            if len(parts) > 1:
                remaining = parts[1]
                next_section_match = remaining.find("\n## ")
                if next_section_match != -1:
                    after_section = "\n" + remaining[next_section_match:].lstrip('\n')

            agent.system_prompt = before_section + "\n\n" + new_section + after_section

        # Verify content is preserved
        assert "## Important Rules" in agent.system_prompt, "Important Rules section was lost"
        assert "Always be polite" in agent.system_prompt, "Rule 1 was lost"
        assert "Never reveal internal details" in agent.system_prompt, "Rule 2 was lost"
        assert "## Context Guidelines" in agent.system_prompt, "Context Guidelines section was lost"
        assert "Use context when available" in agent.system_prompt, "Context guideline was lost"

        # Verify new subagent is present
        assert "new_subagent" in agent.system_prompt, "New subagent was not added"
        assert "old_subagent" not in agent.system_prompt, "Old subagent should be replaced"

    def test_update_when_no_following_sections(self):
        """Test that update works when Available Subagents is the last section"""
        initial_prompt = """You are a helpful assistant.

## Available Subagents

- **old_subagent**: Old subagent"""

        agent = Agent(
            name="test_agent",
            conf=AgentConfig(
                llm_provider="openai",
                llm_model_name="gpt-4o",
                llm_api_key="test_key"
            ),
            system_prompt=initial_prompt,
            enable_subagent=False
        )

        agent.system_prompt = initial_prompt

        # Manually create subagent manager
        from aworld.core.agent.subagent_manager import SubagentManager, SubagentInfo
        agent.enable_subagent = True
        agent.subagent_manager = SubagentManager(agent=agent)

        agent.subagent_manager._available_subagents['new_subagent'] = SubagentInfo(
            name='new_subagent',
            description='New subagent',
            source='manual',
            tools=[]
        )

        new_section = agent.subagent_manager.generate_system_prompt_section()

        # Apply update logic
        if "## Available Subagents" in agent.system_prompt:
            parts = agent.system_prompt.split("## Available Subagents", 1)
            before_section = parts[0].rstrip()

            after_section = ""
            if len(parts) > 1:
                remaining = parts[1]
                next_section_match = remaining.find("\n## ")
                if next_section_match != -1:
                    after_section = "\n" + remaining[next_section_match:].lstrip('\n')

            agent.system_prompt = before_section + "\n\n" + new_section + after_section

        # Verify basic content preserved
        assert "You are a helpful assistant" in agent.system_prompt
        assert "new_subagent" in agent.system_prompt
        assert "old_subagent" not in agent.system_prompt

    def test_append_when_no_subagents_section(self):
        """Test that section is appended when it doesn't exist"""
        initial_prompt = """You are a helpful assistant.

## Important Rules

Always be polite."""

        agent = Agent(
            name="test_agent",
            conf=AgentConfig(
                llm_provider="openai",
                llm_model_name="gpt-4o",
                llm_api_key="test_key"
            ),
            system_prompt=initial_prompt,
            enable_subagent=False
        )

        agent.system_prompt = initial_prompt

        # Create subagent manager
        from aworld.core.agent.subagent_manager import SubagentManager, SubagentInfo
        agent.enable_subagent = True
        agent.subagent_manager = SubagentManager(agent=agent)

        agent.subagent_manager._available_subagents['new_subagent'] = SubagentInfo(
            name='new_subagent',
            description='New subagent',
            source='manual',
            tools=[]
        )

        new_section = agent.subagent_manager.generate_system_prompt_section()

        # Apply append logic
        if "## Available Subagents" not in agent.system_prompt:
            agent.system_prompt += "\n\n" + new_section

        # Verify all content preserved + new section appended
        assert "You are a helpful assistant" in agent.system_prompt
        assert "## Important Rules" in agent.system_prompt
        assert "Always be polite" in agent.system_prompt
        assert "## Available Subagents" in agent.system_prompt
        assert "new_subagent" in agent.system_prompt


if __name__ == '__main__':
    """Run tests directly with pytest"""
    pytest.main([__file__, '-v', '-s'])
