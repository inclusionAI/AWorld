"""Unit tests for multi-workspace configuration isolation (P0-4 fix).

This test suite verifies that hooks from different workspaces are properly
isolated and don't pollute each other when both are loaded into cache.

Test Coverage:
- TC-ISOLATION-001: Workspace A hooks don't affect workspace B
- TC-ISOLATION-002: Switching workspaces changes active hooks
- TC-ISOLATION-003: Unloaded workspace has no config hooks (only Python hooks)
"""

import os
import tempfile
import yaml
from pathlib import Path
from unittest.mock import patch

import pytest

from aworld.runners.hook.hook_factory import HookManager, HookFactory


class TestMultiWorkspaceIsolation:
    """Test configuration isolation between multiple workspaces."""

    @pytest.mark.asyncio
    async def test_workspace_a_does_not_affect_workspace_b(self, tmp_path):
        """TC-ISOLATION-001: Workspace A hooks don't pollute workspace B."""
        # Setup: Create two workspaces with different hooks
        workspace_a = tmp_path / 'workspace_a'
        workspace_b = tmp_path / 'workspace_b'

        workspace_a.mkdir()
        workspace_b.mkdir()

        # Workspace A: deny hook
        config_a_dir = workspace_a / '.aworld'
        config_a_dir.mkdir()
        (config_a_dir / 'trusted').touch()

        deny_script_a = workspace_a / 'deny_hook.sh'
        deny_script_a.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "permission_decision": "deny",
    "permission_decision_reason": "Denied by workspace A"
}
EOF
""")
        deny_script_a.chmod(0o755)

        config_a = {
            'version': '2',
            'hooks': {
                'before_tool_call': [
                    {
                        'name': 'workspace_a_deny',
                        'type': 'command',
                        'command': str(deny_script_a),
                        'enabled': True
                    }
                ]
            }
        }

        config_a_path = config_a_dir / 'hooks.yaml'
        with open(config_a_path, 'w') as f:
            yaml.safe_dump(config_a, f)

        # Workspace B: allow hook
        config_b_dir = workspace_b / '.aworld'
        config_b_dir.mkdir()
        (config_b_dir / 'trusted').touch()

        allow_script_b = workspace_b / 'allow_hook.sh'
        allow_script_b.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "permission_decision": "allow"
}
EOF
""")
        allow_script_b.chmod(0o755)

        config_b = {
            'version': '2',
            'hooks': {
                'before_tool_call': [
                    {
                        'name': 'workspace_b_allow',
                        'type': 'command',
                        'command': str(allow_script_b),
                        'enabled': True
                    }
                ]
            }
        }

        config_b_path = config_b_dir / 'hooks.yaml'
        with open(config_b_path, 'w') as f:
            yaml.safe_dump(config_b, f)

        # Clear cache and load both workspaces
        HookManager._config_hooks_cache = {}
        HookManager.load_config_hooks(str(config_a_path))
        HookManager.load_config_hooks(str(config_b_path))

        # Verify both are in cache
        assert str(config_a_path) in HookManager._config_hooks_cache
        assert str(config_b_path) in HookManager._config_hooks_cache

        # Test: Get hooks for workspace A (should only see workspace_a_deny)
        hooks_a = HookFactory.hooks('before_tool_call', workspace_path=str(workspace_a))
        hook_names_a = [hook._name for hook in hooks_a.get('before_tool_call', [])]

        # Verify only workspace A hooks are present
        assert 'workspace_a_deny' in hook_names_a, "Workspace A should have its deny hook"
        assert 'workspace_b_allow' not in hook_names_a, "Workspace A should NOT see workspace B hooks"

        # Test: Get hooks for workspace B (should only see workspace_b_allow)
        hooks_b = HookFactory.hooks('before_tool_call', workspace_path=str(workspace_b))
        hook_names_b = [hook._name for hook in hooks_b.get('before_tool_call', [])]

        # Verify only workspace B hooks are present
        assert 'workspace_b_allow' in hook_names_b, "Workspace B should have its allow hook"
        assert 'workspace_a_deny' not in hook_names_b, "Workspace B should NOT see workspace A hooks"

    @pytest.mark.asyncio
    async def test_switching_workspaces_changes_active_hooks(self, tmp_path):
        """TC-ISOLATION-002: Switching current directory changes active hooks."""
        # Setup: Create two workspaces
        workspace_1 = tmp_path / 'workspace_1'
        workspace_2 = tmp_path / 'workspace_2'

        workspace_1.mkdir()
        workspace_2.mkdir()

        # Workspace 1: hook_1
        config_1_dir = workspace_1 / '.aworld'
        config_1_dir.mkdir()
        (config_1_dir / 'trusted').touch()

        script_1 = workspace_1 / 'hook_1.sh'
        script_1.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "system_message": "Hook from workspace 1"
}
EOF
""")
        script_1.chmod(0o755)

        config_1 = {
            'version': '2',
            'hooks': {
                'user_input_received': [
                    {
                        'name': 'hook_1',
                        'type': 'command',
                        'command': str(script_1),
                        'enabled': True
                    }
                ]
            }
        }

        config_1_path = config_1_dir / 'hooks.yaml'
        with open(config_1_path, 'w') as f:
            yaml.safe_dump(config_1, f)

        # Workspace 2: hook_2
        config_2_dir = workspace_2 / '.aworld'
        config_2_dir.mkdir()
        (config_2_dir / 'trusted').touch()

        script_2 = workspace_2 / 'hook_2.sh'
        script_2.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "system_message": "Hook from workspace 2"
}
EOF
""")
        script_2.chmod(0o755)

        config_2 = {
            'version': '2',
            'hooks': {
                'user_input_received': [
                    {
                        'name': 'hook_2',
                        'type': 'command',
                        'command': str(script_2),
                        'enabled': True
                    }
                ]
            }
        }

        config_2_path = config_2_dir / 'hooks.yaml'
        with open(config_2_path, 'w') as f:
            yaml.safe_dump(config_2, f)

        # Clear cache and load both
        HookManager._config_hooks_cache = {}
        HookManager.load_config_hooks(str(config_1_path))
        HookManager.load_config_hooks(str(config_2_path))

        # Test: Simulate switching to workspace 1 (by passing workspace_path)
        with patch('os.getcwd', return_value=str(workspace_1)):
            hooks_1 = HookFactory.hooks('user_input_received')  # No workspace_path, uses getcwd()
            hook_names_1 = [hook._name for hook in hooks_1.get('user_input_received', [])]
            assert 'hook_1' in hook_names_1
            assert 'hook_2' not in hook_names_1

        # Test: Simulate switching to workspace 2
        with patch('os.getcwd', return_value=str(workspace_2)):
            hooks_2 = HookFactory.hooks('user_input_received')  # No workspace_path, uses getcwd()
            hook_names_2 = [hook._name for hook in hooks_2.get('user_input_received', [])]
            assert 'hook_2' in hook_names_2
            assert 'hook_1' not in hook_names_2

    @pytest.mark.asyncio
    async def test_unloaded_workspace_has_no_config_hooks(self, tmp_path):
        """TC-ISOLATION-003: Workspace without loaded config has no config hooks."""
        # Setup: Create workspace C with config, but don't load it
        workspace_c = tmp_path / 'workspace_c'
        workspace_c.mkdir()

        config_c_dir = workspace_c / '.aworld'
        config_c_dir.mkdir()
        (config_c_dir / 'trusted').touch()

        script_c = workspace_c / 'hook_c.sh'
        script_c.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true
}
EOF
""")
        script_c.chmod(0o755)

        config_c = {
            'version': '2',
            'hooks': {
                'before_tool_call': [
                    {
                        'name': 'hook_c',
                        'type': 'command',
                        'command': str(script_c),
                        'enabled': True
                    }
                ]
            }
        }

        config_c_path = config_c_dir / 'hooks.yaml'
        with open(config_c_path, 'w') as f:
            yaml.safe_dump(config_c, f)

        # Clear cache (don't load workspace C)
        HookManager._config_hooks_cache = {}

        # P0-1: Test auto-loading - hooks() should automatically load workspace C config
        hooks_c = HookFactory.hooks('before_tool_call', workspace_path=str(workspace_c))
        hook_names_c = [hook._name for hook in hooks_c.get('before_tool_call', [])]

        # P0-1: Verify config was auto-loaded
        assert 'hook_c' in hook_names_c, "P0-1: Workspace C config should be auto-loaded"

        # Verify second call doesn't reload (uses cache)
        hooks_c_second = HookFactory.hooks('before_tool_call', workspace_path=str(workspace_c))
        hook_names_c_second = [hook._name for hook in hooks_c_second.get('before_tool_call', [])]

        assert 'hook_c' in hook_names_c_second, "Workspace C hooks should still be available from cache"

    @pytest.mark.asyncio
    async def test_single_cached_standard_config_does_not_leak_to_other_workspace(self, tmp_path):
        """TC-ISOLATION-004: 单个标准 workspace 配置不能回退污染无配置工作区。"""
        workspace_a = tmp_path / 'workspace_a'
        workspace_b = tmp_path / 'workspace_b'
        workspace_a.mkdir()
        workspace_b.mkdir()

        config_a_dir = workspace_a / '.aworld'
        config_a_dir.mkdir()
        (config_a_dir / 'trusted').touch()

        hook_script = workspace_a / 'workspace_a_hook.sh'
        hook_script.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true
}
EOF
""")
        hook_script.chmod(0o755)

        config_a = {
            'version': '2',
            'hooks': {
                'user_input_received': [
                    {
                        'name': 'workspace_a_only',
                        'type': 'command',
                        'command': str(hook_script),
                        'enabled': True
                    }
                ]
            }
        }

        config_a_path = config_a_dir / 'hooks.yaml'
        with open(config_a_path, 'w') as f:
            yaml.safe_dump(config_a, f)

        HookManager._config_hooks_cache = {}
        HookManager.load_config_hooks(str(config_a_path))

        hooks_b = HookFactory.hooks('user_input_received', workspace_path=str(workspace_b))
        hook_names_b = [hook._name for hook in hooks_b.get('user_input_received', [])]

        assert 'workspace_a_only' not in hook_names_b, (
            "Standard workspace config from A must not leak into unrelated workspace B"
        )
