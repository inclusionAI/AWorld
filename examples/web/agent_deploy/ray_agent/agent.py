import logging
import os
import json
import ray
from aworld.config.conf import AgentConfig, ConfigDict
from aworld.core.agent.llm_agent import Agent
from aworld.core.task import Task
from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners

logger = logging.getLogger(__name__)


class RayAgentDecorator:
    def __init__(self, agent: Agent):
        try:
            # 过滤掉不需要的属性
            agent_dict = {k: v for k, v in agent.__dict__.items() 
                         if not k.startswith('_')}
            self.remote_agent = ray.remote(Agent).remote(**agent_dict)
            logger.info(f"Successfully created remote agent: {agent.id()}")
        except Exception as e:
            logger.error(f"Failed to create remote agent: {str(e)}")
            raise

    def __getattr__(self, name):
        try:
            attr = getattr(self.remote_agent, name)
            if callable(attr):
                def wrapper(*args, **kwargs):
                    try:
                        remote_method = attr
                        return ray.get(remote_method.remote(*args, **kwargs))
                    except ray.exceptions.RayActorError as e:
                        logger.error(f"Ray actor error in {name}: {str(e)}")
                        raise
                    except Exception as e:
                        logger.error(f"Error in remote method {name}: {str(e)}")
                        raise
                return wrapper
            return attr
        except Exception as e:
            logger.error(f"Error in __getattr__ for {name}: {str(e)}")
            raise


class AWorldAgent:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        import ray

        if not ray.is_initialized():
            try:
                ray.init(address="ray://localhost:10001", log_to_driver=True)
                logger.info("Successfully initialized Ray")
            except Exception as e:
                logger.error(f"Failed to initialize Ray: {str(e)}")
                raise

    def get_agent_info(self):
        return {"name": "Ray Agent", "description": "Powerful Agent"}

    async def run(self, prompt: str):
        try:
            llm_provider = os.getenv("LLM_PROVIDER_WEATHER", "openai")
            llm_model_name = os.getenv("LLM_MODEL_NAME_WEATHER")
            llm_api_key = os.getenv("LLM_API_KEY_WEATHER")
            llm_base_url = os.getenv("LLM_BASE_URL_WEATHER")
            llm_temperature = os.getenv("LLM_TEMPERATURE_WEATHER", 0.0)

            if not llm_model_name or not llm_api_key or not llm_base_url:
                raise ValueError(
                    "LLM_MODEL_NAME, LLM_API_KEY, LLM_BASE_URL must be set in your envrionment variables"
                )

            agent_config = AgentConfig(
                llm_provider=llm_provider,
                llm_model_name=llm_model_name,
                llm_api_key=llm_api_key,
                llm_base_url=llm_base_url,
                llm_temperature=llm_temperature,
            )

            path_cwd = os.path.dirname(os.path.abspath(__file__))
            mcp_path = os.path.join(path_cwd, "mcp.json")
            with open(mcp_path, "r") as f:
                mcp_config = json.load(f)

            super_agent = Agent(
                conf=agent_config,
                name="powerful_agent",
                system_prompt="You are a powerful weather agent, you can use playwright to do anything you want",
                mcp_config=mcp_config,
                mcp_servers=mcp_config.get("mcpServers", {}).keys(),
            )

            summary_agent = Agent(
                conf=agent_config,
                name="summary_agent",
                system_prompt="You can summarize the content to a list of key points",
                mcp_servers=mcp_config.get("mcpServers", {}).keys(),
            )

            # Wrap agents with Ray decorator
            super_agent = RayAgentDecorator(super_agent)
            summary_agent = RayAgentDecorator(summary_agent)

            swarm = Swarm(super_agent, summary_agent)

            task = Task(input=prompt, swarm=swarm, event_driven=False)
            res = await Runners.run_task(task, run_conf=ConfigDict({"name": "ray"}))
            yield res.get(task.id).answer
        except Exception as e:
            logger.error(f"Error in run method: {str(e)}")
            raise
