# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import inspect
import os
import traceback
import yaml

from typing import Callable, Union, Tuple

from datasets import Dataset
from omegaconf import OmegaConf
from omegaconf.dictconfig import DictConfig

from aworld.agents.llm_agent import Agent
from aworld.config import BaseConfig, ConfigDict, load_config
from aworld.core.common import Config
from aworld.logs.util import logger
from train.adapter.verl.agent_template import VERL_TEMPLATE
from train.trainer.trainer_processor import TrainerProcessor


class VerlTrainer(TrainerProcessor):
    def train(self):
        """Supported PPO/GRPO only now."""

        from verl.trainer.main_ppo import main

        if not self.initialized:
            raise RuntimeError("Please check all components before training")

        main(self.config)

    def check_dataset(self, dataset: Union[str, Dataset], test_dataset: Union[str, Dataset] = None) -> Tuple[str, str]:
        logger.info("Check dataset...")

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

        if not test_dataset:
            test_dataset = dataset_path

        if isinstance(test_dataset, str):
            # means dataset path
            test_dataset_path = test_dataset
        elif isinstance(test_dataset, Dataset):
            test_dataset.to_parquet(f'{self.run_path}/test_data.parquet')
            test_dataset_path = f'{self.run_path}/test_data.parquet'
        else:
            test_dataset_path = None
        self.test_dataset_path = test_dataset_path

        logger.info(f"View datasets in file: {self.train_dataset_path} and {self.test_dataset_path}")
        return self.train_dataset_path, self.test_dataset_path

    def check_reward(self, reward_func: Union[str, Callable[..., float]]) -> Tuple[str, str]:
        logger.info("Check reward...")

        if isinstance(reward_func, str):
            # means reward func file path
            name = os.path.basename(reward_func).replace(".py", "")
            self.reward_file_path = reward_func
            self.reward_func_name = name
            logger.info(f"View reward function in file: {reward_func}, name is: {name}")
            return reward_func, name

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
            # and the func must is a dependency-free function
            reward_file_path = f'{self.run_path}/reward_func.py'
            with open(reward_file_path, 'w') as writer:
                writer.write(content)
        else:
            reward_file_path = inspect.getfile(reward_func)

        self.reward_file_path = reward_file_path
        self.reward_func_name = reward_func.__name__
        logger.info(f"View reward function in file: {reward_file_path}, name is: {self.reward_file_path}")
        return reward_file_path, reward_func.__name__

    def check_agent(self, agent: Union[str, Agent], context_config, task_config) -> str:
        """Check single agent instance, and create agent loop dynamically.

        NOTE: Single-agent only now, Swarm to be added in the future.

        Returns:
            Return agent yaml file used to VeRL agent loop.
        """
        logger.info("Check agent...")

        if isinstance(agent, str):
            # means an agent yaml config file path
            config_dict = load_config(agent)
            agent = Agent(**config_dict)

        # model params
        model_config: BaseConfig = agent.conf.llm_config
        if isinstance(model_config, dict):
            model_dict = dict(model_config)
        else:
            model_dict = dict(model_config.to_dict())

        for key in ["llm_provider", "llm_model_name", "llm_base_url",
                    "llm_api_key", "llm_client_type", "params", "model_type"]:
            model_dict.pop(key, None)

        model_kv_parameters = ",\n".join([f"{k}={v}" for k, v in model_dict.items()])

        # agent params
        func_name = None
        func_str = ''
        if agent.tools_aggregate_func != agent._tools_aggregate_func:
            # special process tools_aggregate_func
            if agent.tools_aggregate_func.__module__ == '__main__':
                raise ValueError("tools_aggregate_func must be in a independent file")
            else:
                func_str = f"from {agent.tools_aggregate_func.__module__} import {agent.tools_aggregate_func.__name__}"
            func_name = agent.tools_aggregate_func.__name__

        if agent.__class__ == Agent:
            import_str = ''
            extend_params = ''
        else:
            # custom agent, the custom parameters must be explicitly specified
            import_str = f"from {agent.__module__} import {agent.__class__.__name__}"
            base_sig = inspect.signature(Agent.__init__)
            base_params = base_sig.parameters

            sig = inspect.signature(agent.__init__)
            kv = []
            for k, v in sig.parameters.items():
                if k not in base_params:
                    kv.append(f"{k}={getattr(agent, k)}")
            extend_params = ',\n'.join(kv)

        # NOTE: If the basic interface of the `Agent` changes, an upgrade is required
        con = VERL_TEMPLATE.format(agent_name=agent.name(),
                                   agent_desc=agent.desc(),
                                   system_prompt=agent.system_prompt,
                                   mcp_config=agent.mcp_config,
                                   tool_names=agent.tool_names,
                                   agent_names=agent.handoffs,
                                   wait_tool_result=agent.wait_tool_result,
                                   feedback_tool_result=agent.feedback_tool_result,
                                   black_tool_actions=agent.black_tool_actions,
                                   skill_configs=agent.skill_configs,
                                   event_handler_name=agent.event_handler_name,
                                   context_config=context_config,
                                   task_config=task_config,
                                   tool_aggregate_func_import_str=func_str,
                                   tools_aggregate_func=func_name,
                                   parser_module=type(agent.model_output_parser).__module__,
                                   parser_name=type(agent.model_output_parser).__name__,
                                   model_kv_parameters=model_kv_parameters,
                                   agent_import_str=import_str,
                                   real_agent=agent.__class__.__name__,
                                   extend_params=extend_params)
        module = f"{self.run_path}/{agent.name()}"
        with open(f"{module}.py", 'w+') as write:
            write.writelines(con)

        # VeRL agent config file
        module = module.replace(os.getcwd(), '').replace('/', '.')
        module = module[1:] if module[0] == '.' else module
        con = f"""- name: {agent.name()}
  _target_: train.examples.train_gaia_with_aworld_verl.rollout.verl_agent_loop.VerlAgentLoop
               """

        agent_yaml = f"{self.run_path}/agent.yaml"
        with open(agent_yaml, "w+") as write:
            write.writelines(con)
        self.agent_yaml = agent_yaml
        logger.info(f"View agent config in file: {agent_yaml}")
        return self.agent_yaml

    def check_config(self, config: Union[str, Config]) -> DictConfig:
        import verl.trainer.config

        logger.info("Check config...")

        # custom config or config file
        custom_configs = dict()
        if isinstance(config, str):
            try:
                with open(config, "r") as file:
                    custom_configs = yaml.safe_load(file)
            except FileNotFoundError:
                raise ValueError(f"Can not find the file: {config}")
            except Exception:
                raise RuntimeError(f"{config} read fail.\n", traceback.format_exc())
        elif isinstance(config, Config):
            if isinstance(config, BaseConfig):
                custom_configs = ConfigDict(config.model_dump())
            else:
                custom_configs = config
        else:
            raise ValueError("Config must be a string or a Config")

        # full config
        file_path = os.path.join(os.path.dirname(verl.trainer.config.__file__), "_generated_ppo_trainer.yaml")
        try:
            with open(file_path, "r") as file:
                root_configs = yaml.safe_load(file)
        except FileNotFoundError:
            raise ValueError(f"Can not find the file: {config}")
        except Exception:
            raise RuntimeError(f"{config} read fail.\n", traceback.format_exc())

        configs = OmegaConf.merge(root_configs, custom_configs)
        configs = DictConfig(OmegaConf.to_container(configs, resolve=True))
        logger.debug(f"train full configs: {configs}")

        self.config = configs
        # replace to real value, because the values are dynamically generated
        if not self.config.actor_rollout_ref.rollout.agent.agent_loop_config_path:
            if not hasattr(self, 'agent_yaml'):
                raise RuntimeError("Please check agent first before check config")
            self.config.actor_rollout_ref.rollout.agent.agent_loop_config_path = self.agent_yaml

        if not self.config.custom_reward_function.name:
            if not hasattr(self, 'reward_func_name'):
                raise RuntimeError("Please check reward function first before check config")
            self.config.custom_reward_function.name = self.reward_func_name
        if not self.config.custom_reward_function.path:
            self.config.custom_reward_function.path = self.reward_file_path

        if not self.config.data.train_files:
            if not hasattr(self, 'train_dataset_path'):
                raise RuntimeError("Please check train dataset first before check config")
            self.config.data.train_files = [self.train_dataset_path]
        if not self.config.data.val_files:
            if not hasattr(self, 'test_dataset_path'):
                raise RuntimeError("Please check test dataset first before check config")
            self.config.data.val_files = [self.test_dataset_path]

        if not self.config.trainer.default_local_dir:
            local_dir = os.path.join(self.run_path, 'checkpoints')
            os.makedirs(local_dir, exist_ok=True)
            self.config.trainer.default_local_dir = local_dir

        # for check
        yaml.safe_dump(OmegaConf.to_container(self.config), open(f"{self.run_path}/final_trainer.yaml", "w"))
        logger.info(f"View final config in file: {self.run_path}/final_trainer.yaml")
        return self.config
