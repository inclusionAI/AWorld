from typing import Dict, Optional

from aworld.logs.util import logger
from aworld.models import qwen_tokenizer
from aworld.models.openai_tokenizer import openai_tokenizer


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


def num_tokens_from_string(string: str, model: str = "gpt-4o"):
    """Return the number of tokens used by a string."""
    encoding = openai_tokenizer

    return len(encoding.encode(string))

def num_tokens_from_messages(messages: list[dict], model="gpt-4o") -> Dict[str, str]:
    """Return the number of tokens used by a list of messages."""
    import tiktoken

    if model.lower() == "qwen":
        encoding = qwen_tokenizer
    elif model.lower() == "openai":
        encoding = openai_tokenizer
    else:
        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            logger.warning(
                f"{model} model not found. Using cl100k_base encoding.")
            encoding = tiktoken.get_encoding("cl100k_base")

    tokens_per_message = 3
    tokens_per_name = 1

    num_tokens = 0
    for message in messages:
        num_tokens += tokens_per_message
        if isinstance(message, str):
            num_tokens += len(encoding.encode(message))
        else:
            for key, value in message.items():
                num_tokens += len(encoding.encode(str(message)))
                if key == "name":
                    num_tokens += tokens_per_name
    num_tokens += 3
    return num_tokens

