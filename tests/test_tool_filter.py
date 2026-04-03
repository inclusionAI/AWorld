"""
Test tool filtering functionality for slash commands.
"""
import pytest
from aworld_cli.core.tool_filter import (
    matches_pattern,
    filter_tools_by_whitelist,
    temporary_tool_filter
)


class TestMatchesPattern:
    """Test pattern matching for tool names."""

    def test_exact_match(self):
        """Test exact tool name matching."""
        assert matches_pattern("git_status", "git_status")
        assert not matches_pattern("git_diff", "git_status")

    def test_wildcard_match(self):
        """Test wildcard pattern matching."""
        assert matches_pattern("git_status", "git_*")
        assert matches_pattern("git_diff", "git_*")
        assert not matches_pattern("filesystem__read_file", "git_*")

    def test_terminal_prefix(self):
        """Test terminal: prefix conversion."""
        assert matches_pattern("terminal__mcp_execute_command", "terminal:*")
        assert matches_pattern("terminal__git_status", "terminal:git*")
        assert not matches_pattern("terminal__mcp_execute_command", "terminal:git*")

    def test_filesystem_prefix(self):
        """Test filesystem: prefix conversion."""
        assert matches_pattern("filesystem__read_file", "filesystem:*")
        assert matches_pattern("filesystem__read_file", "filesystem:read*")
        assert not matches_pattern("filesystem__write_file", "filesystem:read*")


class TestFilterToolsByWhitelist:
    """Test tool filtering by whitelist patterns."""

    def test_filter_with_patterns(self):
        """Test filtering tools with specific patterns."""
        tools = ["git_status", "git_diff", "filesystem__read_file", "bash"]
        patterns = ["git_*", "bash"]
        filtered = filter_tools_by_whitelist(tools, patterns)

        assert "git_status" in filtered
        assert "git_diff" in filtered
        assert "bash" in filtered
        assert "filesystem__read_file" not in filtered

    def test_filter_terminal_commands(self):
        """Test filtering terminal commands."""
        tools = [
            "terminal__mcp_execute_command",
            "terminal__git_status",
            "git_status",
            "filesystem__read_file"
        ]
        patterns = ["terminal:git*", "git_status"]
        filtered = filter_tools_by_whitelist(tools, patterns)

        assert "terminal__git_status" in filtered
        assert "git_status" in filtered
        assert "terminal__mcp_execute_command" not in filtered
        assert "filesystem__read_file" not in filtered

    def test_empty_whitelist_allows_all(self):
        """Test that empty whitelist allows all tools."""
        tools = ["git_status", "bash", "filesystem__read_file"]
        patterns = []
        filtered = filter_tools_by_whitelist(tools, patterns)

        assert filtered == tools

    def test_no_matches(self):
        """Test when no tools match the patterns."""
        tools = ["git_status", "git_diff"]
        patterns = ["bash", "filesystem:*"]
        filtered = filter_tools_by_whitelist(tools, patterns)

        assert filtered == []


class TestTemporaryToolFilter:
    """Test temporary tool filtering context manager."""

    def test_filter_and_restore_swarm_tools(self):
        """Test that swarm tools are filtered and restored."""
        # Mock swarm object
        class MockSwarm:
            def __init__(self):
                self.tools = ["git_status", "git_diff", "bash", "filesystem__read_file"]
                self.topology = []

        swarm = MockSwarm()
        original_tools = swarm.tools.copy()

        # Apply filter
        with temporary_tool_filter(swarm, ["git_*"]):
            # Inside context: tools should be filtered
            assert len(swarm.tools) == 2
            assert "git_status" in swarm.tools
            assert "git_diff" in swarm.tools
            assert "bash" not in swarm.tools

        # After context: tools should be restored
        assert swarm.tools == original_tools

    def test_filter_and_restore_agent_tools(self):
        """Test that agent tools are filtered and restored."""
        # Mock agent
        class MockAgent:
            def __init__(self):
                self.tool_names = ["git_status", "bash", "filesystem__read_file"]
                self._id = "test_agent"

            def id(self):
                return self._id

        # Mock swarm with agent
        class MockSwarm:
            def __init__(self, agent):
                self.tools = []
                self.topology = [agent]

        agent = MockAgent()
        swarm = MockSwarm(agent)
        original_agent_tools = agent.tool_names.copy()

        # Apply filter
        with temporary_tool_filter(swarm, ["git_*", "bash"]):
            # Inside context: agent tools should be filtered
            assert len(agent.tool_names) == 2
            assert "git_status" in agent.tool_names
            assert "bash" in agent.tool_names
            assert "filesystem__read_file" not in agent.tool_names

        # After context: agent tools should be restored
        assert agent.tool_names == original_agent_tools

    def test_no_filtering_when_none(self):
        """Test that no filtering occurs when allowed_tools is None."""
        class MockSwarm:
            def __init__(self):
                self.tools = ["git_status", "bash"]

        swarm = MockSwarm()
        original_tools = swarm.tools.copy()

        with temporary_tool_filter(swarm, None):
            # No filtering should occur
            assert swarm.tools == original_tools

        # Tools should remain unchanged
        assert swarm.tools == original_tools


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
