# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os

from aworld.agents.llm_agent import Agent
from aworld.config import RunConfig, EngineName
from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners

os.environ["LLM_MODEL_NAME"] = "gpt-4o"
os.environ["LLM_BASE_URL"] = "http://localhost:34567"
os.environ["LLM_API_KEY"] = "your key"

researcher = Agent(
    name="Research Agent",
    system_prompt="You specialize at researching.",
)
summarizer = Agent(
    name="Summary Agent",
    system_prompt="You specialize at summarizing.",
)

swarm = Swarm(researcher)
# Create agent swarm with collaborative workflow
# swarm = Swarm(topology=[(researcher, summarizer)])

question = "Answer only: Hello world!"

if __name__ == "__main__":
    # run use the same process in local
    # result = Runners.sync_run(
    #     input=question,
    #     swarm=swarm,
    # )

    # run use the multiprocess (new process)
    result = Runners.sync_run(
        input=question,
        swarm=swarm,
        run_conf=RunConfig(engine_name=EngineName.LOCAL, reuse_process=False)
    )
    # Hello world!
    print(result.answer)
