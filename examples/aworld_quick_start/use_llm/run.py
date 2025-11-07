# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import os

from aworld.config import ModelConfig
from aworld.models.llm import get_llm_model, call_llm_model, acall_llm_model, acall_llm_model_stream

import examples.aworld_quick_start


async def main():
    # we use utility function to use LLM model
    llm = get_llm_model(ModelConfig(
        llm_provider=os.getenv("LLM_PROVIDER"),
        llm_model_name=os.getenv("LLM_MODEL_NAME"),
        llm_base_url=os.getenv("LLM_BASE_URL"),
        llm_api_key=os.getenv("LLM_API_KEY"),
    ))

    query = "What is an agent?"
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": query},
    ]
    # sync
    print(call_llm_model(llm, messages))
    # async
    print(await acall_llm_model(llm, messages))
    # async stream
    async for chunk in acall_llm_model_stream(llm, messages):
        print(chunk)


if __name__ == "__main__":
    asyncio.run(main())
