# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
import traceback
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Optional, List, Dict, Any

import yaml
from pydantic import BaseModel,Field
from enum import Enum

from aworld.logs.util import logger


def load_config(file_name: str, dir_name: str = None) -> Dict[str, Any]:
    """Dynamically load config file form current path.

    Args:
        file_name: Config file name.
        dir_name: Config file directory.

    Returns:
        Config dict.
    """

    if dir_name:
        file_path = os.path.join(dir_name, file_name)
    else:
        # load conf form current path
        current_dir = Path(__file__).parent.absolute()
        file_path = os.path.join(current_dir, file_name)
    if not os.path.exists(file_path):
        logger.debug(f"{file_path} not exists, please check it.")

    configs = dict()
    try:
        with open(file_path, "r") as file:
            yaml_data = yaml.safe_load(file)
        configs.update(yaml_data)
    except FileNotFoundError:
        logger.debug(f"Can not find the file: {file_path}")
    except Exception as e:
        logger.warning(f"{file_name} read fail.\n", traceback.format_exc())
    return configs


def wipe_secret_info(config: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    """Return a deep copy of this config as a plain Dict as well ass wipe up secret info, used to log."""

    def _wipe_secret(conf):
        def _wipe_secret_plain_value(v):
            if isinstance(v, List):
                return [_wipe_secret_plain_value(e) for e in v]
            elif isinstance(v, Dict):
                return _wipe_secret(v)
            else:
                return v

        key_list = []
        for key in conf.keys():
            key_list.append(key)
        for key in key_list:
            if key.strip('"') in keys:
                conf[key] = '-^_^-'
            else:
                _wipe_secret_plain_value(conf[key])
        return conf

    if not config:
        return config
    return _wipe_secret(config)


class ClientType(Enum):
    SDK = "sdk"
    HTTP = "http"


class ConfigDict(dict):
    """Object mode operates dict, can read non-existent attributes through `get` method."""
    __setattr__ = dict.__setitem__
    __getattr__ = dict.__getitem__

    def __init__(self, seq: dict = None, **kwargs):
        if seq is None:
            seq = OrderedDict()
        super(ConfigDict, self).__init__(seq, **kwargs)
        self.nested(self)

    def nested(self, seq: dict):
        """Nested recursive processing dict.

        Args:
            seq: Python original format dict
        """
        for k, v in seq.items():
            if isinstance(v, dict):
                seq[k] = ConfigDict(v)
                self.nested(v)


class BaseConfig(BaseModel):
    def config_dict(self) -> ConfigDict:
        return ConfigDict(self.model_dump())


class ModelConfig(BaseConfig):
    llm_provider: str = None
    llm_model_name: str = None
    llm_temperature: float = 1.
    llm_base_url: str = None
    llm_api_key: str = None
    llm_client_type: ClientType = ClientType.SDK
    llm_sync_enabled: bool = True
    llm_async_enabled: bool = True
    max_retries: int = 3
    max_model_len: Optional[int] = None  # Maximum model context length
    model_type: Optional[str] = 'qwen' # Model type determines tokenizer and maximum length

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        
        # init max_model_len
        if not hasattr(self, 'max_model_len') or self.max_model_len is None:
            # qwen or other default model_type
            self.max_model_len = 128000
            if self.model_type and self.model_type == 'claude':
                self.max_model_len = 200000

class LlmCompressionConfig(BaseConfig):
    enabled: bool = False
    compress_type: str = 'llm' # llm, llmlingua
    trigger_compress_token_length: int = 10000  # Trigger compression when exceeding this length
    compress_model: ModelConfig = None

class OptimizationConfig(BaseConfig):
    enabled: bool = False
    max_token_budget_ratio: float = 0.5  # Maximum context length ratio

class ContextRuleConfig(BaseConfig):
    """Context interference rule configuration"""

    # ===== Performance optimization configuration =====
    optimization_config: OptimizationConfig = OptimizationConfig()

    # ===== LLM conversation compression configuration =====
    llm_compression_config: LlmCompressionConfig = LlmCompressionConfig()

class AgentConfig(BaseConfig):
    name: str = None
    desc: str = None
    llm_config: ModelConfig = ModelConfig()

    # default reset init in first
    need_reset: bool = True
    # use vision model
    use_vision: bool = True
    max_steps: int = 10
    max_input_tokens: int = 128000
    max_actions_per_step: int = 10
    system_prompt: Optional[str] = None
    system_prompt_template: Optional[str] = None
    agent_prompt: Optional[str] = None
    working_dir: Optional[str] = None
    enable_recording: bool = False
    use_tools_in_prompt: bool = False
    exit_on_failure: bool = False
    ext: dict = {}
    human_tools: List[str] = []

    # context rule
    context_rule: ContextRuleConfig = ContextRuleConfig()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Initialize llm_config with relevant kwargs
        llm_config_kwargs = {k: v for k, v in kwargs.items() if k in ModelConfig.model_fields}
        self.llm_config = ModelConfig(**llm_config_kwargs)

        # Initialize max_model_len if not set
        if self.max_model_len is None:
            if self.llm_config.model_type == 'claude':
                self.max_model_len = 200000
            else:
                self.max_model_len = 128000

class TaskConfig(BaseConfig):
    task_id: str = str(uuid.uuid4())
    task_name: str | None = None
    max_steps: int = 100
    max_actions_per_step: int = 10
    stream: bool = False
    exit_on_failure: bool = False
    ext: dict = {}


class ToolConfig(BaseConfig):
    name: str = None
    custom_executor: bool = False
    enable_recording: bool = False
    working_dir: str = ""
    max_retry: int = 3
    llm_config: ModelConfig = None
    reuse: bool = False
    use_async: bool = False
    exit_on_failure: bool = False
    ext: dict = {}


class RunConfig(BaseConfig):
    name: str = 'local'
    worker_num: int = 1
    reuse_process: bool = True
    cls: Optional[str] = None
    event_bus: Optional[Dict[str, Any]] = None
    tracer: Optional[Dict[str, Any]] = None
    replay_buffer: Optional[Dict[str, Any]] = None


class EvaluationConfig(BaseConfig):
    work_dir: Optional[str] = None
    run_times: int = 1
