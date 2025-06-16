
import logging
import os
import time
from contextlib import asynccontextmanager
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import aworld.trace as trace  # noqa
from aworld.utils.common import get_local_ip
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from aworldspace.routes import tasks, workspaces
from aworldspace.utils.loader import load_modules_from_directory, PIPELINE_MODULES, PIPELINES, \
    get_all_pipelines
from aworldspace.utils.job import generate_openai_chat_completion
from base import OpenAIChatCompletionForm
from config import AGENTS_DIR, LOG_LEVELS, ROOT_LOG

if not os.path.exists(AGENTS_DIR):
    os.makedirs(AGENTS_DIR)

# Add GLOBAL_LOG_LEVEL for Pipeplines
log_level = os.getenv("GLOBAL_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVELS[log_level])
def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    log_dir = ROOT_LOG
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    log_path = os.path.join(log_dir, "aworldserver.log")
    file_handler = TimedRotatingFileHandler(log_path, when='H', interval=1, backupCount=24)
    file_handler.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    file_handler.setFormatter(formatter)

    error_log_path = os.path.join(log_dir, "aworldserver_error.log")
    error_file_handler = TimedRotatingFileHandler(error_log_path, when='D', interval=1, backupCount=24)
    error_file_handler.setLevel(logging.WARNING)
    error_file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(error_file_handler)
setup_logging()


async def on_startup():
    await load_modules_from_directory(AGENTS_DIR)
    await tasks.task_manager.start_task_executor()

    for module in PIPELINE_MODULES.values():
        if hasattr(module, "on_startup"):
            await module.on_startup()


async def on_shutdown():
    for module in PIPELINE_MODULES.values():
        if hasattr(module, "on_shutdown"):
            await module.on_shutdown()


async def reload():
    await on_shutdown()
    # Clear existing pipelines
    PIPELINES.clear()
    PIPELINE_MODULES.clear()
    # Load pipelines afresh
    await on_startup()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await on_startup()
    yield
    await on_shutdown()


app = FastAPI(docs_url="/docs", redoc_url=None, lifespan=lifespan)


origins = ["*"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(tasks.router, prefix="/api/v1/tasks", tags=["tasks"])
app.include_router(workspaces.router, prefix="/api/v1/workspaces", tags=["workspace"])


@app.middleware("http")
async def check_url(request: Request, call_next):
    start_time = int(time.time())
    response = await call_next(request)
    process_time = int(time.time()) - start_time
    response.headers["X-Process-Time"] = str(process_time)

    return response

@app.get("/v1")
@app.get("/")
async def get_status():
    return {"status": True}

@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_completion(form_data: OpenAIChatCompletionForm):
    return await generate_openai_chat_completion(form_data)

@app.get("/health")
async def healthcheck():
    return {
        "status": True
    }