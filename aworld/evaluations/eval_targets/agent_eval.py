import abc

from aworld.evaluations.base import EvalTarget, EvalDataCase
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.runner import Runners
from typing import Optional
from aworld.core.task import Task, TaskResponse

import os


class AworldAgentEvalTarget(EvalTarget[dict]):
    """
    Evaluation target for AWorld agents.

    Args:
        agent (Optional[Agent]): The agent to be evaluated.
        agent_config (Optional[dict | str]): The configuration of the agent to be created.
            example:
            {
                "name": "plan_execute_agent",
                "conf": {
                    "llm_provider": "openai",
                    "llm_model_name": "gpt-3.5-turbo",
                    "llm_temperature": 0.3,
                    "llm_base_url": "https://api.openai.com/v1",
                    "llm_api_key": "sk-xxx"
                },
                "system_prompt": "You are a helpful assistant.",
                "agent_prompt": "You are a helpful assistant.",
            }
        query_column (str): The column name in the dataset that contains the input queries. Defaults to 'query'.
    """

    def __init__(self, agent: Optional[Agent] = None, agent_config: Optional[dict | str] = None, query_column: str = 'query'):
        super().__init__()
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

    async def predict(self, index: int, input: EvalDataCase[dict]) -> dict:
        query_column = self.eval_config.eval_dataset_query_column or self.query_column
        response = await Runners.run(input.case_data[query_column], agent=self.agent)
        return {"answer": response.answer}


class AworldTaskEvalTarget(EvalTarget[dict]):

    @abc.abstractmethod
    async def build_task(self, index: int, input: EvalDataCase[dict]) -> Task:
        """
        Build a task for evaluation.

        Args:
            index (int): The index of the data case.
            input (EvalDataCase[dict]): The input data case for the task.

        Returns:
            Task: The built task.
        """
        raise NotImplementedError

    async def predict(self, index: int, input: EvalDataCase[dict]) -> dict:
        task = await self.build_task(index, input)
        result = await Runners.run_task(task=task)
        if isinstance(result, TaskResponse):
            return {"answer": result.answer}
        if isinstance(result, dict):
            return {"answer": result[task.id].answer}
        else:
            return {"answer": result}
