#!/bin/bash
# TC-HOOK-006: JSON 解析错误测试 hook
# 返回非法 JSON

set -euo pipefail

# 返回非法 JSON（缺少引号）
echo '{continue: true, this is not valid JSON}'
