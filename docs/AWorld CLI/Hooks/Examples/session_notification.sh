#!/bin/bash
# Session Notification Hook
# 在会话开始/结束时发送通知
# Hook Points: session_started, session_finished, session_failed

# 配置通知方式（可选：slack, webhook, email）
NOTIFICATION_TYPE="${AWORLD_NOTIFICATION_TYPE:-none}"
SLACK_WEBHOOK_URL="${AWORLD_SLACK_WEBHOOK_URL:-}"
WEBHOOK_URL="${AWORLD_WEBHOOK_URL:-}"

# 提取事件类型
EVENT_TYPE=$(echo "$AWORLD_MESSAGE_JSON" | jq -r '.payload.event // "unknown"')

# 构建通知消息
case "$EVENT_TYPE" in
  "session_started")
    TITLE="🚀 Session Started"
    MESSAGE="New AWorld session started"
    COLOR="good"
    ;;
  "session_finished")
    TIME_COST=$(echo "$AWORLD_MESSAGE_JSON" | jq -r '.payload.time_cost // 0')
    TITLE="✅ Session Completed"
    MESSAGE="Session completed successfully (took ${TIME_COST}s)"
    COLOR="good"
    ;;
  "session_failed")
    ERROR=$(echo "$AWORLD_MESSAGE_JSON" | jq -r '.payload.error // "unknown error"')
    TITLE="❌ Session Failed"
    MESSAGE="Session failed: ${ERROR}"
    COLOR="danger"
    ;;
  *)
    TITLE="📢 Session Event"
    MESSAGE="Session event: ${EVENT_TYPE}"
    COLOR="warning"
    ;;
esac

# 发送 Slack 通知
if [ "$NOTIFICATION_TYPE" = "slack" ] && [ -n "$SLACK_WEBHOOK_URL" ]; then
  SLACK_PAYLOAD=$(cat <<EOF
{
  "attachments": [
    {
      "fallback": "${TITLE}: ${MESSAGE}",
      "color": "${COLOR}",
      "title": "${TITLE}",
      "text": "${MESSAGE}",
      "fields": [
        {
          "title": "Session ID",
          "value": "${AWORLD_SESSION_ID}",
          "short": true
        },
        {
          "title": "Task ID",
          "value": "${AWORLD_TASK_ID}",
          "short": true
        },
        {
          "title": "Timestamp",
          "value": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
          "short": true
        }
      ],
      "footer": "AWorld Hooks V2",
      "footer_icon": "https://aworld.ai/icon.png"
    }
  ]
}
EOF
)

  # 发送 Slack 消息（后台执行，不阻塞）
  curl -X POST \
    -H 'Content-type: application/json' \
    --data "$SLACK_PAYLOAD" \
    "$SLACK_WEBHOOK_URL" \
    >/dev/null 2>&1 &

fi

# 发送通用 Webhook 通知
if [ "$NOTIFICATION_TYPE" = "webhook" ] && [ -n "$WEBHOOK_URL" ]; then
  WEBHOOK_PAYLOAD=$(cat <<EOF
{
  "event": "${EVENT_TYPE}",
  "session_id": "${AWORLD_SESSION_ID}",
  "task_id": "${AWORLD_TASK_ID}",
  "timestamp": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "message": "${MESSAGE}",
  "payload": $(echo "$AWORLD_MESSAGE_JSON" | jq -c '.payload')
}
EOF
)

  # 发送 Webhook（后台执行，不阻塞）
  curl -X POST \
    -H 'Content-type: application/json' \
    --data "$WEBHOOK_PAYLOAD" \
    "$WEBHOOK_URL" \
    >/dev/null 2>&1 &

fi

# 记录到本地日志文件
NOTIFICATION_LOG_DIR="${AWORLD_CWD}/.aworld/logs"
NOTIFICATION_LOG_FILE="${NOTIFICATION_LOG_DIR}/notifications.log"
mkdir -p "${NOTIFICATION_LOG_DIR}"

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] ${EVENT_TYPE}: ${MESSAGE} (session_id=${AWORLD_SESSION_ID})" >> "${NOTIFICATION_LOG_FILE}"

# 返回 HookJSONOutput（不影响执行）
cat <<EOF
{
  "continue": true,
  "system_message": "📢 ${TITLE}: ${MESSAGE}",
  "additional_context": {
    "notification_sent": true,
    "notification_type": "${NOTIFICATION_TYPE}"
  }
}
EOF
