import logging
import os
from typing import List
from fastapi import FastAPI
from fastapi.responses import RedirectResponse, StreamingResponse
import uvicorn
from .model import (
    AgentModel,
    ChatCompletionChoice,
    ChatCompletionMessage,
    ChatCompletionResponse,
)
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

app = FastAPI()


@app.get("/")
async def root():
    return RedirectResponse("/index.html")


@app.get("/api/agent/list")
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


def handle_webui():
    static_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "webui", "dist"
    )
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


handle_webui()


def run_api_server(port, args=None, **kwargs):
    print(f"Running API server on port {port}")
    uvicorn.run(
        "aworld.cmd.api.api_server:app",
        host="0.0.0.0",
        port=port,
        reload=True,
    )
