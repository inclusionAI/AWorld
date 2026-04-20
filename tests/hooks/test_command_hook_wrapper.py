"""测试 CommandHookWrapper"""

import asyncio
import json
import os
from pathlib import Path

import pytest

from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.runners.hook.v2.wrappers import CommandHookWrapper


@pytest.mark.asyncio
class TestCommandHookWrapperBasic:
    """测试 CommandHookWrapper 基本功能"""

    async def test_initialization(self, command_hook_config):
        """测试初始化"""
        hook = CommandHookWrapper(command_hook_config)

        assert hook._name == 'test-hook'
        assert hook._hook_point == 'before_tool_call'
        assert hook._timeout == 5000
        assert hook._custom_env == {'ALLOWED_PATHS': '/tmp,/workspace'}

    async def test_point_method(self, command_hook_config):
        """测试 point() 方法"""
        hook = CommandHookWrapper(command_hook_config)

        assert hook.point() == 'before_tool_call'

    async def test_repr(self, command_hook_config):
        """测试 __repr__"""
        hook = CommandHookWrapper(command_hook_config)
        repr_str = repr(hook)

        assert 'CommandHookWrapper' in repr_str
        assert 'test-hook' in repr_str
        assert 'before_tool_call' in repr_str


@pytest.mark.asyncio
class TestCommandHookWrapperExecution:
    """测试 Hook 执行流程"""

    async def test_tc_hook_001_valid_path(
        self,
        hooks_scripts_dir,
        mock_context,
        mock_tool_message
    ):
        """TC-HOOK-001: 合法路径验证 - 白名单检查通过"""
        hook = CommandHookWrapper({
            'name': 'validate-path',
            'hook_point': 'before_tool_call',
            'command': str(hooks_scripts_dir / 'validate_path.sh'),
            'env': {'ALLOWED_PATHS': '/tmp,/workspace'}
        })

        # 合法路径 /tmp/test.txt
        result = await hook.exec(mock_tool_message, mock_context)

        # 应该允许继续执行
        assert 'prevent_continuation' not in result.headers
        assert result.headers.get('permission_decision') == 'allow'

    async def test_tc_hook_002_invalid_path(
        self,
        hooks_scripts_dir,
        mock_context
    ):
        """TC-HOOK-002: 非法路径拦截 - 拒绝访问 /etc/passwd"""
        hook = CommandHookWrapper({
            'name': 'validate-path',
            'hook_point': 'before_tool_call',
            'command': str(hooks_scripts_dir / 'validate_path.sh'),
            'env': {'ALLOWED_PATHS': '/tmp,/workspace'}
        })

        # 非法路径 /etc/passwd
        message = Message(
            category='tool_call',
            payload={
                'tool_name': 'terminal',
                'args': {'path': '/etc/passwd'}
            }
        )

        result = await hook.exec(message, mock_context)

        # 应该阻止执行
        assert result.headers.get('prevent_continuation') is True
        assert result.headers.get('permission_decision') == 'deny'
        assert 'denied' in result.headers.get('stop_reason', '').lower()

    async def test_tc_hook_003_path_rewrite(
        self,
        hooks_scripts_dir,
        mock_context
    ):
        """TC-HOOK-003: 路径参数重写 - 相对路径转绝对路径"""
        hook = CommandHookWrapper({
            'name': 'path-rewrite',
            'hook_point': 'before_tool_call',
            'command': str(hooks_scripts_dir / 'validate_path_with_rewrite.sh')
        })

        # 相对路径
        message = Message(
            category='tool_call',
            payload={
                'tool_name': 'terminal',
                'args': {'path': './relative/path.txt'}
            }
        )

        result = await hook.exec(message, mock_context)

        # 检查参数是否被重写
        assert 'updated_input' in result.headers
        updated_path = result.headers['updated_input'].get('path')
        assert updated_path is not None
        # 应该是绝对路径
        assert updated_path.startswith('/')
        # 系统消息应该提示路径已规范化
        assert result.headers.get('system_message') == 'Path normalized to absolute path'


@pytest.mark.asyncio
class TestCommandHookWrapperErrorHandling:
    """测试错误处理和边界情况"""

    async def test_tc_hook_005_script_failure_fail_open(
        self,
        hooks_scripts_dir,
        mock_context,
        mock_tool_message
    ):
        """TC-HOOK-005: Shell 脚本执行失败 - fail-open 策略"""
        # 使用不存在的脚本
        hook = CommandHookWrapper({
            'name': 'non-existent',
            'hook_point': 'before_tool_call',
            'command': str(hooks_scripts_dir / 'non_existent_script.sh')
        })

        result = await hook.exec(mock_tool_message, mock_context)

        # Fail-open: 应该返回原始 message，不阻塞执行
        assert result.category == mock_tool_message.category
        assert result.payload == mock_tool_message.payload
        # 不应该有 prevent_continuation
        assert 'prevent_continuation' not in result.headers

    async def test_tc_hook_006_json_parse_error(
        self,
        hooks_scripts_dir,
        mock_context,
        mock_tool_message
    ):
        """TC-HOOK-006: JSON 解析错误 - 非 JSON 输出处理"""
        hook = CommandHookWrapper({
            'name': 'json-error',
            'hook_point': 'before_tool_call',
            'command': str(hooks_scripts_dir / 'json_error_test.sh')
        })

        result = await hook.exec(mock_tool_message, mock_context)

        # 非法 JSON 应该作为纯文本处理（additional_context）
        assert 'additional_context' in result.headers
        # 不应该阻塞执行
        assert 'prevent_continuation' not in result.headers

    async def test_tc_hook_007_timeout_control(
        self,
        hooks_scripts_dir,
        mock_context,
        mock_tool_message
    ):
        """TC-HOOK-007: 超时控制 - 5 秒超时强制终止"""
        hook = CommandHookWrapper({
            'name': 'timeout-test',
            'hook_point': 'before_tool_call',
            'command': str(hooks_scripts_dir / 'timeout_test.sh'),
            'timeout': 2000  # 2 秒超时（脚本会睡眠 10 秒）
        })

        import time
        start = time.time()
        result = await hook.exec(mock_tool_message, mock_context)
        elapsed = time.time() - start

        # 应该在超时时间内返回（允许一些误差）
        assert elapsed < 3.0  # 2秒超时 + 1秒误差

        # Fail-open: 超时后返回原始 message
        assert result.category == mock_tool_message.category
        assert 'prevent_continuation' not in result.headers


@pytest.mark.asyncio
class TestCommandHookWrapperEnvironment:
    """测试环境变量注入"""

    async def test_tc_hook_008_env_injection(
        self,
        hooks_scripts_dir,
        mock_context,
        mock_tool_message
    ):
        """TC-HOOK-008: 环境变量注入 - 完整上下文传递"""
        hook = CommandHookWrapper({
            'name': 'env-test',
            'hook_point': 'before_tool_call',
            'command': str(hooks_scripts_dir / 'env_test.sh')
        })

        result = await hook.exec(mock_tool_message, mock_context)

        # 检查 hook_specific_output 中的环境变量
        assert 'hook_specific_output' in result.headers
        env_data = result.headers['hook_specific_output'].get('env', {})

        # 验证 AWORLD_* 环境变量
        assert env_data.get('session_id') == 'test-session-123'
        assert env_data.get('task_id') == 'test-task-456'
        assert env_data.get('hook_point') == 'before_tool_call'
        assert env_data.get('hook_name') == 'env-test'

        # 验证 CWD
        cwd = env_data.get('cwd')
        assert cwd is not None
        assert Path(cwd).exists()

        # 验证 MESSAGE_JSON
        message_json_str = env_data.get('message_json')
        assert message_json_str is not None
        message_json = json.loads(message_json_str)
        assert message_json['category'] == 'tool_call'
        assert message_json['payload']['tool_name'] == 'terminal'

        # 验证 CONTEXT_JSON
        context_json_str = env_data.get('context_json')
        assert context_json_str is not None
        context_json = json.loads(context_json_str)
        assert context_json['session_id'] == 'test-session-123'
        assert context_json['task_id'] == 'test-task-456'

    async def test_custom_env_variables(
        self,
        hooks_scripts_dir,
        mock_context
    ):
        """测试自定义环境变量"""
        hook = CommandHookWrapper({
            'name': 'custom-env',
            'hook_point': 'before_tool_call',
            'command': 'echo $CUSTOM_VAR',
            'env': {'CUSTOM_VAR': 'custom_value'}
        })

        message = Message(category='test', payload={})
        result = await hook.exec(message, mock_context)

        # 自定义环境变量应该被注入
        # 输出会作为 additional_context
        assert 'additional_context' in result.headers
        assert 'custom_value' in result.headers['additional_context']


@pytest.mark.asyncio
class TestCommandHookWrapperOutputApplication:
    """测试输出应用逻辑"""

    async def test_apply_additional_context(self, mock_context):
        """测试 additional_context 应用"""
        hook = CommandHookWrapper({
            'name': 'test',
            'hook_point': 'test',
            'command': 'echo "test context"'
        })

        message = Message(category='test', payload={})
        result = await hook.exec(message, mock_context)

        assert 'additional_context' in result.headers
        assert 'test context' in result.headers['additional_context']

    async def test_apply_system_message(
        self,
        hooks_scripts_dir,
        mock_context
    ):
        """测试 system_message 应用"""
        hook = CommandHookWrapper({
            'name': 'test',
            'hook_point': 'test',
            'command': 'echo \'{"systemMessage": "Test message"}\''
        })

        message = Message(category='test', payload={})
        result = await hook.exec(message, mock_context)

        assert result.headers.get('system_message') == 'Test message'

    async def test_apply_updated_input(self, mock_context, mock_tool_message):
        """测试 updated_input 应用到 payload.args"""
        hook = CommandHookWrapper({
            'name': 'test',
            'hook_point': 'test',
            'command': 'echo \'{"updatedInput": {"path": "/new/path", "extra": "value"}}\''
        })

        result = await hook.exec(mock_tool_message, mock_context)

        # updated_input 应该合并到 payload.args
        assert result.payload['args']['path'] == '/new/path'
        assert result.payload['args']['extra'] == 'value'
        # 原有字段应该保留
        assert result.payload['args']['command'] == 'cat'

    async def test_prevent_continuation(self, mock_context):
        """测试 prevent_continuation 应用"""
        hook = CommandHookWrapper({
            'name': 'test',
            'hook_point': 'test',
            'command': 'echo \'{"continue": false, "stopReason": "Test stop"}\''
        })

        message = Message(category='test', payload={})
        result = await hook.exec(message, mock_context)

        assert result.headers.get('prevent_continuation') is True
        assert result.headers.get('stop_reason') == 'Test stop'


@pytest.mark.asyncio
class TestCommandHookWrapperEdgeCases:
    """测试边界情况"""

    async def test_empty_output(self, mock_context):
        """测试空输出"""
        hook = CommandHookWrapper({
            'name': 'empty',
            'hook_point': 'test',
            'command': 'echo ""'
        })

        message = Message(category='test', payload={})
        result = await hook.exec(message, mock_context)

        # 空输出应该返回原始 message
        assert result.category == message.category
        assert result.payload == message.payload

    async def test_plain_text_output(self, mock_context):
        """测试纯文本输出（不是 JSON）"""
        hook = CommandHookWrapper({
            'name': 'plain',
            'hook_point': 'test',
            'command': 'echo "This is plain text"'
        })

        message = Message(category='test', payload={})
        result = await hook.exec(message, mock_context)

        # 纯文本应该作为 additional_context
        assert 'additional_context' in result.headers
        assert 'This is plain text' in result.headers['additional_context']

    async def test_message_without_headers(self, mock_context):
        """测试 Message 没有 headers 属性的情况"""
        hook = CommandHookWrapper({
            'name': 'test',
            'hook_point': 'test',
            'command': 'echo \'{"systemMessage": "Test"}\''
        })

        # 创建没有 headers 的 Message
        message = Message(category='test', payload={})
        if hasattr(message, 'headers'):
            delattr(message, 'headers')

        result = await hook.exec(message, mock_context)

        # 应该自动创建 headers
        assert hasattr(result, 'headers')
        assert result.headers.get('system_message') == 'Test'
