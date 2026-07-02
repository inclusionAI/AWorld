"""测试 HookJSONOutput 协议"""

import json

import pytest

from aworld.runners.hook.v2.protocol import HookJSONOutput


class TestHookJSONOutputParsing:
    """测试 JSON 解析功能"""

    def test_parse_basic_fields(self):
        """测试基本字段解析"""
        json_str = '''
        {
            "continue": false,
            "stopReason": "Access denied"
        }
        '''
        output = HookJSONOutput.from_json(json_str)

        assert output.continue_ is False
        assert output.stop_reason == "Access denied"

    def test_parse_camelcase_fields(self):
        """测试 camelCase 字段名映射"""
        json_str = '''
        {
            "systemMessage": "Warning",
            "additionalContext": "Extra info",
            "updatedInput": {"path": "/new/path"},
            "updatedOutput": {"result": "modified"},
            "permissionDecision": "deny",
            "permissionDecisionReason": "Restricted",
            "watchPaths": ["/path1", "/path2"],
            "asyncTaskId": "task-123",
            "asyncTimeout": 5000,
            "hookSpecificOutput": {"custom": "value"}
        }
        '''
        output = HookJSONOutput.from_json(json_str)

        assert output.system_message == "Warning"
        assert output.additional_context == "Extra info"
        assert output.updated_input == {"path": "/new/path"}
        assert output.updated_output == {"result": "modified"}
        assert output.permission_decision == "deny"
        assert output.permission_decision_reason == "Restricted"
        assert output.watch_paths == ["/path1", "/path2"]
        assert output.async_task_id == "task-123"
        assert output.async_timeout == 5000
        assert output.hook_specific_output == {"custom": "value"}

    def test_parse_snake_case_fields(self):
        """测试 snake_case 字段名"""
        json_str = '''
        {
            "system_message": "Info",
            "additional_context": "Context",
            "updated_input": {"key": "value"},
            "permission_decision": "allow"
        }
        '''
        output = HookJSONOutput.from_json(json_str)

        assert output.system_message == "Info"
        assert output.additional_context == "Context"
        assert output.updated_input == {"key": "value"}
        assert output.permission_decision == "allow"

    def test_parse_async_fields(self):
        """测试异步字段解析"""
        json_str = '''
        {
            "async": true,
            "asyncTaskId": "task-456",
            "asyncTimeout": 30000
        }
        '''
        output = HookJSONOutput.from_json(json_str)

        assert output.async_ is True
        assert output.async_task_id == "task-456"
        assert output.async_timeout == 30000

    def test_parse_permission_decisions(self):
        """测试权限决策字段"""
        # allow
        output1 = HookJSONOutput.from_json('{"permissionDecision": "allow"}')
        assert output1.permission_decision == "allow"

        # deny
        output2 = HookJSONOutput.from_json('{"permissionDecision": "deny"}')
        assert output2.permission_decision == "deny"

        # ask
        output3 = HookJSONOutput.from_json('{"permissionDecision": "ask"}')
        assert output3.permission_decision == "ask"

    def test_parse_invalid_permission_decision(self):
        """测试非法权限决策值"""
        json_str = '{"permissionDecision": "invalid_value"}'

        with pytest.raises(ValueError, match="Invalid permission_decision"):
            HookJSONOutput.from_json(json_str)

    def test_parse_empty_json(self):
        """测试空 JSON"""
        output = HookJSONOutput.from_json('{}')

        # 默认值
        assert output.continue_ is True
        assert output.stop_reason is None
        assert output.system_message is None

    def test_parse_partial_json(self):
        """测试部分字段 JSON"""
        json_str = '{"continue": true, "systemMessage": "OK"}'
        output = HookJSONOutput.from_json(json_str)

        assert output.continue_ is True
        assert output.system_message == "OK"
        assert output.additional_context is None


class TestHookJSONOutputValidation:
    """测试字段验证"""

    def test_invalid_json_raises_error(self):
        """测试非法 JSON 抛出异常"""
        invalid_json = '{continue: true}'  # 缺少引号

        with pytest.raises(json.JSONDecodeError):
            HookJSONOutput.from_json(invalid_json)

    def test_permission_decision_enum_validation(self):
        """测试 permission_decision 枚举验证"""
        # 合法值
        for decision in ['allow', 'deny', 'ask']:
            output = HookJSONOutput.from_json(f'{{"permissionDecision": "{decision}"}}')
            assert output.permission_decision == decision

        # 非法值
        with pytest.raises(ValueError):
            HookJSONOutput.from_json('{"permissionDecision": "unknown"}')


class TestHookJSONOutputSerialization:
    """测试序列化功能"""

    def test_to_dict_basic(self):
        """测试基本 to_dict"""
        output = HookJSONOutput(
            continue_=False,
            stop_reason="Test stop"
        )
        result = output.to_dict()

        assert result == {
            "continue": False,
            "stopReason": "Test stop"
        }

    def test_to_dict_full(self):
        """测试完整字段 to_dict"""
        output = HookJSONOutput(
            continue_=True,
            system_message="Info",
            additional_context="Context",
            updated_input={"key": "value"},
            updated_output={"result": "modified"},
            permission_decision="deny",
            permission_decision_reason="Restricted",
            watch_paths=["/path"],
            async_=True,
            async_task_id="task-123",
            async_timeout=5000,
            hook_specific_output={"custom": "data"}
        )
        result = output.to_dict()

        # 只包含非 None 且非默认值的字段
        assert "systemMessage" in result
        assert "additionalContext" in result
        assert "updatedInput" in result
        assert "updatedOutput" in result
        assert "permissionDecision" in result
        assert "permissionDecisionReason" in result
        assert "watchPaths" in result
        assert "async" in result
        assert "asyncTaskId" in result
        assert "asyncTimeout" in result
        assert "hookSpecificOutput" in result

    def test_to_dict_omits_none_values(self):
        """测试 to_dict 省略 None 值"""
        output = HookJSONOutput(
            continue_=True,
            stop_reason=None,
            system_message=None
        )
        result = output.to_dict()

        # None 值不应该出现在结果中
        assert "stopReason" not in result
        assert "systemMessage" not in result


class TestHookJSONOutputRepr:
    """测试字符串表示"""

    def test_repr_basic(self):
        """测试基本 repr"""
        output = HookJSONOutput(continue_=True)
        assert "continue=True" in repr(output)

    def test_repr_with_stop(self):
        """测试带停止原因的 repr"""
        output = HookJSONOutput(continue_=False, stop_reason="Denied")
        repr_str = repr(output)

        assert "continue=False" in repr_str
        assert "stop_reason='Denied'" in repr_str

    def test_repr_with_permission(self):
        """测试带权限决策的 repr"""
        output = HookJSONOutput(permission_decision="deny")
        repr_str = repr(output)

        assert "permission_decision='deny'" in repr_str


class TestLegacyProtocolCompatibility:
    """测试 P1: Legacy Protocol 兼容性

    测试旧版 hook 脚本的 prevent_continuation 字段兼容性。
    """

    def test_prevent_continuation_true_maps_to_deny(self):
        """TC-LEGACY-001: prevent_continuation: true → permission_decision: 'deny'"""
        json_str = '''
        {
            "continue": true,
            "prevent_continuation": true
        }
        '''
        output = HookJSONOutput.from_json(json_str)

        # 验证映射结果
        assert output.permission_decision == "deny"
        assert output.permission_decision_reason == "Blocked by legacy prevent_continuation flag"

    def test_prevent_continuation_false_no_effect(self):
        """TC-LEGACY-002: prevent_continuation: false → 无影响（默认行为）"""
        json_str = '''
        {
            "continue": true,
            "prevent_continuation": false
        }
        '''
        output = HookJSONOutput.from_json(json_str)

        # 验证没有设置 permission_decision
        assert output.permission_decision is None

    def test_permission_decision_takes_precedence(self):
        """TC-LEGACY-003: 新字段优先级高于旧字段"""
        json_str = '''
        {
            "permissionDecision": "allow",
            "prevent_continuation": true
        }
        '''
        output = HookJSONOutput.from_json(json_str)

        # 验证新字段优先
        assert output.permission_decision == "allow"

    def test_prevent_continuation_with_custom_reason(self):
        """TC-LEGACY-004: prevent_continuation 与自定义原因共存"""
        json_str = '''
        {
            "prevent_continuation": true,
            "permissionDecisionReason": "Custom block reason"
        }
        '''
        output = HookJSONOutput.from_json(json_str)

        # 验证使用自定义原因
        assert output.permission_decision == "deny"
        assert output.permission_decision_reason == "Custom block reason"

    def test_legacy_prevent_continuation_snake_case(self):
        """TC-LEGACY-005: 支持 snake_case 格式的旧字段"""
        json_str = '''
        {
            "prevent_continuation": true
        }
        '''
        output = HookJSONOutput.from_json(json_str)

        assert output.permission_decision == "deny"

    def test_legacy_protocol_backward_compatibility(self):
        """TC-LEGACY-006: 完整的旧版协议场景"""
        # 模拟旧版 hook 脚本输出
        json_str = '''
        {
            "continue": true,
            "prevent_continuation": true,
            "systemMessage": "Access denied by legacy hook"
        }
        '''
        output = HookJSONOutput.from_json(json_str)

        # 验证完整映射
        assert output.continue_ is True
        assert output.permission_decision == "deny"
        assert output.system_message == "Access denied by legacy hook"
        assert "legacy" in output.permission_decision_reason.lower()

    def test_legacy_and_new_protocol_mixed(self):
        """TC-LEGACY-007: 新旧协议混合使用场景"""
        # 新字段 + 旧字段同时存在
        json_str = '''
        {
            "permissionDecision": "ask",
            "prevent_continuation": true,
            "permissionDecisionReason": "Please confirm"
        }
        '''
        output = HookJSONOutput.from_json(json_str)

        # 验证新字段优先
        assert output.permission_decision == "ask"
        assert output.permission_decision_reason == "Please confirm"
