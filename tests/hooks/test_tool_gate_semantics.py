"""Unit tests for before_tool_call gate semantics (P0 fix).

Test Coverage:
- TC-GATE-001: Hook returns deny - tool execution should be blocked
- TC-GATE-002: Hook returns allow - tool execution should continue
- TC-GATE-003: Hook modifies input - tool receives modified input
- TC-GATE-004: No permission_decision - tool executes normally
"""

import asyncio
import tempfile
import yaml
from pathlib import Path
from typing import Tuple, Any, Dict

import pytest

from aworld.config.conf import ToolConfig
from aworld.core.common import ActionModel, Observation
from aworld.core.context.amni import AmniContext
from aworld.core.event.base import Message
from aworld.core.tool.base import AsyncTool, Tool, ToolExecutionDenied
from aworld.runners.hook.hook_factory import HookManager


class MockAsyncTool(AsyncTool):
    """Mock async tool for testing gate semantics."""

    def __init__(self, conf: ToolConfig, **kwargs):
        super().__init__(conf, **kwargs)
        self.execution_count = 0
        self.last_action = None

    async def reset(self, *, seed: int = None, options: Dict[str, str] = None):
        return Observation(content="reset"), {}

    async def do_step(self, action, **kwargs) -> Tuple[Any, float, bool, bool, Dict[str, Any]]:
        """Tool execution that should be blocked by deny hooks."""
        self.execution_count += 1
        self.last_action = action
        obs = Observation(content=f"Tool executed: {action[0].tool_name}")
        # Create proper action_result list to match Observation structure
        from aworld.core.common import ActionResult
        obs.action_result = [ActionResult(
            tool_call_id=action[0].tool_call_id,
            tool_name=action[0].tool_name,
            content=obs.content
        )]
        return (
            obs,
            1.0,
            False,
            False,
            {"execution_count": self.execution_count}
        )

    async def post_step(self, step_res, action, message, **kwargs):
        """Simplified post_step that avoids swarm access."""
        from aworld.core.event.base import AgentMessage

        if not step_res:
            raise Exception(f'{self.name()} no observation has been made.')

        step_res[0].from_agent_name = action[0].agent_name
        for idx, act in enumerate(action):
            step_res[0].action_result[idx].tool_call_id = act.tool_call_id
            step_res[0].action_result[idx].tool_name = act.tool_name

        context = message.context
        # Simplified version - always return AgentMessage
        result = AgentMessage(
            payload=step_res,
            caller=action[0].agent_name,
            sender=self.name(),
            receiver=action[0].agent_name,
            session_id=getattr(context, 'session_id', 'test_session'),
            headers={"context": context}
        )
        return result


class MockSyncTool(Tool):
    """Mock sync tool for testing parity with AsyncTool hook semantics."""

    def __init__(self, conf: ToolConfig, **kwargs):
        super().__init__(conf, **kwargs)
        self.execution_count = 0
        self.last_action = None

    def reset(self, *, seed: int = None, options: Dict[str, str] = None):
        return Observation(content="reset"), {}

    def do_step(self, action, **kwargs) -> Tuple[Any, float, bool, bool, Dict[str, Any]]:
        self.execution_count += 1
        self.last_action = action
        obs = Observation(content=f"Tool executed: {action[0].tool_name}")
        from aworld.core.common import ActionResult
        obs.action_result = [ActionResult(
            tool_call_id=action[0].tool_call_id,
            tool_name=action[0].tool_name,
            content=obs.content
        )]
        return (
            obs,
            1.0,
            False,
            False,
            {"execution_count": self.execution_count}
        )

    def post_step(self, step_res, action, message, **kwargs):
        from aworld.core.event.base import AgentMessage

        if not step_res:
            raise Exception(f'{self.name()} no observation has been made.')

        step_res[0].from_agent_name = action[0].agent_name
        for idx, act in enumerate(action):
            step_res[0].action_result[idx].tool_call_id = act.tool_call_id
            step_res[0].action_result[idx].tool_name = act.tool_name

        context = message.context
        return AgentMessage(
            payload=step_res,
            caller=action[0].agent_name,
            sender=self.name(),
            receiver=action[0].agent_name,
            session_id=getattr(context, 'session_id', 'test_session'),
            headers={"context": context}
        )


@pytest.fixture
def mock_tool():
    """Create a mock tool instance."""
    # Clear global hooks cache before each test
    HookManager._config_hooks_cache = {}

    conf = ToolConfig(name="mock_tool")
    tool = MockAsyncTool(conf=conf)
    return tool


@pytest.fixture
def mock_sync_tool():
    """Create a mock sync tool instance."""
    HookManager._config_hooks_cache = {}

    conf = ToolConfig(name="mock_sync_tool")
    tool = MockSyncTool(conf=conf)
    return tool


@pytest.fixture
def mock_context():
    """Create a mock context with task."""
    from aworld.core.task import Task

    context = AmniContext()
    # Create a minimal task to avoid AttributeError when accessing context.swarm
    task = Task(input="test", context=context)
    context._task = task
    context.task_id = task.id
    return context


class TestToolGateSemantics:
    """Test gate semantics for before_tool_call hook."""

    @pytest.mark.asyncio
    async def test_deny_blocks_execution(self, mock_tool, mock_context, tmp_path, monkeypatch):
        # 测试隔离：清除缓存和设置环境变量
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')
        mock_context.workspace_path = str(tmp_path)

        """TC-GATE-001: Hook returns deny - tool execution should be blocked."""
        # Create hooks config that denies all tool calls
        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Trust workspace first
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

        # P0-4: Clear cache for this specific config path
        if str(config_path) in HookManager._config_hooks_cache:
            del HookManager._config_hooks_cache[str(config_path)]
        HookManager.load_config_hooks(str(config_path))

        # Create action message
        action = [ActionModel(
            tool_name='mock_tool',
            action_name='test_action',
            params={},
            agent_name='test_agent',
            tool_call_id='test_call_1'
        )]

        message = Message(
            category='test',
            payload=action,
            sender='test_agent',
            session_id='test_session'
        )
        message.context = mock_context

        # Execute tool - should raise ToolExecutionDenied
        initial_count = mock_tool.execution_count
        with pytest.raises(ToolExecutionDenied) as exc_info:
            await mock_tool.step(message)

        # Verify exception details
        assert exc_info.value.tool_name == 'mock_tool'
        assert 'Tool blocked by test' in exc_info.value.reason

        # Verify tool was NOT executed
        assert mock_tool.execution_count == initial_count, "Tool should not have been executed"

    @pytest.mark.asyncio
    async def test_allow_continues_execution(self, mock_tool, mock_context, tmp_path, monkeypatch):
        # 测试隔离：清除缓存和设置环境变量
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')
        mock_context.workspace_path = str(tmp_path)

        """TC-GATE-002: Hook returns allow - tool execution should continue."""
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

        # P0-4: Clear cache for this specific config path
        if str(config_path) in HookManager._config_hooks_cache:
            del HookManager._config_hooks_cache[str(config_path)]
        HookManager.load_config_hooks(str(config_path))

        # Create action message
        action = [ActionModel(
            tool_name='mock_tool',
            action_name='test_action',
            params={},
            agent_name='test_agent',
            tool_call_id='test_call_2'
        )]

        message = Message(
            category='test',
            payload=action,
            sender='test_agent',
            session_id='test_session'
        )
        message.context = mock_context

        # Execute tool
        initial_count = mock_tool.execution_count
        result = await mock_tool.step(message)

        # Verify tool WAS executed
        assert mock_tool.execution_count == initial_count + 1, "Tool should have been executed"

    @pytest.mark.asyncio
    async def test_no_permission_executes_normally(self, mock_tool, mock_context, tmp_path, monkeypatch):
        # 测试隔离：清除缓存和设置环境变量
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')
        mock_context.workspace_path = str(tmp_path)

        """TC-GATE-004: No permission_decision - tool executes normally."""
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

        # P0-4: Clear cache for this specific config path
        if str(config_path) in HookManager._config_hooks_cache:
            del HookManager._config_hooks_cache[str(config_path)]
        HookManager.load_config_hooks(str(config_path))

        # Create action message
        action = [ActionModel(
            tool_name='mock_tool',
            action_name='test_action',
            params={},
            agent_name='test_agent',
            tool_call_id='test_call_3'
        )]

        message = Message(
            category='test',
            payload=action,
            sender='test_agent',
            session_id='test_session'
        )
        message.context = mock_context

        # Execute tool
        initial_count = mock_tool.execution_count
        result = await mock_tool.step(message)

        # Verify tool WAS executed (observe-only hook doesn't block)
        assert mock_tool.execution_count == initial_count + 1, "Tool should execute with observe-only hook"

    @pytest.mark.asyncio
    async def test_claude_pre_tool_use_blocks_destructive_rm_rf(self, mock_tool, mock_context, tmp_path, monkeypatch):
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')
        mock_context.workspace_path = str(tmp_path)

        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / '.aworld' / 'trusted').touch()

        deny_script = tmp_path / 'block_rm_rf.sh'
        deny_script.write_text("""#!/bin/bash
if echo "$AWORLD_MESSAGE_JSON" | grep -q 'rm -rf /tmp/build'; then
cat << 'EOF'
{
    "continue": true,
    "permission_decision": "deny",
    "permission_decision_reason": "Destructive command blocked by hook"
}
EOF
else
cat << 'EOF'
{
    "continue": true
}
EOF
fi
""")
        deny_script.chmod(0o755)

        config = {
            'version': '2',
            'hooks': {
                'before_tool_call': [
                    {
                        'name': 'block_rm_rf',
                        'type': 'command',
                        'command': str(deny_script),
                        'enabled': True
                    }
                ]
            }
        }

        with open(config_path, 'w') as f:
            yaml.safe_dump(config, f)

        if str(config_path) in HookManager._config_hooks_cache:
            del HookManager._config_hooks_cache[str(config_path)]
        HookManager.load_config_hooks(str(config_path))

        action = [ActionModel(
            tool_name='mock_tool',
            action_name='bash',
            params={'command': 'rm -rf /tmp/build'},
            agent_name='test_agent',
            tool_call_id='test_call_rm_rf'
        )]

        message = Message(
            category='test',
            payload=action,
            sender='test_agent',
            session_id='test_session'
        )
        message.context = mock_context

        initial_count = mock_tool.execution_count
        with pytest.raises(ToolExecutionDenied) as exc_info:
            await mock_tool.step(message)

        assert 'Destructive command blocked by hook' in exc_info.value.reason
        assert mock_tool.execution_count == initial_count

    @pytest.mark.asyncio
    async def test_claude_post_tool_use_adds_audit_context(self, mock_tool, mock_context, tmp_path, monkeypatch):
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')
        mock_context.workspace_path = str(tmp_path)

        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / '.aworld' / 'trusted').touch()

        audit_script = tmp_path / 'post_tool_audit.sh'
        audit_script.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "system_message": "Tests queued after file write",
    "updated_output": {
        "info": {
            "audit_logged": true,
            "audit_source": "after_tool_call"
        }
    }
}
EOF
""")
        audit_script.chmod(0o755)

        config = {
            'version': '2',
            'hooks': {
                'after_tool_call': [
                    {
                        'name': 'post_tool_audit',
                        'type': 'command',
                        'command': str(audit_script),
                        'enabled': True
                    }
                ]
            }
        }

        with open(config_path, 'w') as f:
            yaml.safe_dump(config, f)

        if str(config_path) in HookManager._config_hooks_cache:
            del HookManager._config_hooks_cache[str(config_path)]
        HookManager.load_config_hooks(str(config_path))

        action = [ActionModel(
            tool_name='mock_tool',
            action_name='write_file',
            params={'path': 'src/app.ts'},
            agent_name='test_agent',
            tool_call_id='test_call_post_tool'
        )]

        message = Message(
            category='test',
            payload=action,
            sender='test_agent',
            session_id='test_session'
        )
        message.context = mock_context

        initial_count = mock_tool.execution_count
        result = await mock_tool.step(message)

        assert mock_tool.execution_count == initial_count + 1
        assert result.headers.get('system_message') == 'Tests queued after file write'
        assert result.payload[4]['audit_logged'] is True
        assert result.payload[4]['audit_source'] == 'after_tool_call'

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tool_fixture_name", "tool_name"),
        [
            ("mock_tool", "mock_tool"),
            ("mock_sync_tool", "mock_sync_tool"),
        ],
    )
    async def test_tool_step_applies_hook_rewrites_and_output_updates_for_sync_and_async(
        self,
        request,
        tool_fixture_name,
        tool_name,
        mock_context,
        tmp_path,
        monkeypatch,
    ):
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')
        mock_context.workspace_path = str(tmp_path)

        tool = request.getfixturevalue(tool_fixture_name)

        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / '.aworld' / 'trusted').touch()

        rewrite_script = tmp_path / 'rewrite_input.sh'
        rewrite_script.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "system_message": "Scoped cleanup request accepted",
    "updated_input": {
        "actions": [
            {
                "tool_name": "__TOOL_NAME__",
                "action_name": "safe_cleanup",
                "params": {
                    "path": "./tmp/build",
                    "mode": "scoped"
                },
                "agent_name": "test_agent",
                "tool_call_id": "rewritten_call"
            }
        ]
    }
}
EOF
""".replace("__TOOL_NAME__", tool_name))
        rewrite_script.chmod(0o755)

        post_script = tmp_path / 'rewrite_output.sh'
        post_script.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "updated_output": {
        "observation": {
            "content": "Hook-adjusted output",
            "action_result": [
                {
                    "tool_call_id": "rewritten_call",
                    "tool_name": "__TOOL_NAME__",
                    "content": "Hook-adjusted output",
                    "success": true,
                    "is_done": true
                }
            ]
        },
        "info": {
            "audit_logged": true,
            "audit_source": "shared-helper-test"
        }
    }
}
EOF
""".replace("__TOOL_NAME__", tool_name))
        post_script.chmod(0o755)

        config = {
            'version': '2',
            'hooks': {
                'before_tool_call': [
                    {
                        'name': 'rewrite_input',
                        'type': 'command',
                        'command': str(rewrite_script),
                        'enabled': True
                    }
                ],
                'after_tool_call': [
                    {
                        'name': 'rewrite_output',
                        'type': 'command',
                        'command': str(post_script),
                        'enabled': True
                    }
                ]
            }
        }

        with open(config_path, 'w') as f:
            yaml.safe_dump(config, f)

        if str(config_path) in HookManager._config_hooks_cache:
            del HookManager._config_hooks_cache[str(config_path)]
        HookManager.load_config_hooks(str(config_path))

        action = [ActionModel(
            tool_name=tool_name,
            action_name='cleanup',
            params={'path': '.'},
            agent_name='test_agent',
            tool_call_id='original_call'
        )]

        message = Message(
            category='test',
            payload=action,
            sender='test_agent',
            session_id='test_session'
        )
        message.context = mock_context

        step_result = tool.step(message)
        if asyncio.iscoroutine(step_result):
            result = await step_result
        else:
            result = step_result

        assert tool.execution_count == 1
        assert tool.last_action[0].action_name == 'safe_cleanup'
        assert tool.last_action[0].tool_call_id == 'rewritten_call'
        assert tool.last_action[0].params == {'path': './tmp/build', 'mode': 'scoped'}
        assert result.headers.get('system_message') == 'Scoped cleanup request accepted'
        assert result.payload[0].content == 'Hook-adjusted output'
        assert result.payload[4]['audit_logged'] is True
        assert result.payload[4]['audit_source'] == 'shared-helper-test'
