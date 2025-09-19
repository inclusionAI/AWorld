from aworld.evaluations.base import EvalTarget, EvalDataCase
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.runner import Runners
from typing import Optional

import os


class AworldAgentEvalTarget(EvalTarget[dict]):

    def __init__(self, agent: Optional[Agent] = None, agent_config: Optional[dict | str] = None, query_column: str = 'query'):
        self.query_column = query_column

        if agent is not None:
            self.agent = agent
        elif agent_config is not None:
            self.agent = self._create_agent_from_config(agent_config)
        else:
            raise ValueError("Either 'agent' or 'agent_config' must be provided")

    def _create_agent_from_config(self, agent_config):
        if isinstance(agent_config, str):
            import json
            agent_config = json.loads(agent_config)
        if isinstance(agent_config, dict):
            agent_conf_dict = agent_config.get('conf', agent_config)
            if isinstance(agent_conf_dict, AgentConfig):
                agent_conf = agent_conf_dict
            else:
                agent_conf = AgentConfig(
                    llm_provider=agent_conf_dict.get('llm_provider', os.getenv("LLM_PROVIDER")),
                    llm_model_name=agent_conf_dict.get('llm_model_name', os.getenv("LLM_MODEL_NAME")),
                    llm_temperature=float(agent_conf_dict.get('llm_temperature', os.getenv("LLM_TEMPERATURE", "0.3"))),
                    llm_base_url=agent_conf_dict.get('llm_base_url', os.getenv("LLM_BASE_URL")),
                    llm_api_key=agent_conf_dict.get('llm_api_key', os.getenv("LLM_API_KEY")),
                )
            return Agent(
                conf=agent_conf,
                name=agent_config.get('name', 'agent_for_eval'),
                system_prompt=agent_config.get('system_prompt', ""),
                agent_prompt=agent_config.get('agent_prompt', "")
            )

        raise ValueError(f"Invalid agent_config type: {type(agent_config)}")

    async def predict(self, input: EvalDataCase[dict]) -> dict:
        response = await Runners.run(input.case_data[self.query_column], agent=self.agent)
        return {"answer": response.answer}
