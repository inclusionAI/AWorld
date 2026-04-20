"""End-to-end tests for P1 legacy protocol compatibility.

This test suite verifies that legacy hook scripts using `prevent_continuation`
field continue to work correctly after the P0-3/P0-4 fixes.

Test Coverage:
- TC-LEGACY-E2E-001: Legacy hook script with prevent_continuation: true blocks execution
- TC-LEGACY-E2E-002: Legacy hook script with prevent_continuation: false allows execution
- TC-LEGACY-E2E-003: Mixed legacy and new protocol hooks work together
"""

import tempfile
import yaml
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aworld.core.context.amni import AmniContext
from aworld.core.event.base import Message
from aworld.runners.hook.hook_factory import HookManager
from aworld.runners.hook.hooks import HookPoint
from aworld.runners.hook.utils import run_hooks


class TestLegacyProtocolE2E:
    """End-to-end test for legacy protocol compatibility."""

    @pytest.mark.asyncio
    async def test_legacy_prevent_continuation_blocks_execution(self, tmp_path, monkeypatch):
        """TC-LEGACY-E2E-001: Legacy hook script blocks execution"""
        # 测试隔离
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        # Setup: Create workspace with legacy deny hook
        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)

        trust_marker = tmp_path / '.aworld' / 'trusted'
        trust_marker.touch()

        # Legacy hook script using prevent_continuation: true
        legacy_deny_script = tmp_path / 'legacy_deny_hook.sh'
        legacy_deny_script.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "prevent_continuation": true,
    "systemMessage": "Blocked by legacy hook"
}
EOF
""")
        legacy_deny_script.chmod(0o755)

        config = {
            'version': '2',
            'hooks': {
                'user_input_received': [
                    {
                        'name': 'legacy_deny_hook',
                        'type': 'command',
                        'command': str(legacy_deny_script),
                        'enabled': True
                    }
                ]
            }
        }

        with open(config_path, 'w') as f:
            yaml.safe_dump(config, f)

        # Clear cache and load hooks
        HookManager._config_hooks_cache = {}
        HookManager.load_config_hooks(str(config_path))

        # Create mock executor
        mock_executor = AsyncMock(return_value="mock response")

        # Simulate console hook processing
        context = AmniContext()
        user_input = "test input"

        should_execute = True

        user_input_msg = Message(
            category='user_input',
            payload={'content': user_input},
            sender='user',
            session_id='test_session'
        )
        user_input_msg.context = context

        async for hook_result in run_hooks(
            context=context,
            hook_point=HookPoint.USER_INPUT_RECEIVED,
            hook_from='cli',
            payload=user_input,
            message=user_input_msg
        ):
            if hook_result and hasattr(hook_result, 'headers'):
                permission_decision = hook_result.headers.get('permission_decision')
                if permission_decision == 'deny':
                    should_execute = False
                    break

        # Execute only if allowed
        if should_execute:
            await mock_executor(user_input)

        # CRITICAL ASSERTION: Executor should NOT have been called
        assert mock_executor.call_count == 0, \
            "Legacy prevent_continuation: true should block executor"

    @pytest.mark.asyncio
    async def test_legacy_prevent_continuation_allows_execution(self, tmp_path, monkeypatch):
        """TC-LEGACY-E2E-002: Legacy hook script allows execution"""
        # 测试隔离
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        # Setup: Create workspace with legacy allow hook
        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)

        trust_marker = tmp_path / '.aworld' / 'trusted'
        trust_marker.touch()

        # Legacy hook script using prevent_continuation: false
        legacy_allow_script = tmp_path / 'legacy_allow_hook.sh'
        legacy_allow_script.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "prevent_continuation": false,
    "systemMessage": "Allowed by legacy hook"
}
EOF
""")
        legacy_allow_script.chmod(0o755)

        config = {
            'version': '2',
            'hooks': {
                'user_input_received': [
                    {
                        'name': 'legacy_allow_hook',
                        'type': 'command',
                        'command': str(legacy_allow_script),
                        'enabled': True
                    }
                ]
            }
        }

        with open(config_path, 'w') as f:
            yaml.safe_dump(config, f)

        # Clear cache and load hooks
        HookManager._config_hooks_cache = {}
        HookManager.load_config_hooks(str(config_path))

        # Create mock executor
        mock_executor = AsyncMock(return_value="mock response")

        # Simulate console hook processing
        context = AmniContext()
        user_input = "test input"

        should_execute = True

        user_input_msg = Message(
            category='user_input',
            payload={'content': user_input},
            sender='user',
            session_id='test_session'
        )
        user_input_msg.context = context

        async for hook_result in run_hooks(
            context=context,
            hook_point=HookPoint.USER_INPUT_RECEIVED,
            hook_from='cli',
            payload=user_input,
            message=user_input_msg
        ):
            if hook_result and hasattr(hook_result, 'headers'):
                permission_decision = hook_result.headers.get('permission_decision')
                if permission_decision == 'deny':
                    should_execute = False
                    break

        # Execute only if allowed
        if should_execute:
            await mock_executor(user_input)

        # CRITICAL ASSERTION: Executor SHOULD have been called
        assert mock_executor.call_count == 1, \
            "Legacy prevent_continuation: false should allow executor"
        mock_executor.assert_called_once_with(user_input)

    @pytest.mark.asyncio
    async def test_mixed_legacy_and_new_protocol_hooks(self, tmp_path, monkeypatch):
        """TC-LEGACY-E2E-003: Mixed legacy and new protocol hooks"""
        # 测试隔离
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        # Setup: Create workspace with both legacy and new hooks
        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)

        trust_marker = tmp_path / '.aworld' / 'trusted'
        trust_marker.touch()

        # Legacy hook script
        legacy_script = tmp_path / 'legacy_hook.sh'
        legacy_script.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "prevent_continuation": false,
    "systemMessage": "Legacy hook passed"
}
EOF
""")
        legacy_script.chmod(0o755)

        # New protocol hook script
        new_script = tmp_path / 'new_hook.sh'
        new_script.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "permissionDecision": "deny",
    "permissionDecisionReason": "Blocked by new hook"
}
EOF
""")
        new_script.chmod(0o755)

        config = {
            'version': '2',
            'hooks': {
                'user_input_received': [
                    {
                        'name': 'legacy_hook',
                        'type': 'command',
                        'command': str(legacy_script),
                        'enabled': True
                    },
                    {
                        'name': 'new_hook',
                        'type': 'command',
                        'command': str(new_script),
                        'enabled': True
                    }
                ]
            }
        }

        with open(config_path, 'w') as f:
            yaml.safe_dump(config, f)

        # Clear cache and load hooks
        HookManager._config_hooks_cache = {}
        HookManager.load_config_hooks(str(config_path))

        # Create mock executor
        mock_executor = AsyncMock(return_value="mock response")

        # Simulate console hook processing
        context = AmniContext()
        user_input = "test input"

        should_execute = True

        user_input_msg = Message(
            category='user_input',
            payload={'content': user_input},
            sender='user',
            session_id='test_session'
        )
        user_input_msg.context = context

        async for hook_result in run_hooks(
            context=context,
            hook_point=HookPoint.USER_INPUT_RECEIVED,
            hook_from='cli',
            payload=user_input,
            message=user_input_msg
        ):
            if hook_result and hasattr(hook_result, 'headers'):
                permission_decision = hook_result.headers.get('permission_decision')
                if permission_decision == 'deny':
                    should_execute = False
                    break  # First deny wins

        # Execute only if allowed
        if should_execute:
            await mock_executor(user_input)

        # CRITICAL ASSERTION: New hook's deny should block execution
        assert mock_executor.call_count == 0, \
            "New protocol hook's deny should block execution (legacy hook's allow is ignored)"


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
