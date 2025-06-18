# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import json
import logging
import os

from dotenv import load_dotenv

from aworld.config.conf import AgentConfig, TaskConfig
from aworld.core.agent.base import Agent
from aworld.core.memory import MemoryConfig
from aworld.core.task import Task
from aworld.output.ui.base import AworldUI
from aworld.runner import Runners
from custom.custom_rich_aworld_ui import RichAworldUI

GAIA_SYSTEM_PROMPT = f"""You are an all-capable AI assistant, aimed at solving any task presented by the user. You have various tools at your disposal that you can call upon to efficiently complete complex requests. Whether it's programming, information retrieval, file processing, or web browsing, you can handle it all.
Please note that the task may be complex. Do not attempt to solve it all at once. You should break the task down and use different tools step by step to solve it. After using each tool, clearly explain the execution results and suggest the next steps.
Please utilize appropriate tools for the task, analyze the results obtained from these tools, and provide your reasoning. Always use available tools such as browser, calcutor, etc. to verify correctness rather than relying on your internal knowledge.
If you believe the problem has been solved, please output the `final answer`. The `final answer` should be given in <answer></answer> format, while your other thought process should be output in <think></think> tags.
Your `final answer` should be a number OR as few words as possible OR a comma separated list of numbers and/or strings. If you are asked for a number, don't use comma to write your number neither use units such as $ or percent sign unless specified otherwise. If you are asked for a string, don't use articles, neither abbreviations (e.g. for cities), and write the digits in plain text unless specified otherwise. If you are asked for a comma separated list, apply the above rules depending of whether the element to be put in the list is a number or a string.

Here are some tips to help you give better instructions: 
<tips>
1. Do not use any tools outside of the provided tools list.
2. Even if the task is complex, there is always a solution. If you can’t find the answer using one method, try another approach or use different tools to find the solution.
3. When using browser `playwright_click` tool, you need to check if the element exists and is clickable before clicking it. 
4. Before providing the `final answer`, carefully reflect on whether the task has been fully solved. If you have not solved the task, please provide your reasoning and suggest the next steps.
5. Due to context length limitations, always try to complete browser-based tasks with the minimal number of steps possible.
6. When providing the `final answer`, answer the user's question directly and precisely. For example, if asked "what animal is x?" and x is a monkey, simply answer "monkey" rather than "x is a monkey".
7. When you need to process excel file, prioritize using the `excel` tool instead of writing custom code with `terminal-controller` tool.
8. If you need to download a file, please use the `terminal-controller` tool to download the file and save it to the specified path.
9. The browser doesn't support direct searching on www.google.com. Use the `google-search` to get the relevant website URLs or contents instead of `ms-playwright` directly.
10. Always use only one tool at a time in each step of your execution.
11. Using `mcp__ms-playwright__browser_pdf_save` tool to save the pdf file of URLs to the specified path.
12. Using `mcp__terminal-controller__execute_command` tool to set the timeout to 300 seconds when downloading large files such as pdf.
13. Using `mcp__ms-playwright__browser_take_screenshot` tool to save the screenshot of URLs to the specified path when you need to understand the gif / jpg of the URLs.
14. When there are questions related to YouTube video comprehension, use tools in `youtube_download_server` and `video_server` to analyze the video content by the given question.
</tips>

Now, here is the task. Stay focused and complete it carefully using the appropriate tools!
"""


# async def pre_chunks_msg(msg: dict):
#     if msg['role'] == "user" or len(msg['content']) <= os.environ.get("CHUNK_SIZE", 10000):
#         return msg
#     logging.info("pre_chunks_msg chunks msg start")
#     result = await call_mcp_tool(
#         "chunk-server",
#         "wrap_mcp_result",
#         parameter={
#             "original_result": msg['content'],
#             "tool_name": "chunk-server",
#             "content_type": "text"
#         },
#         server_config={
#             "url": "http://localhost:10000/sse"
#         }
#     )
#     result = json.loads(result.content[0].text)
#     result_msg = f"\n\n {result['content']}\n\n{result['chunk_info']['hint']} \n\n"
#     result_msg += f"\nmessage_id:{result['chunk_info']['message_id']}"
#     result_msg += f"\ncurrent_index:{result['chunk_info']['current_index']}"
#     result_msg += f"\ntotal_chunk:{result['chunk_info']['total_chunks']}"
#     result_msg += f"\nhas_more:{result['chunk_info']['has_more']}"
#     msg['content'] = result_msg
#     logging.info(f"pre_chunks_msg chunks msg result {result_msg}")
#

if __name__ == '__main__':
    user_input = "What is the minimum number of page links a person must click on to go from the english Wikipedia page on The Lord of the Rings (the book) to the english Wikipedia page on A Song of Ice and Fire (the book series)? In your count, include each link you would click on to get to the page. Use the pages as they appeared at the end of the day on July 3, 2023."



    load_dotenv()
    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name=os.environ["LLM_MODEL_NAME"],
        llm_api_key=os.environ["LLM_API_KEY"],
        llm_base_url=os.environ["LLM_BASE_URL"]
    )
    amap_agent = Agent(
        conf=agent_config,
        name="gaia_agent",
        memory_config=MemoryConfig(provider="mem0", enable_summary=True, summary_single_context_length=5000),
        system_prompt=GAIA_SYSTEM_PROMPT,
        mcp_servers=[
            # "e2b-server",
            # "terminal-controller",
            # "excel",
            # "calculator",
            "ms-playwright",
            # "audio_server",
            # "image_server",
            # "video_server",
            "search_server",
            # "download_server",
            # "document_server",
            # "youtube_server",
            # "reasoning_server",
            # "chunk-server"
        ],  # MCP server name for agent to use
        history_messages=100,
        mcp_config={
            "mcpServers": {
                "e2b-server": {
                    "command": "npx",
                    "args": [
                        "-y",
                        "@e2b/mcp-server"
                    ],
                    "env": {
                        "E2B_API_KEY": os.environ["E2B_API_KEY"],
                        "SESSION_REQUEST_CONNECT_TIMEOUT": "60"
                    }
                },
                "filesystem": {
                    "command": "npx",
                    "args": [
                        "-y",
                        "@modelcontextprotocol/server-filesystem",
                        "${FILESYSTEM_SERVER_WORKDIR}"
                    ]
                },
                "terminal-controller": {
                    "command": "python",
                    "args": [
                        "-m",
                        "terminal_controller"
                    ]
                },
                "calculator": {
                    "command": "python",
                    "args": [
                        "-m",
                        "mcp_server_calculator"
                    ]
                },
                "excel": {
                    "command": "npx",
                    "args": [
                        "--yes",
                        "@negokaz/excel-mcp-server"
                    ],
                    "env": {
                        "EXCEL_MCP_PAGING_CELLS_LIMIT": "4000",
                        "SESSION_REQUEST_CONNECT_TIMEOUT": "20"
                    }
                },
                "google-search": {
                    "command": "npx",
                    "args": [
                        "-y",
                        "@adenot/mcp-google-search"
                    ],
                    "env": {
                        "GOOGLE_API_KEY": os.environ["GOOGLE_API_KEY"],
                        "GOOGLE_SEARCH_ENGINE_ID": os.environ["GOOGLE_CSE_ID"]
                    }
                },
                "ms-playwright": {
                    "command": "npx",
                    "args": [
                        "@playwright/mcp@latest"
                    ],
                    "env": {
                        "PLAYWRIGHT_TIMEOUT": "120000",
                        "SESSION_REQUEST_CONNECT_TIMEOUT": "120"
                    }
                },
                "audio_server": {
                    "command": "python",
                    "args": [
                        "-m",
                        "mcp_servers.audio_server"
                    ],
                    "env": {
                        "AUDIO_LLM_API_KEY": os.environ["AUDIO_LLM_API_KEY"],
                        "AUDIO_LLM_BASE_URL": os.environ["AUDIO_LLM_BASE_URL"],
                        "AUDIO_LLM_MODEL_NAME": os.environ["AUDIO_LLM_MODEL_NAME"],
                    }
                },
                "image_server": {
                    "command": "python",
                    "args": [
                        "-m",
                        "mcp_servers.image_server"
                    ],
                    "env": {
                        "LLM_API_KEY": os.environ.get("LLM_API_KEY"),
                        "LLM_MODEL_NAME": os.environ.get("LLM_MODEL_NAME"),
                        "LLM_BASE_URL": os.environ.get("LLM_BASE_URL"),
                    }
                },
                "youtube_server": {
                    "command": "python",
                    "args": [
                        "-m",
                        "mcp_servers.youtube_server"
                    ],
                    "env": {
                    }
                },
                "video_server": {
                    "command": "python",
                    "args": [
                        "-m",
                        "mcp_servers.video_server"
                    ],
                    "env": {
                        "LLM_API_KEY": os.environ.get("LLM_API_KEY"),
                        "LLM_MODEL_NAME": os.environ.get("LLM_MODEL_NAME"),
                        "LLM_BASE_URL": os.environ.get("LLM_BASE_URL"),
                    }
                },
                "search_server": {
                    "command": "python",
                    "args": [
                        "-m",
                        "mcp_servers.search_server"
                    ],
                    "env": {
                        "GOOGLE_API_KEY": os.environ["GOOGLE_API_KEY"],
                        "GOOGLE_CSE_ID": os.environ["GOOGLE_CSE_ID"],
                        "SESSION_REQUEST_CONNECT_TIMEOUT": "120"
                    }
                },
                "download_server": {
                    "command": "python",
                    "args": [
                        "-m",
                        "mcp_servers.download_server"
                    ],
                    "env": {
                        "SESSION_REQUEST_CONNECT_TIMEOUT": "120"
                    }
                },
                "document_server": {
                    "command": "python",
                    "args": [
                        "-m",
                        "mcp_servers.document_server"
                    ]
                },
                "browser_server": {
                    "command": "python",
                    "args": [
                        "-m",
                        "mcp_servers.browser_server"
                    ],
                    "env": {
                        "LLM_API_KEY": os.environ.get("LLM_API_KEY"),
                        "LLM_MODEL_NAME": os.environ.get("LLM_MODEL_NAME"),
                        "LLM_BASE_URL": os.environ.get("LLM_BASE_URL"),
                    }
                },
                "reasoning_server": {
                    "command": "python",
                    "args": [
                        "-m",
                        "mcp_servers.reasoning_server"
                    ],
                    "env": {
                        "LLM_API_KEY": os.environ.get("LLM_API_KEY"),
                        "LLM_MODEL_NAME": os.environ.get("LLM_MODEL_NAME"),
                        "LLM_BASE_URL": os.environ.get("LLM_BASE_URL"),
                    }
                }
            }
        }
    )



    async def _run(agent, input):
        task = Task(
            input=input,
            agent=agent,
            conf=TaskConfig()
        )

        rich_ui = RichAworldUI()

        async for output in Runners.streamed_run_task(task).stream_events():
            await AworldUI.parse_output(output, rich_ui)


    asyncio.run(_run(amap_agent, user_input))
