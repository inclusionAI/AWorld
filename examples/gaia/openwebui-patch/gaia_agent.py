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

line_output_prefix = "$$$GAIA_FMT_OUTPUT$$$"


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
                "examples.gaia.gaia_agent_stream_runner",
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
                yield self._response_line(
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

            asyncio.streams._DEFAULT_LIMIT = 10 * 1024 * 1024  # 10MB

            process = await asyncio.subprocess.create_subprocess_exec(
                *cmd,
                cwd="/app/aworld",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
                env=env,
            )

            logger.info(f">>> Gaia Agent: process={process}")

            yield self._response_line("## GAIA agent start!")

            while True:
                try:
                    line = await process.stdout.readline()
                    if not line:
                        break

                    line = line.decode("utf-8").rstrip()

                    print(f">>> Gaia Agent: {line}")

                    if line.startswith(line_output_prefix):

                        line = line[len(line_output_prefix) :]

                        line = json.loads(line)

                        yield self._response_line(line)

                except Exception as e:
                    # Handle the case where a separator is found but the chunk is too long
                    if "Separator is found, but chunk is longer than limit" in str(e):
                        logger.warning(
                            f">>> Gaia Agent: Chunk size limit exceeded: {e}"
                        )
                        continue
                    else:
                        logger.error(
                            f">>> Gaia Agent Error: error={traceback.format_exc()}, line={line}"
                        )
                        yield self._response_line(f"Gaia Agent Error: {e}, line={line}")
                        break
                finally:
                    await asyncio.sleep(0.001)

        except Exception as e:
            emsg = traceback.format_exc()
            logger.error(f">>> Gaia Agent Error: exception {emsg}")
            yield self._response_line(f"Gaia Agent Error: {emsg}")

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
            yield self._response_line(f"[Done]Gaia Task End!")

    def _response_line(self, line: str):
        return f"{line}\n"
