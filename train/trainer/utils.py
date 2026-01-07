# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Dict, Type

from train.integration.trl.trl_trainer import TrlTrainer
from train.integration.verl.verl_trainer import VerlTrainer
from train.trainer.trainer_processor import TrainerProcessor

VERL = "verl"
TRL = "trl"

TRAIN_PROCESSOR: Dict[str, Type[TrainerProcessor]] = {
    VERL: VerlTrainer,
    TRL: TrlTrainer,
}

TRAIN_DEFAULT_CONFIG = {
    VERL: {
        "reward_model": {"enable": True, "model": {"path": ""}},
        "data": {"train_files": "", "val_files": "", "train_batch_size": 1},
        "algorithm": {"adv_estimator": "grpo", "use_kl_in_reward": False, "kl_ctrl": {"kl_coef": 0.0}},
        "trainer": {"nnodes": 1, "n_gpus_per_node": 1, "total_epochs": 1, "val_before_train": False,
                    "project_name": "verl_train", "experiment_name": "verl_experiment", "log_val_generations": 0,
                    "save_freq": 30, "test_freq": 0, "default_local_dir": ""},
        "actor_rollout_ref": {
            "model": {"path": ""},
            "rollout": {"log_prob_micro_batch_size_per_gpu": 1, "agent": {"agent_loop_config_path": ""}}
        }
    },
    TRL: {
        "output_dir": ".", "learning_rate": 5e-6, "per_device_train_batch_size": 2,
        "gradient_accumulation_steps": 1, "num_train_epochs": 1, "logging_steps": 10,
        "save_strategy": "steps", "save_steps": 100, "eval_strategy": "no", "remove_unused_columns": False,
        "fp16": True, "max_prompt_length": 12800, "max_completion_length": 128, "num_generations": 2,
        "report_to": ["none"], "use_liger_kernel": False, "push_to_hub": False, "max_steps": 2
    }
}
