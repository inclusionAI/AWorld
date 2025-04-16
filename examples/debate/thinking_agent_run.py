import asyncio
import os

from dotenv import load_dotenv

from aworld.agents.debate.stream_output_agent import StreamOutputAgent
from aworld.config import AgentConfig


async def run():
    load_dotenv()
    base_config = {
        "llm_provider": "openai",
        "llm_model_name": "DeepSeek-R1",
        "llm_base_url": os.environ['LLM_BASE_URL'],
        "llm_api_key": os.environ['LLM_API_KEY'],
    }

    agentConfig = AgentConfig.model_validate(base_config | {
        "name": "StreamAgent"
    })

    search_sys_prompt = "You are a helpful agent."

    agent = StreamOutputAgent(conf=agentConfig,
                              system_prompt=search_sys_prompt)

    messages = [
        {"role": "system", "content": search_sys_prompt},
        {"role": "user", "content": "1+1=？"}
    ]
    output = await  agent.async_call_llm(messages)
    # async for reason in output.reason_generator:
    #     print(reason, flush=True, end="")

    async for response in output.response_generator:
        print(response, flush=True, end="")

    print()
    print(output.reasoning)

if __name__ == '__main__':
    asyncio.run(run())
