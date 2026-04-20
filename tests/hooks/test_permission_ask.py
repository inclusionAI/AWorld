"""测试 permission_decision='ask' 模式"""

import os
import sys
from unittest.mock import patch

import pytest

from aworld.core.context.base import Context
from aworld.core.context.session import Session
from aworld.core.event.base import Message
from aworld.runners.hook.v2.permission import PermissionDecisionHandler, get_permission_handler
from aworld.runners.hook.v2.wrappers import CommandHookWrapper


class TestPermissionDecisionHandler:
    """测试 PermissionDecisionHandler"""

    def test_detect_interactive_tty(self):
        """测试 TTY 检测（交互式环境）"""
        with patch('sys.stdin.isatty', return_value=True):
            handler = PermissionDecisionHandler()
            assert handler._is_interactive is True

    def test_detect_interactive_non_tty(self):
        """测试非 TTY 检测（非交互式环境）"""
        with patch('sys.stdin.isatty', return_value=False):
            handler = PermissionDecisionHandler()
            assert handler._is_interactive is False

    @pytest.mark.asyncio
    async def test_resolve_permission_allow(self):
        """测试直接 allow 决策"""
        handler = PermissionDecisionHandler()
        decision, reason = await handler.resolve_permission(
            decision='allow',
            reason='Test allow'
        )
        assert decision == 'allow'
        assert 'Test allow' in reason or "allow" in reason

    @pytest.mark.asyncio
    async def test_resolve_permission_deny(self):
        """测试直接 deny 决策"""
        handler = PermissionDecisionHandler()
        decision, reason = await handler.resolve_permission(
            decision='deny',
            reason='Test deny'
        )
        assert decision == 'deny'
        assert 'Test deny' in reason or "deny" in reason

    @pytest.mark.asyncio
    async def test_resolve_permission_ask_non_interactive(self):
        """测试 ask 决策在非交互式环境下自动降级为 deny"""
        with patch('sys.stdin.isatty', return_value=False):
            handler = PermissionDecisionHandler()
            decision, reason = await handler.resolve_permission(
                decision='ask',
                reason='Need permission'
            )
            assert decision == 'deny'
            assert 'non-interactive' in reason.lower()

    @pytest.mark.asyncio
    async def test_resolve_permission_ask_env_override_allow(self):
        """测试环境变量 AWORLD_PERMISSION_MODE=allow 覆盖"""
        with patch.dict(os.environ, {'AWORLD_PERMISSION_MODE': 'allow'}):
            handler = PermissionDecisionHandler()
            decision, reason = await handler.resolve_permission(
                decision='ask',
                reason='Need permission'
            )
            assert decision == 'allow'
            assert 'AWORLD_PERMISSION_MODE' in reason

    @pytest.mark.asyncio
    async def test_resolve_permission_ask_env_override_deny(self):
        """测试环境变量 AWORLD_PERMISSION_MODE=deny 覆盖"""
        with patch.dict(os.environ, {'AWORLD_PERMISSION_MODE': 'deny'}):
            handler = PermissionDecisionHandler()
            decision, reason = await handler.resolve_permission(
                decision='ask',
                reason='Need permission'
            )
            assert decision == 'deny'
            assert 'AWORLD_PERMISSION_MODE' in reason

    @pytest.mark.asyncio
    async def test_resolve_permission_ask_interactive_not_implemented(self):
        """测试 ask 决策在交互式环境下（未设置回调，返回 deny）"""
        with patch('sys.stdin.isatty', return_value=True):
            handler = PermissionDecisionHandler()
            decision, reason = await handler.resolve_permission(
                decision='ask',
                reason='Need permission'
            )
            # 当前实现：交互式提示未配置回调，auto-deny
            assert decision == 'deny'
            assert 'not configured' in reason.lower()

    def test_get_permission_handler_singleton(self):
        """测试全局单例"""
        handler1 = get_permission_handler()
        handler2 = get_permission_handler()
        assert handler1 is handler2


class TestPermissionAskIntegration:
    """测试 permission_decision='ask' 端到端流程"""

    @pytest.fixture
    def mock_context(self) -> Context:
        """创建测试用 Context"""
        session = Session()
        session.session_id = 'test-session-123'
        context = Context(task_id='test-task-456', session=session)
        context.agent_id = 'test-agent-789'
        return context

    @pytest.mark.asyncio
    async def test_tc_hook_009_ask_non_interactive_auto_deny(
        self,
        mock_context,
        tmp_path
    ):
        """TC-HOOK-009: Hook 返回 permission_decision='ask'，非交互式环境自动降级为 deny"""
        # 创建返回 'ask' 的 hook
        hook_script = tmp_path / 'ask_permission.sh'
        hook_script.write_text('''#!/bin/bash
echo '{"continue": true, "permission_decision": "ask", "permission_decision_reason": "Path requires approval: $PATH_ARG"}'
''')
        hook_script.chmod(0o755)

        config = {
            'name': 'test-ask-hook',
            'hook_point': 'before_tool_call',
            'command': str(hook_script),
            'env': {'PATH_ARG': '/etc/passwd'}
        }
        hook = CommandHookWrapper(config)

        # 创建测试 message
        message = Message(
            category='tool_call',
            payload={'tool_name': 'terminal', 'args': {'path': '/etc/passwd'}},
            session_id='test-session-123',
            sender='test-agent'
        )
        message.context = mock_context

        # 模拟非交互式环境
        with patch('sys.stdin.isatty', return_value=False):
            # 执行 hook
            result = await hook.exec(message, mock_context)

            # 验证 hook 返回 'ask'
            assert result.headers.get('permission_decision') == 'ask'
            assert 'requires approval' in result.headers.get('permission_decision_reason', '')

            # 模拟 DefaultHandler.run_hooks() 的权限解析逻辑
            from aworld.runners.hook.v2.permission import get_permission_handler

            handler = get_permission_handler()
            final_decision, resolution_reason = await handler.resolve_permission(
                decision='ask',
                reason=result.headers.get('permission_decision_reason'),
                context={'hook_name': 'test-ask-hook', 'hook_point': 'before_tool_call'}
            )

            # 验证自动降级为 deny
            assert final_decision == 'deny'
            assert 'non-interactive' in resolution_reason.lower()

    @pytest.mark.asyncio
    async def test_tc_hook_010_allow_pass_through(
        self,
        mock_context,
        tmp_path
    ):
        """TC-HOOK-010: Hook 返回 permission_decision='allow'，正常通过"""
        hook_script = tmp_path / 'allow_permission.sh'
        hook_script.write_text('''#!/bin/bash
echo '{"continue": true, "permission_decision": "allow"}'
''')
        hook_script.chmod(0o755)

        config = {
            'name': 'test-allow-hook',
            'hook_point': 'before_tool_call',
            'command': str(hook_script)
        }
        hook = CommandHookWrapper(config)

        message = Message(
            category='tool_call',
            payload={'tool_name': 'terminal', 'args': {}},
            session_id='test-session-123',
            sender='test-agent'
        )
        message.context = mock_context

        result = await hook.exec(message, mock_context)

        assert result.headers.get('permission_decision') == 'allow'

        # 权限解析（应该直接返回 allow）
        from aworld.runners.hook.v2.permission import get_permission_handler

        handler = get_permission_handler()
        final_decision, _ = await handler.resolve_permission(
            decision='allow',
            reason=None
        )

        assert final_decision == 'allow'

    @pytest.mark.asyncio
    async def test_tc_hook_011_deny_blocks_execution(
        self,
        mock_context,
        tmp_path
    ):
        """TC-HOOK-011: Hook 返回 permission_decision='deny'，阻止执行"""
        hook_script = tmp_path / 'deny_permission.sh'
        hook_script.write_text('''#!/bin/bash
echo '{"continue": false, "permission_decision": "deny", "stop_reason": "Access denied by policy"}'
''')
        hook_script.chmod(0o755)

        config = {
            'name': 'test-deny-hook',
            'hook_point': 'before_tool_call',
            'command': str(hook_script)
        }
        hook = CommandHookWrapper(config)

        message = Message(
            category='tool_call',
            payload={'tool_name': 'terminal', 'args': {}},
            session_id='test-session-123',
            sender='test-agent'
        )
        message.context = mock_context

        result = await hook.exec(message, mock_context)

        # 验证 deny 决策
        assert result.headers.get('permission_decision') == 'deny'
        assert result.headers.get('prevent_continuation') is True
        assert 'denied' in result.headers.get('stop_reason', '').lower()

    @pytest.mark.asyncio
    async def test_permission_decision_with_env_override(
        self,
        mock_context,
        tmp_path
    ):
        """测试环境变量覆盖 ask 决策"""
        hook_script = tmp_path / 'ask_env_override.sh'
        hook_script.write_text('''#!/bin/bash
echo '{"continue": true, "permission_decision": "ask"}'
''')
        hook_script.chmod(0o755)

        config = {
            'name': 'test-env-override-hook',
            'hook_point': 'before_tool_call',
            'command': str(hook_script)
        }
        hook = CommandHookWrapper(config)

        message = Message(
            category='tool_call',
            payload={'tool_name': 'terminal'},
            session_id='test-session-123',
            sender='test-agent'
        )
        message.context = mock_context

        # 设置环境变量 AWORLD_PERMISSION_MODE=allow
        with patch.dict(os.environ, {'AWORLD_PERMISSION_MODE': 'allow'}):
            result = await hook.exec(message, mock_context)

            # 验证 hook 返回 'ask'
            assert result.headers.get('permission_decision') == 'ask'

            # 权限解析应该根据环境变量返回 'allow'
            from aworld.runners.hook.v2 import permission

            # 清除单例，强制重新创建
            permission._permission_handler = None

            handler = permission.get_permission_handler()
            final_decision, reason = await handler.resolve_permission(
                decision='ask',
                reason=None
            )

            assert final_decision == 'allow'
            assert 'AWORLD_PERMISSION_MODE' in reason

            # 清理：恢复单例为 None
            permission._permission_handler = None
