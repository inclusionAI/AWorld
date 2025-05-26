"""
Browser MCP Server

This module provides MCP server functionality for browser automation and interaction.
It handles tasks such as web scraping, form submission, and automated browsing.

Main functions:
- browse_url: Opens a URL and performs specified actions
- submit_form: Fills and submits forms on web pages
"""

import json
import os
import sys
import traceback

from browser_use import Agent
from browser_use.agent.views import AgentHistoryList
from browser_use.browser.browser import Browser, BrowserConfig
from browser_use.browser.context import BrowserContext, BrowserContextConfig
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from aworld.logs.util import Color
from examples.gaia.utils import color_log, setup_logger

load_dotenv()
logger = setup_logger("GaiaBrowser", output_folder_path=os.getenv("LOG_FILE_PATH"), file_name="browser.log")

mcp = FastMCP("browser-server")
extended_browser_system_prompt = """
10. Download:

-  Save the most relevant files (text/image/pdf/...) to local path for further processing
-  **ALWAYS** download the .pdf files for further processing. DO NOT click the link to open the pdf file in new tabs.

11. Robot Detection:

- If the page is a robot detection page, abort immediately. Then navigate to the most authoritative source for similar information instead

# Efficiecy Guidelines

1. Use specific search queries with key terms from the task

2. Avoid getting distracted by tangential information

3. If blocked by paywalls, try archive.org or similar alternatives

4. Document each significant finding clearly and concisely

5. Precisely extract the necessary information with minimal browsing steps.
"""


@mcp.tool(description="Perform browser actions using the browser-use package.")
async def browser_use(
    task: str = Field(description="The task to perform using the browser."),
) -> str:
    """
    Perform browser actions using the browser-use package.
    Args:
        task (str): The task to perform using the browser.
    Returns:
        str: The result of the browser actions.
    """
    browser = Browser(
        config=BrowserConfig(
            headless=False,
            new_context_config=BrowserContextConfig(
                cookies_file=os.getenv("COOKIES_FILE_PATH"),
                disable_security=True,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                minimum_wait_page_load_time=5,
                maximum_wait_page_load_time=10,
                save_recording_path=os.getenv("FILESYSTEM_SERVER_WORKDIR"),
                save_downloads_path=os.getenv("FILESYSTEM_SERVER_WORKDIR"),
                trace_path=os.getenv("LOG_FILE_PATH") + "/browser_trace.log",
            ),
        )
    )
    browser_context = BrowserContext(
        config=BrowserContextConfig(trace_path=os.getenv("LOG_FILE_PATH") + "/browser_trace.log"),
        browser=browser,
    )
    agent = Agent(
        task=task,
        llm=ChatOpenAI(
            model=os.getenv("LLM_MODEL_NAME"),
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL"),
            temperature=1.0,
        ),
        browser_context=browser_context,
        extend_system_message=extended_browser_system_prompt,
    )
    try:
        color_log(logger, f"🎯 Task: {task}", Color.purple)
        browser_execution: AgentHistoryList = await agent.run(max_steps=50)
        if browser_execution is not None and browser_execution.is_done() and browser_execution.is_successful():
            exec_trace = browser_execution.extracted_content()
            color_log(logger, f"🎢 Detail: {json.dumps(exec_trace, ensure_ascii=False, indent=4)}", Color.purple)
            color_log(logger, f"📈 Result: {browser_execution.final_result()}", Color.purple)
            return browser_execution.final_result()
        return f"Browser execution failed for task: {task}"
    except Exception as e:
        logger.error(f"Browser execution failed: {traceback.format_exc()}")
        return f"Browser execution failed for task: {task} due to {str(e)}"
    finally:
        await browser.close()


def main():
    load_dotenv()
    mcp.run(transport="stdio")


# Make the module callable
def __call__():
    """
    Make the module callable for uvx.
    This function is called when the module is executed directly.
    """
    main()


sys.modules[__name__].__call__ = __call__

# Run the server when the script is executed directly
if __name__ == "__main__":
    main()
