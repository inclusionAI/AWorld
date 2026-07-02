"""Unit tests for user_input_received gate semantics (P0-3 fix).

Test Coverage:
- TC-INPUT-001: Hook returns deny - executor should be blocked
- TC-INPUT-002: Hook returns allow - executor should continue
- TC-INPUT-003: Hook modifies input - executor receives modified input
"""

import asyncio
import tempfile
import yaml
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aworld.core.context.amni import AmniContext
from aworld.core.event.base import Message
from aworld.runners.hook.hook_factory import HookManager
from aworld.runners.hook.hooks import HookPoint
from aworld.runners.hook.utils import run_hooks


class TestUserInputGateSemantics:
    """Test gate semantics for user_input_received hook."""

    @pytest.mark.asyncio
    async def test_deny_blocks_executor(self, tmp_path, monkeypatch):
        # 测试隔离：清除缓存和设置环境变量
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        """TC-INPUT-001: Hook returns deny - executor should be blocked."""
        # Create hooks config that denies user input
        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Trust workspace
        trust_marker = tmp_path / '.aworld' / 'trusted'
        trust_marker.touch()

        # Create deny hook script
        deny_script = tmp_path / 'deny_input_hook.sh'
        deny_script.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "permission_decision": "deny",
    "permission_decision_reason": "User input contains forbidden keyword"
}
EOF
""")
        deny_script.chmod(0o755)

        config = {
            'version': '2',
            'hooks': {
                'user_input_received': [
                    {
                        'name': 'deny_input_hook',
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

        # Create context and test input
        context = AmniContext()
        context.workspace_path = str(tmp_path)
        user_input = "dangerous command"

        # Simulate console behavior: run hooks and check permission_decision
        hook_blocked = False
        async for hook_result in run_hooks(
            context=context,
            hook_point=HookPoint.USER_INPUT_RECEIVED,
            hook_from='cli',
            payload=user_input,
            workspace_path=str(tmp_path),
        ):
            if hook_result and hasattr(hook_result, 'headers'):
                permission_decision = hook_result.headers.get('permission_decision')
                if permission_decision == 'deny':
                    hook_blocked = True
                    deny_reason = hook_result.headers.get('permission_decision_reason', 'Blocked')
                    assert 'forbidden keyword' in deny_reason
                    break

        # Verify executor would be blocked
        assert hook_blocked is True, "Hook should have blocked user input"

    @pytest.mark.asyncio
    async def test_allow_continues_executor(self, tmp_path, monkeypatch):
        # 测试隔离：清除缓存和设置环境变量
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        """TC-INPUT-002: Hook returns allow - executor should continue."""
        # Create hooks config that allows user input
        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Trust workspace
        trust_marker = tmp_path / '.aworld' / 'trusted'
        trust_marker.touch()

        # Create allow hook script
        allow_script = tmp_path / 'allow_input_hook.sh'
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
                        'name': 'allow_input_hook',
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

        # Create context and test input
        context = AmniContext()
        context.workspace_path = str(tmp_path)
        user_input = "safe command"

        # Simulate console behavior: run hooks and check permission_decision
        hook_blocked = False
        async for hook_result in run_hooks(
            context=context,
            hook_point=HookPoint.USER_INPUT_RECEIVED,
            hook_from='cli',
            payload=user_input,
            workspace_path=str(tmp_path),
        ):
            if hook_result and hasattr(hook_result, 'headers'):
                permission_decision = hook_result.headers.get('permission_decision')
                if permission_decision == 'deny':
                    hook_blocked = True
                    break

        # Verify executor would NOT be blocked
        assert hook_blocked is False, "Hook should have allowed user input"

    @pytest.mark.asyncio
    async def test_hook_modifies_input(self, tmp_path, monkeypatch):
        # 测试隔离：清除缓存和设置环境变量
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        """TC-INPUT-003: Hook modifies input - executor receives modified input."""
        # Create hooks config that modifies user input
        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Trust workspace
        trust_marker = tmp_path / '.aworld' / 'trusted'
        trust_marker.touch()

        # Create modify hook script
        modify_script = tmp_path / 'modify_input_hook.sh'
        modify_script.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "updated_input": "modified: original input"
}
EOF
""")
        modify_script.chmod(0o755)

        config = {
            'version': '2',
            'hooks': {
                'user_input_received': [
                    {
                        'name': 'modify_input_hook',
                        'type': 'command',
                        'command': str(modify_script),
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

        # Create context and test input
        context = AmniContext()
        context.workspace_path = str(tmp_path)
        user_input = "original input"

        # Simulate console behavior: run hooks and check updated_input
        modified_input = user_input
        async for hook_result in run_hooks(
            context=context,
            hook_point=HookPoint.USER_INPUT_RECEIVED,
            hook_from='cli',
            payload=user_input,
            workspace_path=str(tmp_path),
        ):
            if hook_result and hasattr(hook_result, 'headers'):
                updated_input = hook_result.headers.get('updated_input')
                if updated_input:
                    if isinstance(updated_input, str):
                        modified_input = updated_input
                        break

        # Verify input was modified
        assert modified_input == "modified: original input", "Hook should have modified input"
        assert modified_input != user_input, "Input should be different from original"

    @pytest.mark.asyncio
    async def test_multiple_hooks_chain_updated_input_between_hooks(self, tmp_path, monkeypatch):
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / '.aworld' / 'trusted').touch()

        hook1_script = tmp_path / 'hook1.sh'
        hook1_script.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "updated_input": {"content": "[Validated] user query"}
}
EOF
""")
        hook1_script.chmod(0o755)

        hook2_script = tmp_path / 'hook2.sh'
        log_file = tmp_path / 'hook2.log'
        hook2_script.write_text(f"""#!/bin/bash
echo "$AWORLD_MESSAGE_JSON" >> "{log_file}"
cat << 'EOF'
{{
    "continue": true,
    "updated_input": "Processed input"
}}
EOF
""")
        hook2_script.chmod(0o755)

        config = {
            'version': '2',
            'hooks': {
                'user_input_received': [
                    {
                        'name': 'validator_hook',
                        'type': 'command',
                        'command': str(hook1_script),
                        'enabled': True
                    },
                    {
                        'name': 'processor_hook',
                        'type': 'command',
                        'command': str(hook2_script),
                        'enabled': True
                    }
                ]
            }
        }

        with open(config_path, 'w') as f:
            yaml.safe_dump(config, f)

        HookManager._config_hooks_cache = {}
        HookManager.load_config_hooks(str(config_path))

        context = AmniContext()
        context.workspace_path = str(tmp_path)
        user_input = "Original input"
        user_input_msg = Message(
            category='user_input',
            payload=user_input,
            session_id='test-session-123',
            sender='cli_user'
        )
        user_input_msg.context = context

        modified_input = user_input
        async for hook_result in run_hooks(
            context=context,
            hook_point=HookPoint.USER_INPUT_RECEIVED,
            hook_from='cli',
            message=user_input_msg,
            workspace_path=str(tmp_path)
        ):
            if hook_result and hasattr(hook_result, 'headers'):
                updated_input = hook_result.headers.get('updated_input')
                if isinstance(updated_input, dict) and 'content' in updated_input:
                    modified_input = updated_input['content']
                elif isinstance(updated_input, str):
                    modified_input = updated_input

        assert modified_input == "Processed input"
        assert log_file.exists()
        log_text = log_file.read_text()
        assert '"payload": "[Validated] user query"' in log_text
