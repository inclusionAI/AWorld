import threading
import aworld.trace as trace
import os
import time
from aworld.trace.instrumentation.threading import instrument_theading
from aworld.logs.util import logger, trace_logger
from aworld.agents.llm_agent import Agent
from aworld.core.common import Config
from aworld.config.conf import AgentConfig
from aworld.runner import Runners
from aworld.trace.server import get_trace_server

os.environ["MONITOR_SERVICE_NAME"] = "otlp_example"
trace.configure(trace.ObservabilityConfig(trace_server_enabled=True,))
instrument_theading()


class SyncAgent(Agent):
    def __init__(self, name: str, conf: Config | None = None,
                 system_prompt: str = None, agent_prompt: str = None, **kwargs):
        super().__init__(name=name, conf=conf, system_prompt=system_prompt, agent_prompt=agent_prompt, **kwargs)


def run_agent(input: str):
    agent = SyncAgent(
        conf=AgentConfig(
            llm_provider=os.getenv("LLM_PROVIDER"),
            llm_model_name=os.getenv("LLM_MODEL_NAME"),
            llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
            llm_base_url=os.getenv("LLM_BASE_URL"),
            llm_api_key=os.getenv("LLM_API_KEY"),),
        name="test_agent",
        system_prompt="You are a mathematical calculation agent.",
        agent_prompt="Please provide the calculation results directly without any other explanatory text. Here are the content: {task}"
    )

    try:
        res = Runners.sync_run(
            input=input,
            agent=agent,
            session_id="123"
        )
        print(res.answer)
    except Exception as e:
        logger.error(traceback.format_exc())


def child_thread_func():
    logger.info("child thread running")
    with trace.span("child_thread") as span:
        trace_logger.info("child thread running")
    time.sleep(1000)


def main():
    logger.info("main running")
    with trace.span("test_fastapi") as span:
        trace_logger.info("start run child_thread_func")
        threading.Thread(target=child_thread_func).start()
        threading.Thread(target=child_thread_func).start()
        run_agent("What is the square of 5?")
    get_trace_server().join()


# if __name__ == "__main__":
#     main()
