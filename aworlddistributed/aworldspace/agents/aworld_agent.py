import logging
import os
from typing import Any, List, Optional
from datetime import datetime

from aworld.config.conf import AgentConfig
from pydantic import BaseModel, Field

from aworldspace.base_agent import AworldBaseAgent

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

class Pipeline(AworldBaseAgent):
    class Valves(BaseModel):
        llm_provider: Optional[str] = Field(default=None, description="llm_model_name")
        llm_model_name: Optional[str] = Field(default=None, description="llm_model_name")
        llm_base_url: Optional[str] = Field(default=None,description="llm_base_urly")
        llm_api_key: Optional[str] = Field(default=None,description="llm api key" )
        system_prompt: str = Field(default=GAIA_SYSTEM_PROMPT,description="system_prompt")
        history_messages: int = Field(default=100, description="rounds of history messages")

    def __init__(self):
        self.valves = self.Valves()
        self.agent_config = AgentConfig(
            name=self.agent_name(),
            llm_provider=self.valves.llm_provider if self.valves.llm_provider else os.environ.get("LLM_PROVIDER"),
            llm_model_name=self.valves.llm_model_name if self.valves.llm_model_name else os.environ.get("LLM_MODEL_NAME"),
            llm_api_key=self.valves.llm_api_key if self.valves.llm_api_key else os.environ.get("LLM_API_KEY"),
            llm_base_url=self.valves.llm_base_url if self.valves.llm_base_url else os.environ.get("LLM_BASE_URL"),
            system_prompt=self.valves.system_prompt if self.valves.system_prompt else GAIA_SYSTEM_PROMPT
        )

        logging.info("aworld init success")

    async def get_custom_input(self, user_message: str, model_id: str, messages: List[dict], body: dict) -> Any:
        return user_message +"\n cur time is "+ str(datetime.now().strftime("%Y-%m-%d %H:%M:%S")) + "\n please use chinese"

    async def get_agent_config(self):
        return self.agent_config

    def agent_name(self) -> str:
        return "AworldAgent"

    async def get_mcp_servers(self) -> list[str]:
        return [
            "e2b-server",
            "terminal-controller",
            "excel",
            "calculator",
            "ms-playwright",
            "audio_server",
            "image_server",
            "video_server",
            "search_server",
            "download_server",
            # "document_server",
            "youtube_server",
            "reasoning_server",
        ]

    async def load_mcp_config(self) -> dict:
        return {
            "mcpServers": {
                "e2b-server": {
                    "command": "npx",
                    "args": [
                        "-y",
                        "@e2b/mcp-server"
                    ],
                    "env": {
                        "E2B_API_KEY": os.environ["E2B_API_KEY"]
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
                        "EXCEL_MCP_PAGING_CELLS_LIMIT": "4000"
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
                    ]
                },
                "image_server": {
                    "command": "python",
                    "args": [
                        "-m",
                        "mcp_servers.image_server"
                    ]
                },
                "youtube_server": {
                    "command": "python",
                    "args": [
                        "-m",
                        "mcp_servers.youtube_server"
                    ]
                },
                "video_server": {
                    "command": "python",
                    "args": [
                        "-m",
                        "mcp_servers.video_server"
                    ]
                },
                "search_server": {
                    "command": "python",
                    "args": [
                        "-m",
                        "mcp_servers.search_server"
                    ]
                },
                "download_server": {
                    "command": "python",
                    "args": [
                        "-m",
                        "mcp_servers.download_server"
                    ]
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
                    ]
                },
                "reasoning_server": {
                    "command": "python",
                    "args": [
                        "-m",
                        "mcp_servers.reasoning_server"
                    ]
                }
            }
        }