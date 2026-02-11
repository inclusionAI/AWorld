# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import copy
import inspect
import os.path
from typing import Dict, Any, List, Union

from aworld.core.context.base import Context
from aworld.logs.util import logger
from aworld.models.qwen_tokenizer import qwen_tokenizer
from aworld.models.openai_tokenizer import openai_tokenizer
from aworld.utils import import_package

# Global cache for tiktoken encodings to prevent memory leaks
_TIKTOKEN_ENCODING_CACHE = {}


def _get_cached_tiktoken_encoding(model: str):
    """
    Get cached tiktoken encoding to prevent memory leaks.

    Args:
        model: Model name (e.g., 'gpt-4o', 'claude-3-opus')

    Returns:
        Cached tiktoken encoding object
    """
    if model not in _TIKTOKEN_ENCODING_CACHE:
        import tiktoken
        try:
            _TIKTOKEN_ENCODING_CACHE[model] = tiktoken.encoding_for_model(model)
            logger.debug(f"Created and cached tiktoken encoding for model: {model}")
        except KeyError:
            logger.debug(f"{model} model not found. Using cl100k_base encoding.")
            # Cache cl100k_base if not already cached
            if "cl100k_base" not in _TIKTOKEN_ENCODING_CACHE:
                _TIKTOKEN_ENCODING_CACHE["cl100k_base"] = tiktoken.get_encoding("cl100k_base")
            # Reuse cl100k_base for this model
            _TIKTOKEN_ENCODING_CACHE[model] = _TIKTOKEN_ENCODING_CACHE["cl100k_base"]
    return _TIKTOKEN_ENCODING_CACHE[model]


class ModelUtils:
    """Utility class for model-related operations"""

    # Model context window sizes mapping
    # Key: model prefix, Value: context window size
    MODEL_CONTEXT_WINDOWS: Dict[str, int] = {
        # OpenAI models
        "gpt-4o": 128 * 1024,
        "gpt-4o-mini": 128 * 1024,
        "gpt-4-turbo": 128 * 1024,
        "gpt-4": 8 * 1024,
        "gpt-3.5-turbo": 16 * 1024,

        # Anthropic models
        "claude-sonnet-4": 200 * 1024,
        "claude-3.7-sonnet": 200 * 1024,
        "claude-opus-4.1": 200 * 1024,
        "claude-3.5-haiku": 200 * 1024,
        "claude-3.5-sonnet": 200 * 1024,
        "claude-opus-4": 200 * 1024,
        "claude-3-haiku": 200 * 1024,
        "claude-3-opus": 200 * 1024,
        "claude-3-sonnet": 200 * 1024,
        "claude-2": 100 * 1024,
        "claude-instant": 100 * 1024,

        # Google models
        "gemini-pro": 32 * 1024,
        "gemini-2.5-flash": 1024 * 1024,
        "gemini-2.5-pro": 1024 * 1024,
        "gemini-2.5-flash-lite": 1024 * 1024,
        "gemini-2.5-flash-lite-preview": 1024 * 1024,

        # Meta models
        "llama-2": 4 * 1024,
        "llama-3": 8 * 1024,
        "codellama": 16 * 1024,

        # Mistral models
        "mistral": 8 * 1024,
        "mixtral": 32 * 1024,

        # BAILING models (Ant Group)
        "ling-max-1.5-0527": 128 * 1024,

        # QWEN models (Alibaba Cloud) - Additional models
        "qwen2.5-1.5b-instruct": 32 * 1024,
        "qwen2.5-vl-3b-instruct": 32 * 1024,
        "qwen3-235b-a22b-instruct-2507": 256 * 1024,

        # KIMI models
        "kimi-k2-instruct": 128 * 1024,
        "kimi-k2-instruct-0905": 256 * 1024,

        # DEEPSEEK models - Additional models
        "deepseek-r1-0528": 64 * 1024,
        "deepseek-v3.1": 128 * 1024,

        # BYTEDANCE models
        "seed-oss-36b-instruct": 128 * 1024,

        # ZHIPUAI models - Additional models
        "glm-4.5": 128 * 1024,
        "glm-4.6": 128 * 1024,
        "glm-4.5v": 64 * 1024,

        # OpenAI Open Source models
        "gpt-oss-120b": 128 * 1024,

        # Default fallback
        "default": 64 * 1024
    }

    @staticmethod
    def get_context_window(model_name: str) -> int:
        """
        Get the context window size for a given model name.
        Priority: 1. Exact match, 2. Prefix match, 3. Default fallback

        Args:
            model_name (str): The name of the model (e.g., 'gpt-4o', 'claude-3-opus')

        Returns:
            int: The context window size in tokens. Returns default size if no match found.
        """
        if not model_name:
            return ModelUtils.MODEL_CONTEXT_WINDOWS["default"]

        model_name = model_name.lower()

        # Step 1: Try exact match first (highest priority)
        if model_name in ModelUtils.MODEL_CONTEXT_WINDOWS:
            return ModelUtils.MODEL_CONTEXT_WINDOWS[model_name]

        # Step 2: Try prefix matching (lower priority)
        for prefix, context_size in ModelUtils.MODEL_CONTEXT_WINDOWS.items():
            if prefix != "default" and model_name.__contains__(prefix):
                return context_size

        # Step 3: Return default if no match found
        return ModelUtils.MODEL_CONTEXT_WINDOWS["default"]

    @staticmethod
    def add_model_context_window(model_prefix: str, context_size: int) -> None:
        """
        Add or update a model context window size to the configuration.

        Args:
            model_prefix (str): The model prefix to add/update
            context_size (int): The context window size in tokens
        """
        ModelUtils.MODEL_CONTEXT_WINDOWS[model_prefix] = context_size

    @staticmethod
    def get_all_model_contexts() -> Dict[str, int]:
        """
        Get all configured model context window sizes.

        Returns:
            Dict[str, int]: Dictionary mapping model prefixes to context window sizes
        """
        return ModelUtils.MODEL_CONTEXT_WINDOWS.copy()

    @staticmethod
    def calculate_token_breakdown(messages: list[dict], model: str = "gpt-4o") -> Dict[str, int]:
        """
        Calculate token breakdown by message role categories.

        Args:
            messages (list[dict]): List of message dictionaries with 'role' and 'content' keys
            model (str): Model name for tokenization

        Returns:
            Dict[str, int]: Dictionary containing token counts for each category:
                           - 'total': Total tokens
                           - 'system': System message tokens
                           - 'user': User message tokens
                           - 'assistant': Assistant message tokens
                           - 'tool': Tool message tokens
                           - 'other': Other/unknown role tokens
        """
        try:
            # Initialize token counters
            system_tokens = 0
            user_tokens = 0
            assistant_tokens = 0
            tool_tokens = 0
            other_tokens = 0

            for message in messages:
                try:
                    role = message.get('role', 'unknown')
                    content = message.get('content', '')

                    # Handle empty content case
                    if not content:
                        if message.get("tool_calls"):
                            assistant_tokens += num_tokens_from_string(str(message.get("tool_calls")))
                        continue

                    if isinstance(content, list):
                        # Multi-modal content
                        for item in content:
                            try:
                                if isinstance(item, dict) and item.get('type') == 'text':
                                    item_tokens = num_tokens_from_string(str(item.get('text', '')), model)
                                    if role == 'system':
                                        system_tokens += item_tokens
                                    elif role == 'user':
                                        user_tokens += item_tokens
                                    elif role == 'assistant':
                                        assistant_tokens += item_tokens
                                    elif role == 'tool':
                                        tool_tokens += item_tokens
                                    else:
                                        other_tokens += item_tokens
                            except Exception:
                                # Skip problematic items, continue processing
                                continue
                    else:
                        # Regular text content
                        try:
                            content_tokens = num_tokens_from_string(str(content), model)
                            if role == 'system':
                                system_tokens += content_tokens
                            elif role == 'user':
                                user_tokens += content_tokens
                            elif role == 'assistant':
                                assistant_tokens += content_tokens
                                if message.get("tool_calls"):
                                    assistant_tokens += num_tokens_from_string(str(message.get("tool_calls")))
                            elif role == 'tool':
                                tool_tokens += content_tokens
                            else:
                                other_tokens += content_tokens
                        except Exception as err:
                            # Skip problematic content, continue processing
                            logger.warning(f"calculate_token_breakdown Exception is {err}")
                            continue
                except Exception as err:
                    # Skip problematic messages, continue processing
                    logger.warning(f"calculate_token_breakdown Exception is {err}")
                    continue

            # Calculate total
            total_tokens = system_tokens + user_tokens + assistant_tokens + tool_tokens + other_tokens

            return {
                'total': total_tokens,
                'system': system_tokens,
                'user': user_tokens,
                'assistant': assistant_tokens,
                'tool': tool_tokens,
                'other': other_tokens
            }

        except Exception as e:
            # If any error occurs, return safe defaults
            logger.warning(f"Error calculating token breakdown: {str(e)}")
            return {
                'total': 0,
                'system': 0,
                'user': 0,
                'assistant': 0,
                'tool': 0,
                'other': 0
            }

def usage_process(usage: Dict[str, Union[int, Dict[str, int]]] = {}, context: Context = None):
    if not context:
        context = Context()

    stacks = inspect.stack()
    index = 0
    for idx, stack in enumerate(stacks):
        index = idx + 1
        file = os.path.basename(stack.filename)
        # supported use `llm.py` utility function only
        if 'call_llm_model' in stack.function and file == 'llm.py':
            break

    if index >= len(stacks):
        logger.warning("not category usage find to count")
    else:
        instance = stacks[index].frame.f_locals.get('self')
        name = getattr(instance, "_name", "unknown")
        usage[name] = copy.copy(usage)
    # total usage
    context.add_token(usage)


def num_tokens_from_string(string: str, model: str = "openai"):
    """Return the number of tokens used by a list of messages."""
    if model.lower() == "qwen":
        encoding = qwen_tokenizer
    elif model.lower() == "openai":
        encoding = openai_tokenizer
    else:
        # Use cached encoding to prevent memory leaks
        encoding = _get_cached_tiktoken_encoding(model)
    return len(encoding.encode(string))

def num_tokens_from_messages(messages, model="openai"):
    """Return the number of tokens used by a list of messages."""
    import_package("tiktoken")

    if model.lower() == "qwen":
        encoding = qwen_tokenizer
    elif model.lower() == "openai":
        encoding = openai_tokenizer
    else:
        # Use cached encoding to prevent memory leaks
        encoding = _get_cached_tiktoken_encoding(model)

    tokens_per_message = 3
    tokens_per_name = 1

    num_tokens = 0
    for message in messages:
        num_tokens += tokens_per_message
        if isinstance(message, str):
            num_tokens += len(encoding.encode(message))
        else:
            for key, value in message.items():
                num_tokens += len(encoding.encode(str(value)))
                if key == "name":
                    num_tokens += tokens_per_name
    num_tokens += 3
    return num_tokens


def truncate_tokens_from_messages(messages: List[Dict[str, Any]], max_tokens: int, keep_both_sides: bool = False, model: str = "gpt-4o"):
    import_package("tiktoken")

    if model.lower() == "qwen":
        return qwen_tokenizer.truncate(messages, max_tokens, keep_both_sides)
    elif model.lower() == "openai":
        return openai_tokenizer.truncate(messages, max_tokens, keep_both_sides)

    # Use cached encoding to prevent memory leaks
    encoding = _get_cached_tiktoken_encoding(model)
    return encoding.truncate(messages, max_tokens, keep_both_sides)


def agent_desc_transform(agent_dict: Dict[str, Any],
                         agents: List[str] = None,
                         provider: str = 'openai',
                         strategy: str = 'min') -> List[Dict[str, Any]]:
    """Default implement transform framework standard protocol to openai protocol of agent description.

    Args:
        agent_dict: Dict of descriptions of agents that are registered in the agent factory.
        agents: Description of special agents to use.
        provider: Different descriptions formats need to be processed based on the provider.
        strategy: The value is `min` or `max`, when no special agents are provided, `min` indicates no content returned,
                 `max` means get all agents' descriptions.
    """
    agent_as_tools = []
    if not agents and strategy == 'min':
        return agent_as_tools
    if provider and 'openai' in provider:
        for agent_name, agent_info in agent_dict.items():
            if agents and agent_name not in agents:
                logger.debug(
                    f"{agent_name} can not supported in {agents}, you can set `tools` params to support it.")
                continue
            
            for action in agent_info["abilities"]:
                # Build parameter properties
                properties = {}
                required = []
                for param_name, param_info in action["params"].items():
                    properties[param_name] = {
                        "description": param_info["desc"],
                        "type": param_info["type"] if param_info["type"] != "str" else "string"
                    }
                    if param_info.get("required", False):
                        required.append(param_name)

                openai_function_schema = {
                    "name": f'{agent_name}', # __{action["name"]}
                    "description": action["desc"],
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required
                    }
                }

                agent_as_tools.append({
                    "type": "function",
                    "function": openai_function_schema
                })
    logger.debug(f"agent_desc_transform is {agent_as_tools}")
    return agent_as_tools


def tool_desc_transform(tool_dict: Dict[str, Any],
                        tools: List[str] = None,
                        black_tool_actions: Dict[str, List[str]] = {},
                        provider: str = 'openai',
                        strategy: str = 'min') -> List[Dict[str, Any]]:
    """Default implement transform framework standard protocol to openai protocol of tool description.

    Args:
        tool_dict: Dict of descriptions of tools that are registered in the agent factory.
        tools: Description of special tools to use.
        provider: Different descriptions formats need to be processed based on the provider.
        strategy: The value is `min` or `max`, when no special tools are provided, `min` indicates no content returned,
                 `max` means get all tools' descriptions.
    """
    openai_tools = []
    if not tools and strategy == 'min':
        return openai_tools

    if black_tool_actions is None:
        black_tool_actions = {}

    if provider and 'openai' in provider:
        for tool_name, tool_info in tool_dict.items():
            if tools and tool_name not in tools and tool_name.replace("async_", "") not in tools:
                logger.debug(
                    f"{tool_name} can not supported in {tools}, you can set `tools` params to support it.")
                continue

            black_actions = black_tool_actions.get(tool_name, [])
            for action in tool_info["actions"]:
                if action['name'] in black_actions:
                    continue
                # Build parameter properties
                properties = {}
                required = []
                for param_name, param_info in action["params"].items():
                    properties[param_name] = {
                        "description": param_info["desc"],
                        "type": param_info["type"] if param_info["type"] != "str" else "string"
                    }
                    if param_info.get("required", False):
                        required.append(param_name)

                openai_function_schema = {
                    "name": f'{tool_name}__{action["name"]}',
                    "description": action["desc"],
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required
                    }
                }

                openai_tools.append({
                    "type": "function",
                    "function": openai_function_schema
                })
    return openai_tools
