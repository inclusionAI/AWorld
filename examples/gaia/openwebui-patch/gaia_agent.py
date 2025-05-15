import asyncio
from typing import AsyncGenerator, Callable, Awaitable
from pydantic import BaseModel, Field
import sys
import traceback
import logging
import os
import json
import re

logger = logging.getLogger(__name__)


class Pipe:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()
        self.data_prefix = "data:"
        self.emitter = None

    def _get_model_config(self):
        try:
            model_cfg = os.getenv("GAIA_MODEL_CONFIG")
            return json.loads(model_cfg)
        except Exception as e:
            logger.error(
                f">>> Gaia Agent: Error loading model config, model_cfg={model_cfg}: {traceback.format_exc()}"
            )
            raise e

    def pipes(self):
        models = self._get_model_config()

        return [
            {
                "id": model["id"],
                "name": f"gaia_agent@{model['model']}",
            }
            for model in models
        ]

    async def pipe(
        self, body: dict, __event_emitter__: Callable[[dict], Awaitable[None]] = None
    ) -> AsyncGenerator[str, None]:

        self.emitter = __event_emitter__
        process = None

        try:
            logger.info(f">>> Gaia Agent: body={body}")

            prompt = body["messages"][-1]["content"]
            model = body["model"].replace("gaia_agent.", "")
            logger.info(f">>> Gaia Agent: prompt={prompt}, model={model}")

            cmd = [
                sys.executable,
                "-m",
                "examples.gaia.gaia_agent_runner",
                "--prompt",
                prompt,
            ]

            env = os.environ.copy()
            models = self._get_model_config()
            selected_model = next((m for m in models if m["id"] == model), None)
            if not selected_model:
                logger.warning(
                    f">>> Gaia Agent: Model ID '{model}' not found in configuration!"
                )
                yield self._wrap_line(
                    f">>> Gaia Agent: Model ID '{model}' not found in configuration!"
                )
                return

            env["LLM_PROVIDER"] = selected_model.get("provider")
            env["LLM_API_KEY"] = selected_model.get("api_key")
            env["LLM_BASE_URL"] = selected_model.get("base_url")
            env["LLM_MODEL_NAME"] = selected_model.get("model")
            env["LLM_TEMPERATURE"] = str(selected_model.get("temperature", 0))

            logger.info(
                f">>> Gaia Agent: Using model configuration: {selected_model['model']}"
            )

            process = await asyncio.subprocess.create_subprocess_exec(
                *cmd,
                cwd="/app/aworld",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
                env=env,
            )

            logger.info(f">>> Gaia Agent: process={process}")

            while True:
                line = await process.stdout.readline()
                if not line:
                    break

                line = line.decode("utf-8").rstrip().replace("\[[\d{1,3}m", "")

                if re.search(r"\d{4}-\d{2}-\d{2}", line) :
                    if (
                        " __main__ " in line
                        or " [agent] " in line
                        or "ActionModel" in line
                        or "- INFO - step:" in line
                        or "mcp observation:" in line
                    ):
                        logger.info(f">>> Gaia Agent: line={line}")
                        yield self._wrap_line(f"{line}")
                        continue
                elif not (
                    re.search(r"^Starting .* Server...$", line)
                    or any(pattern in line for pattern in [
                        "`e2b-server` is a powerful",
                        "Processing request of type",
                        "No handlers found",
                        "Serving Flask app",
                        "Debug mode:",
                        "Running on http:",
                        "npm WARN exec The following package was not found"
                    ])
                ):
                    logger.info(f">>> Gaia Agent: line={line}")
                    yield self._wrap_line(f"{line}")
                    continue

                logger.info(f">>> Gaia Agent: ignore line={line}")

            return_code = await process.wait()
            yield self._wrap_line(f"Process exited with code {return_code}")
            await asyncio.sleep(0.1)

        except Exception as e:
            emsg = traceback.format_exc()
            logger.error(f">>> Gaia Agent: exception {emsg}")
            yield self._wrap_line(f"Gaia Agent Error: {emsg}")

        finally:
            if process:
                try:
                    logger.info("Stopping gaia agent process...")
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=3.0)
                        logger.info("Stopping gaia agent process timeout!")
                    except asyncio.TimeoutError:
                        logger.warning(
                            "Stopping gaia agent process timeout, force kill!"
                        )
                        process.kill()
                        await process.wait()
                        logger.info("Gaia agent process force killed!")
                except Exception as e:
                    logger.error(f"Error stopping gaia agent process: {e}")
            yield self._wrap_line(f"[Done]Gaia Task End!")

    def _wrap_line(self, line: str) -> str:
        line = line.replace("<think>", "<_think_>")
        line = line.replace("</think>", "<_think_/>")
        return f"{line}\n"
