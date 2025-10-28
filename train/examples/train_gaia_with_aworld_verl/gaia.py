# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
import uuid
from datetime import datetime

from aworld.agents.amni_llm_agent import ApplicationAgent
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ConfigDict, TaskConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.context.amni import TaskInput, ApplicationContext
from aworld.core.context.amni.config import get_default_config, init_middlewares, AgentContextConfig, \
    CONTEXT_OFFLOAD_TOOL_NAME_WHITE
from aworld.core.memory import MemoryConfig, MemoryLLMConfig
from aworld.core.task import Task
# from train.adapter.verl.aworld_agent_loop import AworldAgentLoop
from aworld.memory.main import MemoryFactory

GAIA_SYSTEM_PROMPT = """You are an all-capable AI assistant, aimed at solving any task presented by the user. You have various tools at your disposal that you can call upon to efficiently complete complex requests. Whether it's programming, information retrieval, file processing, or web browsing, you can handle it all.
Please note that the task may be complex. Do not attempt to solve it all at once. You should break the task down and use different tools step by step to solve it. After using each tool, clearly explain the execution results and suggest the next steps.
Please utilize appropriate tools for the task, analyze the results obtained from these tools, and provide your reasoning. Always use available tools such as browser, calcutor, etc. to verify correctness rather than relying on your internal knowledge.
If you believe the problem has been solved, please output the `final answer`. The `final answer` should be given in <answer></answer> format, while your other thought process should be output in <think></think> tags.
Your `final answer` should be a number OR as few words as possible OR a comma separated list of numbers and/or strings. If you are asked for a number, don't use comma to write your number neither use units such as $ or percent sign unless specified otherwise. If you are asked for a string, don't use articles, neither abbreviations (e.g. for cities), and write the digits in plain text unless specified otherwise. If you are asked for a comma separated list, apply the above rules depending of whether the element to be put in the list is a number or a string.

Here are some tips to help you give better instructions: 
<tips>
1. Do not use any tools outside of the provided tools list.
2. Even if the task is complex, there is always a solution. If you can’t find the answer using one method, try another approach or use different tools to find the solution.
3. When using browser `mcp__ms-playwright__browser_click` tool, you need to check if the element exists and is clickable before clicking it. 
4. Before providing the `final answer`, carefully reflect on whether the task has been fully solved. If you have not solved the task, please provide your reasoning and suggest the next steps.
5. Due to context length limitations, always try to complete browser-based tasks with the minimal number of steps possible.
6. When providing the `final answer`, answer the user's question directly and precisely. For example, if asked "what animal is x?" and x is a monkey, simply answer "monkey" rather than "x is a monkey".
7. When you need to process excel file, prioritize using the `excel` tool instead of writing custom code with `terminal-controller` tool.
8. If you need to download a file, please use the `terminal-controller` tool to download the file and save it to the specified path.
9. The browser doesn't support direct searching on www.google.com. Use the `google-search` to get the relevant website URLs or contents instead of `ms-playwright` directly.
10. Always use only one tool at a time in each step of your execution.
11. Using `mcp__ms-playwright__browser_pdf_save` tool to save the pdf file of URLs to the specified path.
12. Using `mcp__terminal-controller__execute_command` tool to set the timeout to `600` seconds when downloading large files such as pdf.
13. When using `mcp__ms-playwright__browser_navigate`, Playwright provides page-related information in json such as Page Title, Page Snapshot, etc. Due to context limitations, try to extract as much content as possible from the original playwright information, and use tools such as `mcp__ms-playwright__browser_click` to mimic human behavior to obtain the correct answer, avoid using other tools such as `mcp__ms-playwright__browser_take_screenshot`.
14. When there are questions related to video comprehension, use `youtube_download_server` tool to download the video. After downloading the video, use the `audio_server` tool to transcribe the audio of the video, and then use the `video_server` tool to understand the video. The `video_server` has two functions, namely `mcp_analyze_video` and `mcp_extract_video_subtitles`. `mcp_extract_video_subtitles` may return an empty result, indicating that there are currently no subtitles available for extraction in the video segment.
15. Use the `start_time` and `end_time` parameters to parse the video in segments to avoid issues caused by overly long videos.
16. If you need to download or create new files, please operate under the `tmp/` path, and delete these tmp files after you have finished using them.
17. The directory named gaia_dataset and all of its contents are a read-only data source. Your task is to work with the data, but you must not write, modify, or delete any files or folders within any path that ends with /gaia_dataset/.
18. When using `image_server__mcp_image_recognition` tool to recognize images, the URL or path you provided should be a local path. Therefore, if it's an image on the internet, please download it to your local device first.
19. When using `e2b_code_interpreter` tool to parse a local file, you need first to upload the local file to e2b sandbox with the following code and then parse the file. If you have uploaded a file, you should use the sandbox_id returned by the e2b_upload_file function as input to the `mcp__e2b-code-server__e2b_run_code` tool.
</tips>

Now, here is the task. Stay focused and complete it carefully using the appropriate tools!
"""



def build_gaia_agent(llm_model_name, llm_base_url, llm_api_key, mcp_config, server_manager = None, tokenizer = None):

    MemoryFactory.init(
        config=MemoryConfig(
            provider="aworld",
            llm_config=MemoryLLMConfig(
                provider="openai",
                model_name=os.getenv("LLM_MODEL_NAME"),
                api_key=os.getenv("LLM_API_KEY"),
                base_url=os.getenv("LLM_BASE_URL")
            )
        )
    )

    conf=AgentConfig(
        llm_config=ConfigDict(
            llm_model_name=llm_model_name,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_provider="verl",
            llm_temperature=1.0,
            top_p=1.0,
            top_k=80,
            timeout=7200,
            params={
                "client": server_manager,
                "tokenizer": tokenizer,
                "request_id": uuid.uuid4().hex,
                "tool_parser": "hermes"
            }
        ),
        # memory_config=AgentMemoryConfig(history_rounds=100, enable_summary=False, summary_rounds=15, summary_context_length=32000),
    )

    if os.getenv("GAIA_AGENT_CONTEXT", "common"):
        return Agent(
            conf=conf,
            name="gaia_super_agent",
            system_prompt=GAIA_SYSTEM_PROMPT,
            # MCP tool configuration for the agent
            mcp_config=mcp_config,
            mcp_servers=list(server_name for server_name in mcp_config.get("mcpServers", {}).keys()),
        )
    else:
        return ApplicationAgent(
            conf=conf,
            name="gaia_super_agent",
            system_prompt=GAIA_SYSTEM_PROMPT,
            # MCP tool configuration for the agent
            mcp_config=mcp_config,
            mcp_servers=list(server_name for server_name in mcp_config.get("mcpServers", {}).keys()),
        )


async def build_amni_gaia_task(user_input: str, target: [Agent, Swarm], timeout, session_id: str = None, task_id: str = None):
    # 1. init middlewares
    init_middlewares()

    # 2. build context config
    # context_config = AmniConfigFactory.create(AmniConfigLevel.NAVIGATOR)
    # 定制化
    context_config = get_default_config()
    context_config.agent_config = AgentContextConfig(
        enable_system_prompt_augment=True,
        neuron_names= ["basic", "task", "work_dir", "todo", "action_info"],
        history_rounds= 100,
        enable_summary=False,
        summary_rounds= 30,
        summary_context_length= 40960,
        tool_result_offload= True,
        tool_action_white_list= CONTEXT_OFFLOAD_TOOL_NAME_WHITE,
        tool_result_length_threshold= 30000
    )

    # 3. build context
    if not session_id:
        session_id = f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    if not task_id:
        task_id = f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    task_input = TaskInput(
        user_id=f"user",
        session_id=session_id,
        task_id=task_id,
        task_content=user_input,
        origin_user_input=user_input
    )

    async def build_context(_task_input: TaskInput) -> ApplicationContext:
        """Important Config"""
        return await ApplicationContext.from_input(_task_input, context_config=context_config)

    context = await build_context(task_input)


    # 4. build swarm

    # build gaia swarm
    if isinstance(target, Agent):
        swarm = target
    else:
        swarm = Swarm(agent=target, max_steps=30)
        target.task = user_input
    await context.build_agents_state(swarm.topology)

    # 5. build task with context
    return Task(
        id=context.task_id,
        user_id=context.user_id,
        session_id=context.session_id,
        input=context.task_input,
        endless_threshold=5,
        swarm=swarm,
        context=context,
        conf=TaskConfig(
            stream=False,
            exit_on_failure=True
        ),
        timeout=timeout
    )

async def build_common_gaia_task(user_input: str, target: [Agent, Swarm], timeout):
    task_id = f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    if isinstance(target, Swarm):
        return Task(id=task_id, input=user_input, swarm=target, timeout=timeout)
    else:
        target.task = user_input
        return Task(id=task_id, input=user_input, agent=target, timeout=timeout)

async def build_gaia_task(user_input: str, target: [Agent, Swarm], timeout):
    if os.getenv("GAIA_AGENT_CONTEXT", "common"):
        return await build_common_gaia_task(user_input, target, timeout)
    else:
        return await build_amni_gaia_task(user_input, target, timeout)