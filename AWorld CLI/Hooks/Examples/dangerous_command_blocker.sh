#!/bin/bash
# Dangerous Command Blocker Hook
# 阻止执行危险的系统命令
# Hook Point: before_tool_call

# 危险命令模式列表
DANGEROUS_PATTERNS=(
  "rm -rf /"
  "rm -rf /*"
  "dd if=/dev/zero"
  "mkfs\."
  ":(){ :|:& };:"  # fork bomb
  "chmod -R 777"
  "chown -R"
  "> /dev/sda"
  "mv /* /dev/null"
)

# 检查 message payload 中是否包含危险命令
MESSAGE_CONTENT=$(echo "$AWORLD_MESSAGE_JSON" | tr '\n' ' ')

# 遍历危险模式进行检测
for pattern in "${DANGEROUS_PATTERNS[@]}"; do
  if echo "$MESSAGE_CONTENT" | grep -qF "$pattern"; then
    # 检测到危险命令，阻止执行
    cat <<EOF
{
  "continue": false,
  "stop_reason": "Dangerous command detected: ${pattern}",
  "permission_decision": "deny",
  "permission_decision_reason": "Security policy blocks dangerous commands",
  "system_message": "⚠️  Security Alert: The command you tried to execute (${pattern}) is blocked by security policy."
}
EOF
    exit 0
  fi
done

# 未检测到危险命令，允许执行
cat <<EOF
{
  "continue": true
}
EOF
