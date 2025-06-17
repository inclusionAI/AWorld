import os
from typing import List
from fastapi import FastAPI
from fastapi.responses import FileResponse
import uvicorn
from .model import (
    AgentModel,
    ChatCompletion,
    ChatCompletionChoice,
    ChatCompletionMessage,
    ChatRequest,
    ChatResponse,
)

app = FastAPI()

app

@app.get("/")
async def root():
    base_path = os.path.dirname(os.path.abspath(__file__))
    return FileResponse(os.path.join(base_path, "static", "index.html"))


@app.post("/api/agent/list")
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


@app.post("/api/agent/chat/completion")
async def chat_completion(request: ChatRequest) -> ChatResponse:
    return ChatResponse(
        completion=ChatCompletion(
            choices=[
                ChatCompletionChoice(
                    message=ChatCompletionMessage(
                        role="assistant", content="Hello, world!"
                    )
                )
            ]
        )
    )


def run_api_server(port, args=None, **kwargs):
    print(f"Running API server on port {port}")
    uvicorn.run(
        "aworld.cmd.api.api_server:app",
        host="0.0.0.0",
        port=port,
        reload=True,
    )
