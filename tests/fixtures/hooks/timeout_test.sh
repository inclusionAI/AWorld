#!/bin/bash
# TC-HOOK-007: 超时测试 hook
# 故意睡眠超过超时时间

# 捕获 SIGTERM 信号
trap 'exit 1' TERM

# 睡眠 10 秒（测试时会设置更短的超时时间）
sleep 10

echo '{"continue": true}'
