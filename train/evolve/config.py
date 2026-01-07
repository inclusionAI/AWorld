# coding: utf-8
# Copyright (c) inclusionAI.
from typing import Optional

from aworld.config import BaseConfig, ModelConfig, RunConfig


class EvolutionConfig(BaseConfig):
    llm_config: Optional[ModelConfig] = None
    hitl_plan: bool = False
    hitl_all: bool = False
    run_conf: Optional[RunConfig] = None
