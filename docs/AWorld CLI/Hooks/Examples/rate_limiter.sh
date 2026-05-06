#!/bin/bash
# Rate Limiter Hook
# 限制工具调用频率，防止滥用
# Hook Point: before_tool_call

# 配置
RATE_LIMIT_DIR="${AWORLD_CWD}/.aworld/rate_limits"
MAX_CALLS_PER_MINUTE=20
WINDOW_SECONDS=60

# 创建速率限制目录
mkdir -p "${RATE_LIMIT_DIR}"

# 速率限制文件（基于会话 ID）
RATE_LIMIT_FILE="${RATE_LIMIT_DIR}/${AWORLD_SESSION_ID}.count"

# 获取当前时间（Unix 时间戳）
CURRENT_TIME=$(date +%s)

# 初始化计数器
if [ ! -f "$RATE_LIMIT_FILE" ]; then
  echo "0:${CURRENT_TIME}" > "$RATE_LIMIT_FILE"
fi

# 读取当前计数和时间窗口起始时间
IFS=':' read -r COUNT WINDOW_START < "$RATE_LIMIT_FILE"

# 计算时间窗口是否过期
TIME_ELAPSED=$((CURRENT_TIME - WINDOW_START))

if [ "$TIME_ELAPSED" -ge "$WINDOW_SECONDS" ]; then
  # 时间窗口过期，重置计数器
  COUNT=0
  WINDOW_START=$CURRENT_TIME
fi

# 增加计数
COUNT=$((COUNT + 1))

# 检查是否超过限制
if [ "$COUNT" -gt "$MAX_CALLS_PER_MINUTE" ]; then
  # 超过速率限制，拒绝执行
  RETRY_AFTER=$((WINDOW_SECONDS - TIME_ELAPSED))
  cat <<EOF
{
  "continue": false,
  "stop_reason": "Rate limit exceeded: ${MAX_CALLS_PER_MINUTE} calls per ${WINDOW_SECONDS}s",
  "permission_decision": "deny",
  "permission_decision_reason": "Rate limiting policy",
  "system_message": "⏱️  Rate Limit: You have exceeded ${MAX_CALLS_PER_MINUTE} tool calls in ${WINDOW_SECONDS} seconds. Please wait ${RETRY_AFTER} seconds before retrying.",
  "additional_context": {
    "rate_limit": {
      "max_calls": ${MAX_CALLS_PER_MINUTE},
      "window_seconds": ${WINDOW_SECONDS},
      "current_count": ${COUNT},
      "retry_after": ${RETRY_AFTER}
    }
  }
}
EOF
else
  # 未超过限制，允许执行并更新计数
  echo "${COUNT}:${WINDOW_START}" > "$RATE_LIMIT_FILE"

  cat <<EOF
{
  "continue": true,
  "additional_context": {
    "rate_limit": {
      "current_count": ${COUNT},
      "max_calls": ${MAX_CALLS_PER_MINUTE},
      "remaining": $((MAX_CALLS_PER_MINUTE - COUNT))
    }
  }
}
EOF
fi

# 清理过期的速率限制文件（超过 1 小时）
find "${RATE_LIMIT_DIR}" -type f -mmin +60 -delete 2>/dev/null || true
