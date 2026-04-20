"""测试 P0-1: 运行时自动加载 hooks 配置

测试用例：
- TC-AUTO-001: 调用 hooks() 时自动加载当前工作区配置
- TC-AUTO-002: 不重复加载已加载的配置
- TC-AUTO-003: 显式加载仍然有效
"""

import os
import tempfile
import pytest

from aworld.runners.hook.hook_factory import HookFactory, HookManager


class TestAutoLoadConfig:
    """测试自动配置加载"""

    @pytest.fixture
    def hook_config_dir(self):
        """创建临时 hook 配置目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, '.aworld'), exist_ok=True)
            yield tmpdir

    @pytest.fixture
    def hooks_yaml(self, hook_config_dir):
        """创建 hooks.yaml 配置文件"""
        yaml_path = os.path.join(hook_config_dir, '.aworld', 'hooks.yaml')

        # 创建一个简单的测试脚本
        test_script = os.path.join(hook_config_dir, '.aworld', 'test_hook.sh')
        with open(test_script, 'w') as f:
            f.write('''#!/bin/bash
cat <<EOF
{
  "continue": true,
  "system_message": "Auto-loaded hook executed"
}
EOF
''')
        os.chmod(test_script, 0o755)

        # 创建配置文件
        with open(yaml_path, 'w') as f:
            f.write(f'''version: "v2"

hooks:
  before_tool_call:
    - name: "auto-load-test-hook"
      type: command
      command: "{test_script}"
      enabled: true
      timeout: 2000
''')
        return yaml_path

    def test_auto_load_on_hooks_call(self, hook_config_dir, hooks_yaml, monkeypatch):
        """TC-AUTO-001: 调用 hooks() 时自动加载当前工作区配置"""
        # 测试隔离
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        # 临时切换工作目录
        original_cwd = os.getcwd()
        try:
            os.chdir(hook_config_dir)

            # 验证配置未加载
            assert len(HookManager._config_hooks_cache) == 0

            # 调用 hooks()（不显式加载配置）
            all_hooks = HookFactory.hooks()

            # 验证配置已自动加载（使用 realpath 规范化路径）
            config_path = os.path.realpath(os.path.join(hook_config_dir, '.aworld', 'hooks.yaml'))
            assert config_path in HookManager._config_hooks_cache, "配置应该被自动加载"

            # 验证 hook 存在
            assert 'before_tool_call' in all_hooks
            assert len(all_hooks['before_tool_call']) > 0

            # 验证是我们的测试 hook
            hook_names = [h._name for h in all_hooks['before_tool_call']]
            assert 'auto-load-test-hook' in hook_names

        finally:
            os.chdir(original_cwd)

    def test_no_duplicate_loading(self, hook_config_dir, hooks_yaml, monkeypatch):
        """TC-AUTO-002: 不重复加载已加载的配置"""
        # 测试隔离
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        # 临时切换工作目录
        original_cwd = os.getcwd()
        try:
            os.chdir(hook_config_dir)

            # 第一次调用 hooks()
            hooks1 = HookFactory.hooks()

            # 记录加载时间戳（使用 realpath 规范化路径）
            config_path = os.path.realpath(os.path.join(hook_config_dir, '.aworld', 'hooks.yaml'))
            first_timestamp = HookManager._config_hooks_cache[config_path]['mtime']

            # 第二次调用 hooks()
            hooks2 = HookFactory.hooks()

            # 验证时间戳未变（未重新加载）
            second_timestamp = HookManager._config_hooks_cache[config_path]['mtime']
            assert first_timestamp == second_timestamp, "配置不应该被重复加载"

            # 验证结果一致
            assert len(hooks1['before_tool_call']) == len(hooks2['before_tool_call'])

        finally:
            os.chdir(original_cwd)

    def test_explicit_loading_still_works(self, hook_config_dir, hooks_yaml, monkeypatch):
        """TC-AUTO-003: 显式加载仍然有效"""
        # 测试隔离
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        # 临时切换工作目录
        original_cwd = os.getcwd()
        try:
            os.chdir(hook_config_dir)

            # 显式加载配置
            HookManager.load_config_hooks(hooks_yaml)

            # 验证配置已加载（使用 realpath 规范化路径）
            config_path = os.path.realpath(os.path.join(hook_config_dir, '.aworld', 'hooks.yaml'))
            assert config_path in HookManager._config_hooks_cache

            # 调用 hooks()（不应重新加载）
            all_hooks = HookFactory.hooks()

            # 验证 hook 存在
            assert 'before_tool_call' in all_hooks
            hook_names = [h._name for h in all_hooks['before_tool_call']]
            assert 'auto-load-test-hook' in hook_names

        finally:
            os.chdir(original_cwd)

    def test_no_auto_load_without_config_file(self, hook_config_dir, monkeypatch):
        """TC-AUTO-004: 没有配置文件时不会出错"""
        # 测试隔离
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        # 创建没有 hooks.yaml 的临时目录
        with tempfile.TemporaryDirectory() as empty_dir:
            original_cwd = os.getcwd()
            try:
                os.chdir(empty_dir)

                # 调用 hooks() 不应该报错
                all_hooks = HookFactory.hooks()

                # 应该只有 Python hooks（如果有的话）
                assert isinstance(all_hooks, dict)
                # 验证没有加载任何配置
                assert len(HookManager._config_hooks_cache) == 0

            finally:
                os.chdir(original_cwd)

    def test_auto_load_respects_workspace_path_parameter(self, hook_config_dir, hooks_yaml, monkeypatch):
        """TC-AUTO-005: 使用 workspace_path 参数时自动加载正确的配置"""
        # 测试隔离
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        # 不切换工作目录，而是通过参数指定
        original_cwd = os.getcwd()

        # 调用 hooks() 并指定 workspace_path
        all_hooks = HookFactory.hooks(workspace_path=hook_config_dir)

        # 验证配置已自动加载（使用 realpath 规范化路径）
        config_path = os.path.realpath(os.path.join(hook_config_dir, '.aworld', 'hooks.yaml'))
        assert config_path in HookManager._config_hooks_cache, "应该加载指定路径的配置"

        # 验证 hook 存在
        assert 'before_tool_call' in all_hooks
        hook_names = [h._name for h in all_hooks['before_tool_call']]
        assert 'auto-load-test-hook' in hook_names


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
