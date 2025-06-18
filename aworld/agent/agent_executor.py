from .data_model import ChatCompletionChoice, ChatCompletionMessage, ChatCompletionRequest, ChatCompletionResponse
from . import agent_loader

async def stream_run(request: ChatCompletionRequest):
    agent_model = agent_loader.get_agent_model(request.model)
    agent_instance = agent_model.agent_instance
    async for chunk in agent_instance.run(request):
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
