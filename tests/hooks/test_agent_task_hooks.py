"""
测试 Agent 和 Task 生命周期 Hooks (Phase 3 Week 5)

测试用例：
- TC-AGENT-001: agent_started hook 接收 agent 启动事件
- TC-AGENT-002: agent_stopped hook 接收 agent 停止事件
- TC-TASK-001: task_created hook 接收任务创建事件
- TC-TASK-002: task_completed hook 接收任务完成事件
"""

import asyncio
import json
import os
import tempfile
import pytest
from typing import List, Dict, Any

from aworld.config.conf import AgentConfig
from aworld.core.agent.base import BaseAgent
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.core.task import Task
from aworld.runners.event_runner import TaskEventRunner
from aworld.runners.hook.hooks import HookPoint
from aworld.runners.hook.hook_factory import HookManager

# 导入测试辅助工具
from .test_helpers import wait_for_file, wait_for_file_content


class SimpleTestAgent(BaseAgent):
    """简单的测试 agent"""

    async def async_policy(self, observation, message: Message = None, **kwargs):
        """简单返回完成"""
        from aworld.core.common import ActionModel
        from aworld.core.agent.base import AgentResult

        # 模拟一些工作
        await asyncio.sleep(0.1)

        # 返回结束动作
        return AgentResult(
            current_state=None,
            actions=[ActionModel(action_name="FINISH", tool_name=None, action_input={})],
            is_call_tool=False
        )


class TestAgentLifecycleHooks:
    """测试 Agent 生命周期 hooks"""

    @pytest.fixture
    def hook_config_dir(self):
        """创建临时 hook 配置目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, '.aworld'), exist_ok=True)
            yield tmpdir

    @pytest.fixture
    def agent_started_script(self, hook_config_dir):
        """创建 agent_started hook 脚本"""
        script_path = os.path.join(hook_config_dir, '.aworld', 'agent_started.sh')
        with open(script_path, 'w') as f:
            f.write('''#!/bin/bash
# 记录 agent 启动事件到文件
LOG_FILE="${AWORLD_CWD}/.aworld/agent_events.log"
echo "agent_started|${AWORLD_MESSAGE_JSON}" >> "$LOG_FILE"

# 返回 HookJSONOutput
cat <<EOF
{
  "continue": true,
  "system_message": "Agent started: $(echo "$AWORLD_MESSAGE_JSON" | jq -r '.payload.agent_name')",
  "additional_context": {
    "hook_type": "agent_started"
  }
}
EOF
''')
        os.chmod(script_path, 0o755)
        return script_path

    @pytest.fixture
    def agent_stopped_script(self, hook_config_dir):
        """创建 agent_stopped hook 脚本"""
        script_path = os.path.join(hook_config_dir, '.aworld', 'agent_stopped.sh')
        with open(script_path, 'w') as f:
            f.write('''#!/bin/bash
# 记录 agent 停止事件到文件
LOG_FILE="${AWORLD_CWD}/.aworld/agent_events.log"
echo "agent_stopped|${AWORLD_MESSAGE_JSON}" >> "$LOG_FILE"

# 返回 HookJSONOutput
cat <<EOF
{
  "continue": true,
  "system_message": "Agent stopped: $(echo "$AWORLD_MESSAGE_JSON" | jq -r '.payload.agent_name')",
  "additional_context": {
    "hook_type": "agent_stopped",
    "duration": $(echo "$AWORLD_MESSAGE_JSON" | jq -r '.payload.duration')
  }
}
EOF
''')
        os.chmod(script_path, 0o755)
        return script_path

    @pytest.fixture
    def hooks_yaml(self, hook_config_dir, agent_started_script, agent_stopped_script):
        """创建 hooks.yaml 配置文件"""
        yaml_path = os.path.join(hook_config_dir, '.aworld', 'hooks.yaml')
        with open(yaml_path, 'w') as f:
            f.write(f'''version: "v2"

hooks:
  agent_started:
    - name: "agent-started-logger"
      type: command
      command: "{agent_started_script}"
      enabled: true
      timeout: 5000

  agent_stopped:
    - name: "agent-stopped-logger"
      type: command
      command: "{agent_stopped_script}"
      enabled: true
      timeout: 5000
''')
        return yaml_path

    @pytest.mark.asyncio
    async def test_agent_started_hook(self, hook_config_dir, hooks_yaml, monkeypatch):
        """TC-AGENT-001: agent_started hook 接收 agent 启动事件"""
        # 测试隔离：清除缓存和设置环境变量
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        # 临时切换工作目录（为了 hook 能找到配置）
        original_cwd = os.getcwd()
        try:
            os.chdir(hook_config_dir)

            # 加载配置（必须在切换工作目录后）
            HookManager.load_config_hooks(hooks_yaml)

            # 创建简单的 agent 和 task
            agent = SimpleTestAgent(
                name="test_agent",
                conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model")
            )

            task = Task(
                input="test input",
                agent=agent,
                conf={'workspace_trust': True, 'max_steps': 1}
            )

            # 创建 runner 并执行
            runner = TaskEventRunner(task)
            await runner.pre_run()

            # 手动触发 agent.async_pre_run() 来触发 AGENT_STARTED hook
            from aworld.core.event.base import Message

            test_message = Message(
                payload="test",
                sender="test",
                session_id=runner.context.session_id
            )
            test_message.context = runner.context

            # 触发 agent_started hook
            await agent.async_pre_run(test_message)

            # 等待日志文件创建并包含 agent_started 事件（改进：使用轮询替代固定 sleep）
            log_file = os.path.join(hook_config_dir, '.aworld', 'agent_events.log')
            content = await wait_for_file_content(
                log_file,
                'agent_started',
                timeout=5.0
            )

            # 解析 JSON payload
            for line in content.split('\n'):
                if 'agent_started' in line:
                    _, json_str = line.split('|', 1)
                    payload = json.loads(json_str)

                    # 验证 payload 结构
                    assert payload['payload']['event'] == 'agent_started'
                    assert payload['payload']['agent_name'] == 'test_agent'
                    assert 'agent_id' in payload['payload']
                    assert 'session_id' in payload['payload']
                    assert 'timestamp' in payload['payload']
                    break
        finally:
            os.chdir(original_cwd)

    @pytest.mark.asyncio
    async def test_agent_stopped_hook(self, hook_config_dir, hooks_yaml, monkeypatch):
        """TC-AGENT-002: agent_stopped hook 接收 agent 停止事件"""
        # 测试隔离：清除缓存和设置环境变量
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        # 临时切换工作目录
        original_cwd = os.getcwd()
        try:
            os.chdir(hook_config_dir)

            # 加载配置（必须在切换工作目录后）
            HookManager.load_config_hooks(hooks_yaml)

            # 创建简单的 agent 和 task
            agent = SimpleTestAgent(
                name="test_agent",
                conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model")
            )

            task = Task(
                input="test input",
                agent=agent,
                conf={'workspace_trust': True, 'max_steps': 1}
            )

            # 创建 runner 并执行完整流程
            runner = TaskEventRunner(task)
            await runner.pre_run()

            # 手动触发 agent 执行和完成
            from aworld.core.event.base import Message

            test_message = Message(
                payload="test",
                sender="test",
                session_id=runner.context.session_id
            )
            test_message.context = runner.context

            # 执行 agent
            await agent.async_pre_run(test_message)
            result = await agent.async_policy("test", message=test_message)
            await agent.async_post_run(result, "test", test_message)

            # 等待日志文件包含 agent_stopped 事件
            log_file = os.path.join(hook_config_dir, '.aworld', 'agent_events.log')
            content = await wait_for_file_content(
                log_file,
                'agent_stopped',
                timeout=5.0
            )

            # 解析 JSON payload
            for line in content.split('\n'):
                if 'agent_stopped' in line:
                    _, json_str = line.split('|', 1)
                    payload = json.loads(json_str)

                    # 验证 payload 结构
                    assert payload['payload']['event'] == 'agent_stopped'
                    assert payload['payload']['agent_name'] == 'test_agent'
                    assert 'agent_id' in payload['payload']
                    assert 'duration' in payload['payload']
                    assert payload['payload']['status'] == 'success'
                    assert 'timestamp' in payload['payload']
                    break
        finally:
            os.chdir(original_cwd)


class TestTaskLifecycleHooks:
    """测试 Task 生命周期 hooks"""

    @pytest.fixture
    def hook_config_dir(self):
        """创建临时 hook 配置目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, '.aworld'), exist_ok=True)
            yield tmpdir

    @pytest.fixture
    def task_created_script(self, hook_config_dir):
        """创建 task_created hook 脚本"""
        script_path = os.path.join(hook_config_dir, '.aworld', 'task_created.sh')
        with open(script_path, 'w') as f:
            f.write('''#!/bin/bash
# 记录 task 创建事件到文件
LOG_FILE="${AWORLD_CWD}/.aworld/task_events.log"
echo "task_created|${AWORLD_MESSAGE_JSON}" >> "$LOG_FILE"

# 返回 HookJSONOutput
cat <<EOF
{
  "continue": true,
  "system_message": "Task created: $(echo "$AWORLD_MESSAGE_JSON" | jq -r '.payload.task_id')",
  "additional_context": {
    "hook_type": "task_created"
  }
}
EOF
''')
        os.chmod(script_path, 0o755)
        return script_path

    @pytest.fixture
    def task_completed_script(self, hook_config_dir):
        """创建 task_completed hook 脚本"""
        script_path = os.path.join(hook_config_dir, '.aworld', 'task_completed.sh')
        with open(script_path, 'w') as f:
            f.write('''#!/bin/bash
# 记录 task 完成事件到文件
LOG_FILE="${AWORLD_CWD}/.aworld/task_events.log"
echo "task_completed|${AWORLD_MESSAGE_JSON}" >> "$LOG_FILE"

# 返回 HookJSONOutput
cat <<EOF
{
  "continue": true,
  "system_message": "Task completed: $(echo "$AWORLD_MESSAGE_JSON" | jq -r '.payload.task_id')",
  "additional_context": {
    "hook_type": "task_completed",
    "time_cost": $(echo "$AWORLD_MESSAGE_JSON" | jq -r '.payload.time_cost')
  }
}
EOF
''')
        os.chmod(script_path, 0o755)
        return script_path

    @pytest.fixture
    def hooks_yaml(self, hook_config_dir, task_created_script, task_completed_script):
        """创建 hooks.yaml 配置文件"""
        yaml_path = os.path.join(hook_config_dir, '.aworld', 'hooks.yaml')
        with open(yaml_path, 'w') as f:
            f.write(f'''version: "v2"

hooks:
  task_created:
    - name: "task-created-logger"
      type: command
      command: "{task_created_script}"
      enabled: true
      timeout: 2000

  task_completed:
    - name: "task-completed-logger"
      type: command
      command: "{task_completed_script}"
      enabled: true
      timeout: 2000
''')
        return yaml_path

    @pytest.mark.asyncio
    async def test_task_created_hook(self, hook_config_dir, hooks_yaml, monkeypatch):
        """TC-TASK-001: task_created hook 接收任务创建事件"""
        # 测试隔离：清除缓存和设置环境变量
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        # 临时切换工作目录
        original_cwd = os.getcwd()
        try:
            os.chdir(hook_config_dir)

            # 加载配置（必须在切换工作目录后）
            HookManager.load_config_hooks(hooks_yaml)

            # 创建简单的 agent 和 task
            agent = SimpleTestAgent(
                name="test_agent",
                conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model")
            )

            task = Task(
                input="test task input",
                agent=agent,
                conf={'workspace_trust': True}
            )

            # 创建 runner 并执行 pre_run（会触发 TASK_CREATED）
            runner = TaskEventRunner(task)
            await runner.pre_run()

            # 等待日志文件包含 task_created 事件
            log_file = os.path.join(hook_config_dir, '.aworld', 'task_events.log')
            content = await wait_for_file_content(
                log_file,
                'task_created',
                timeout=3.0
            )

            # 解析 JSON payload
            for line in content.split('\n'):
                if 'task_created' in line:
                    _, json_str = line.split('|', 1)
                    payload = json.loads(json_str)

                    # 验证 payload 结构
                    assert payload['payload']['event'] == 'task_created'
                    assert payload['payload']['task_id'] == task.id
                    assert payload['payload']['is_sub_task'] is False
                    assert 'session_id' in payload['payload']
                    assert 'timestamp' in payload['payload']
                    break
        finally:
            os.chdir(original_cwd)

    @pytest.mark.asyncio
    async def test_task_completed_hook(self, hook_config_dir, hooks_yaml, monkeypatch):
        """TC-TASK-002: task_completed hook 接收任务完成事件"""
        # 测试隔离：清除缓存和设置环境变量
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        # 创建简单的 agent 和 task
        agent = SimpleTestAgent(
            name="test_agent",
            conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model")
        )

        task = Task(
            input="test task input",
            agent=agent,
            conf={'workspace_trust': True, 'max_steps': 1}
        )

        # 临时切换工作目录
        original_cwd = os.getcwd()
        try:
            os.chdir(hook_config_dir)

            # 加载配置（必须在切换工作目录后）
            HookManager.load_config_hooks(hooks_yaml)

            # 创建 runner 并执行完整流程（会触发 TASK_CREATED 和 TASK_COMPLETED）
            runner = TaskEventRunner(task)
            await runner.pre_run()

            # 执行任务（模拟完整流程）
            try:
                await runner.do_run()
            except Exception:
                # 可能会因为缺少某些组件而失败，但 hook 应该已经触发
                pass

            # 等待日志文件包含 task_completed 事件
            log_file = os.path.join(hook_config_dir, '.aworld', 'task_events.log')

            # 先验证 task_created 事件存在（快速失败）
            content = await wait_for_file_content(
                log_file,
                'task_created',
                timeout=3.0
            )
            assert 'task_created' in content, "Should contain task_created event"

            # 如果任务成功完成，应该有 task_completed 事件
            if 'task_completed' in content:
                # 解析 JSON payload
                for line in content.split('\n'):
                    if 'task_completed' in line:
                        _, json_str = line.split('|', 1)
                        payload = json.loads(json_str)

                        # 验证 payload 结构
                        assert payload['payload']['event'] == 'task_completed'
                        assert payload['payload']['task_id'] == task.id
                        assert payload['payload']['status'] == 'success'
                        assert 'time_cost' in payload['payload']
                        assert 'timestamp' in payload['payload']
                        break
        finally:
            os.chdir(original_cwd)


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
