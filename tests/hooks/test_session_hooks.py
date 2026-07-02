"""测试 session 生命周期 hooks"""

import pytest
from aworld.core.context.base import Context
from aworld.core.context.session import Session
from aworld.core.event.base import Message
from aworld.runners.hook.v2.wrappers import CommandHookWrapper


class TestSessionHooks:
    """测试 session 生命周期 hooks"""

    @pytest.fixture
    def mock_context(self) -> Context:
        """创建测试用 Context"""
        session = Session()
        session.session_id = 'test-session-123'
        context = Context(task_id='test-task-456', session=session)
        context.agent_id = 'test-agent-789'
        return context

    @pytest.mark.asyncio
    async def test_tc_session_001_session_started_hook(
        self,
        mock_context,
        tmp_path
    ):
        """TC-SESSION-001: session_started hook 接收会话开始事件"""
        # 创建记录 hook 执行的脚本
        hook_script = tmp_path / 'session_started_log.sh'
        log_file = tmp_path / 'session_started.log'
        hook_script.write_text(f'''#!/bin/bash
echo "session_started triggered: session_id=$AWORLD_SESSION_ID task_id=$AWORLD_TASK_ID" >> {log_file}
echo '{{"continue": true}}'
''')
        hook_script.chmod(0o755)

        # 配置 hook
        config = {
            'name': 'test-session-started-hook',
            'hook_point': 'session_started',
            'command': str(hook_script)
        }
        hook = CommandHookWrapper(config)

        # 创建 session_started message
        message = Message(
            category='session_lifecycle',
            payload={
                'event': 'session_started',
                'session_id': 'test-session-123',
                'task_id': 'test-task-456',
                'start_time': 1234567890.0
            },
            session_id='test-session-123',
            sender='task_runner'
        )
        message.context = mock_context

        # 执行 hook
        result = await hook.exec(message, mock_context)

        # 验证 hook 执行成功
        assert result is not None
        assert log_file.exists()
        log_content = log_file.read_text()
        assert 'session_started triggered' in log_content
        assert 'session_id=test-session-123' in log_content
        assert 'task_id=test-task-456' in log_content

    @pytest.mark.asyncio
    async def test_tc_session_002_session_finished_hook(
        self,
        mock_context,
        tmp_path
    ):
        """TC-SESSION-002: session_finished hook 接收会话完成事件"""
        hook_script = tmp_path / 'session_finished_log.sh'
        log_file = tmp_path / 'session_finished.log'
        hook_script.write_text(f'''#!/bin/bash
echo "session_finished triggered: session_id=$AWORLD_SESSION_ID status=success" >> {log_file}
echo '{{"continue": true}}'
''')
        hook_script.chmod(0o755)

        config = {
            'name': 'test-session-finished-hook',
            'hook_point': 'session_finished',
            'command': str(hook_script)
        }
        hook = CommandHookWrapper(config)

        # 创建 session_finished message
        message = Message(
            category='session_lifecycle',
            payload={
                'event': 'session_finished',
                'session_id': 'test-session-123',
                'task_id': 'test-task-456',
                'time_cost': 10.5,
                'status': 'success'
            },
            session_id='test-session-123',
            sender='task_runner'
        )
        message.context = mock_context

        result = await hook.exec(message, mock_context)

        assert result is not None
        assert log_file.exists()
        log_content = log_file.read_text()
        assert 'session_finished triggered' in log_content
        assert 'session_id=test-session-123' in log_content

    @pytest.mark.asyncio
    async def test_tc_session_003_session_failed_hook(
        self,
        mock_context,
        tmp_path
    ):
        """TC-SESSION-003: session_failed hook 接收会话失败事件"""
        hook_script = tmp_path / 'session_failed_log.sh'
        log_file = tmp_path / 'session_failed.log'
        hook_script.write_text(f'''#!/bin/bash
echo "session_failed triggered: session_id=$AWORLD_SESSION_ID error=$AWORLD_MESSAGE_JSON" >> {log_file}
echo '{{"continue": true}}'
''')
        hook_script.chmod(0o755)

        config = {
            'name': 'test-session-failed-hook',
            'hook_point': 'session_failed',
            'command': str(hook_script)
        }
        hook = CommandHookWrapper(config)

        # 创建 session_failed message
        message = Message(
            category='session_lifecycle',
            payload={
                'event': 'session_failed',
                'session_id': 'test-session-123',
                'task_id': 'test-task-456',
                'time_cost': 5.2,
                'error': 'Test error',
                'error_type': 'RuntimeError',
                'status': 'failed'
            },
            session_id='test-session-123',
            sender='task_runner'
        )
        message.context = mock_context

        result = await hook.exec(message, mock_context)

        # 验证 hook 执行成功
        assert result is not None
        assert log_file.exists()
        log_content = log_file.read_text()
        assert 'session_failed triggered' in log_content
        assert 'session_id=test-session-123' in log_content

    @pytest.mark.asyncio
    async def test_tc_session_004_hook_can_block_session_start(
        self,
        mock_context,
        tmp_path
    ):
        """TC-SESSION-004: session hook 可以阻止会话启动"""
        hook_script = tmp_path / 'session_block.sh'
        hook_script.write_text('''#!/bin/bash
echo '{"continue": false, "stop_reason": "Session rejected by policy", "permission_decision": "deny"}'
''')
        hook_script.chmod(0o755)

        config = {
            'name': 'test-session-block-hook',
            'hook_point': 'session_started',
            'command': str(hook_script)
        }
        hook = CommandHookWrapper(config)

        message = Message(
            category='session_lifecycle',
            payload={'event': 'session_started'},
            session_id='test-session-123',
            sender='task_runner'
        )
        message.context = mock_context

        result = await hook.exec(message, mock_context)

        # 验证阻止标记
        assert result is not None
        assert result.headers.get('prevent_continuation') is True
        assert 'rejected by policy' in result.headers.get('stop_reason', '').lower()
