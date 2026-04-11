"""测试工具调用相关 hooks"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from aworld.core.context.base import Context
from aworld.core.context.session import Session
from aworld.core.event.base import Message
from aworld.runners.hook.v2.wrappers import CommandHookWrapper


class TestToolCallFailedHook:
    """测试 tool_call_failed hook"""

    @pytest.fixture
    def mock_context(self) -> Context:
        """创建测试用 Context"""
        session = Session()
        session.session_id = 'test-session-123'
        context = Context(task_id='test-task-456', session=session)
        context.agent_id = 'test-agent-789'
        return context

    @pytest.mark.asyncio
    async def test_tc_tool_001_tool_call_failed_hook_receives_error(
        self,
        mock_context,
        tmp_path
    ):
        """TC-TOOL-001: tool_call_failed hook 接收工具调用失败事件"""
        hook_script = tmp_path / 'tool_failed_log.sh'
        log_file = tmp_path / 'tool_failed.log'
        hook_script.write_text(f'''#!/bin/bash
echo "tool_call_failed triggered: tool=$AWORLD_MESSAGE_JSON" >> {log_file}
echo '{{"continue": true}}'
''')
        hook_script.chmod(0o755)

        config = {
            'name': 'test-tool-failed-hook',
            'hook_point': 'tool_call_failed',
            'command': str(hook_script)
        }
        hook = CommandHookWrapper(config)

        # 创建 tool_call_failed message
        message = Message(
            category='tool_call',
            payload={
                'tool_name': 'terminal',
                'action': [{'tool_name': 'terminal', 'args': {'command': 'invalid'}}],
                'error': 'Command not found',
                'error_type': 'RuntimeError',
                'traceback': 'Traceback (most recent call last):\\n  ...'
            },
            session_id='test-session-123',
            sender='agent'
        )
        message.context = mock_context

        result = await hook.exec(message, mock_context)

        # 验证 hook 执行成功
        assert result is not None
        assert log_file.exists()
        log_content = log_file.read_text()
        assert 'tool_call_failed triggered' in log_content


class TestToolCallInputModification:
    """测试 before_tool_call hook 修改输入"""

    @pytest.fixture
    def mock_context(self) -> Context:
        """创建测试用 Context"""
        session = Session()
        session.session_id = 'test-session-123'
        context = Context(task_id='test-task-456', session=session)
        context.agent_id = 'test-agent-789'
        return context

    @pytest.mark.asyncio
    async def test_tc_tool_002_before_tool_call_modifies_input(
        self,
        mock_context,
        tmp_path
    ):
        """TC-TOOL-002: before_tool_call hook 修改工具输入参数"""
        hook_script = tmp_path / 'modify_input.sh'
        hook_script.write_text('''#!/bin/bash
# 模拟修改输入：将命令参数从危险命令改为安全命令
echo '{"continue": true, "updated_input": [{"tool_name": "terminal", "args": {"command": "echo safe"}}]}'
''')
        hook_script.chmod(0o755)

        config = {
            'name': 'test-input-modifier-hook',
            'hook_point': 'before_tool_call',
            'command': str(hook_script)
        }
        hook = CommandHookWrapper(config)

        # 创建 before_tool_call message
        message = Message(
            category='tool_call',
            payload=[{'tool_name': 'terminal', 'args': {'command': 'rm -rf /'}}],
            session_id='test-session-123',
            sender='agent'
        )
        message.context = mock_context

        result = await hook.exec(message, mock_context)

        # 验证 updated_input 字段
        assert result is not None
        assert 'updated_input' in result.headers
        updated_input = result.headers['updated_input']
        assert isinstance(updated_input, list)
        assert len(updated_input) == 1
        assert updated_input[0]['tool_name'] == 'terminal'
        assert updated_input[0]['args']['command'] == 'echo safe'

    @pytest.mark.asyncio
    async def test_tc_tool_003_before_tool_call_blocks_dangerous_command(
        self,
        mock_context,
        tmp_path
    ):
        """TC-TOOL-003: before_tool_call hook 阻止危险命令执行"""
        hook_script = tmp_path / 'block_dangerous.sh'
        hook_script.write_text('''#!/bin/bash
# 检测到危险命令，阻止执行
if echo "$AWORLD_MESSAGE_JSON" | grep -q "rm -rf"; then
    echo '{"continue": false, "stop_reason": "Dangerous command detected: rm -rf", "permission_decision": "deny"}'
else
    echo '{"continue": true}'
fi
''')
        hook_script.chmod(0o755)

        config = {
            'name': 'test-block-dangerous-hook',
            'hook_point': 'before_tool_call',
            'command': str(hook_script)
        }
        hook = CommandHookWrapper(config)

        message = Message(
            category='tool_call',
            payload=[{'tool_name': 'terminal', 'args': {'command': 'rm -rf /'}}],
            session_id='test-session-123',
            sender='agent'
        )
        message.context = mock_context

        result = await hook.exec(message, mock_context)

        # 验证阻止标记
        assert result is not None
        assert result.headers.get('prevent_continuation') is True
        assert 'dangerous command' in result.headers.get('stop_reason', '').lower()


class TestToolCallOutputModification:
    """测试 after_tool_call hook 修改输出"""

    @pytest.fixture
    def mock_context(self) -> Context:
        """创建测试用 Context"""
        session = Session()
        session.session_id = 'test-session-123'
        context = Context(task_id='test-task-456', session=session)
        context.agent_id = 'test-agent-789'
        return context

    @pytest.mark.asyncio
    async def test_tc_tool_004_after_tool_call_modifies_output(
        self,
        mock_context,
        tmp_path
    ):
        """TC-TOOL-004: after_tool_call hook 修改工具输出结果"""
        hook_script = tmp_path / 'modify_output.sh'
        hook_script.write_text('''#!/bin/bash
# 模拟修改输出：过滤敏感信息
echo '{"continue": true, "updated_output": {"observation": {"content": "Filtered output: [REDACTED]"}}}'
''')
        hook_script.chmod(0o755)

        config = {
            'name': 'test-output-modifier-hook',
            'hook_point': 'after_tool_call',
            'command': str(hook_script)
        }
        hook = CommandHookWrapper(config)

        # 创建 after_tool_call message（模拟工具返回结果）
        message = Message(
            category='tool_call',
            payload=(
                {'content': 'Password: secret123'},  # observation
                0.0,  # reward
                False,  # done
                False,  # truncated
                {}  # info
            ),
            session_id='test-session-123',
            sender='agent'
        )
        message.context = mock_context

        result = await hook.exec(message, mock_context)

        # 验证 updated_output 字段
        assert result is not None
        assert 'updated_output' in result.headers
        updated_output = result.headers['updated_output']
        assert isinstance(updated_output, dict)
        assert 'observation' in updated_output
        assert 'Filtered output' in updated_output['observation']['content']
        assert '[REDACTED]' in updated_output['observation']['content']

    @pytest.mark.asyncio
    async def test_tc_tool_005_after_tool_call_adds_audit_info(
        self,
        mock_context,
        tmp_path
    ):
        """TC-TOOL-005: after_tool_call hook 添加审计信息到输出"""
        hook_script = tmp_path / 'add_audit.sh'
        log_file = tmp_path / 'audit.log'
        hook_script.write_text(f'''#!/bin/bash
# 记录审计日志
echo "Tool call at $(date): session_id=$AWORLD_SESSION_ID" >> {log_file}
# 在输出中添加审计信息
echo '{{"continue": true, "updated_output": {{"info": {{"audit_logged": true, "audit_file": "{log_file}"}}}}}}'
''')
        hook_script.chmod(0o755)

        config = {
            'name': 'test-audit-hook',
            'hook_point': 'after_tool_call',
            'command': str(hook_script)
        }
        hook = CommandHookWrapper(config)

        message = Message(
            category='tool_call',
            payload=(
                {'content': 'Command executed'},
                0.0,
                False,
                False,
                {}
            ),
            session_id='test-session-123',
            sender='agent'
        )
        message.context = mock_context

        result = await hook.exec(message, mock_context)

        # 验证审计日志已创建
        assert log_file.exists()
        log_content = log_file.read_text()
        assert 'Tool call at' in log_content
        assert 'session_id=test-session-123' in log_content

        # 验证 updated_output 包含审计信息
        assert result is not None
        assert 'updated_output' in result.headers
        updated_output = result.headers['updated_output']
        assert 'info' in updated_output
        assert updated_output['info']['audit_logged'] is True
