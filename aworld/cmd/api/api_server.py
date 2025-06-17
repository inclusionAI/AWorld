import logging
import os
from typing import List
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
import uvicorn
from .model import (
    AgentModel,
    ChatCompletion,
    ChatCompletionChoice,
    ChatCompletionMessage,
    ChatRequest,
    ChatResponse,
)
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

app = FastAPI()


# Mount static files
static_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui", "dist")
if not os.path.exists(static_path):
    logger.warning(
        f"WebUI dist files not found at {static_path}, run `npm run build` in the webui directory to generate the static files."
    )
    import subprocess

    p = subprocess.Popen(
        ["npm", "run", "build"],
        cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui"),
    )
    p.wait()
    if p.returncode != 0:
        logger.error(f"Failed to build WebUI dist files, error code: {p.returncode}")
        exit(1)
    else:
        logger.info("WebUI dist files built successfully")

logger.info(f"Mounting static files from {static_path}")
app.mount("/", StaticFiles(directory=static_path, html=True), name="static")


@app.get("/")
async def root():
    return RedirectResponse("/index.html")


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
