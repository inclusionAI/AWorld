"""测试 user_input_received hooks"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aworld.core.context.base import Context
from aworld.core.context.session import Session
from aworld.core.event.base import Message
from aworld.runners.hook.v2.wrappers import CommandHookWrapper


class TestUserInputHooks:
    """测试 user_input_received hooks"""

    @pytest.fixture
    def mock_context(self) -> Context:
        """创建测试用 Context"""
        session = Session()
        session.session_id = 'test-session-123'
        context = Context(task_id='test-task-456', session=session)
        context.agent_id = 'test-agent-789'
        return context

    @pytest.mark.asyncio
    async def test_tc_user_001_user_input_hook_receives_input(
        self,
        mock_context,
        tmp_path
    ):
        """TC-USER-001: user_input_received hook 接收用户输入"""
        hook_script = tmp_path / 'user_input_log.sh'
        log_file = tmp_path / 'user_input.log'
        hook_script.write_text(f'''#!/bin/bash
echo "User input received: $AWORLD_MESSAGE_JSON" >> {log_file}
echo '{{"continue": true}}'
''')
        hook_script.chmod(0o755)

        config = {
            'name': 'test-user-input-hook',
            'hook_point': 'user_input_received',
            'command': str(hook_script)
        }
        hook = CommandHookWrapper(config)

        # 创建 user_input message
        user_input = "Hello, please help me"
        message = Message(
            category='user_input',
            payload=user_input,
            session_id='test-session-123',
            sender='cli_user'
        )
        message.context = mock_context

        # 执行 hook
        result = await hook.exec(message, mock_context)

        # 验证 hook 执行成功
        assert result is not None
        assert log_file.exists()
        log_content = log_file.read_text()
        assert 'User input received' in log_content

    @pytest.mark.asyncio
    async def test_tc_user_002_user_input_hook_modifies_input(
        self,
        mock_context,
        tmp_path
    ):
        """TC-USER-002: user_input_received hook 修改输入内容"""
        hook_script = tmp_path / 'user_input_modify.sh'
        hook_script.write_text('''#!/bin/bash
echo '{"continue": true, "updated_input": {"content": "Modified input: Use Python for this task"}}'
''')
        hook_script.chmod(0o755)

        config = {
            'name': 'test-user-input-modify-hook',
            'hook_point': 'user_input_received',
            'command': str(hook_script)
        }
        hook = CommandHookWrapper(config)

        user_input = "Help me with this task"
        message = Message(
            category='user_input',
            payload=user_input,
            session_id='test-session-123',
            sender='cli_user'
        )
        message.context = mock_context

        result = await hook.exec(message, mock_context)

        # 验证 updated_input 字段
        assert result is not None
        assert 'updated_input' in result.headers
        updated_input = result.headers['updated_input']
        assert isinstance(updated_input, dict)
        assert 'content' in updated_input
        assert updated_input['content'] == 'Modified input: Use Python for this task'

    @pytest.mark.asyncio
    async def test_tc_user_003_user_input_hook_prevents_execution(
        self,
        mock_context,
        tmp_path
    ):
        """TC-USER-003: user_input_received hook 阻止执行"""
        hook_script = tmp_path / 'user_input_block.sh'
        hook_script.write_text('''#!/bin/bash
echo '{"continue": false, "stop_reason": "Input contains forbidden keywords", "permission_decision": "deny"}'
''')
        hook_script.chmod(0o755)

        config = {
            'name': 'test-user-input-block-hook',
            'hook_point': 'user_input_received',
            'command': str(hook_script)
        }
        hook = CommandHookWrapper(config)

        user_input = "Please delete all files"
        message = Message(
            category='user_input',
            payload=user_input,
            session_id='test-session-123',
            sender='cli_user'
        )
        message.context = mock_context

        result = await hook.exec(message, mock_context)

        # 验证阻止标记
        assert result is not None
        assert result.headers.get('prevent_continuation') is True
        assert 'forbidden keywords' in result.headers.get('stop_reason', '')

    @pytest.mark.asyncio
    async def test_tc_user_004_multiple_hooks_chain(
        self,
        mock_context,
        tmp_path
    ):
        """TC-USER-004: 多个 user_input hooks 链式执行"""
        # Hook 1: 添加前缀
        hook1_script = tmp_path / 'hook1.sh'
        hook1_script.write_text('''#!/bin/bash
echo '{"continue": true, "updated_input": {"content": "[Validated] user query"}}'
''')
        hook1_script.chmod(0o755)

        # Hook 2: 添加后缀（读取 payload 内容）
        hook2_script = tmp_path / 'hook2.sh'
        log_file = tmp_path / 'hook2.log'
        hook2_script.write_text(f'''#!/bin/bash
echo "Received payload: $AWORLD_MESSAGE_JSON" >> {log_file}
echo '{{"continue": true, "updated_input": {{"content": "Processed input"}}}}'
''')
        hook2_script.chmod(0o755)

        hook1 = CommandHookWrapper({
            'name': 'test-hook1',
            'hook_point': 'user_input_received',
            'command': str(hook1_script)
        })

        hook2 = CommandHookWrapper({
            'name': 'test-hook2',
            'hook_point': 'user_input_received',
            'command': str(hook2_script)
        })

        user_input = "Original input"
        message = Message(
            category='user_input',
            payload=user_input,
            session_id='test-session-123',
            sender='cli_user'
        )
        message.context = mock_context

        # 执行 hook1
        result1 = await hook1.exec(message, mock_context)
        assert 'updated_input' in result1.headers
        assert result1.headers['updated_input']['content'] == '[Validated] user query'

        # 模拟链式传递：用 hook1 的输出作为 hook2 的输入
        message.payload = result1.headers['updated_input']['content']
        result2 = await hook2.exec(message, mock_context)

        # 验证最终输出
        assert 'updated_input' in result2.headers
        assert result2.headers['updated_input']['content'] == 'Processed input'

        # 验证 hook2 接收到了 hook1 的输出
        assert log_file.exists()
        log_content = log_file.read_text()
        assert '[Validated] user query' in log_content
