# # coding: utf-8
# # Copyright (c) 2025 inclusionAI.
# from aworld.agents.llm_agent import Agent
# from aworld.config import AgentConfig
# from train.agent_template import TEMPLATE
# from train.agent_trainer import AgentTrainer
#
# agent = Agent(
#     conf=AgentConfig(
#         llm_model_name="gpt-4o",
#         llm_api_key='a',
#         llm_base_url='',
#     ),
#     name="demo_agent",
#     desc="demo_agent",
#     system_prompt="Demo agent",
# )
#
# # agent动态生成 agent.yaml, agent_loop
#
# print(agent.conf)
#
# model_config = agent.conf.llm_config
# model_dict = dict(model_config)
# model_dict.pop("llm_provider", None)
# model_dict.pop("llm_model_name", None)
# model_dict.pop("llm_base_url", None)
# model_dict.pop("llm_api_key", None)
# model_dict.pop("llm_client_type", None)
# model_dict.pop("params", None)
# model_dict.pop("model_type", None)
#
# kv_parameters = ",\n".join([f"{k}={v}" for k, v in model_dict.items()])
# print(kv_parameters)
#
# import os
# mcp_config = {
#         "mcpServers": {
#             "gaia_server": {
#                 "type": "streamable-http",
#                 "url": "https://playground.aworldagents.com/environments/mcp",
#                 "timeout": 600,
#                 "sse_read_timeout": 600,
#                 "headers": {
#                     "ENV_CODE": "gaia",
#                     "Authorization": f'Bearer {os.environ.get("INVITATION_CODE", "")}',
#                 }
#             }
#         }
#     }
#
# con = TEMPLATE.format(agent_name=agent.name(),
#                       agent_desc=agent.desc(),
#                       system_prompt=agent.system_prompt,
#                       mcp_config=mcp_config,
#                       parser_module=type(agent.model_output_parser).__module__,
#                       parser_name=type(agent.model_output_parser).__name__,
#                       kv_parameters=kv_parameters
#                       )
# with open(f"xx.py", 'w+') as write:
#     write.writelines(con)
#
# con = f"""- name: {agent.name()}
#   _target_: {AgentTrainer.__module__}.VerlAgentLoop
#         """
# with open(f"agent.yaml", "w+") as write:
#     write.writelines(con)
import os

# import hydra
# from omegaconf import OmegaConf
# @hydra.main(config_path="examples", config_name="ppo_trainer", version_base=None)
# def main(config):
#     """Main entry point for PPO training with Hydra configuration management.
#
#     Args:
#         config_dict: Hydra configuration dictionary containing training parameters.
#     """
#     print(config)

import traceback
import yaml

config = "examples/ppo_trainer.yaml"
def main():
    args = None
    if isinstance(config, str):
        configs = dict()
        try:
            with open(config, "r") as file:
                yaml_data = yaml.safe_load(file)
            configs.update(yaml_data)
        except FileNotFoundError:
            raise ValueError(f"Can not find the file: {config}")
        except Exception:
            raise RuntimeError(f"{config} read fail.\n", traceback.format_exc())

        yaml.dump(configs, open(f"xx.yaml", "w"))
        args = sum([[f'{k}={v} \\'] for k, v in configs.items()], [])
    print(args)

if __name__ == "__main__":
    main()