#!/bin/bash
# Audit Logger Hook
# 记录所有工具调用到审计日志文件
# Hook Point: after_tool_call

# 配置审计日志文件路径
AUDIT_LOG_DIR="${AWORLD_CWD}/.aworld/logs"
AUDIT_LOG_FILE="${AUDIT_LOG_DIR}/audit.log"

# 创建日志目录（如果不存在）
mkdir -p "${AUDIT_LOG_DIR}"

# 解析 message JSON 获取工具信息
TOOL_NAME=$(echo "$AWORLD_MESSAGE_JSON" | jq -r '.payload[0].content // .payload.tool_name // "unknown"' 2>/dev/null || echo "unknown")

# 记录审计日志
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
LOG_ENTRY="{\"timestamp\":\"${TIMESTAMP}\",\"session_id\":\"${AWORLD_SESSION_ID}\",\"task_id\":\"${AWORLD_TASK_ID}\",\"hook_point\":\"${AWORLD_HOOK_POINT}\",\"tool_name\":\"${TOOL_NAME}\"}"

echo "$LOG_ENTRY" >> "${AUDIT_LOG_FILE}"

# 返回 HookJSONOutput，在输出中添加审计信息
cat <<EOF
{
  "continue": true,
  "updated_output": {
    "info": {
      "audit_logged": true,
      "audit_timestamp": "${TIMESTAMP}",
      "audit_file": "${AUDIT_LOG_FILE}"
    }
  }
}
EOF
