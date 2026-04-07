"""
Test suite for Phase 3 slash command system.

Tests command registration, routing, and execution.
"""
import asyncio
import os
import sys
import pytest
from pathlib import Path

# Add aworld-cli to path
sys.path.insert(0, str(Path(__file__).parent.parent / "aworld-cli" / "src"))

from aworld_cli.core.command_system import CommandRegistry, CommandContext
from aworld_cli.commands import help_cmd, commit, review, diff


class TestCommandRegistration:
    """Test command registration system."""

    def test_commands_registered(self):
        """Verify all commands are registered."""
        expected_commands = ['help', 'commit', 'review', 'diff']
        for cmd_name in expected_commands:
            cmd = CommandRegistry.get(cmd_name)
            assert cmd is not None, f"Command /{cmd_name} not registered"
            assert cmd.name == cmd_name

    def test_command_types(self):
        """Verify command types are correct."""
        # Tool command
        help_cmd = CommandRegistry.get('help')
        assert help_cmd.command_type == 'tool'

        # Prompt commands
        for cmd_name in ['commit', 'review', 'diff']:
            cmd = CommandRegistry.get(cmd_name)
            assert cmd.command_type == 'prompt', f"/{cmd_name} should be prompt command"

    def test_list_commands(self):
        """Test listing all registered commands."""
        commands = CommandRegistry.list_commands()
        assert len(commands) >= 4  # At least our 4 commands
        assert 'help' in commands
        assert 'commit' in commands
        assert 'review' in commands
        assert 'diff' in commands


class TestHelpCommand:
    """Test /help command (tool command)."""

    @pytest.mark.asyncio
    async def test_help_command_execution(self):
        """Test /help command executes and returns help text."""
        cmd = CommandRegistry.get('help')
        context = CommandContext(cwd=os.getcwd(), user_args='')

        result = await cmd.execute(context)

        assert result is not None
        assert 'Available commands:' in result
        assert '/help' in result
        assert '/commit' in result
        assert '/review' in result
        assert '/diff' in result

    @pytest.mark.asyncio
    async def test_help_command_with_args(self):
        """Test /help command ignores arguments."""
        cmd = CommandRegistry.get('help')
        context = CommandContext(cwd=os.getcwd(), user_args='some args')

        result = await cmd.execute(context)

        # Should still return help text even with args
        assert 'Available commands:' in result


class TestCommitCommand:
    """Test /commit command (prompt command)."""

    @pytest.mark.asyncio
    async def test_commit_pre_execute_validation(self, tmp_path):
        """Test /commit validates git repository."""
        cmd = CommandRegistry.get('commit')

        # Non-git directory should fail validation
        context = CommandContext(cwd=str(tmp_path), user_args='')
        error = await cmd.pre_execute(context)

        assert error is not None
        assert 'git repository' in error.lower()

    @pytest.mark.asyncio
    async def test_commit_prompt_generation(self):
        """Test /commit generates appropriate prompt."""
        cmd = CommandRegistry.get('commit')
        context = CommandContext(cwd=os.getcwd(), user_args='')

        # Skip if not in git repo
        error = await cmd.pre_execute(context)
        if error:
            pytest.skip("Not in git repository")

        prompt = await cmd.get_prompt(context)

        assert prompt is not None
        assert 'Git Commit Task' in prompt
        assert 'CRITICAL RULES' in prompt
        assert 'HEREDOC' in prompt

    def test_commit_allowed_tools(self):
        """Test /commit specifies correct allowed tools."""
        cmd = CommandRegistry.get('commit')

        allowed_tools = cmd.allowed_tools
        assert 'terminal__mcp_execute_command' in allowed_tools
        assert 'git_status' in allowed_tools
        assert 'git_diff' in allowed_tools
        assert 'git_commit' in allowed_tools


class TestReviewCommand:
    """Test /review command (prompt command)."""

    @pytest.mark.asyncio
    async def test_review_pre_execute_validation(self, tmp_path):
        """Test /review validates git repository."""
        cmd = CommandRegistry.get('review')

        # Non-git directory should fail validation
        context = CommandContext(cwd=str(tmp_path), user_args='')
        error = await cmd.pre_execute(context)

        assert error is not None
        assert 'git repository' in error.lower()

    @pytest.mark.asyncio
    async def test_review_prompt_generation(self):
        """Test /review generates appropriate prompt."""
        cmd = CommandRegistry.get('review')
        context = CommandContext(cwd=os.getcwd(), user_args='')

        # Skip if not in git repo
        error = await cmd.pre_execute(context)
        if error:
            pytest.skip("Not in git repository")

        prompt = await cmd.get_prompt(context)

        assert prompt is not None
        assert 'Code Review Task' in prompt
        assert 'Review Checklist' in prompt
        assert 'Code Quality' in prompt

    def test_review_allowed_tools(self):
        """Test /review specifies correct allowed tools."""
        cmd = CommandRegistry.get('review')

        allowed_tools = cmd.allowed_tools
        assert 'git_diff' in allowed_tools
        assert 'CAST_ANALYSIS' in allowed_tools
        assert 'filesystem__read_file' in allowed_tools


class TestDiffCommand:
    """Test /diff command (prompt command)."""

    @pytest.mark.asyncio
    async def test_diff_pre_execute_validation(self, tmp_path):
        """Test /diff validates git repository."""
        cmd = CommandRegistry.get('diff')

        # Non-git directory should fail validation
        context = CommandContext(cwd=str(tmp_path), user_args='')
        error = await cmd.pre_execute(context)

        assert error is not None
        assert 'git repository' in error.lower()

    @pytest.mark.asyncio
    async def test_diff_prompt_with_default_ref(self):
        """Test /diff generates prompt with default HEAD ref."""
        cmd = CommandRegistry.get('diff')
        context = CommandContext(cwd=os.getcwd(), user_args='')

        # Skip if not in git repo
        error = await cmd.pre_execute(context)
        if error:
            pytest.skip("Not in git repository")

        prompt = await cmd.get_prompt(context)

        assert prompt is not None
        assert 'Diff Summary Task' in prompt
        assert 'HEAD' in prompt  # Default ref

    @pytest.mark.asyncio
    async def test_diff_prompt_with_custom_ref(self):
        """Test /diff generates prompt with custom ref."""
        cmd = CommandRegistry.get('diff')
        context = CommandContext(cwd=os.getcwd(), user_args='main')

        # Skip if not in git repo
        error = await cmd.pre_execute(context)
        if error:
            pytest.skip("Not in git repository")

        prompt = await cmd.get_prompt(context)

        assert prompt is not None
        assert 'main' in prompt  # Custom ref

    def test_diff_allowed_tools(self):
        """Test /diff specifies correct allowed tools."""
        cmd = CommandRegistry.get('diff')

        allowed_tools = cmd.allowed_tools
        assert 'git_diff' in allowed_tools
        assert 'git_status' in allowed_tools


class TestCommandContext:
    """Test CommandContext dataclass."""

    def test_context_creation(self):
        """Test creating CommandContext."""
        context = CommandContext(
            cwd='/tmp',
            user_args='test args',
            sandbox=None,
            agent_config=None
        )

        assert context.cwd == '/tmp'
        assert context.user_args == 'test args'
        assert context.sandbox is None
        assert context.agent_config is None

    def test_context_defaults(self):
        """Test CommandContext default values."""
        context = CommandContext(cwd='/tmp', user_args='')

        assert context.sandbox is None
        assert context.agent_config is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
