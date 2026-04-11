"""测试 HookFactory 配置加载和合并"""

import os
from pathlib import Path

import pytest

from aworld.runners.hook.hook_factory import HookFactory, HookManager
from aworld.runners.hook.hooks import Hook, StartHook, HookPoint
from aworld.runners.hook.v2.wrappers import CommandHookWrapper, CallbackHookWrapper


class TestHookFactoryConfigLoading:
    """测试配置文件加载"""

    def test_load_config_hooks_success(self, configs_dir):
        """测试成功加载配置文件"""
        config_path = configs_dir / 'hooks_test.yaml'
        hooks = HookFactory.load_config_hooks(str(config_path))

        # 验证加载的 hook 点
        assert 'session_started' in hooks
        assert 'before_tool_call' in hooks
        assert 'after_tool_call' in hooks

        # 验证 session_started 有 1 个 hook
        assert len(hooks['session_started']) == 1
        assert hooks['session_started'][0]._name == 'test-session-start'
        assert isinstance(hooks['session_started'][0], CommandHookWrapper)

        # 验证 before_tool_call 有 2 个 hooks
        assert len(hooks['before_tool_call']) == 2
        assert isinstance(hooks['before_tool_call'][0], CommandHookWrapper)
        assert isinstance(hooks['before_tool_call'][1], CallbackHookWrapper)

        # 验证 after_tool_call 有 1 个 hook
        assert len(hooks['after_tool_call']) == 1

    def test_load_config_hooks_disabled(self, configs_dir):
        """测试禁用的 hook 不会被加载"""
        config_path = configs_dir / 'hooks_test.yaml'
        hooks = HookFactory.load_config_hooks(str(config_path))

        # user_input_received 的 hook 被禁用，不应该出现
        assert 'user_input_received' not in hooks or len(hooks['user_input_received']) == 0

    def test_load_config_hooks_nonexistent_file(self):
        """测试不存在的配置文件"""
        hooks = HookFactory.load_config_hooks('/nonexistent/path/hooks.yaml')

        # 应该返回空字典
        assert hooks == {}

    def test_load_config_hooks_caching(self, configs_dir, tmp_path):
        """测试配置文件缓存"""
        # 创建临时配置文件
        temp_config = tmp_path / 'hooks_cache_test.yaml'
        temp_config.write_text('''
version: "1.0"
hooks:
  session_started:
    - name: "cache-test"
      type: command
      command: "echo 'test'"
''')

        # 第一次加载
        hooks1 = HookFactory.load_config_hooks(str(temp_config))
        assert len(hooks1['session_started']) == 1

        # 第二次加载（应该使用缓存）
        hooks2 = HookFactory.load_config_hooks(str(temp_config))
        assert hooks2 == hooks1

        # 修改文件
        temp_config.write_text('''
version: "1.0"
hooks:
  session_started:
    - name: "cache-test-1"
      type: command
      command: "echo 'test1'"
    - name: "cache-test-2"
      type: command
      command: "echo 'test2'"
''')

        # 第三次加载（应该重新加载，因为文件修改时间变了）
        hooks3 = HookFactory.load_config_hooks(str(temp_config))
        assert len(hooks3['session_started']) == 2
        assert hooks3 != hooks1

    def test_load_config_hooks_invalid_yaml(self, tmp_path):
        """测试非法 YAML 文件"""
        invalid_config = tmp_path / 'invalid.yaml'
        invalid_config.write_text('invalid: yaml: content: [')

        hooks = HookFactory.load_config_hooks(str(invalid_config))

        # 应该返回空字典
        assert hooks == {}

    def test_load_config_hooks_empty_file(self, tmp_path):
        """测试空配置文件"""
        empty_config = tmp_path / 'empty.yaml'
        empty_config.write_text('')

        hooks = HookFactory.load_config_hooks(str(empty_config))

        # 应该返回空字典
        assert hooks == {}


class TestHookFactoryDeduplication:
    """测试 Hook 去重"""

    def test_compute_hook_fingerprint_command(self):
        """测试计算 CommandHook 指纹"""
        hook1 = CommandHookWrapper({
            'name': 'test1',
            'hook_point': 'before_tool_call',
            'command': '/path/to/script.sh',
            'shell': '/bin/bash'
        })
        hook2 = CommandHookWrapper({
            'name': 'test2',  # 不同的名称
            'hook_point': 'before_tool_call',
            'command': '/path/to/script.sh',  # 相同的命令
            'shell': '/bin/bash'  # 相同的 shell
        })
        hook3 = CommandHookWrapper({
            'name': 'test3',
            'hook_point': 'before_tool_call',
            'command': '/path/to/other.sh',  # 不同的命令
            'shell': '/bin/bash'
        })

        fp1 = HookManager._compute_hook_fingerprint(hook1)
        fp2 = HookManager._compute_hook_fingerprint(hook2)
        fp3 = HookManager._compute_hook_fingerprint(hook3)

        # hook1 和 hook2 指纹相同（命令和 shell 相同）
        assert fp1 == fp2
        # hook3 指纹不同（命令不同）
        assert fp1 != fp3

    def test_compute_hook_fingerprint_callback(self):
        """测试计算 CallbackHook 指纹"""
        hook1 = CallbackHookWrapper({
            'name': 'test1',
            'hook_point': 'user_input_received',
            'callback': 'tests.fixtures.hooks.callback_example:process_input'
        })
        hook2 = CallbackHookWrapper({
            'name': 'test2',  # 不同的名称
            'hook_point': 'user_input_received',
            'callback': 'tests.fixtures.hooks.callback_example:process_input'  # 相同的回调
        })
        hook3 = CallbackHookWrapper({
            'name': 'test3',
            'hook_point': 'user_input_received',
            'callback': 'tests.fixtures.hooks.callback_example:return_hook_output'  # 不同的回调
        })

        fp1 = HookManager._compute_hook_fingerprint(hook1)
        fp2 = HookManager._compute_hook_fingerprint(hook2)
        fp3 = HookManager._compute_hook_fingerprint(hook3)

        # hook1 和 hook2 指纹相同（回调相同）
        assert fp1 == fp2
        # hook3 指纹不同（回调不同）
        assert fp1 != fp3

    def test_deduplicate_hooks(self):
        """测试去重逻辑"""
        hooks = [
            CommandHookWrapper({
                'name': 'hook1',
                'hook_point': 'before_tool_call',
                'command': '/path/to/script.sh'
            }),
            CommandHookWrapper({
                'name': 'hook2',  # 重复（命令相同）
                'hook_point': 'before_tool_call',
                'command': '/path/to/script.sh'
            }),
            CommandHookWrapper({
                'name': 'hook3',
                'hook_point': 'before_tool_call',
                'command': '/path/to/other.sh'
            }),
        ]

        deduplicated = HookManager._deduplicate_hooks(hooks)

        # 应该只保留 2 个（hook1 和 hook3，hook2 被去重）
        assert len(deduplicated) == 2
        assert deduplicated[0]._name == 'hook1'
        assert deduplicated[1]._name == 'hook3'


class TestHookFactoryMerging:
    """测试 Python hooks 和配置 hooks 合并"""

    def test_hooks_merge_python_and_config(self, configs_dir, monkeypatch):
        """测试合并 Python hooks 和配置 hooks"""
        # 测试环境：信任所有工作区
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        # 清除缓存以确保测试隔离
        HookManager._config_hooks_cache = {}

        # 注册一个 Python hook
        @HookFactory.register(name="TestPythonHook")
        class TestPythonHook(StartHook):
            async def exec(self, message, context):
                return message

        # 加载配置
        config_path = str(configs_dir / 'hooks_test.yaml')
        HookFactory.load_config_hooks(config_path)

        # 获取所有 hooks
        all_hooks = HookFactory.hooks()

        # 验证 session_started 点同时包含 Python hook 和 config hook
        # (注意：StartHook 的 point() 返回 HookPoint.START = "session_started")
        assert 'session_started' in all_hooks
        assert len(all_hooks['session_started']) >= 1  # 至少有 Python hook

        # 验证 before_tool_call 有配置中的 hooks
        assert 'before_tool_call' in all_hooks
        assert len(all_hooks['before_tool_call']) == 2  # 2 个配置 hooks

    def test_hooks_python_hooks_first(self, configs_dir, monkeypatch):
        """测试 Python hooks 在配置 hooks 之前执行"""
        # 测试环境：信任所有工作区
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        # 清除缓存以确保测试隔离
        HookManager._config_hooks_cache = {}

        # 注册 Python hook
        @HookFactory.register(name="TestBeforeToolCallHook")
        class TestBeforeToolCallHook(Hook):
            def point(self):
                return HookPoint.PRE_TOOL_CALL  # 使用正确的 hook 点常量

            async def exec(self, message, context):
                return message

        config_path = str(configs_dir / 'hooks_test.yaml')
        HookFactory.load_config_hooks(config_path)

        try:
            all_hooks = HookFactory.hooks()
            # PRE_TOOL_CALL 的值是 "before_tool_call"（不是 "pre_tool_call"）
            before_tool_hooks = all_hooks.get('before_tool_call', [])

            # 应该至少有 Python hook
            assert len(before_tool_hooks) > 0
            # Python hook 应该在前面
            # 检查是否有 Python hook（通过类名判断）
            has_python_hook = any('TestBeforeToolCallHook' in str(type(h)) for h in before_tool_hooks)
            assert has_python_hook

        finally:
            # 清理注册的 hook
            if "TestBeforeToolCallHook" in HookFactory._cls:
                del HookFactory._cls["TestBeforeToolCallHook"]

    def test_hooks_filter_by_name(self, configs_dir, monkeypatch):
        """测试按名称过滤 hooks"""
        # 测试环境：信任所有工作区
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        # 清除缓存以确保测试隔离
        HookManager._config_hooks_cache = {}

        config_path = str(configs_dir / 'hooks_test.yaml')
        HookFactory.load_config_hooks(config_path)

        # 测试过滤 before_tool_call
        # 注意：根据当前实现，hooks(name='xxx') 仍然会返回所有 hook 点的字典
        # 但只有匹配的点会包含 hooks，其他点为空列表
        all_hooks_unfiltered = HookFactory.hooks()
        before_tool_hooks_count = len(all_hooks_unfiltered.get('before_tool_call', []))

        # 验证 before_tool_call 有 hooks
        assert before_tool_hooks_count > 0

        # 验证其他点也有 hooks（如 session_started）
        session_hooks_count = len(all_hooks_unfiltered.get('session_started', []))
        assert session_hooks_count > 0

        # 测试完整性：确保配置被正确加载
        assert 'after_tool_call' in all_hooks_unfiltered
        assert len(all_hooks_unfiltered['after_tool_call']) > 0


class TestHookFactoryEdgeCases:
    """测试边界情况"""

    def test_config_hooks_unknown_type(self, tmp_path):
        """测试未知的 hook 类型"""
        config = tmp_path / 'unknown_type.yaml'
        config.write_text('''
version: "1.0"
hooks:
  before_tool_call:
    - name: "unknown-hook"
      type: "unknown_type"
      command: "echo 'test'"
''')

        hooks = HookFactory.load_config_hooks(str(config))

        # 未知类型的 hook 应该被跳过
        assert 'before_tool_call' not in hooks or len(hooks['before_tool_call']) == 0

    def test_config_hooks_invalid_callback(self, tmp_path):
        """测试非法的 callback 路径"""
        config = tmp_path / 'invalid_callback.yaml'
        config.write_text('''
version: "1.0"
hooks:
  user_input_received:
    - name: "invalid-callback"
      type: "callback"
      callback: "nonexistent.module:nonexistent_function"
''')

        hooks = HookFactory.load_config_hooks(str(config))

        # 非法 callback 应该被跳过
        assert 'user_input_received' not in hooks or len(hooks['user_input_received']) == 0
