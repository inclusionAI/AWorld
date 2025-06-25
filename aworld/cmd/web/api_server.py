import logging
from fastapi import FastAPI
import uvicorn


logger = logging.getLogger(__name__)

app = FastAPI()

from .routers import chats,workspaces,sessions

app.include_router(chats.router, prefix=chats.prefix)
app.include_router(workspaces.router, prefix=workspaces.prefix)
app.include_router(sessions.router, prefix=sessions.prefix)

def run_server(port, args=None, **kwargs):
    logger.info(f"Running API server on port {port}")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
    )
