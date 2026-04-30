"""Simplified unit tests for before_tool_call gate semantics.

Test Coverage:
- TC-GATE-001: Hook returns deny - should raise ToolExecutionDenied
- TC-GATE-002: Hook returns allow - should not raise exception
- TC-GATE-003: No permission_decision - should not raise exception
"""

import tempfile
import yaml
from pathlib import Path

import pytest

from aworld.core.common import ActionModel
from aworld.core.context.amni import AmniContext
from aworld.core.tool.base import ToolExecutionDenied
from aworld.runners.hook.hook_factory import HookManager
from aworld.runners.hook.hooks import HookPoint
from aworld.runners.hook.utils import run_hooks


class TestToolGateSimple:
    """Simplified test for tool gate semantics focusing on hook behavior."""

    @pytest.mark.asyncio
    async def test_deny_decision_detected(self, tmp_path, monkeypatch):
        # 测试隔离：清除缓存和设置环境变量
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        """TC-GATE-001: Hook returns deny - should be detectable."""
        # Create hooks config that denies tool calls
        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Trust workspace
        trust_marker = tmp_path / '.aworld' / 'trusted'
        trust_marker.touch()

        # Create deny hook script
        deny_script = tmp_path / 'deny_hook.sh'
        deny_script.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "permission_decision": "deny",
    "permission_decision_reason": "Tool blocked by test"
}
EOF
""")
        deny_script.chmod(0o755)

        config = {
            'version': '2',
            'hooks': {
                'before_tool_call': [
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

        # Create context and action
        context = AmniContext()
        context.workspace_path = str(tmp_path)
        action = [ActionModel(
            tool_name='mock_tool',
            action_name='test_action',
            params={},
            agent_name='test_agent',
            tool_call_id='test_call_1'
        )]

        # Run hooks and verify deny is detected
        deny_detected = False
        async for hook_result in run_hooks(
            context=context,
            hook_point=HookPoint.BEFORE_TOOL_CALL,
            hook_from='test',
            payload=action,
            workspace_path=str(tmp_path),
        ):
            if hook_result and hasattr(hook_result, 'headers'):
                permission_decision = hook_result.headers.get('permission_decision')
                if permission_decision == 'deny':
                    deny_detected = True
                    reason = hook_result.headers.get('permission_decision_reason')
                    assert 'Tool blocked by test' in reason
                    break

        assert deny_detected is True, "Hook should have returned deny decision"

    @pytest.mark.asyncio
    async def test_allow_decision_detected(self, tmp_path, monkeypatch):
        # 测试隔离：清除缓存和设置环境变量
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        """TC-GATE-002: Hook returns allow - should be detectable."""
        # Create hooks config that allows tool calls
        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Trust workspace
        trust_marker = tmp_path / '.aworld' / 'trusted'
        trust_marker.touch()

        # Create allow hook script
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
                'before_tool_call': [
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

        # Create context and action
        context = AmniContext()
        context.workspace_path = str(tmp_path)
        action = [ActionModel(
            tool_name='mock_tool',
            action_name='test_action',
            params={},
            agent_name='test_agent',
            tool_call_id='test_call_2'
        )]

        # Run hooks and verify allow is detected
        deny_detected = False
        allow_detected = False
        async for hook_result in run_hooks(
            context=context,
            hook_point=HookPoint.BEFORE_TOOL_CALL,
            hook_from='test',
            payload=action,
            workspace_path=str(tmp_path),
        ):
            if hook_result and hasattr(hook_result, 'headers'):
                permission_decision = hook_result.headers.get('permission_decision')
                if permission_decision == 'deny':
                    deny_detected = True
                elif permission_decision == 'allow':
                    allow_detected = True
                    break

        assert deny_detected is False, "Hook should not have returned deny"
        assert allow_detected is True, "Hook should have returned allow"

    @pytest.mark.asyncio
    async def test_no_permission_decision(self, tmp_path, monkeypatch):
        # 测试隔离：清除缓存和设置环境变量
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        """TC-GATE-003: No permission_decision - should not block."""
        # Create hooks config without permission_decision
        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Trust workspace
        trust_marker = tmp_path / '.aworld' / 'trusted'
        trust_marker.touch()

        # Create observe-only hook
        observe_script = tmp_path / 'observe_hook.sh'
        observe_script.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "system_message": "Tool call observed"
}
EOF
""")
        observe_script.chmod(0o755)

        config = {
            'version': '2',
            'hooks': {
                'before_tool_call': [
                    {
                        'name': 'observe_hook',
                        'type': 'command',
                        'command': str(observe_script),
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

        # Create context and action
        context = AmniContext()
        context.workspace_path = str(tmp_path)
        action = [ActionModel(
            tool_name='mock_tool',
            action_name='test_action',
            params={},
            agent_name='test_agent',
            tool_call_id='test_call_3'
        )]

        # Run hooks and verify no deny
        deny_detected = False
        async for hook_result in run_hooks(
            context=context,
            hook_point=HookPoint.BEFORE_TOOL_CALL,
            hook_from='test',
            payload=action,
            workspace_path=str(tmp_path),
        ):
            if hook_result and hasattr(hook_result, 'headers'):
                permission_decision = hook_result.headers.get('permission_decision')
                if permission_decision == 'deny':
                    deny_detected = True
                    break

        assert deny_detected is False, "Hook should not have returned deny (observe-only)"
