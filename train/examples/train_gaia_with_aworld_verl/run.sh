#!/usr/bin/env bash

export VLLM_USE_V1=1
export VERL_LOGGING_LEVEL=DEBUG
export NCCL_DEBUG=DEBUG
export RAY_DEDUP_LOGS=0
export MLFLOW_TRACKING_URI=sqlite:////tmp/mlruns.db
export CUDA_LAUNCH_BLOCKING=1

set -xeuo pipefail

# ================= cluster topology =================
export GPUS_PER_NODE=${SLURM_GPUS_ON_NODE:-${GPUS_PER_NODE:-8}}  # GPUs on this node
NNODES=${SLURM_JOB_NUM_NODES:-${NNODES:-1}}
export NNODES
export RAY_NUM_NODES=$NNODES

echo "Using $NNODES nodes and $GPUS_PER_NODE GPUs per node..."

# ================= data/model/tool =================
HDFS_ROOT=${HDFS_ROOT:-$PWD}
DATA_ROOT=${DATA_ROOT:-$PWD}

# Prefer local model if present, otherwise fall back to HF hub path
# model_path=${model_path:-$DATA_ROOT/Qwen/Qwen3-4B}
# model_path=/ossfs/workspace/ckpt/aworld_agent_baishi_sft_ckpt_258_0a56ee17df5a8397
# model_path=/agent/share/public/common_models/Qwen_Qwen3_32B
model_path=/agent/share/public/yingyu/aworld_sft_yingyu_182

# Use the default output directory produced by create_dataset.py
# train_files=$DATA_ROOT/datasets/train.parquet
# test_files=$DATA_ROOT/datasets/test.parquet
# train_files=/ossfs/workspace/aworld/rlhf/verl/simple_data/train.parquet
# test_files=/ossfs/workspace/aworld/rlhf/verl/simple_data/test.parquet

train_files=/ossfs/workspace/aworld/rlhf/verl/data/simple_dataset_only20.parquet
test_files=/ossfs/workspace/aworld/rlhf/verl/data/simple_dataset_only20.parquet

# train_files=/ossfs/workspace/aworld/rlhf/verl/data/simple_dataset.parquet
# test_files=/ossfs/workspace/aworld/rlhf/verl/data/simple_dataset.parquet

# train_files=/ossfs/workspace/aworld/rlhf/verl/data/simple_dataset_1010_long.parquet
# test_files=/ossfs/workspace/aworld/rlhf/verl/data/simple_dataset_1010_long.parquet

# train_files=/ossfs/workspace/aworld/rlhf/verl/data/simple_dataset_1010.parquet
# test_files=/ossfs/workspace/aworld/rlhf/verl/data/simple_dataset_1010.parquet

# =================== custom ===================
path_to_train="/ossfs/workspace/aworld/AWorld/train"
reward_fn_name=gaia_reward_func
reward_fn_file_path=${path_to_train}/examples/train_gaia_with_aworld_verl/metrics/gaia_reward_function.py

# Agent config
agent_loop_config_path=${path_to_train}/examples/train_gaia_with_aworld_verl/agent.yaml

# set dummy_tool_config_path to enable auto_tool_choice
dummy_tool_config_path=${path_to_train}/examples/train_gaia_with_aworld_verl/dummy_tool_config.yaml

# =================== wandb ===================
project_name=gaia
experiment_name=qwen3_32b_sft_rl_1014
ckpts_dir=/ossfs/workspace/ckpt/${project_name}/${experiment_name}

# ================= algorithm =================
adv_estimator=grpo

use_kl_in_reward=false
kl_coef=0.0
use_kl_loss=false
kl_loss_coef=0.0

clip_ratio_low=0.2
clip_ratio_high=0.28

max_turns=8

# max_prompt_length=122980
# max_response_length=8092
# max_model_len=131072
# max_prompt_length=98304
# max_response_length=32768
# max_model_len=131072
max_prompt_length=65536
max_response_length=65536
# max_model_len=262144


max_num_batched_tokens=32768
max_num_seqs=8
gpu_memory_utilization=0.8

temperature=1.0
top_p=1.0
top_k=80

actor_lr=1e-6

train_batch_size=1
ppo_mini_batch_size=1
ppo_micro_batch_size_per_gpu=1
log_prob_micro_batch_size_per_gpu=1
n_resp_per_prompt=8
n_resp_per_prompt_val=1

# =================== logging ===================
export RAY_LOGGING_LEVEL=DEBUG
export HYDRA_FULL_ERROR=1

# ================= performance =================
export NCCL_IBEXT_DISABLE=1
export NCCL_NVLS_ENABLE=1
export NCCL_IB_HCA=mlx5
export UCX_NET_DEVICES=mlx5_0:1,mlx5_1:1,mlx5_2:1,mlx5_3:1,mlx5_4:1,mlx5_5:1,mlx5_6:1,mlx5_7:1
export VLLM_ATTENTION_BACKEND=FLASH_ATTN

infer_tp=8  # vLLM tensor parallel size
train_sp=8  # Ulysses sequence parallel size for actor
offload=true

actor_max_token_len_per_gpu=$(( (max_prompt_length + max_response_length) * 1 ))
log_prob_max_token_len_per_gpu=$(( actor_max_token_len_per_gpu * 1 ))

train_files="['$train_files']"
test_files="['$test_files']"

python3 -m verl.trainer.main_ppo \
    algorithm.adv_estimator=$adv_estimator \
    algorithm.use_kl_in_reward=$use_kl_in_reward \
    algorithm.kl_ctrl.kl_coef=$kl_coef \
    data.train_files="$train_files" \
    data.val_files="$test_files" \
    data.return_raw_chat=true \
    data.train_batch_size=$train_batch_size \
    data.max_prompt_length=$max_prompt_length \
    data.max_response_length=$max_response_length \
    data.filter_overlong_prompts=true \
    data.truncation='error' \
    actor_rollout_ref.model.path="$model_path" \
    actor_rollout_ref.model.use_remove_padding=true \
    actor_rollout_ref.model.enable_gradient_checkpointing=true \
    actor_rollout_ref.model.use_fused_kernels=true \
    actor_rollout_ref.actor.use_kl_loss=$use_kl_loss \
    actor_rollout_ref.actor.kl_loss_coef=$kl_loss_coef \
    actor_rollout_ref.actor.clip_ratio_low=$clip_ratio_low \
    actor_rollout_ref.actor.clip_ratio_high=$clip_ratio_high \
    actor_rollout_ref.actor.clip_ratio_c=10.0 \
    actor_rollout_ref.actor.optim.lr=$actor_lr \
    actor_rollout_ref.actor.use_dynamic_bsz=false \
    actor_rollout_ref.actor.ppo_mini_batch_size=$ppo_mini_batch_size \
    actor_rollout_ref.actor.ppo_micro_batch_size_per_gpu=$ppo_micro_batch_size_per_gpu \
    actor_rollout_ref.actor.ppo_max_token_len_per_gpu=$actor_max_token_len_per_gpu \
    actor_rollout_ref.actor.ulysses_sequence_parallel_size=$train_sp \
    actor_rollout_ref.actor.strategy="fsdp2" \
    actor_rollout_ref.actor.fsdp_config.model_dtype=bf16 \
    actor_rollout_ref.actor.fsdp_config.param_offload=$offload \
    actor_rollout_ref.actor.fsdp_config.optimizer_offload=$offload \
    actor_rollout_ref.actor.fsdp_config.fsdp_size=-1 \
    actor_rollout_ref.ref.log_prob_max_token_len_per_gpu=$log_prob_max_token_len_per_gpu \
    actor_rollout_ref.rollout.name=vllm \
    actor_rollout_ref.rollout.mode=async \
    actor_rollout_ref.rollout.max_num_batched_tokens=$max_num_batched_tokens \
    actor_rollout_ref.rollout.tensor_model_parallel_size=8 \
    actor_rollout_ref.rollout.enforce_eager=true \
    actor_rollout_ref.rollout.free_cache_engine=true \
    actor_rollout_ref.rollout.temperature=$temperature \
    actor_rollout_ref.rollout.top_p=$top_p \
    actor_rollout_ref.rollout.top_k=$top_k \
    actor_rollout_ref.rollout.dtype="bfloat16" \
    actor_rollout_ref.rollout.multi_turn.enable=false \
    actor_rollout_ref.rollout.multi_turn.max_user_turns=$max_turns \
    actor_rollout_ref.rollout.multi_turn.max_assistant_turns=$max_turns \
    actor_rollout_ref.rollout.multi_turn.tool_config_path=$dummy_tool_config_path \
    actor_rollout_ref.rollout.multi_turn.format=hermes \
    actor_rollout_ref.rollout.agent.agent_loop_config_path=$agent_loop_config_path \
    actor_rollout_ref.rollout.gpu_memory_utilization=$gpu_memory_utilization \
    actor_rollout_ref.rollout.max_num_seqs=$max_num_seqs \
    actor_rollout_ref.rollout.n=$n_resp_per_prompt \
    actor_rollout_ref.rollout.val_kwargs.top_p=0.6 \
    actor_rollout_ref.rollout.val_kwargs.temperature=1.0 \
    actor_rollout_ref.rollout.val_kwargs.n=$n_resp_per_prompt_val \
    actor_rollout_ref.rollout.log_prob_micro_batch_size_per_gpu=$log_prob_micro_batch_size_per_gpu \
    actor_rollout_ref.rollout.trace.backend=mlflow \
    custom_reward_function.path="${reward_fn_file_path}"\
    custom_reward_function.name="${reward_fn_name}"\
    trainer.logger="['console', 'mlflow']" \
    trainer.project_name=$project_name \
    trainer.experiment_name=$experiment_name \
    trainer.n_gpus_per_node="$GPUS_PER_NODE" \
    trainer.val_before_train=false \
    trainer.log_val_generations=0 \
    trainer.nnodes="$NNODES" \
    trainer.save_freq=30 \
    trainer.default_local_dir="${ckpts_dir}" \
    trainer.test_freq=0 \
    trainer.total_epochs=1 "$@"


#     # actor_rollout_ref.rollout.max_model_len=$max_model_len \
