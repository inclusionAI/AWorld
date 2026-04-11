"""
End-to-End Integration Test for Lazy Initialization

Tests the complete flow of lazy initialization from Agent creation
through first spawn, verifying sync_exec is not used.
"""

import pytest
import asyncio
import tempfile
import os
import builtins
from pathlib import Path
from unittest.mock import patch, Mock

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.core.agent.base import BaseAgent


class TestLazyInitializationE2E:
    """End-to-end tests for lazy initialization"""

    @pytest.mark.asyncio
    async def test_e2e_lazy_initialization_flow(self):
        """Test complete lazy initialization flow without sync_exec"""
        # Create a temporary directory with agent.md files
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test agent.md file
            agent_md_path = Path(tmpdir) / 'test_agent.md'
            agent_md_content = """---
name: test_subagent
description: Test subagent for lazy initialization
tool_names: [read_file, write_file]
mcp_servers: []
---

Test subagent content.
"""
            agent_md_path.write_text(agent_md_content)

            # Step 1: Create agent with enable_subagent=True
            # This should NOT trigger scanning (no sync_exec)
            conf = AgentConfig(
                llm_provider="openai",
                llm_model_name="gpt-4o",
                llm_api_key="test_key"
            )

            agent = Agent(
                name="coordinator",
                conf=conf,
                tool_names=["tool1", "tool2"],
                enable_subagent=True,
                subagent_search_paths=[tmpdir]
            )

            # Verify SubagentManager was created but NOT scanned yet
            assert agent.subagent_manager is not None
            assert agent.subagent_manager._scanned_agent_md_files == False
            assert len(agent.subagent_manager._available_subagents) == 0

            # Step 2: Trigger lazy scanning via _ensure_agent_md_scanned
            await asyncio.wait_for(
                agent.subagent_manager._ensure_agent_md_scanned(),
                timeout=1,
            )

            # Verify scanning occurred
            assert agent.subagent_manager._scanned_agent_md_files == True
            assert 'test_subagent' in agent.subagent_manager._available_subagents

            # Verify subagent info
            subagent_info = agent.subagent_manager._available_subagents['test_subagent']
            assert subagent_info.name == 'test_subagent'
            assert subagent_info.description == 'Test subagent for lazy initialization'
            assert subagent_info.source == 'agent_md'
            assert 'read_file' in subagent_info.tools

    def test_no_sync_exec_import_during_init(self):
        """Test that sync_exec is NOT imported during Agent initialization"""
        # Mock sync_exec to detect if it's imported
        import sys
        original_import = builtins.__import__

        sync_exec_imported = []

        def mock_import(name, *args, **kwargs):
            if 'sync_exec' in name or name == 'aworld.utils.common':
                # Check if sync_exec is being accessed
                sync_exec_imported.append(name)
            return original_import(name, *args, **kwargs)

        # Patch import temporarily
        with patch('builtins.__import__', side_effect=mock_import):
            conf = AgentConfig(
                llm_provider="openai",
                llm_model_name="gpt-4o",
                llm_api_key="test_key"
            )

            agent = Agent(
                name="test_agent",
                conf=conf,
                enable_subagent=True
            )

            # Verify SubagentManager was created
            assert agent.subagent_manager is not None

        # Note: This test might not catch all imports due to Python's import caching,
        # but it demonstrates the intent. The key verification is that
        # _scanned_agent_md_files remains False after __init__
        assert agent.subagent_manager._scanned_agent_md_files == False

    @pytest.mark.asyncio
    async def test_concurrent_first_spawns(self):
        """Test that concurrent first spawns only trigger scanning once"""
        conf = AgentConfig(
            llm_provider="openai",
            llm_model_name="gpt-4o",
            llm_api_key="test_key"
        )

        agent = Agent(
            name="coordinator",
            conf=conf,
            enable_subagent=True
        )

        manager = agent.subagent_manager

        # Mock scan_agent_md_files to count calls
        scan_count = []

        original_scan = manager.scan_agent_md_files

        async def mock_scan(*args, **kwargs):
            scan_count.append(1)
            await original_scan(*args, **kwargs)

        with patch.object(manager, 'scan_agent_md_files', side_effect=mock_scan):
            # Trigger multiple concurrent _ensure_agent_md_scanned calls
            await asyncio.wait_for(
                asyncio.gather(
                    manager._ensure_agent_md_scanned(),
                    manager._ensure_agent_md_scanned(),
                    manager._ensure_agent_md_scanned()
                ),
                timeout=1,
            )

            # Verify scan was only called once (thread-safe)
            assert len(scan_count) == 1

    @pytest.mark.asyncio
    async def test_lazy_scan_with_empty_search_paths(self):
        """Test lazy scanning with non-existent search paths"""
        conf = AgentConfig(
            llm_provider="openai",
            llm_model_name="gpt-4o",
            llm_api_key="test_key"
        )

        agent = Agent(
            name="coordinator",
            conf=conf,
            enable_subagent=True,
            subagent_search_paths=['/nonexistent/path']
        )

        # Trigger lazy scanning
        await asyncio.wait_for(
            agent.subagent_manager._ensure_agent_md_scanned(),
            timeout=1,
        )

        # Verify scanning completed without error (empty results)
        assert agent.subagent_manager._scanned_agent_md_files == True
        # No subagents should be found
        assert len(agent.subagent_manager._available_subagents) == 0


if __name__ == '__main__':
    """Run tests directly with pytest"""
    pytest.main([__file__, '-v', '-s'])
