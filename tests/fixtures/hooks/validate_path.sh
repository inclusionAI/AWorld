#!/bin/bash
# TC-HOOK-001, TC-HOOK-002: 路径验证 hook
# 检查路径是否在白名单中

set -euo pipefail

# 从环境变量读取配置
ALLOWED_PATHS="${ALLOWED_PATHS:-/tmp,/workspace}"
PATH_ARG=$(echo "$AWORLD_MESSAGE_JSON" | jq -r '.payload.args.path // ""')

# 检查路径是否为空
if [ -z "$PATH_ARG" ]; then
    echo '{"continue": true}'
    exit 0
fi

# 检查是否在白名单中
IFS=',' read -ra ALLOWED <<< "$ALLOWED_PATHS"
for allowed_path in "${ALLOWED[@]}"; do
    if [[ "$PATH_ARG" == "$allowed_path"* ]]; then
        # 允许访问
        echo '{"continue": true, "permission_decision": "allow"}'
        exit 0
    fi
done

# 拒绝访问
jq -n --arg path "$PATH_ARG" '{
    "continue": false,
    "stop_reason": ("Path access denied: " + $path),
    "permission_decision": "deny",
    "permission_decision_reason": ("Access to " + $path + " is restricted")
}'
