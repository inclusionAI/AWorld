"""
测试 LLM Call Hooks (Phase 3 Week 6)

测试用例：
- TC-LLM-001: before_llm_call hook 接收 LLM 调用前事件
- TC-LLM-002: after_llm_call hook 接收 LLM 调用后事件
"""

import asyncio
import json
import os
import tempfile
import pytest
from typing import List, Dict, Any
from unittest.mock import AsyncMock, Mock

from aworld.config.conf import ModelConfig
from aworld.models.llm import LLMModel
from aworld.models.model_response import ModelResponse
from aworld.core.context.session import Session
from aworld.core.context.amni import AmniContext
from aworld.runners.hook.hooks import HookPoint
from aworld.runners.hook.hook_factory import HookManager
from aworld.core.llm_provider import LLMProviderBase

# 导入测试辅助工具
from .test_helpers import wait_for_file_content


class MockLLMProvider(LLMProviderBase):
    """Mock LLM Provider for testing"""

    def __init__(self, model_name="mock-model", **kwargs):
        super().__init__(model_name=model_name, **kwargs)

    def _init_provider(self):
        """Initialize provider (required abstract method)"""
        pass

    def postprocess_response(self, response, **kwargs):
        """Postprocess response (required abstract method)"""
        return response

    async def acompletion(self, messages, **kwargs):
        """Mock async completion"""
        await asyncio.sleep(0.1)  # 模拟异步调用
        return ModelResponse(
            id="mock-response-id",
            model=self.model_name,
            content="Mock response from LLM",
            usage={"total_tokens": 50, "completion_tokens": 30, "prompt_tokens": 20}
        )

    def completion(self, messages, **kwargs):
        """Mock sync completion"""
        return ModelResponse(
            id="mock-response-id",
            model=self.model_name,
            content="Mock response from LLM",
            usage={"total_tokens": 50, "completion_tokens": 30, "prompt_tokens": 20}
        )


class TestLLMCallHooks:
    """测试 LLM Call hooks"""

    @pytest.fixture
    def hook_config_dir(self):
        """创建临时 hook 配置目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, '.aworld'), exist_ok=True)
            yield tmpdir

    @pytest.fixture
    def before_llm_call_script(self, hook_config_dir):
        """创建 before_llm_call hook 脚本"""
        script_path = os.path.join(hook_config_dir, '.aworld', 'before_llm_call.sh')
        with open(script_path, 'w') as f:
            f.write('''#!/bin/bash
# 记录 LLM 调用前事件到文件
LOG_FILE="${AWORLD_CWD}/.aworld/llm_events.log"
echo "before_llm_call|${AWORLD_MESSAGE_JSON}" >> "$LOG_FILE"

# 返回 HookJSONOutput
cat <<EOF
{
  "continue": true,
  "system_message": "LLM call started: $(echo "$AWORLD_MESSAGE_JSON" | jq -r '.payload.model_name')",
  "additional_context": {
    "hook_type": "before_llm_call"
  }
}
EOF
''')
        os.chmod(script_path, 0o755)
        return script_path

    @pytest.fixture
    def after_llm_call_script(self, hook_config_dir):
        """创建 after_llm_call hook 脚本"""
        script_path = os.path.join(hook_config_dir, '.aworld', 'after_llm_call.sh')
        with open(script_path, 'w') as f:
            f.write('''#!/bin/bash
# 记录 LLM 调用后事件到文件
LOG_FILE="${AWORLD_CWD}/.aworld/llm_events.log"
echo "after_llm_call|${AWORLD_MESSAGE_JSON}" >> "$LOG_FILE"

# 返回 HookJSONOutput
cat <<EOF
{
  "continue": true,
  "system_message": "LLM call completed: $(echo "$AWORLD_MESSAGE_JSON" | jq -r '.payload.time_cost')s",
  "additional_context": {
    "hook_type": "after_llm_call"
  }
}
EOF
''')
        os.chmod(script_path, 0o755)
        return script_path

    @pytest.fixture
    def hooks_yaml(self, hook_config_dir, before_llm_call_script, after_llm_call_script):
        """创建 hooks.yaml 配置文件"""
        yaml_path = os.path.join(hook_config_dir, '.aworld', 'hooks.yaml')
        with open(yaml_path, 'w') as f:
            f.write(f'''version: "v2"

hooks:
  before_llm_call:
    - name: "before-llm-call-logger"
      type: command
      command: "{before_llm_call_script}"
      shell: "/bin/bash"
      enabled: true
      timeout: 2000

  after_llm_call:
    - name: "after-llm-call-logger"
      type: command
      command: "{after_llm_call_script}"
      shell: "/bin/bash"
      enabled: true
      timeout: 2000
''')
        return yaml_path

    @pytest.mark.asyncio
    async def test_before_llm_call_hook(self, hook_config_dir, hooks_yaml, monkeypatch):
        """TC-LLM-001: before_llm_call hook 接收 LLM 调用前事件"""
        # 测试隔离：清除缓存和设置环境变量
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        # 临时切换工作目录
        original_cwd = os.getcwd()
        try:
            os.chdir(hook_config_dir)

            # 加载配置（必须在切换工作目录后）
            HookManager.load_config_hooks(hooks_yaml)

            # 创建 mock LLM model
            llm_model = LLMModel(custom_provider=MockLLMProvider())

            # 创建 context
            session = Session()
            context = AmniContext(session_id=session.session_id, task_id="test-task")
            context.trace_id = "test-trace"

            # 调用 LLM
            messages = [{"role": "user", "content": "Hello"}]
            response = await llm_model.acompletion(messages, context=context)

            # 等待日志文件包含 before_llm_call 事件
            log_file = os.path.join(hook_config_dir, '.aworld', 'llm_events.log')
            content = await wait_for_file_content(
                log_file,
                'before_llm_call',
                timeout=3.0
            )

            # 解析 JSON payload
            for line in content.split('\n'):
                if 'before_llm_call' in line:
                    _, json_str = line.split('|', 1)
                    payload = json.loads(json_str)

                    # 验证 payload 结构
                    assert payload['payload']['event'] == 'before_llm_call'
                    assert payload['payload']['model_name'] == 'mock-model'
                    assert 'provider_name' in payload['payload']
                    assert 'messages' in payload['payload']
                    assert 'request_id' in payload['payload']
                    assert 'timestamp' in payload['payload']
                    break
        finally:
            os.chdir(original_cwd)

    @pytest.mark.asyncio
    async def test_after_llm_call_hook(self, hook_config_dir, hooks_yaml, monkeypatch):
        """TC-LLM-002: after_llm_call hook 接收 LLM 调用后事件"""
        # 测试隔离：清除缓存和设置环境变量
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        # 临时切换工作目录
        original_cwd = os.getcwd()
        try:
            os.chdir(hook_config_dir)

            # 加载配置（必须在切换工作目录后）
            HookManager.load_config_hooks(hooks_yaml)

            # 创建 mock LLM model
            llm_model = LLMModel(custom_provider=MockLLMProvider())

            # 创建 context
            session = Session()
            context = AmniContext(session_id=session.session_id, task_id="test-task")
            context.trace_id = "test-trace"

            # 调用 LLM
            messages = [{"role": "user", "content": "Hello"}]
            response = await llm_model.acompletion(messages, context=context)

            # 等待日志文件包含 after_llm_call 事件
            log_file = os.path.join(hook_config_dir, '.aworld', 'llm_events.log')
            content = await wait_for_file_content(
                log_file,
                'after_llm_call',
                timeout=3.0
            )

            # 解析 JSON payload
            for line in content.split('\n'):
                if 'after_llm_call' in line:
                    _, json_str = line.split('|', 1)
                    payload = json.loads(json_str)

                    # 验证 payload 结构
                    assert payload['payload']['event'] == 'after_llm_call'
                    assert payload['payload']['model_name'] == 'mock-model'
                    assert 'request_id' in payload['payload']
                    assert 'time_cost' in payload['payload']
                    assert payload['payload']['status'] == 'success'
                    assert 'response_content' in payload['payload']
                    assert 'timestamp' in payload['payload']
                    break
        finally:
            os.chdir(original_cwd)


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
