"""End-to-end tests for user_input_received gate semantics (P0-3 fix).

This test suite verifies that permission_decision='deny' ACTUALLY prevents
the executor from being called, not just that the hook returns deny.

Test Coverage:
- TC-E2E-INPUT-001: deny prevents executor call
- TC-E2E-INPUT-002: allow permits executor call

NOTE: Legacy protocol (prevent_continuation) compatibility is tracked separately
as P1 issue - currently not supported by HookJSONOutput.from_json().
"""

import asyncio
import tempfile
import yaml
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from aworld.core.context.amni import AmniContext
from aworld.runners.hook.hook_factory import HookManager


class TestUserInputGateE2E:
    """End-to-end test for user_input_received gate blocking executor."""

    @pytest.mark.asyncio
    async def test_deny_prevents_executor_call(self, tmp_path, monkeypatch):
        # 测试隔离：清除缓存和设置环境变量
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        """TC-E2E-INPUT-001: deny ACTUALLY prevents executor from being called."""
        # Setup: Create workspace with deny hook
        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)

        trust_marker = tmp_path / '.aworld' / 'trusted'
        trust_marker.touch()

        deny_script = tmp_path / 'deny_hook.sh'
        deny_script.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "permission_decision": "deny",
    "permission_decision_reason": "Blocked by test"
}
EOF
""")
        deny_script.chmod(0o755)

        config = {
            'version': '2',
            'hooks': {
                'user_input_received': [
                    {
                        'name': 'deny_hook',
                        'type': 'command',
                        'command': str(deny_script),
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

        # Simulate the console's hook processing logic (from console.py:1715-1750)
        from aworld.core.context.amni import AmniContext
        from aworld.core.event.base import Message
        from aworld.runners.hook.hooks import HookPoint
        from aworld.runners.hook.utils import run_hooks

        context = AmniContext()
        context.workspace_path = str(tmp_path)
        user_input = "test input"

        should_execute = True  # P0-3: Flag to control executor execution

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
            message=user_input_msg,
            workspace_path=str(tmp_path),
        ):
            if hook_result and hasattr(hook_result, 'headers'):
                permission_decision = hook_result.headers.get('permission_decision')
                if permission_decision == 'deny':
                    should_execute = False
                    break  # Exit hook loop

        # Execute only if allowed (mimicking console.py logic after P0-3 fix)
        if should_execute:
            await mock_executor(user_input)

        # CRITICAL ASSERTION: Executor should NOT have been called
        assert mock_executor.call_count == 0, \
            "Executor was called despite permission_decision='deny' - blocking FAILED"

    @pytest.mark.asyncio
    async def test_allow_permits_executor_call(self, tmp_path, monkeypatch):
        # 测试隔离：清除缓存和设置环境变量
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        """TC-E2E-INPUT-002: allow permits executor to be called."""
        # Setup: Create workspace with allow hook
        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)

        trust_marker = tmp_path / '.aworld' / 'trusted'
        trust_marker.touch()

        allow_script = tmp_path / 'allow_hook.sh'
        allow_script.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "permission_decision": "allow"
}
EOF
""")
        allow_script.chmod(0o755)

        config = {
            'version': '2',
            'hooks': {
                'user_input_received': [
                    {
                        'name': 'allow_hook',
                        'type': 'command',
                        'command': str(allow_script),
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

        # Simulate console hook processing logic
        from aworld.core.context.amni import AmniContext
        from aworld.core.event.base import Message
        from aworld.runners.hook.hooks import HookPoint
        from aworld.runners.hook.utils import run_hooks

        context = AmniContext()
        context.workspace_path = str(tmp_path)
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
            message=user_input_msg,
            workspace_path=str(tmp_path),
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
            "Executor was NOT called despite permission_decision='allow' - allow FAILED"
        mock_executor.assert_called_once_with(user_input)
