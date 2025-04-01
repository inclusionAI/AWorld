# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from pydantic import BaseModel
import asyncio
from aworld.core.common import Tools

from aworld.core.client import Client
from aworld.agents.gaia.agent import PlanAgent, ExecuteAgent
from aworld.config.conf import AgentConfig, TaskConfig
from aworld.core.swarm import Swarm
from aworld.core.task import  Task
from aworld.dataset.mock import mock_dataset
from aworld.utils.diagnostic_tools import Diagnostic


class GaiaRunParams(BaseModel):
    query: str
    llmProvider: str = "openai"
    llmModelName: str = "gpt-4o"


async def gaia_run():
    # Initialize client
    client = Client()

    # One sample for example
    test_sample = mock_dataset("gaia")
    print("task_prompt", test_sample)

    import os

    GOOGLE_API_KEY = "AI----"
    GOOGLE_ENGINE_ID = "9-----"
    os.environ['GOOGLE_API_KEY'] = GOOGLE_API_KEY
    os.environ['GOOGLE_ENGINE_ID'] = GOOGLE_ENGINE_ID
    #test_sample="The attached spreadsheet shows the inventory for a movie and video game rental store in Seattle, Washington. What is the title of the oldest Blu-Ray recorded in this spreadsheet? Return it as appearing in the spreadsheet. Here are the necessary table files: {file_path}, for processing excel file, you can write python code and leverage excel toolkit to process the file step-by-step and get the information."
   # "The attached spreadsheet shows the inventory for a movie and video game rental store in Seattle, Washington. What is the title of the oldest Blu-Ray recorded in this spreadsheet? Return it as appearing in the spreadsheet. Here are the necessary table files: /Users/honglifeng/Documents/project/agi/aworld/aworld/dataset/gaia/gaia.xlsx, for processing excel file, you can write python code and leverage excel toolkit to process the file step-by-step and get the information."
    # test_sample = "What is the surname of the equine veterinarian mentioned in 1.E Exercises from the chemistry materials licensed by Marisa Alviar-Agnew & Henry Agnew under the CK-12 license in LibreText's Introductory Chemistry materials as compiled 08/21/2023?"

    # Create agents
    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_api_key="sk-",
        llm_base_url="https://api"
    )

    agent1 = PlanAgent(conf=agent_config)
    agent2 = ExecuteAgent(conf=agent_config, tool_names=[Tools.DOCUMENT_ANALYSIS.value,fiel],mcp_server=[fiel,tool1,tool2])

    # agent2 = ExecuteAgent(conf=agent_config, tool_names=[Tools.DOCUMENT_ANALYSIS.value,
    #                                                      Tools.PYTHON_EXECUTE.value, Tools.SEARCH_API.value])
    # print("-------agent_config------")
    # print(agent_config)
    # print([Tools.DOCUMENT_ANALYSIS.value, Tools.PYTHON_EXECUTE.value, Tools.SEARCH_API.value])
    # return

    # Create swarm for multi-agents
    # define (head_node1, tail_node1), (head_node1, tail_node1) edge in the topology graph
    swarm = Swarm((agent1, agent2))

    # Define a task
    task = Task(input=test_sample, swarm=swarm, conf=TaskConfig())

    # Run task
    result = client.submit(task=[task])
    diagnostic_data = await Diagnostic.get_diagnostics()
    print(f"######Diagnostic all data: {diagnostic_data}")
    print("############-diagnostic-start############")
    # for diagnostic in diagnostic_data:
    #     print(diagnostic.model_dump())
    print("###########-diagnostic-end-#############")

    # print(f"######Diagnostic data: {diagnostic_data}")
    print(f"Task completed: {result['success']}")
    print(f"Time cost: {result['time_cost']}")
    print(f"Task Answer: {result['task_0']['answer']}")


def gaia_run_for_ui(params: "GaiaRunParams"):
    # Create agents
    agent_config = AgentConfig(
        llm_provider=params.llmProvider,
        llm_model_name=params.llmModelName,
    )
    agent1 = PlanAgent(conf=agent_config)
    agent2 = ExecuteAgent(conf=agent_config)

    # Create swarm for multi-agents
    # define (head_node1, tail_node1), (head_node1, tail_node1) edge in the topology graph
    swarm = Swarm((agent1, agent2))
    # TODO 测试临时用
    test_sample = mock_dataset("gaia")
    # Define a task
    task = Task(input=test_sample, swarm=swarm, conf=TaskConfig())

    # Run task
    # Initialize client
    client = Client()
    result = client.submit(task=[task])

    return result


if __name__ == "__main__":
    asyncio.run(gaia_run())