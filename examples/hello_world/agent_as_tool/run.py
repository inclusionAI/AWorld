# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
from aworld.agents.llm_agent import Agent
from aworld.config import RunConfig, EngineName
from aworld.config.conf import AgentConfig, ModelConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.tool.func_to_tool import be_tool
from aworld.runner import Runners


# as a local tool, not recommended in cluster mode
@be_tool(tool_name='tool_1', tool_desc="tool description")
def tool_1() -> str:
    return "hello world!"


model_config = ModelConfig(
    llm_provider=os.getenv("LLM_PROVIDER", "openai"),
    llm_model_name=os.getenv("LLM_MODEL_NAME", "gpt-4o"),
    llm_base_url=os.getenv("LLM_BASE_URL", "your base url"),
    llm_api_key=os.getenv("LLM_API_KEY", "your key"),
    llm_temperature=os.getenv("LLM_TEMPERATURE", 0.0)
)
agent_config = AgentConfig(
    llm_config=model_config,
)

as_tool = Agent(name="tool_agent",
                conf=agent_config,
                system_prompt="""You are a helpful agent, and must use tool_1 once.""",
                tool_names=['tool_1'])

agent = Agent(name="my_agent",
              conf=agent_config,
              system_prompt="""You are a helpful agent, and must use tool agent once.""",
              agent_names=[as_tool.id()])
# as_tool agent no need to be in the topology
swarm = Swarm(agent, register_agents=[as_tool])

if __name__ == "__main__":
    # run use the same process in local
    # res = Runners.sync_run(input="use tool agent say", swarm=swarm)

    # run use the multiprocess (new process)
    res = Runners.sync_run(input="use tool agent say", swarm=swarm,
                           run_conf=RunConfig(engine_name=EngineName.LOCAL, reuse_process=False))

    # run use ray
    # res = Runners.sync_run(input="use tool agent say", swarm=swarm,
    #                            run_conf=RunConfig(engine_name=EngineName.RAY))

    # run use pyspark
    # res = Runners.sync_run(input="use tool agent say", swarm=swarm,
    #                        run_conf=RunConfig(engine_name=EngineName.SPARK, in_local=True))

    # hello world
    print(res.answer)
