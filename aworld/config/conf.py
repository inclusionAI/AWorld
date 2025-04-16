# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
import traceback
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any

import yaml
from pydantic import BaseModel
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

class ModelConfig(BaseModel):
    llm_provider: str = None
    llm_model_name: str = None
    llm_temperature: float = 1.
    llm_base_url: str = None
    llm_api_key: str = None
    llm_client_type: ClientType = ClientType.SDK
    llm_sync_enabled: bool = True
    llm_async_enabled: bool = True


class AgentConfig(BaseModel):
    name: str = None
    desc: str = None
    llm_config: ModelConfig = ModelConfig()
    # for compatibility
    llm_provider: str = None
    llm_model_name: str = None
    llm_temperature: float = 1.
    llm_base_url: str = None
    llm_api_key: str = None
    llm_client_type: ClientType = ClientType.SDK
    llm_sync_enabled: bool = True
    llm_async_enabled: bool = True

    # default reset init in first
    need_reset: bool = True
    # use vision model
    use_vision: bool = True
    max_steps: int = 10
    max_input_tokens: int = 128000
    max_actions_per_step: int = 10
    system_prompt: Optional[str] = None
    agent_prompt: Optional[str] = None
    output_prompt: Optional[str] = None
    working_dir: Optional[str] = None
    enable_recording: bool = False
    ext: dict = {}


class TaskConfig(BaseModel):
    task_id: str = str(uuid.uuid4())
    task_name: str | None = None
    max_steps: int = 100
    max_actions_per_step: int = 10
    ext: dict = {}


class ToolConfig(BaseModel):
    name: str = None
    custom_executor: bool = False
    enable_recording: bool = False
    working_dir: str = ""
    max_retry: int = 3
    llm_config: ModelConfig = None
    reuse: bool = False
    ext: dict = {}


class ConfigDict(dict):
    """Object mode operates dict, can read non-existent attributes through `get` method."""
    __setattr__ = dict.__setitem__
    __getattr__ = dict.__getitem__

    def __init__(self, seq: dict, **kwargs):
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
