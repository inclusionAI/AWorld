from .. import (
    BaseAWorldAgent,
    ChatCompletionChoice,
    ChatCompletionMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
)
from . import agent_loader
import logging
import aworld.trace as trace
import os
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# bugfix for tracer exception
trace.configure()


async def stream_run(request: ChatCompletionRequest):
    logger.info(f"Stream run agent: request={request.model_dump_json()}")
    agent = agent_loader.get_agent(request.model)
    instance: BaseAWorldAgent = agent.instance
    env_file = os.path.join(agent.path, ".env")
    if os.path.exists(env_file):
        logger.info(f"Loading environment variables from {env_file}")
        load_dotenv(env_file, override=True, verbose=True)

    async for chunk in instance.run(request=request):
        with open("output.txt", "a") as f:
            f.write(chunk)
            f.write("\n")
        response = ChatCompletionResponse(
            choices=[
                ChatCompletionChoice(
                    index=0,
                    delta=ChatCompletionMessage(
                        role="assistant",
                        content=chunk,
                    ),
                )
            ]
        )
        yield response
