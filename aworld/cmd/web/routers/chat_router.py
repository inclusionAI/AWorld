import logging
from typing import List
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from aworld.agents.model import (
    AgentModel,
    ChatCompletionChoice,
    ChatCompletionMessage,
    ChatCompletionResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()

prefix = "/api/agent"

@router.get("list")
@router.get("models")
async def list_agents() -> List[AgentModel]:
    return [
        AgentModel(
            agent_id="agent1",
            agent_name="agent1",
            agent_description="agent1",
            agent_type="agent1",
            agent_status="agent1",
        )
    ]


@router.post("chat/completion")
async def chat_completion() -> StreamingResponse:
    import json
    import asyncio

    async def generate_stream():
        for i in range(10):
            response = ChatCompletionResponse(
                choices=[
                    ChatCompletionChoice(
                        index=i,
                        delta=ChatCompletionMessage(
                            role="assistant",
                            content=f"## Hello, world! {i}\n\n",
                        ),
                    )
                ]
            )

            yield f"data: {json.dumps(response.model_dump())}\n\n"
            await asyncio.sleep(1)

    # 返回SSE流式响应
    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
