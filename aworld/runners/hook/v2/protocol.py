"""Hook 输出协议定义

Defines the JSON output format for hooks, supporting:
- Flow control (continue/stop)
- Permission decisions (allow/deny/ask)
- Input/output modification
- System messages and additional context
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


@dataclass
class HookJSONOutput:
    """Hook 输出协议（JSON 格式）

    Hook 可以返回 JSON 格式的输出，控制执行流程、修改参数、注入上下文等。

    Attributes:
        continue_: 是否继续执行（默认 True）
        stop_reason: 停止原因（当 continue_=False 时）
        system_message: 系统消息（显示给用户）
        additional_context: 额外上下文（注入到对话）
        updated_input: 修改后的输入参数（PreToolUse hooks）
        updated_output: 修改后的输出结果（PostToolUse hooks）
        permission_decision: 权限决策 (allow/deny/ask)
        permission_decision_reason: 拒绝原因或确认提示文本
        watch_paths: 监听的文件路径列表
        async_: 是否异步执行
        async_task_id: 异步任务 ID
        async_timeout: 异步超时时间（毫秒）
        hook_specific_output: Hook 特定的输出字段

    Example:
        >>> output = HookJSONOutput.from_json('{"continue": false, "stopReason": "Access denied"}')
        >>> output.continue_
        False
        >>> output.stop_reason
        'Access denied'
    """

    # === 流程控制 ===
    continue_: bool = True
    stop_reason: Optional[str] = None

    # === 消息和上下文 ===
    system_message: Optional[str] = None
    additional_context: Optional[str] = None

    # === 输入输出修改 ===
    updated_input: Optional[Dict[str, Any]] = None
    updated_output: Optional[Dict[str, Any]] = None

    # === Permission 控制 ===
    permission_decision: Optional[Literal['allow', 'deny', 'ask']] = None
    permission_decision_reason: Optional[str] = None

    # === 文件监听 ===
    watch_paths: Optional[List[str]] = None

    # === 异步执行 ===
    async_: bool = False
    async_task_id: Optional[str] = None
    async_timeout: Optional[int] = None  # milliseconds

    # === Hook 特定输出 ===
    hook_specific_output: Optional[Dict[str, Any]] = None

    @classmethod
    def from_json(cls, json_str: str) -> 'HookJSONOutput':
        """从 JSON 字符串解析 Hook 输出

        支持的字段名映射：
        - continue -> continue_
        - async -> async_
        - stopReason/stop_reason -> stop_reason
        - systemMessage/system_message -> system_message
        - additionalContext/additional_context -> additional_context
        - updatedInput/updated_input -> updated_input
        - updatedOutput/updated_output -> updated_output
        - permissionDecision/permission_decision -> permission_decision
        - permissionDecisionReason/permission_decision_reason -> permission_decision_reason
        - watchPaths/watch_paths -> watch_paths
        - asyncTaskId/async_task_id -> async_task_id
        - asyncTimeout/async_timeout -> async_timeout
        - hookSpecificOutput/hook_specific_output -> hook_specific_output

        Legacy Protocol Support (P1):
        - prevent_continuation (deprecated) -> permission_decision
          - prevent_continuation: true → permission_decision: 'deny'
          - prevent_continuation: false → no action (default behavior)
          - Note: permission_decision takes precedence if both are present

        Args:
            json_str: JSON 格式字符串

        Returns:
            HookJSONOutput 实例

        Raises:
            json.JSONDecodeError: JSON 解析失败
            ValueError: 字段值不合法
        """
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(
                f"Invalid JSON output from hook: {e.msg}",
                e.doc,
                e.pos
            )

        # 字段名映射（camelCase -> snake_case）
        field_mapping = {
            'continue': 'continue_',
            'stopReason': 'stop_reason',
            'systemMessage': 'system_message',
            'additionalContext': 'additional_context',
            'updatedInput': 'updated_input',
            'updatedOutput': 'updated_output',
            'permissionDecision': 'permission_decision',
            'permissionDecisionReason': 'permission_decision_reason',
            'watchPaths': 'watch_paths',
            'async': 'async_',
            'asyncTaskId': 'async_task_id',
            'asyncTimeout': 'async_timeout',
            'hookSpecificOutput': 'hook_specific_output',
        }

        # 转换字段名
        normalized_data = {}
        for key, value in data.items():
            normalized_key = field_mapping.get(key, key)
            normalized_data[normalized_key] = value

        # P1: Legacy protocol compatibility - prevent_continuation -> permission_decision
        # 支持旧版 hook 脚本的 prevent_continuation 字段
        if 'prevent_continuation' in normalized_data:
            prevent = normalized_data.pop('prevent_continuation')

            # 只有在新字段未设置时才应用旧字段
            if 'permission_decision' not in normalized_data:
                if prevent:
                    # prevent_continuation: true → 阻止执行
                    normalized_data['permission_decision'] = 'deny'
                    # 如果没有提供原因，添加默认原因
                    if 'permission_decision_reason' not in normalized_data:
                        normalized_data['permission_decision_reason'] = (
                            'Blocked by legacy prevent_continuation flag'
                        )
                # prevent_continuation: false → 允许执行（默认行为，无需设置）

        # 验证 permission_decision 枚举值
        if 'permission_decision' in normalized_data:
            decision = normalized_data['permission_decision']
            if decision not in ('allow', 'deny', 'ask', None):
                raise ValueError(
                    f"Invalid permission_decision: {decision}. "
                    f"Must be one of: 'allow', 'deny', 'ask'"
                )

        # 创建实例
        return cls(**{k: v for k, v in normalized_data.items() if v is not None})

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式（用于序列化）

        Returns:
            字典，包含所有非 None 的字段
        """
        result = {}

        if not self.continue_:
            result['continue'] = self.continue_

        if self.stop_reason:
            result['stopReason'] = self.stop_reason

        if self.system_message:
            result['systemMessage'] = self.system_message

        if self.additional_context:
            result['additionalContext'] = self.additional_context

        if self.updated_input:
            result['updatedInput'] = self.updated_input

        if self.updated_output:
            result['updatedOutput'] = self.updated_output

        if self.permission_decision:
            result['permissionDecision'] = self.permission_decision

        if self.permission_decision_reason:
            result['permissionDecisionReason'] = self.permission_decision_reason

        if self.watch_paths:
            result['watchPaths'] = self.watch_paths

        if self.async_:
            result['async'] = self.async_

        if self.async_task_id:
            result['asyncTaskId'] = self.async_task_id

        if self.async_timeout:
            result['asyncTimeout'] = self.async_timeout

        if self.hook_specific_output:
            result['hookSpecificOutput'] = self.hook_specific_output

        return result

    def __repr__(self) -> str:
        """字符串表示"""
        parts = []
        if not self.continue_:
            parts.append(f"continue=False")
        if self.stop_reason:
            parts.append(f"stop_reason={self.stop_reason!r}")
        if self.permission_decision:
            parts.append(f"permission_decision={self.permission_decision!r}")
        if self.updated_input:
            parts.append(f"updated_input={self.updated_input}")
        if self.updated_output:
            parts.append(f"updated_output={self.updated_output}")
        if self.async_:
            parts.append(f"async=True")

        return f"HookJSONOutput({', '.join(parts) if parts else 'continue=True'})"
