# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import inspect
import os
import traceback
import yaml

from typing import Callable, Union, Any

from datasets import Dataset
from omegaconf import OmegaConf
from omegaconf.dictconfig import DictConfig

from aworld.agents.llm_agent import Agent
from aworld.config import BaseConfig, ConfigDict, load_config
from aworld.core.common import Config
from train.adapter.verl.agent_template import VERL_TEMPLATE
from train.trainer.trainer_wrapper import TrainerWrapper


class VerlTrainer(TrainerWrapper):
    def train(self):
        from verl.trainer.main_ppo import main

        if not self.initialized:
            raise RuntimeError("Please check all components before training")

        main(self.config)

    def check_dataset(self, dataset: Union[str, Dataset], test_dataset: Union[str, Dataset] = None):
        if isinstance(dataset, str):
            # means dataset path
            dataset_path = dataset
            assert dataset_path.endswith('.parquet'), "VeRL only support parquet dataset now"
        elif isinstance(dataset, Dataset):
            dataset.to_parquet(f'{self.run_path}/train_data.parquet')
            dataset_path = f'{self.run_path}/train_data.parquet'
        else:
            raise ValueError("Train dataset must be a string or a Dataset")
        self.train_dataset_path = dataset_path

        if isinstance(test_dataset, str):
            # means dataset path
            test_dataset_path = test_dataset
        elif isinstance(test_dataset, Dataset):
            test_dataset.to_parquet(f'{self.run_path}/test_data.parquet')
            test_dataset_path = f'{self.run_path}/test_data.parquet'
        else:
            test_dataset_path = None
        self.test_dataset_path = test_dataset_path

    def check_reward(self, reward_func: Union[str, Callable[..., float]]):
        if isinstance(reward_func, str):
            return reward_func, os.path.basename(reward_func).replace(".py", "")

        # data_source, solution_str, ground_truth, extra_info=None
        sig = inspect.signature(reward_func)
        if len(sig.parameters) != 4:
            raise ValueError(f"VeRL reward function must have 4 parameters")

        bind = sig.bind_partial(**sig.parameters)
        for k, param in bind.arguments.items():
            if param.kind != inspect.Parameter.POSITIONAL_OR_KEYWORD:
                raise ValueError(f"VeRL reward function {param} kind must is a special parameter")

            if param.annotation.__name__ != '_empty':
                if k == 'data_source' and param.annotation.__name__ != 'str':
                    raise ValueError(f"VeRL reward function {param} 'data_source' type must be str")
                elif k == 'solution_str' and param.annotation.__name__ != 'str':
                    raise ValueError(f"VeRL reward function {param} 'solution_str' type must be str")
                elif k == 'ground_truth' and param.annotation.__name__ not in ['str', 'int', 'float']:
                    raise ValueError(f"VeRL reward function {param} 'ground_truth' type must be [str, int, float]")
                elif k == 'extra_info' and param.annotation.__name__ not in ['Optional', 'dict', 'Dict']:
                    raise ValueError(f"VeRL reward function {param} 'extra_info' type must be Optional")

        content = inspect.getsource(reward_func)
        if 'if __name__' in content and '__main__' in content:
            # have __name__ == '__main__', save to function to the new file
            reward_file_path = f'{self.run_path}/reward_func.py'
            with open(reward_file_path, 'w') as writer:
                writer.write(content)
        else:
            reward_file_path = inspect.getfile(reward_func)

        self.reward_file_path = reward_file_path
        self.reward_func_name = reward_func.__name__
        return reward_file_path, reward_func.__name__

    def check_agent(self, agent: Union[str, Agent]):
        if isinstance(agent, str):
            config_dict = load_config(agent)
            agent = Agent(**config_dict)

        model_config: BaseConfig = agent.conf.llm_config
        if isinstance(model_config, dict):
            model_dict = dict(model_config)
        else:
            model_dict = dict(model_config.to_dict())
        model_dict.pop("llm_provider", None)
        model_dict.pop("llm_model_name", None)
        model_dict.pop("llm_base_url", None)
        model_dict.pop("llm_api_key", None)
        model_dict.pop("llm_client_type", None)
        model_dict.pop("params", None)
        model_dict.pop("model_type", None)

        kv_parameters = ",\n".join([f"{k}={v}" for k, v in model_dict.items()])
        con = VERL_TEMPLATE.format(agent_name=agent.name(),
                                   agent_desc=agent.desc(),
                                   system_prompt=agent.system_prompt,
                                   mcp_config=agent.mcp_config,
                                   parser_module=type(agent.model_output_parser).__module__,
                                   parser_name=type(agent.model_output_parser).__name__,
                                   kv_parameters=kv_parameters)
        module = f"{self.run_path}/{agent.name()}"
        with open(f"{module}.py", 'w+') as write:
            write.writelines(con)

        module = module.replace(os.getcwd(), '').replace('/', '.')
        if module[0] == '.':
            module = module[1:]
        con = f"""- name: {agent.name()}
  _target_: {module}.VerlAgentLoop
               """
        with open(f"{self.run_path}/agent.yaml", "w+") as write:
            write.writelines(con)
        self.agent_yaml = f"{self.run_path}/agent.yaml"
        return self.agent_yaml

    def check_config(self, config: Union[str, Any]):
        import verl.trainer.config

        file_path = os.path.join(os.path.dirname(verl.trainer.config.__file__), "ppo_trainer.yaml")
        try:
            with open(file_path, "r") as file:
                yaml_data = yaml.safe_load(file)
        except FileNotFoundError:
            raise ValueError(f"Can not find the file: {config}")
        except Exception:
            raise RuntimeError(f"{config} read fail.\n", traceback.format_exc())


        configs = DictConfig(yaml_data) or DictConfig({})
        if isinstance(config, str):
            try:
                with open(config, "r") as file:
                    yaml_data = yaml.safe_load(file)
                configs.merge_with(yaml_data)
            except FileNotFoundError:
                raise ValueError(f"Can not find the file: {config}")
            except Exception:
                raise RuntimeError(f"{config} read fail.\n", traceback.format_exc())

        elif isinstance(config, Config):
            if isinstance(config, BaseConfig):
                config_dict = ConfigDict(config.model_dump())
                configs.merge_with(config_dict)

        else:
            raise ValueError("Config must be a string or a Config")

        self.config = configs
        if not self.config['actor_rollout_ref']['rollout']['agent']['agent_loop_config_path']:
            if not hasattr(self, 'agent_yaml'):
                raise RuntimeError("Please check agent first before check config")
            self.config['actor_rollout_ref']['rollout']['agent']['agent_loop_config_path'] = self.agent_yaml

        if not self.config['custom_reward_function']['name']:
            if not hasattr(self, 'reward_func_name'):
                raise RuntimeError("Please check reward function first before check config")
            self.config['custom_reward_function']['name'] = self.reward_func_name
        if not self.config['custom_reward_function']['path']:
            self.config['custom_reward_function']['path'] = self.reward_file_path

        if not self.config['data']['train_files']:
            if not hasattr(self, 'train_dataset_path'):
                raise RuntimeError("Please check train dataset first before check config")
            self.config['data']['train_files'] = [self.train_dataset_path]
        if not self.config['data']['val_files']:
            if not hasattr(self, 'test_dataset_path'):
                raise RuntimeError("Please check test dataset first before check config")
            self.config['data']['val_files'] = [self.test_dataset_path]

        if not self.config['trainer']['default_local_dir']:
            local_dir = os.path.join(self.run_path, 'checkpoints')
            os.makedirs(local_dir, exist_ok=True)
            self.config['trainer']['default_local_dir'] = local_dir

        # for check
        yaml.safe_dump(OmegaConf.to_container(self.config), open(f"{self.run_path}/final_trainer.yaml", "w"))
        return self.config
