from aworld.config.conf import AgentConfig, TaskConfig
from aworld.core.agent.base import Agent
from aworld.core.task import Task
from datasets import load_dataset
from gaia.question_preprocess import add_file_path
import asyncio
import re
import logging
import traceback
import os


def setup_logging():
    # 创建日志记录器
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)  # 设置全局日志级别为 DEBUG

    # 创建文件处理器，将日志写入文件
    file_handler = logging.FileHandler(f"yy_run_provider_error_gpt41.log", mode="a", encoding="utf-8")
    file_handler.setLevel(logging.INFO)  # 文件日志级别为 DEBUG

    # 设置日志格式
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    file_handler.setFormatter(formatter)

    # 添加处理器到日志记录器
    logger.addHandler(file_handler)


if __name__ == '__main__':

    llm_api_key = os.getenv("LLM_API_KEY", "")
    llm_base_url = os.getenv("LLM_BASE_URL", "")

    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name="anthropic/claude-3.7-sonnet",
        llm_api_key=llm_api_key, 
        llm_base_url=llm_base_url,
    )

    # 任务路径
    directory = "/Users/yuchengyue/AWorld_mcp/gaia/gaia_dataset/2023/validation"

    search_sys_prompt = f"""You are an all-capable AI assistant, aimed at solving any task presented by the user. You have various tools at your disposal that you can call upon to efficiently complete complex requests. Whether it's programming, information retrieval, file processing, or web browsing, you can handle it all.
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
</tips>

Now, here is the task. Stay focused and complete it carefully using the appropriate tools!
"""

    full_dataset = load_dataset(
        "./gaia/gaia_dataset/GAIA.py",
        name="2023_all",
        split="validation",  # validation 165 + test 301
    )

    # 初始化日志配置
    setup_logging()

    for i in range(len(full_dataset)):
        try:
            logging.info(f"Start to process: {i}")
            logging.info(f"Detail: {full_dataset[i]}")
            logging.info(f"Question: {full_dataset[i]['Question']}")
            logging.info(f"Level: {full_dataset[i]['Level']}")
            logging.info(f"Tools: {full_dataset[i]['Annotator Metadata']['Tools']}")
            question = add_file_path(full_dataset[i])["Question"]

            code = Agent(
                conf=agent_config,
                name="code_agent",
                system_prompt=search_sys_prompt,
                mcp_servers=[
                    "e2b-server", 
                    "filesystem", 
                    "terminal-controller",
                    "excel",
                    "calculator",
                    "google-search",
                    "ms-playwright",
                    "audio_server",
                    "image_server",
                ] # MCP server name for agent to use amap-amap-sse
            )

            task = Task(input=question, agent=code, conf=TaskConfig())
            result = asyncio.run(task.run())

            match = re.search(r'<answer>(.*?)</answer>', result["answer"])
            if match:
                answer = match.group(1)
                logging.info(f"Agent answer: {answer}")
                logging.info(f"Correct answer: {full_dataset[i]['Final answer']}")
                
                if answer == full_dataset[i]["Final answer"]:
                    logging.info(f"Question {i} Correct!")
                else:
                    logging.info("Incorrect!")
            
        except Exception as e:
            logging.error(f"Error processing {i}: {traceback.format_exc()}")
            continue

