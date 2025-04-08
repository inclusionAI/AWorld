# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.core.envs.tool import ToolFactory
from aworld.core.swarm import Swarm

from aworld.core.client import Client
from aworld.core.common import Agents, Tools
from aworld.core.task import Task
from aworld.agents.browser.agent import BrowserAgent
from aworld.config.conf import AgentConfig
from aworld.agents.browser.config import BrowserAgentConfig
from aworld.virtual_environments import BrowserTool
from aworld.virtual_environments.conf import BrowserToolConfig
from aworld.config.conf import ModelConfig

import playwright
from tqdm import tqdm
import json
import os
from datetime import datetime


if __name__ == '__main__':
    
    # read gaia-web
    gaia_li=[]
    with open("/Users/zhuige/Documents/llm/agent/projects/web_understanding/datasets/GAIA/sele_web_data/tmp.jsonl", "r") as f:
        for line in f:
            data = json.loads(line)
            gaia_li.append(data)

    current_time = datetime.now()
    formatted_time = current_time.strftime("%Y%m%d_%H%M%S")

    save_path = os.path.join("/Users/zhuige/Documents/llm/agent/projects/web_understanding/datasets/GAIA/aworld_res", formatted_time)
    if not os.path.exists(save_path):
        os.makedirs(save_path)
    os.makedirs(os.path.join(save_path,"trajectory"))
    

    client = Client()
    inner_llm_model_config = ModelConfig(
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_temperature=0.3,
        llm_api_key="dummy-key",
        llm_base_url="http://localhost:5000",
        max_input_tokens = 128000
    )
    browser_tool_config = BrowserToolConfig(width=1280,
                                            height=720,
                                            headless=False,
                                            keep_browser_open=True,
                                            llm_config=inner_llm_model_config)
    agent_config = BrowserAgentConfig(
        tool_calling_method="raw",
        agent_name=Agents.BROWSER.value,
        llm_provider="openai",
        llm_model_name="gpt-4o",
        llm_num_ctx=32000,
        llm_temperature=1,
        llm_api_key="dummy-key",
        llm_base_url="http://localhost:5000",
        max_actions_per_step=10
    )
    task_config = {
        'max_steps': 100,
        'max_actions_per_step': 20
    }
    
    open(os.path.join(save_path,"val_log.txt"),"w")
    with open(os.path.join(save_path,"val_log.txt"),"a") as f:
        # for a_task in tqdm(gaia_li,desc="run gaia validation"):
        for a_task in gaia_li:
            try:
                browser_agent=BrowserAgent(conf=agent_config,save_file_path=os.path.join(save_path,"trajectory",a_task["task_id"]+".json"))
                browser_tool=ToolFactory(Tools.BROWSER.value, conf=browser_tool_config)
    
                task = Task(input=a_task["ques"],
                            swarm=Swarm(browser_agent),
                            tools=[browser_tool],
                            task_config=task_config)
                client.submit(task)
                a_task["done"] = True
                llm_output=browser_agent.trajectory.get_history()[-1][3].content
                a_task["llm_output"]=llm_output
            except Exception as e:
                print(e)
                a_task["done"] = False
            finally:
                f.write(json.dumps(a_task) + "\n")
                f.flush()
                browser_tool.close()
