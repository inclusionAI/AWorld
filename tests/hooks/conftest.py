"""Pytest fixtures for hooks tests"""

import os
from pathlib import Path
from typing import Dict

import pytest

from aworld.core.context.base import Context
from aworld.core.event.base import Message


@pytest.fixture
def fixtures_dir() -> Path:
    """返回 fixtures 目录路径"""
    return Path(__file__).parent.parent / 'fixtures'


@pytest.fixture
def hooks_scripts_dir(fixtures_dir) -> Path:
    """返回 hooks 脚本目录路径"""
    return fixtures_dir / 'hooks'


@pytest.fixture
def configs_dir(fixtures_dir) -> Path:
    """返回配置文件目录路径"""
    return fixtures_dir / 'configs'


@pytest.fixture
def mock_context() -> Context:
    """创建 mock Context 对象"""
    from aworld.core.context.session import Session

    # 创建 mock Session
    session = Session()
    session.session_id = 'test-session-123'

    # 创建 Context 并传入 session
    context = Context(
        task_id='test-task-456',
        session=session
    )
    context.agent_id = 'test-agent-789'

    return context


@pytest.fixture
def mock_tool_message() -> Message:
    """创建 mock 工具调用 Message"""
    return Message(
        category='tool_call',
        payload={
            'tool_name': 'terminal',
            'args': {
                'path': '/tmp/test.txt',
                'command': 'cat'
            }
        }
    )


@pytest.fixture
def mock_user_input_message() -> Message:
    """创建 mock 用户输入 Message"""
    return Message(
        category='user_input',
        payload='分析 @document.txt'
    )


@pytest.fixture
def command_hook_config(hooks_scripts_dir) -> Dict:
    """基础 CommandHook 配置"""
    return {
        'name': 'test-hook',
        'hook_point': 'before_tool_call',
        'command': str(hooks_scripts_dir / 'validate_path.sh'),
        'timeout': 5000,
        'env': {
            'ALLOWED_PATHS': '/tmp,/workspace'
        }
    }


@pytest.fixture
def callback_hook_config() -> Dict:
    """基础 CallbackHook 配置"""
    return {
        'name': 'test-callback',
        'hook_point': 'user_input_received',
        'callback': 'tests.fixtures.hooks.callback_example:process_input'
    }
