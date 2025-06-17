import logging
from fastapi import FastAPI
import uvicorn


logger = logging.getLogger(__name__)

app = FastAPI()

from .routers import chats,workspaces

app.include_router(chats.router, prefix=chats.prefix)
app.include_router(workspaces.router, prefix=workspaces.prefix)

def run_server(port, args=None, **kwargs):
    logger.info(f"Running API server on port {port}")
    uvicorn.run(
        "aworld.cmd.web.api_server:app",
        host="0.0.0.0",
        port=port,
        reload=True,
    )
