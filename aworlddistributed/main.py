import importlib.util
import inspect
import json
import logging
import os
import subprocess
import sys
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from logging.handlers import TimedRotatingFileHandler
from typing import Generator, Iterator, AsyncGenerator, Optional

import aworld.trace as trace  # noqa
from aworld.core.task import Task
from aworld.trace.opentelemetry.memory_storage import InMemoryStorage
from aworld.utils.common import get_local_ip
from fastapi import FastAPI, Request, status, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from aworldspace.utils.utils import get_last_user_message
from base import OpenAIChatCompletionForm
from config import AGENTS_DIR, LOG_LEVELS

if not os.path.exists(AGENTS_DIR):
    os.makedirs(AGENTS_DIR)

trace.trace_configure(
    backends=["memory"],
    storage=InMemoryStorage()
)

PIPELINES = {}
PIPELINE_MODULES = {}
PIPELINE_NAMES = {}

# Add GLOBAL_LOG_LEVEL for Pipeplines
log_level = os.getenv("GLOBAL_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVELS[log_level])
def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    log_dir = os.path.join(os.getenv("LOG_DIR_PATH", "logs") , get_local_ip())
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

def get_all_pipelines():
    pipelines = {}
    for pipeline_id in PIPELINE_MODULES.keys():
        pipeline = PIPELINE_MODULES[pipeline_id]

        if hasattr(pipeline, "type"):
            if pipeline.type == "manifold":
                manifold_pipelines = []

                # Check if pipelines is a function or a list
                if callable(pipeline.pipelines):
                    manifold_pipelines = pipeline.pipelines()
                else:
                    manifold_pipelines = pipeline.pipelines

                for p in manifold_pipelines:
                    manifold_pipeline_id = f'{pipeline_id}.{p["id"]}'

                    manifold_pipeline_name = p["name"]
                    if hasattr(pipeline, "name"):
                        manifold_pipeline_name = (
                            f"{pipeline.name}{manifold_pipeline_name}"
                        )

                    pipelines[manifold_pipeline_id] = {
                        "module": pipeline_id,
                        "type": pipeline.type if hasattr(pipeline, "type") else "pipe",
                        "id": manifold_pipeline_id,
                        "name": manifold_pipeline_name,
                        "valves": (
                            pipeline.valves if hasattr(pipeline, "valves") else None
                        ),
                    }
            if pipeline.type == "filter":
                pipelines[pipeline_id] = {
                    "module": pipeline_id,
                    "type": (pipeline.type if hasattr(pipeline, "type") else "pipe"),
                    "id": pipeline_id,
                    "name": (
                        pipeline.name if hasattr(pipeline, "name") else pipeline_id
                    ),
                    "pipelines": (
                        pipeline.valves.pipelines
                        if hasattr(pipeline, "valves")
                        and hasattr(pipeline.valves, "pipelines")
                        else []
                    ),
                    "priority": (
                        pipeline.valves.priority
                        if hasattr(pipeline, "valves")
                        and hasattr(pipeline.valves, "priority")
                        else 0
                    ),
                    "valves": pipeline.valves if hasattr(pipeline, "valves") else None,
                }
        else:
            pipelines[pipeline_id] = {
                "module": pipeline_id,
                "type": (pipeline.type if hasattr(pipeline, "type") else "pipe"),
                "id": pipeline_id,
                "name": (pipeline.name if hasattr(pipeline, "name") else pipeline_id),
                "valves": pipeline.valves if hasattr(pipeline, "valves") else None,
            }

    return pipelines


def parse_frontmatter(content):
    frontmatter = {}
    for line in content.split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            frontmatter[key.strip().lower()] = value.strip()
    return frontmatter


def install_frontmatter_requirements(requirements):
    if requirements:
        req_list = [req.strip() for req in requirements.split(",")]
        for req in req_list:
            print(f"Installing requirement: {req}")
            subprocess.check_call([sys.executable, "-m", "pip", "install", req])
    else:
        print("No requirements found in frontmatter.")


async def load_module_from_path(module_name, module_path):

    try:
        # Read the module content
        with open(module_path, "r") as file:
            content = file.read()

        # Parse frontmatter
        frontmatter = {}
        if content.startswith('"""'):
            end = content.find('"""', 3)
            if end != -1:
                frontmatter_content = content[3:end]
                frontmatter = parse_frontmatter(frontmatter_content)

        # Install requirements if specified
        if "requirements" in frontmatter:
            install_frontmatter_requirements(frontmatter["requirements"])

        # Load the module
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        logging.info(f"Loaded module start: {module.__name__}")
        if hasattr(module, "Pipeline"):
            return module.Pipeline()
        else:
            logging.info(f"Loaded module failed: {module.__name__ } No Pipeline class found")
            raise Exception("No Pipeline class found")
    except Exception as e:
        logging.info(f"Error loading module: {module_name}, error is {e}")
        traceback.print_exc()
        # Move the file to the error folder
        failed_pipelines_folder = os.path.join(AGENTS_DIR, "failed")
        if not os.path.exists(failed_pipelines_folder):
            os.makedirs(failed_pipelines_folder)

        # failed_file_path = os.path.join(failed_pipelines_folder, f"{module_name}.py")
        # if module_path.__contains__(PIPELINES_DIR):
        #     os.rename(module_path, failed_file_path)
        print(e)
    return None


async def load_modules_from_directory(directory):
    logging.info(f"load_modules_from_directory: {directory}")
    global PIPELINE_MODULES
    global PIPELINE_NAMES

    for filename in os.listdir(directory):
        if filename.endswith(".py"):
            module_name = filename[:-3]  # Remove the .py extension
            module_path = os.path.join(directory, filename)

            # Create subfolder matching the filename without the .py extension
            subfolder_path = os.path.join(directory, module_name)
            if not os.path.exists(subfolder_path):
                os.makedirs(subfolder_path)
                logging.info(f"Created subfolder: {subfolder_path}")

            # Create a valves.json file if it doesn't exist
            valves_json_path = os.path.join(subfolder_path, "valves.json")
            if not os.path.exists(valves_json_path):
                with open(valves_json_path, "w") as f:
                    json.dump({}, f)
                logging.info(f"Created valves.json in: {subfolder_path}")

            pipeline = await load_module_from_path(module_name, module_path)
            if pipeline:
                # Overwrite pipeline.valves with values from valves.json
                if os.path.exists(valves_json_path):
                    with open(valves_json_path, "r") as f:
                        valves_json = json.load(f)
                        if hasattr(pipeline, "valves"):
                            ValvesModel = pipeline.valves.__class__
                            # Create a ValvesModel instance using default values and overwrite with valves_json
                            combined_valves = {
                                **pipeline.valves.model_dump(),
                                **valves_json,
                            }
                            valves = ValvesModel(**combined_valves)
                            pipeline.valves = valves

                            logging.info(f"Updated valves for module: {module_name}")

                pipeline_id = pipeline.id if hasattr(pipeline, "id") else module_name
                PIPELINE_MODULES[pipeline_id] = pipeline
                PIPELINE_NAMES[pipeline_id] = module_name
                logging.info(f"Loaded module success: {module_name}")
            else:
                logging.warning(f"No Pipeline class found in {module_name}")

    global PIPELINES
    PIPELINES = get_all_pipelines()

async def on_startup():
    await load_modules_from_directory(AGENTS_DIR)
    

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
    PIPELINE_NAMES.clear()
    # Load pipelines afresh
    await on_startup()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await on_startup()
    yield
    await on_shutdown()


app = FastAPI(docs_url="/docs", redoc_url=None, lifespan=lifespan)

app.state.PIPELINES = PIPELINES


origins = ["*"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def check_url(request: Request, call_next):
    start_time = int(time.time())
    app.state.PIPELINES = get_all_pipelines()
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
async def generate_openai_chat_completion(form_data: OpenAIChatCompletionForm):
    messages = [message.model_dump() for message in form_data.messages]
    user_message = get_last_user_message(messages)

    if (
        form_data.model not in app.state.PIPELINES
        or app.state.PIPELINES[form_data.model]["type"] == "filter"
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline {form_data.model} not found",
        )

    def job():
        print(form_data.model)

        pipeline = app.state.PIPELINES[form_data.model]
        pipeline_id = form_data.model

        print(pipeline_id)

        if pipeline["type"] == "manifold":
            manifold_id, pipeline_id = pipeline_id.split(".", 1)
            pipe = PIPELINE_MODULES[manifold_id].pipe
        else:
            pipe = PIPELINE_MODULES[pipeline_id].pipe

        def process_line(model, line):
            if isinstance(line, Task):
                task_output_meta = line.outputs._metadata
                line = openai_chat_chunk_message_template(model, "", task_output_meta=task_output_meta)
                return f"data: {json.dumps(line)}\n\n"
            if isinstance(line, BaseModel):
                line = line.model_dump_json()
                line = f"data: {line}"
            if isinstance(line, dict):
                line = f"data: {json.dumps(line)}"

            try:
                line = line.decode("utf-8")
            except Exception:
                pass

            if line.startswith("data:"):
                return f"{line}\n\n"
            else:
                line = openai_chat_chunk_message_template(model, line)
                return f"data: {json.dumps(line)}\n\n"

        if form_data.stream:
            async def stream_content():
                async def execute_pipe(_pipe):
                    if inspect.iscoroutinefunction(_pipe):
                        return await _pipe(user_message=user_message,
                                          model_id=pipeline_id,
                                          messages=messages,
                                          body=form_data.model_dump())
                    else:
                        return _pipe(user_message=user_message,
                                    model_id=pipeline_id,
                                    messages=messages,
                                    body=form_data.model_dump())

                try:
                    res = await execute_pipe(pipe)

                    # Directly return if the response is a StreamingResponse
                    if isinstance(res, StreamingResponse):
                        async for data in res.body_iterator:
                            yield data
                        return
                    if isinstance(res, dict):
                        yield f"data: {json.dumps(res)}\n\n"
                        return

                except Exception as e:
                    logging.error(f"Error: {e}")
                    import traceback
                    traceback.print_exc()
                    yield f"data: {json.dumps({'error': {'detail': str(e)}})}\n\n"
                    return

                if isinstance(res, str):
                    message = openai_chat_chunk_message_template(form_data.model, res)
                    yield f"data: {json.dumps(message)}\n\n"

                if isinstance(res, Iterator):
                    for line in res:
                        yield process_line(form_data.model, line)

                if isinstance(res, AsyncGenerator):
                    async for line in res:
                        yield process_line(form_data.model, line)
                    logging.info(f"AsyncGenerator end...")

                if isinstance(res, str) or isinstance(res, Generator) or isinstance(res, AsyncGenerator):
                    finish_message = openai_chat_chunk_message_template(
                        form_data.model, ""
                    )
                    finish_message["choices"][0]["finish_reason"] = "stop"
                    print(f"Pipe-Dataline:::: DONE")
                    yield f"data: {json.dumps(finish_message)}\n\n"
                    yield "data: [DONE]"

            return StreamingResponse(stream_content(), media_type="text/event-stream")
        else:
            res = pipe(
                user_message=user_message,
                model_id=pipeline_id,
                messages=messages,
                body=form_data.model_dump(),
            )
            logging.info(f"stream:false:{res}")

            if isinstance(res, dict):
                return res
            elif isinstance(res, BaseModel):
                return res.model_dump()
            else:

                message = ""

                if isinstance(res, str):
                    message = res

                if isinstance(res, Generator):
                    for stream in res:
                        message = f"{message}{stream}"

                logging.info(f"stream:false:{message}")
                return {
                    "id": f"{form_data.model}-{str(uuid.uuid4())}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": form_data.model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": message,
                            },
                            "logprobs": None,
                            "finish_reason": "stop",
                        }
                    ],
                }


    return await run_in_threadpool(job)

def openai_chat_chunk_message_template(
    model: str,
    content: Optional[str] = None,
    tool_calls: Optional[list[dict]] = None,
    usage: Optional[dict] = None,
    **kwargs
) -> dict:
    template = openai_chat_message_template(model, **kwargs)
    template["object"] = "chat.completion.chunk"

    template["choices"][0]["index"] = 0
    template["choices"][0]["delta"] = {}

    if content:
        template["choices"][0]["delta"]["content"] = content

    if tool_calls:
        template["choices"][0]["delta"]["tool_calls"] = tool_calls

    if not content and not tool_calls:
        template["choices"][0]["finish_reason"] = "stop"

    if usage:
        template["usage"] = usage
    return template

def openai_chat_message_template(model: str, **kwargs):
    return {
        "id": f"{model}-{str(uuid.uuid4())}",
        "created": int(time.time()),
        "model": model,
        "node_id": get_local_ip(),
        "task_output_meta": kwargs.get("task_output_meta"),
        "choices": [{"index": 0, "logprobs": None, "finish_reason": None}],
    }

@app.get("/health")
async def healthcheck():
    return {"status": True}