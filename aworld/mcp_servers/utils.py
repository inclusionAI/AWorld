import inspect
import json
import os
import random
import re
from typing import Any, Callable, Dict, List

import yaml
from mcp.server import FastMCP

from aworld.config import ModelConfig
from aworld.logs.util import logger


def run_mcp_server(
    server_name: str, funcs: List[Callable], port: int = random.randint(1000, 9999)
) -> FastMCP:
    """
    Run the MCP server with the given name and port

    Args:
        server_name: Name of the MCP server
        funcs: List of functions to register with the server
        port: Port number for the MCP server
    Returns:
        FastMCP: The running MCP server instance
    """
    mcp = FastMCP(server_name)
    for func in funcs:
        mcp.add_tool(func)
    mcp.settings.port = port
    mcp.run(transport="sse")
    return mcp


def get_current_filename_without_extension() -> str:
    """
    Get the current file name without extension and path from caller's file

    Returns:
        str: Current file name without extension
    """
    caller_frame = inspect.stack()[1]
    caller_filename = caller_frame.filename
    base_filename = os.path.basename(caller_filename)
    filename_without_extension = os.path.splitext(base_filename)[0]
    return filename_without_extension


def read_config_from_yaml(filepath: str) -> Dict[str, Any]:
    """
    Read YAML configuration file and return as dictionary

    Args:
        filepath: Relative or absolute path to YAML file

    Returns:
        Dict[str, Any]: Dictionary containing configuration data

    Raises:
        FileNotFoundError: When file does not exist
        yaml.YAMLError: When YAML parsing fails
    """
    if not os.path.isabs(filepath):
        project_root = os.path.dirname((os.path.dirname(__file__)))
        config_dir = os.path.join(project_root, "config")
        filepath = os.path.join(config_dir, filepath)
        logger.debug(f"Resolved config path: {filepath}")

    if not os.path.exists(filepath):
        logger.error(f"Configuration file not found: {filepath}")
        raise FileNotFoundError(f"Configuration file not found: {filepath}")

    try:
        with open(filepath, "r", encoding="utf-8") as file:
            logger.info(f"Reading configuration from: {filepath}")
            config_data = yaml.safe_load(file)
        return config_data
    except yaml.YAMLError as e:
        logger.error(f"YAML parsing error: {str(e)}")
        raise yaml.YAMLError(f"YAML parsing error: {str(e)}")


def read_llm_config_from_yaml(filepath: str) -> ModelConfig:
    """
    Read LLM configuration from YAML file and return as ModelConfig

    Args:
        filepath: Relative or absolute path to YAML file

    Returns:
        ModelConfig: Configuration for LLM model

    Raises:
        KeyError: When llm_config section is missing
    """
    config = read_config_from_yaml(filepath)
    if "llm_config" not in config:
        logger.error(f"No llm_config section found in {filepath}")
        raise KeyError(f"No llm_config section found in {filepath}")
    return ModelConfig(**config["llm_config"])


def handle_llm_response(response_content: str, result_key: str) -> str:
    """Process LLM response uniformly

    Args:
        response_content: Raw response content from LLM
        result_key: Key name to extract from JSON

    Returns:
        str: Extracted result content

    Raises:
        ValueError: When response is empty or result key doesn't exist
    """
    if not response_content:
        raise ValueError("No response from llm.")

    json_pattern = r"```json\s*(.*?)\s*```"
    match = re.search(json_pattern, response_content, re.DOTALL)
    if match:
        response_content = match.group(1)

    json_content = json.loads(response_content)
    result = json_content.get(result_key)
    if not result:
        raise ValueError(f"No {result_key} in response.")
    return json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    import asyncio

    from aworld.mcp.utils import mcp_tool_desc_transform

    mcp_tools = asyncio.run(mcp_tool_desc_transform(["image"]))
    logger.success(f"{json.dumps(mcp_tools, indent=4, ensure_ascii=False)}")
