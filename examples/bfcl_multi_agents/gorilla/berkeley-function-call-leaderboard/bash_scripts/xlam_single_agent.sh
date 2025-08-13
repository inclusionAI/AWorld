#!/bin/bash
set -e

TARGET_DIR="/Users/jackiezhangant/codes/ACT_LMM/BFCL/gorilla/berkeley-function-call-leaderboard/bfcl_eval"
MODEL_NAME="AworldLocal/SingleAgent[xlam-lp-70b]"
# TEST_CATEGORY="multi_turn_long_context"
TEST_CATEGORY="multi_turn_miss_func"

NUM_PROCESSES=3
LOG_DIR="/Users/jackiezhangant/codes/ACT_LMM/BFCL/gorilla/berkeley-function-call-leaderboard/bash_scripts/bash_logs"
LOG_FILE="${LOG_DIR}/xlam_single_agent.log"

cd "$TARGET_DIR"

mkdir -p "$LOG_DIR"

echo "Starting evaluation for model: ${MODEL_NAME}"
echo "Logging output to: ${LOG_FILE}"


python bfcl_eval_mp.py \
    --model "$MODEL_NAME" \
    --test-category "$TEST_CATEGORY" \
    --num-processes $NUM_PROCESSES 2>&1 | tee "$LOG_FILE"

echo "Evaluation finished."