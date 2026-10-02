#!/bin/bash
# Output Filter Hook
# 过滤工具输出中的敏感信息
# Hook Point: after_tool_call

# 敏感信息模式列表（正则表达式）
SENSITIVE_PATTERNS=(
  # API Keys
  "sk-[a-zA-Z0-9]{48}"           # OpenAI API key
  "AIza[0-9A-Za-z_-]{35}"        # Google API key
  "AKIA[0-9A-Z]{16}"             # AWS Access Key ID

  # Passwords
  "[Pp]assword[=:][^ ]{6,}"
  "[Pp]wd[=:][^ ]{6,}"

  # Tokens
  "[Tt]oken[=:][^ ]{20,}"
  "[Bb]earer [A-Za-z0-9_-]+"

  # Secrets
  "[Ss]ecret[=:][^ ]{10,}"

  # Email addresses
  "[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"

  # IP addresses (optional)
  # "[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}"

  # Credit card numbers
  "[0-9]{4}[- ]?[0-9]{4}[- ]?[0-9]{4}[- ]?[0-9]{4}"
)

# 提取输出内容
OUTPUT_CONTENT=$(echo "$AWORLD_MESSAGE_JSON" | jq -r '.payload[0].content // .payload // ""' 2>/dev/null)

# 如果输出为空，直接返回
if [ -z "$OUTPUT_CONTENT" ]; then
  echo '{"continue": true}'
  exit 0
fi

# 检测敏感信息
CONTAINS_SENSITIVE=false
for pattern in "${SENSITIVE_PATTERNS[@]}"; do
  if echo "$OUTPUT_CONTENT" | grep -qE "$pattern"; then
    CONTAINS_SENSITIVE=true
    break
  fi
done

# 如果不包含敏感信息，直接通过
if [ "$CONTAINS_SENSITIVE" = "false" ]; then
  echo '{"continue": true}'
  exit 0
fi

# 过滤敏感信息
FILTERED_CONTENT="$OUTPUT_CONTENT"
for pattern in "${SENSITIVE_PATTERNS[@]}"; do
  FILTERED_CONTENT=$(echo "$FILTERED_CONTENT" | sed -E "s/${pattern}/[REDACTED]/g")
done

# 返回过滤后的输出
cat <<EOF
{
  "continue": true,
  "updated_output": {
    "observation": {
      "content": $(echo "$FILTERED_CONTENT" | jq -Rs .)
    },
    "info": {
      "sensitive_info_filtered": true
    }
  },
  "system_message": "🔒 Sensitive information has been filtered from the output"
}
EOF
