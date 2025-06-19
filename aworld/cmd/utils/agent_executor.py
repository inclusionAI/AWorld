from .. import (
    ChatCompletionChoice,
    ChatCompletionMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
)
from . import agent_loader
import logging
import aworld.trace as trace

logger = logging.getLogger(__name__)

# bugfix for tracer exception
trace.configure()


async def stream_run(request: ChatCompletionRequest):
    logger.info(f"Stream run agent: request={request.model_dump_json()}")
    agent = agent_loader.get_agent(request.model)
    async for chunk in agent.instance.run(request=request):
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
