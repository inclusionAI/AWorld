import os
from typing import Any

from pydantic.alias_generators import to_camel

from aworld.config.conf import TaskConfig, AgentConfig, ModelConfig
from aworld.core.agent.swarm import Swarm, TeamSwarm
from aworld.core.common import Observation, ActionModel
from aworld.core.context.amni import ApplicationContext
from aworld.core.event.base import Message
from aworld.core.task import Task
from aworld.logs.util import logger
from aworld.runner import Runners
from aworldspace.agents.trading_team.agents.strategy_expert import StrategyExpert
from aworldspace.models import ApplicationAgent
from aworldspace.agents.trading_team import PortfolioManager
from aworldspace.agents.trading_team.application import ScreenApplicationAgent, ResearchTeamApplicationAgent, \
    DebateTeamApplicationAgent, BacktestApplicationAgent, WorkflowApplicationAgent
from aworldspace.agents.trading_team.prompts.workflow_prompt import workflow_sys_prompt

conf: AgentConfig = AgentConfig(
    llm_model_name=os.getenv("TRADING_AGENT_LLM_MODEL_NAME"),
    llm_base_url=os.getenv("TRADING_AGENT_LLM_BASE_URL", "https://openrouter.ai/api/v1"),
    llm_api_key=os.getenv("TRADING_AGENT_LLM_API_KEY"),
    llm_temperature=float(os.getenv("TEMPERATURE", "0.5")),
)


class FinancialDataAnalysisAgent(ApplicationAgent):
    async def async_policy(
            self, observation: Observation, info: dict[str, Any] = {}, message: Message = None, **kwargs
    ) -> list[ActionModel]:
        context = self.get_task_context(message)
        current_task = context._task
        swarm = await self._build_swarm(context=context)
        sub_task = await self.build_sub_aworld_run_task(
            observation=observation,
            swarm=swarm,
            sub_task_context=context,
            parent_task=current_task,
        )
        task_result = await Runners.run_task(sub_task)
        task_response = task_result[sub_task.id] if task_result else None
        answer = task_response.answer if task_response is not None else None

        logger.info(f"🎉 {self.__class__.name} has finished the task!")
        return [ActionModel(policy_info=answer, agent_name=self.id())]

    async def build_sub_aworld_run_task(
            self,
            observation: Observation,
            swarm: Swarm | None,
            sub_task_context: ApplicationContext,
            parent_task: Task,
    ) -> Task:
        aworld_run_task = Task(
            user_id=sub_task_context.user_id,
            session_id=sub_task_context.session_id,
            input=observation.content,
            endless_threshold=5,
            swarm=swarm,
            context=sub_task_context,
            conf=TaskConfig(stream=False, exit_on_failure=True),
            is_sub_task=True,
            outputs=parent_task.outputs,
            parent_task=parent_task,
        )
        return aworld_run_task

    async def _build_swarm(self, context: ApplicationContext) -> Swarm:
        manager = PortfolioManager()
        screen_agent = ScreenApplicationAgent(conf=conf, name="screen_agent")
        research_team = ResearchTeamApplicationAgent(conf=conf, name="research_team")
        debate_team = DebateTeamApplicationAgent(conf=conf, name="debate_team")
        strategy_expert = StrategyExpert()
        backtest_agent = BacktestApplicationAgent(conf=conf, name="backtest_agent")
        workflow_agent = WorkflowApplicationAgent(conf=AgentConfig(
            llm_config=ModelConfig(
                llm_model_name=os.getenv("TRADING_AGENT_WORKFLOW_LLM_MODEL_NAME"),
                llm_base_url=os.getenv("TRADING_AGENT_WORKFLOW_LLM_BASE_URL", "https://openrouter.ai/api/v1"),
                llm_api_key=os.getenv("TRADING_AGENT_WORKFLOW_LLM_API_KEY"),
                llm_temperature=float(os.getenv("TEMPERATURE", "0.1")),
            ),
            use_vision=False
        ),
            name="workflow_agent",
            system_prompt=workflow_sys_prompt(),
        )
        # return TeamSwarm(manager, research_team, backtest_agent, workflow_agent)
        return TeamSwarm(manager, research_team, backtest_agent, workflow_agent)

    def agent_name(self) -> str:
        return to_camel(self.__class__.__name__)
