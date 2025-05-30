import logging
import os
import json
import asyncio
import traceback

import ray
import ray.util.queue

logger = logging.getLogger(__name__)

ray.init(address="ray://localhost:10001")

print(
    """This cluster consists of
    {} nodes in total
    {} CPU resources in total
""".format(
        len(ray.nodes()), ray.cluster_resources()["CPU"]
    )
)


# 1. 定义Ray Actor
@ray.remote
class AgentActor:
    def __init__(self):
        self.init_tag = False
        self.queue = ray.util.queue.Queue(maxsize=1000)
        self.running = False
        
    def _send_result(self, msg: str):
        try:
            self.queue.put(msg)
        except Exception as e:
            print(f"Error sending result: {e}")

    async def run(self, prompt: str):
        try:
            print(f"Starting run with prompt: {prompt}")
            self.running = True
            
            if not self.init_tag:
                print("Initializing AWorld Agent...")
                from aworld.config.conf import AgentConfig, TaskConfig
                from aworld.core.agent.base import Agent
                from aworld.core.task import Task
                from aworld.output.ui.base import AworldUI
                from aworld.output.ui.markdown_aworld_ui import MarkdownAworldUI
                from aworld.runner import Runners
                self.init_tag = True
                print(f"agent init completed")

            llm_provider = os.getenv("LLM_PROVIDER_WEATHER", "openai")
            llm_model_name = os.getenv("LLM_MODEL_NAME_WEATHER")
            llm_api_key = os.getenv("LLM_API_KEY_WEATHER")
            llm_base_url = os.getenv("LLM_BASE_URL_WEATHER")
            llm_temperature = float(os.getenv("LLM_TEMPERATURE_WEATHER", 0.0))

            print(f"Environment variables - Provider: {llm_provider}, Model: {llm_model_name}, Base URL: {llm_base_url}")

            if not llm_model_name or not llm_api_key or not llm_base_url:
                error_msg = "LLM_MODEL_NAME_WEATHER, LLM_API_KEY_WEATHER, LLM_BASE_URL_WEATHER must be set in your environment variables"
                print(f"Error: {error_msg}")
                self._send_result(f"Error: {error_msg}")
                return

            agent_config = AgentConfig(
                llm_provider=llm_provider,
                llm_model_name=llm_model_name,
                llm_api_key=llm_api_key,
                llm_base_url=llm_base_url,
                llm_temperature=llm_temperature,
            )

            path_cwd = os.path.dirname(os.path.abspath(__file__))
            mcp_path = os.path.join(path_cwd, "mcp.json")
            
            if not os.path.exists(mcp_path):
                error_msg = f"MCP config file not found: {mcp_path}"
                print(f"Error: {error_msg}")
                self._send_result(f"Error: {error_msg}")
                return
                
            with open(mcp_path, "r") as f:
                mcp_config = json.load(f)

            print("Creating agent...")
            super_agent = Agent(
                conf=agent_config,
                name="weather_agent",
                system_prompt="You are a weather agent, you can query real-time weather information",
                mcp_config=mcp_config,
                mcp_servers=[
                    "weather_server",
                ],
            )

            print("Creating task...")
            task = Task(input=prompt, agent=super_agent, conf=TaskConfig())

            print("Creating UI...")
            rich_ui = MarkdownAworldUI()
            
            print("Starting task execution...")
            async for output in Runners.streamed_run_task(task).stream_events():
                logger.info(f"Agent Output: {output}")
                res = await AworldUI.parse_output(output, rich_ui)
                for item in res if isinstance(res, list) else [res]:
                    self._send_result(str(item))
                    
        except Exception as e:
            error_msg = f"Error in run method: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            self._send_result(f"Error: {error_msg}")
        finally:
            self.running = False
            print("Run method completed")
                
    def get_result(self):
        """同步方法获取单个结果"""
        try:
            return self.queue.get(timeout=1.0)
        except:
            return None
    
    def is_running(self):
        """检查是否还在运行"""
        return self.running


@ray.remote
def ray_env():
    import os, sys, socket
    return (
        os.environ,
        sys.path,
        os.listdir("/home/ray/anaconda3/lib/python3.12/site-packages"),
        socket.gethostname(),
    )


async def call_ray_async(ray_method, *args, **kwargs):
    return await ray_method.remote(*args, **kwargs)

async def _main_async():
    prompt = "What's the weather like in Beijing today?"
    agent = AgentActor.remote()
    print(f"agent: {agent}")

    agent.run.remote(prompt)
    print(f"Started agent task")

    # 轮询获取结果
    while True:
        try:
            result = await agent.get_result.remote()
            if result:
                print(f"Received: {result}")
            elif is_running := await agent.is_running.remote():
                print(f"Agent is running")
            elif not is_running:
                print("Agent finished")
                break
            await asyncio.sleep(0.1)

        except ray.exceptions.ActorDiedError as e:
            print(f"Actor died: {e}")
            break
        except Exception as e:
            print(f"Error in main loop: {e}")
            break


if __name__ == "__main__":
    async def main():
        r = await call_ray_async(ray_env)
        print(f"ray_env: {r[0]}")
        print(f"sys.path: {r[1]}")
        print(f"site-packages: {r[2]}")
        print(f"host: {r[3]}")
        await _main_async()
    
    asyncio.run(main())
