#!/bin/bash
# TC-HOOK-003: 路径重写 hook
# 将相对路径转换为绝对路径

set -euo pipefail

PATH_ARG=$(echo "$AWORLD_MESSAGE_JSON" | jq -r '.payload.args.path // ""')

# 检查是否为相对路径（不以 / 开头）
if [[ -n "$PATH_ARG" && "$PATH_ARG" != /* ]]; then
    # 转换为绝对路径
    ABS_PATH="$(cd "$(dirname "$PATH_ARG" 2>/dev/null || echo ".")" && pwd)/$(basename "$PATH_ARG")"

    jq -n --arg path "$ABS_PATH" '{
        "continue": true,
        "updated_input": {
            "path": $path
        },
        "system_message": "Path normalized to absolute path"
    }'
else
    # 已经是绝对路径，不修改
    echo '{"continue": true}'
fi
