"""
Unit Tests for Lazy Initialization of agent.md Scanning

Tests that agent.md files are NOT scanned during __init__ but instead
deferred until first spawn() call, avoiding sync_exec issues.
"""

import pytest
import asyncio
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, call

from aworld.core.agent.subagent_manager import SubagentManager
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig


class TestLazyInitialization:
    """Test lazy initialization of agent.md scanning"""

    def test_subagent_manager_does_not_scan_on_init(self):
        """Test that SubagentManager does NOT scan agent.md files during __init__"""
        agent = Mock(spec=Agent)
        agent.name.return_value = "test_agent"
        agent.tool_names = []

        # Create SubagentManager
        manager = SubagentManager(
            agent=agent,
            agent_md_search_paths=['./.claude/agents']
        )

        # Verify no scanning occurred during __init__
        assert manager._scanned_agent_md_files == False
        assert manager._agent_md_search_paths == ['./.claude/agents']
        assert len(manager._available_subagents) == 0

    @pytest.mark.asyncio
    async def test_lazy_scan_on_first_spawn(self):
        """Test that agent.md scanning happens on first spawn() call"""
        agent = Mock(spec=Agent)
        agent.name.return_value = "test_agent"
        agent.tool_names = []

        manager = SubagentManager(
            agent=agent,
            agent_md_search_paths=['./.claude/agents']
        )

        # Mock scan_agent_md_files to verify it gets called
        with patch.object(manager, 'scan_agent_md_files', new_callable=AsyncMock) as mock_scan:
            # Manually register a subagent after "scanning"
            async def fake_scan(*args, **kwargs):
                from aworld.core.agent.subagent_manager import SubagentInfo
                manager._available_subagents['test_subagent'] = SubagentInfo(
                    name='test_subagent',
                    description='Test subagent',
                    source='agent_md',
                    tools=['tool1']
                )

            mock_scan.side_effect = fake_scan

            # Trigger _ensure_agent_md_scanned
            await manager._ensure_agent_md_scanned()

            # Verify scan was called
            assert mock_scan.called
            assert mock_scan.call_args == call(search_paths=['./.claude/agents'])

            # Verify flag was set
            assert manager._scanned_agent_md_files == True

    @pytest.mark.asyncio
    async def test_lazy_scan_is_idempotent(self):
        """Test that _ensure_agent_md_scanned is idempotent (only scans once)"""
        agent = Mock(spec=Agent)
        agent.name.return_value = "test_agent"
        agent.tool_names = []

        manager = SubagentManager(
            agent=agent,
            agent_md_search_paths=['./.claude/agents']
        )

        # Mock scan_agent_md_files
        with patch.object(manager, 'scan_agent_md_files', new_callable=AsyncMock) as mock_scan:
            # Call multiple times
            await manager._ensure_agent_md_scanned()
            await manager._ensure_agent_md_scanned()
            await manager._ensure_agent_md_scanned()

            # Verify scan was only called once
            assert mock_scan.call_count == 1

    @pytest.mark.asyncio
    async def test_spawn_triggers_lazy_scan(self):
        """Test that spawn() triggers lazy scanning before checking for subagent"""
        agent = Mock(spec=Agent)
        agent.name.return_value = "test_agent"
        agent.tool_names = []
        agent.conf = AgentConfig()

        manager = SubagentManager(
            agent=agent,
            agent_md_search_paths=['./.claude/agents']
        )

        # Mock _ensure_agent_md_scanned
        with patch.object(manager, '_ensure_agent_md_scanned', new_callable=AsyncMock) as mock_ensure:
            # Attempt spawn (will fail because subagent doesn't exist, but that's okay)
            try:
                await manager.spawn(name='nonexistent', directive='test')
            except ValueError:
                pass  # Expected - subagent doesn't exist

            # Verify _ensure_agent_md_scanned was called
            assert mock_ensure.called

    def test_agent_initialization_without_sync_exec(self):
        """Test that Agent initialization does NOT use sync_exec"""
        conf = AgentConfig(
            llm_provider="openai",
            llm_model_name="gpt-4o",
            llm_api_key="test_key"
        )

        # Patch scan_agent_md_files to ensure it's NOT called during __init__
        with patch('aworld.core.agent.subagent_manager.SubagentManager.scan_agent_md_files', new_callable=AsyncMock) as mock_scan:
            agent = Agent(
                name="test_agent",
                conf=conf,
                enable_subagent=True,
                subagent_search_paths=['./.claude/agents']
            )

            # Verify scan_agent_md_files was NOT called during __init__
            assert not mock_scan.called

            # Verify SubagentManager was created with search paths
            assert agent.subagent_manager is not None
            assert agent.subagent_manager._agent_md_search_paths == ['./.claude/agents']
            assert agent.subagent_manager._scanned_agent_md_files == False

    @pytest.mark.asyncio
    async def test_default_search_paths_when_none_provided(self):
        """Test that default search paths are used when none provided"""
        agent = Mock(spec=Agent)
        agent.name.return_value = "test_agent"
        agent.tool_names = []

        # Create manager without search paths
        manager = SubagentManager(agent=agent)

        # Mock scan_agent_md_files
        with patch.object(manager, 'scan_agent_md_files', new_callable=AsyncMock) as mock_scan:
            await manager._ensure_agent_md_scanned()

            # Verify default paths were used
            assert mock_scan.called
            expected_paths = ['./.claude/agents', '~/.claude/agents', './agents']
            assert mock_scan.call_args == call(search_paths=expected_paths)


if __name__ == '__main__':
    """Run tests directly with pytest"""
    pytest.main([__file__, '-v', '-s'])
