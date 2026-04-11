#!/bin/bash
# TC-HOOK-008: 环境变量注入测试 hook
# 检查所有 AWORLD_* 环境变量是否正确注入

set -euo pipefail

# 收集环境变量
jq -n \
  --arg session_id "$AWORLD_SESSION_ID" \
  --arg task_id "$AWORLD_TASK_ID" \
  --arg cwd "$AWORLD_CWD" \
  --arg hook_point "$AWORLD_HOOK_POINT" \
  --arg hook_name "$AWORLD_HOOK_NAME" \
  --arg message_json "$AWORLD_MESSAGE_JSON" \
  --arg context_json "$AWORLD_CONTEXT_JSON" \
  '{
    "continue": true,
    "hook_specific_output": {
      "hookEventName": "EnvTest",
      "env": {
        "session_id": $session_id,
        "task_id": $task_id,
        "cwd": $cwd,
        "hook_point": $hook_point,
        "hook_name": $hook_name,
        "message_json": $message_json,
        "context_json": $context_json
      }
    }
  }'
