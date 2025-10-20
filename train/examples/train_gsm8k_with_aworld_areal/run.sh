#!/usr/bin/env bash

set -xeuo pipefail

path_to_train=${1:-$PWD}
python3 -m areal.launcher.local $path_to_train/examples/train_gsm8k_with_aworld_areal/custom_workflow.py \
    --config $path_to_train/examples/train_gsm8k_with_aworld_areal/gsm8k_grpo.yaml \
    experiment_name=train_gsm8k_with_aworld \
    trial_name=train_gsm8k_with_aworld_trail \
    allocation_mode=sglang.d2p1t1+d2p1t1 \
    cluster.n_nodes=1 \
    cluster.n_gpus_per_node=4 \
    gconfig.max_new_tokens=2048 \
    train_dataset.batch_size=1024 \
    +sglang.attention_backend=triton