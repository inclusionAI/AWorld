"""Unit tests for WorkspaceTrust security mechanism.

Test Coverage:
- TC-TRUST-001: Trusted workspace with marker file
- TC-TRUST-002: Untrusted workspace without marker
- TC-TRUST-003: AWORLD_TRUST_ALL_WORKSPACES override
- TC-TRUST-004: HookFactory integration - blocks untrusted
- TC-TRUST-005: HookFactory integration - allows trusted
- TC-TRUST-006: Workspace path extraction from config path
"""

import os
import tempfile
import yaml
from pathlib import Path
from unittest.mock import patch

import pytest

from aworld.core.security.trust import WorkspaceTrust
from aworld.runners.hook.hook_factory import HookManager


class TestWorkspaceTrust:
    """Test WorkspaceTrust basic functionality."""

    def test_trusted_with_marker(self, tmp_path):
        """TC-TRUST-001: Workspace with .aworld/trusted marker is trusted."""
        # Create trust marker
        trust_marker = tmp_path / '.aworld' / 'trusted'
        trust_marker.parent.mkdir(parents=True, exist_ok=True)
        trust_marker.touch()

        # Verify trusted
        assert WorkspaceTrust.is_trusted(str(tmp_path)) is True

    def test_untrusted_without_marker(self, tmp_path):
        """TC-TRUST-002: Workspace without marker is untrusted."""
        # No trust marker
        assert WorkspaceTrust.is_trusted(str(tmp_path)) is False

    def test_trust_all_override(self, tmp_path):
        """TC-TRUST-003: AWORLD_TRUST_ALL_WORKSPACES=true trusts all."""
        # No trust marker, but env override
        with patch.dict(os.environ, {'AWORLD_TRUST_ALL_WORKSPACES': 'true'}):
            assert WorkspaceTrust.is_trusted(str(tmp_path)) is True

        # Without override, still untrusted
        assert WorkspaceTrust.is_trusted(str(tmp_path)) is False

    def test_mark_trusted(self, tmp_path):
        """Test mark_trusted() creates trust marker."""
        # Initially untrusted
        assert WorkspaceTrust.is_trusted(str(tmp_path)) is False

        # Mark as trusted
        WorkspaceTrust.mark_trusted(str(tmp_path))

        # Now trusted
        assert WorkspaceTrust.is_trusted(str(tmp_path)) is True

        # Verify marker file exists
        trust_marker = tmp_path / '.aworld' / 'trusted'
        assert trust_marker.exists()

    def test_get_workspace_from_config_path(self, tmp_path):
        """TC-TRUST-006: Extract workspace path from config file path."""
        # Valid config path
        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        workspace = WorkspaceTrust.get_workspace_from_config_path(str(config_path))
        assert workspace == str(tmp_path)

        # Nested project
        nested_project = tmp_path / 'projects' / 'myproject'
        config_path = nested_project / '.aworld' / 'hooks.yaml'
        workspace = WorkspaceTrust.get_workspace_from_config_path(str(config_path))
        assert workspace == str(nested_project)

        # Invalid path (no .aworld)
        invalid_path = tmp_path / 'some_file.yaml'
        workspace = WorkspaceTrust.get_workspace_from_config_path(str(invalid_path))
        assert workspace is None


class TestHookFactoryTrustIntegration:
    """Test HookFactory integration with WorkspaceTrust."""

    def test_blocks_untrusted_workspace(self, tmp_path):
        """TC-TRUST-004: HookFactory blocks loading hooks from untrusted workspace."""
        # Create hooks config in untrusted workspace
        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)

        config = {
            'version': '2',
            'hooks': {
                'before_tool_call': [
                    {
                        'name': 'malicious_hook',
                        'type': 'command',
                        'command': 'echo "malicious"',
                        'enabled': True
                    }
                ]
            }
        }

        with open(config_path, 'w') as f:
            yaml.safe_dump(config, f)

        # Load hooks - should return empty dict (blocked)
        hooks = HookManager.load_config_hooks(str(config_path))
        assert hooks == {}
        assert 'before_tool_call' not in hooks

    def test_allows_trusted_workspace(self, tmp_path):
        """TC-TRUST-005: HookFactory allows loading hooks from trusted workspace."""
        # Mark workspace as trusted
        trust_marker = tmp_path / '.aworld' / 'trusted'
        trust_marker.parent.mkdir(parents=True, exist_ok=True)
        trust_marker.touch()

        # Create hooks config
        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config = {
            'version': '2',
            'hooks': {
                'before_tool_call': [
                    {
                        'name': 'safe_hook',
                        'type': 'command',
                        'command': 'echo "safe"',
                        'enabled': True
                    }
                ]
            }
        }

        with open(config_path, 'w') as f:
            yaml.safe_dump(config, f)

        # Load hooks - should succeed
        hooks = HookManager.load_config_hooks(str(config_path))
        assert 'before_tool_call' in hooks
        assert len(hooks['before_tool_call']) == 1

    def test_trust_all_override_for_loading(self, tmp_path):
        """Test AWORLD_TRUST_ALL_WORKSPACES allows loading from any workspace."""
        # Create hooks config in untrusted workspace
        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)

        config = {
            'version': '2',
            'hooks': {
                'user_input_received': [
                    {
                        'name': 'dev_hook',
                        'type': 'command',
                        'command': 'echo "dev"',
                        'enabled': True
                    }
                ]
            }
        }

        with open(config_path, 'w') as f:
            yaml.safe_dump(config, f)

        # Without override - blocked
        hooks = HookManager.load_config_hooks(str(config_path))
        assert hooks == {}

        # With override - allowed
        with patch.dict(os.environ, {'AWORLD_TRUST_ALL_WORKSPACES': 'true'}):
            # P0-4: Clear cache for this specific config path
            if str(config_path) in HookManager._config_hooks_cache:
                del HookManager._config_hooks_cache[str(config_path)]

            hooks = HookManager.load_config_hooks(str(config_path))
            assert 'user_input_received' in hooks
            assert len(hooks['user_input_received']) == 1
