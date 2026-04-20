#!/bin/bash
# Path Sanitizer Hook
# 清理文件路径参数，防止路径遍历攻击
# Hook Point: before_tool_call

# 解析 message JSON 获取工具参数
TOOL_ARGS=$(echo "$AWORLD_MESSAGE_JSON" | jq -r '.payload[0].args // {}' 2>/dev/null)

# 检查是否包含路径参数
HAS_PATH=false
for key in "path" "file_path" "directory" "file"; do
  if echo "$TOOL_ARGS" | jq -e ".${key}" > /dev/null 2>&1; then
    HAS_PATH=true
    break
  fi
done

# 如果没有路径参数，直接通过
if [ "$HAS_PATH" = "false" ]; then
  echo '{"continue": true}'
  exit 0
fi

# 检测危险路径模式
DANGEROUS_PATH_PATTERNS=(
  "../"
  "..\\\\"
  "/etc/"
  "/proc/"
  "/sys/"
  "~/"
)

MESSAGE_CONTENT=$(echo "$AWORLD_MESSAGE_JSON" | tr '\n' ' ')

# 检测危险路径
for pattern in "${DANGEROUS_PATH_PATTERNS[@]}"; do
  if echo "$MESSAGE_CONTENT" | grep -qF "$pattern"; then
    # 检测到危险路径模式

    # 尝试清理路径：移除 ../ 并限制在工作目录内
    SANITIZED_MESSAGE=$(echo "$MESSAGE_CONTENT" | sed 's|\.\./||g' | sed 's|\.\.[/\\]||g')

    # 返回清理后的输入
    cat <<EOF
{
  "continue": true,
  "updated_input": $(echo "$SANITIZED_MESSAGE" | jq -c '.payload'),
  "system_message": "⚠️  Path sanitized: Removed potentially dangerous path traversal patterns"
}
EOF
    exit 0
  fi
done

# 路径安全，允许执行
echo '{"continue": true}'
