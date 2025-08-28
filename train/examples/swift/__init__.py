from swift.trainers import TrainerFactory

TrainerFactory.TRAINER_MAPPING["aworld_grpo"] = 'train.examples.swift.AworldTrainer'
TrainerFactory.TRAINING_ARGS_MAPPING["aworld_grpo"] = 'swift.trainers.GRPOConfig'
