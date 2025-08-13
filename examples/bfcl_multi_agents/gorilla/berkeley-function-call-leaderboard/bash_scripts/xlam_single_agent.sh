#!/bin/bash

# 1. 遇到任何错误立即退出
set -e

# 2. 定义变量，方便维护和阅读
TARGET_DIR="/Users/jackiezhangant/codes/ACT_LMM/BFCL/gorilla/berkeley-function-call-leaderboard/bfcl_eval"
MODEL_NAME="AworldLocal/SingleAgent[xlam-lp-70b]"
# TEST_CATEGORY="multi_turn_long_context"
TEST_CATEGORY="multi_turn_miss_func"

NUM_PROCESSES=3
LOG_DIR="/Users/jackiezhangant/codes/ACT_LMM/BFCL/gorilla/berkeley-function-call-leaderboard/bash_scripts/bash_logs"
LOG_FILE="${LOG_DIR}/xlam_single_agent.log"

# 切换到目标工作目录
cd "$TARGET_DIR"

# 3. 确保日志目录存在
mkdir -p "$LOG_DIR"

echo "Starting evaluation for model: ${MODEL_NAME}"
echo "Logging output to: ${LOG_FILE}"

# 4. 执行Python脚本
#    - 参数使用引号包裹
#    - 将标准输出和错误输出重定向，并通过tee记录到日志
python bfcl_eval_mp.py \
    --model "$MODEL_NAME" \
    --test-category "$TEST_CATEGORY" \
    --num-processes $NUM_PROCESSES 2>&1 | tee "$LOG_FILE"

echo "Evaluation finished."