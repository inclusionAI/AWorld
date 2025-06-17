import logging
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
import uvicorn
import os
import subprocess
from fastapi.staticfiles import StaticFiles


logger = logging.getLogger(__name__)

app = FastAPI()


@app.get("/")
async def root():
    return RedirectResponse("/index.html")


from .routers import chats,workspaces

app.include_router(chats.router, prefix=chats.prefix)
app.include_router(workspaces.router, prefix=workspaces.prefix)

def build_webui() -> str:
    webui_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui")

    logger.warning(f"Build WebUI at {webui_path} ...")

    p = subprocess.Popen(
        ["sh", "-c", "npm install && npm run build"],
        cwd=webui_path,
    )
    p.wait()
    if p.returncode != 0:
        raise Exception(f"Failed to build WebUI, error code: {p.returncode}")
    else:
        logger.info("WebUI build successfully")

    static_path = os.path.join(webui_path, "dist")
    return static_path


static_path = build_webui()
logger.info(f"Mounting static files from {static_path}")
app.mount("/", StaticFiles(directory=static_path, html=True), name="static")


def run_server(port, args=None, **kwargs):
    logger.info(f"Running Web server on port {port}")
    uvicorn.run(
        "aworld.cmd.web.web_server:app",
        host="0.0.0.0",
        port=port,
        reload=True,
    )
