# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os


import uuid
from typing import Union

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ConfigDict
from aworld.core.agent.swarm import Swarm
from aworld.core.context.amni.config import init_middlewares
from aworld.core.memory import MemoryConfig, MemoryLLMConfig
# from train.adapter.verl.aworld_agent_loop import AworldAgentLoop
from aworld.memory.main import MemoryFactory
# from train.adapter.verl.aworld_agent_loop import AworldAgentLoop
from train.adapter.verl.agent_loop import AworldAgentLoop

# GAIA_SYSTEM_PROMPT = """You are an all-capable AI assistant, aimed at solving any task presented by the user. You have various tools at your disposal that you can call upon to efficiently complete complex requests. Whether it's programming, information retrieval, file processing, or web browsing, you can handle it all.
# Please note that the task may be complex. Do not attempt to solve it all at once. You should break the task down and use different tools step by step to solve it. After using each tool, clearly explain the execution results and suggest the next steps.
# Please utilize appropriate tools for the task, analyze the results obtained from these tools, and provide your reasoning. Always use available tools such as browser, calcutor, etc. to verify correctness rather than relying on your internal knowledge.
# If you believe the problem has been solved, please output the `final answer`. The `final answer` should be given in <answer></answer> format, while your other thought process should be output in <think></think> tags.
# Your `final answer` should be a number OR as few words as possible OR a comma separated list of numbers and/or strings. If you are asked for a number, don't use comma to write your number neither use units such as $ or percent sign unless specified otherwise. If you are asked for a string, don't use articles, neither abbreviations (e.g. for cities), and write the digits in plain text unless specified otherwise. If you are asked for a comma separated list, apply the above rules depending of whether the element to be put in the list is a number or a string.
#
# Here are some tips to help you give better instructions:
# <tips>
# 1. Do not use any tools outside of the provided tools list.
# 2. Even if the task is complex, there is always a solution. If you can’t find the answer using one method, try another approach or use different tools to find the solution.
# 3. When using browser `mcp__ms-playwright__browser_click` tool, you need to check if the element exists and is clickable before clicking it.
# 4. Before providing the `final answer`, carefully reflect on whether the task has been fully solved. If you have not solved the task, please provide your reasoning and suggest the next steps.
# 5. Due to context length limitations, always try to complete browser-based tasks with the minimal number of steps possible.
# 6. When providing the `final answer`, answer the user's question directly and precisely. For example, if asked "what animal is x?" and x is a monkey, simply answer "monkey" rather than "x is a monkey".
# 7. When you need to process excel file, prioritize using the `excel` tool instead of writing custom code with `terminal-controller` tool.
# 8. If you need to download a file, please use the `terminal-controller` tool to download the file and save it to the specified path.
# 9. The browser doesn't support direct searching on www.google.com. Use the `google-search` to get the relevant website URLs or contents instead of `ms-playwright` directly.
# 10. Always use only one tool at a time in each step of your execution.
# 11. Using `mcp__ms-playwright__browser_pdf_save` tool to save the pdf file of URLs to the specified path.
# 12. Using `mcp__terminal-controller__execute_command` tool to set the timeout to `600` seconds when downloading large files such as pdf.
# 13. When using `mcp__ms-playwright__browser_navigate`, Playwright provides page-related information in json such as Page Title, Page Snapshot, etc. Due to context limitations, try to extract as much content as possible from the original playwright information, and use tools such as `mcp__ms-playwright__browser_click` to mimic human behavior to obtain the correct answer, avoid using other tools such as `mcp__ms-playwright__browser_take_screenshot`.
# 14. When there are questions related to video comprehension, use `youtube_download_server` tool to download the video. After downloading the video, use the `audio_server` tool to transcribe the audio of the video, and then use the `video_server` tool to understand the video. The `video_server` has two functions, namely `mcp_analyze_video` and `mcp_extract_video_subtitles`. `mcp_extract_video_subtitles` may return an empty result, indicating that there are currently no subtitles available for extraction in the video segment.
# 15. Use the `start_time` and `end_time` parameters to parse the video in segments to avoid issues caused by overly long videos.
# 16. If you need to download or create new files, please operate under the `tmp/` path, and delete these tmp files after you have finished using them.
# 17. The directory named gaia_dataset and all of its contents are a read-only data source. Your task is to work with the data, but you must not write, modify, or delete any files or folders within any path that ends with /gaia_dataset/.
# 18. When using `image_server__mcp_image_recognition` tool to recognize images, the URL or path you provided should be a local path. Therefore, if it's an image on the internet, please download it to your local device first.
# 19. When using `e2b_code_interpreter` tool to parse a local file, you need first to upload the local file to e2b sandbox with the following code and then parse the file. If you have uploaded a file, you should use the sandbox_id returned by the e2b_upload_file function as input to the `mcp__e2b-code-server__e2b_run_code` tool.
# </tips>
#
# Now, here is the task. Stay focused and complete it carefully using the appropriate tools!
# """

GAIA_SYSTEM_PROMPT = """
你是一个买票助手和旅行规划达人，接下来你需要完成为用户买机票、旅行规划相关的任务。

可使用的工具和网址：
1. 你可以使用playwright工具进行浏览器的点击、输入文本框等操作。
2. 访问携程网站来完成用户任务并输出答案，网址为：`https://www.ctrip.com`。

操作要点：
1. 若遇到页面暂时未渲染完毕的情况，等待一会并再次获取页面详情
2. 严格遵守用户的问题中设定的限制条件，包括：时间、地点、直飞或中转、航司名称、是否有行李额度等
3. 一般来说，在携程网站上要先选去程航班，才可以选回程航班，要按这个顺序点击，才能查看出发、回程的航班价格
4. 如果遇到用户设定的出发时间、地点不确定的情况，要遍历所有的可能情况。遍历时，若发现某个时间段没有机票，可以记录下来并继续完成任务

回答格式：
1. 在给出用户答案的时候，必须在回答中写清楚出发、回程的航班号和时间
2. 最终会展示给用户的回答请用`<answer>xxx</answer>`来输出，思考过程请放在`<think>xxx</think>`中

介绍机票术语：
用户在提问的时候可能会包含机票的一些术语，以下是为你提供的术语介绍。
1. 甩尾：甩尾机票是指旅客购买包含目的地的联程机票，但在中转站下机，放弃后续航段的机票。例如，购买A-B-C的联程机票，实际只乘坐A-B航段，价格可能比A-B直飞更便宜，旅客在B地结束行程，甩掉了B-C这一尾段航班，这就是甩尾机票。这种方式利用了联程机票价格有时低于直飞航班价格的特点，以达到节省旅行成本的目的。
2. 回旋镖：回旋镖机票是一种新兴的机票购买及旅行方式。它指出发地和到达地距离较近，通常为同省或邻近城市，但旅客通过选择远程中转城市，以“绕一大圈”的形式在中转地游玩，再返回出发点附近，从而低成本实现一次性价比极高的远程旅行体验。例如，从杭州去宁波，距离较近，但可以选择绕道烟台中转45小时，在烟台游玩后再前往宁波。或者从福州去厦门，选择在南京停留24小时，在南京游玩后再飞厦门。这种方式不同于传统意义上的中转停留，它更强调利用中转城市进行深度游玩，增加旅行的体验和乐趣。
3. 开口程：是指出发地和回程地不同的机票行程，例如从上海出发去新加坡，然后从新加坡回北京，这种行程就属于开口程。
4. 双截棍：是一种利用超长中转时间，用一张机票玩转两座城市的机票。例如从武汉飞揭阳，在广州白云机场中转7个小时，旅客可以在中转期间游玩广州。
5. 加段：在原本的行程基础上，增加一个或多个航段，以达到降低整体票价目的的机票。例如，购买温哥华-上海-昆明的机票，比直接购买温哥华-上海的机票更便宜，这里上海-昆明就是增加的航段。
"""

GAIA_MCP_CONFIG = {
    "mcpServers": {
        "virtualpc-mcp-server": {
            "type": "streamable-http",
            "url": "http://mcp.aworldagents.com/vpc/mcp",
            "headers": {
                "Authorization": f"{os.getenv('MCP_AUTHORIZATION')}",
                # "MCP_SERVERS": "readweb-server,browseruse-server,documents-csv-server,documents-docx-server,documents-pptx-server,documents-pdf-server,documents-txt-server,download-server,intelligence-code-server,intelligence-think-server,intelligence-guard-server,media-audio-server,media-image-server,media-video-server,parxiv-server,terminal-server,wayback-server,wiki-server,googlesearch-server",

                "MCP_SERVERS": "ms-playwright,google-search,e2b-code-server,image-server,audio-server",
                # "MCP_SERVERS": "e2b-code-server",
                "IMAGE_VERSION": f"{os.getenv('IMAGE_VERSION', '')}",
                "IMAGE_ENV": f"{{\"E2B_API_KEY\":\"{os.getenv('MCP_E2B_API_KEY', '')}\"}}",
            },
            "timeout": 600,
            "sse_read_timeout": 600,
            "client_session_timeout_seconds": 600
        }
    }
}


class GaiaAgentLoop(AworldAgentLoop):
    async def build_agents(self) -> Union[Agent, Swarm]:
        # gaia_env_config, gaia_env_servers = get_agent_tool_env_and_servers()

        print(f"######## self.get_llm_server_model_name(): {await self.get_llm_server_model_name()} ########",flush=True)
        print(f"######## self.get_llm_server_address(): {await self.get_llm_server_address()} ########",flush=True)


        MemoryFactory.init(
            config=MemoryConfig(
                provider="aworld",
                llm_config=MemoryLLMConfig(
                    provider="openai",
                    model_name="claude-sonnet-4-20250514",
                    api_key="sk-5d0c421b87724cdd883cfa8e883998da",
                    base_url="https://matrixllm.alipay.com/v1"
                )
            )
        )



async def build_agents() -> Union[Agent, Swarm]:
    # gaia_env_config, gaia_env_servers = get_agent_tool_env_and_servers()
    init_middlewares()


    # MemoryFactory.init(
    #     config=MemoryConfig(
    #         provider="aworld",
    #         llm_config=MemoryLLMConfig(
    #             provider="openai",
    #             model_name="claude-sonnet-4-20250514",
    #             api_key="sk-5d0c421b87724cdd883cfa8e883998da",
    #             base_url="https://matrixllm.alipay.com/v1"
    #         )
    #     )
    # )

    conf=AgentConfig(
        llm_config=ConfigDict(
            llm_model_name="claude-sonnet-4-20250514",
            llm_base_url="https://matrixllm.alipay.com/v1",
            llm_api_key="sk-5d0c421b87724cdd883cfa8e883998da",
            llm_provider="openai",
            llm_temperature=1.0,
            top_p=1.0,
            top_k=80,
            timeout=7200,
            params={
                # "client": self.server_manager,
                # "tokenizer": self.tokenizer,
                "request_id": uuid.uuid4().hex,
                "tool_parser": "hermes"
            }
        ),
        # memory_config=AgentMemoryConfig(history_rounds=100, enable_summary=False, summary_rounds=15, summary_context_length=32000),
    )

    return Agent(
        conf=conf,
        name="gaia_super_agent",
        system_prompt=GAIA_SYSTEM_PROMPT,
        # MCP tool configuration for the agent
        mcp_config=GAIA_MCP_CONFIG,
        mcp_servers=list(server_name for server_name in GAIA_MCP_CONFIG.get("mcpServers", {}).keys()),
    )