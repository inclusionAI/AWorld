import uuid

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ConfigDict
from aworld.core.agent.swarm import Swarm
from aworld.core.memory import MemoryConfig, MemoryLLMConfig
# from train.adapter.verl.aworld_agent_loop import AworldAgentLoop
from aworld.memory.main import MemoryFactory
from examples.xbench.agents.orchestrator_agent.config import orchestrator_agent_config

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

GAIA_MCP_CONFIG = {
    "mcpServers": {
        "virtualpc-mcp-server": {
            "type": "streamable-http",
            "url": "http://mcp.aworldagents.com/vpc/mcp",
            "headers": {
                "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhcHAiOiJhd29ybGRjb3JlLWFnZW50IiwidmVyc2lvbiI6MSwidGltZSI6MTc1NjM0ODcyMi45MTYyODd9.zM_l1VghOHaV6lC_0fYmZ35bLnH8uxIaA8iGeyuwQWY",
                # "MCP_SERVERS": "readweb-server,browseruse-server,documents-csv-server,documents-docx-server,documents-pptx-server,documents-pdf-server,documents-txt-server,download-server,intelligence-code-server,intelligence-think-server,intelligence-guard-server,media-audio-server,media-image-server,media-video-server,parxiv-server,terminal-server,wayback-server,wiki-server,googlesearch-server",

                "MCP_SERVERS": "ms-playwright,google-search,e2b-code-server,image-server,audio-server",
                # "MCP_SERVERS": "e2b-code-server",
                "IMAGE_ENV": "{\"E2B_API_KEY\":\"e2b_1a9ada478b1c4a7d53837b9595b8e44e45a6b37a\"}", # 在客户端指定tool的环境变量值，注意JSON String结构
            },
            "timeout": 600,
            "sse_read_timeout": 600,
            "client_session_timeout_seconds": 600
        }
    }
}

# Orchestrator Agent - responsible for task analysis and agent coordination
def build_xbench_swarm():
    # orchestrator_agent = OrchestratorAgent(
    #     name="orchestrator_agent",
    #     desc="orchestrator_agent",
    #     conf=orchestrator_agent_config,
    #     system_prompt=orchestrator_agent_system_prompt,
    #     mcp_servers=orchestrator_mcp_servers,
    #     mcp_config=MCP_CONFIG
    # )
    #
    # web_agent = WebAgent(
    #     name="web_agent",
    #     desc="You are a professional web browsing expert, skilled in collecting, organizing, and analyzing information through browser operations. Your goal is to obtain the most comprehensive and detailed web information",
    #     conf=web_agent_config,
    #     system_prompt=web_agent_system_prompt,
    #     mcp_servers=web_mcp_servers,
    #     mcp_config=MCP_CONFIG,
    #     black_tool_actions={
    #         "document_server": [
    #             "mcpreadtext",
    #             "mcpreadjson",
    #             "mcpreadhtmltext",
    #             "mcpreadxml"
    #         ]
    #     }
    # )
    #
    # coding_agent = CodingAgent(
    #     name="coding_agent",
    #     desc="You are a coding expert, skilled in using coding, executing code, and other abilities to complete tasks",
    #     conf=coding_agent_config,
    #     system_prompt=coding_agent_system_prompt,
    #     mcp_servers=coding_mcp_servers,
    #     mcp_config=MCP_CONFIG
    # )

    # print(f"######## self.get_llm_server_model_name(): {await self.get_llm_server_model_name()} ########",flush=True)
    # print(f"######## self.get_llm_server_address(): {await self.get_llm_server_address()} ########",flush=True)


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

    agent = Agent(
        conf=orchestrator_agent_config,
        name="gaia_super_agent",
        system_prompt=GAIA_SYSTEM_PROMPT,
        # MCP tool configuration for the agent
        mcp_config=GAIA_MCP_CONFIG,
        mcp_servers=list(server_name for server_name in GAIA_MCP_CONFIG.get("mcpServers", {}).keys()),
    )

    return Swarm(agent, max_steps=30)
