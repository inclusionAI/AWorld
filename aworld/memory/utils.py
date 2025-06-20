import tiktoken
from aworld.logs.util import logger

MODEL_TO_ENCODING = {
    "gpt-3.5-turbo": "cl100k_base",
    "gpt-4": "cl100k_base",
    "text-davinci-003": "p50k_base",
    "text-embedding-ada-002": "cl100k_base",
    "text-curie-001": "r50k_base",
    "text-babbage-001": "r50k_base",
    "text-ada-001": "r50k_base",
}

def get_encoding_for_model(model_name: str) -> tiktoken.Encoding:
    """
    Automatically select the corresponding encoder based on the model name.
    """
    encoding_name = MODEL_TO_ENCODING.get(model_name)
    if encoding_name is None:
        logger.warning(f"model '{model_name}' not found in mapping table.")
        return "cl100k_base"
    return encoding_name

def count_tokens(model_name: str, content: str):
    encoding = tiktoken.get_encoding(get_encoding_for_model(model_name))

    tokens = encoding.encode(content)

    token_count = len(tokens) # TODO: use tiktoken.encoding_for_model(model_name).encode(content)

    return token_count
