import asyncio
import os
import traceback

from dotenv import load_dotenv
load_dotenv()

from train.examples.train_gaia_with_aworld_verl.gaia import build_gaia_agent, build_gaia_task

from train.examples.train_gaia_with_aworld_verl.mcp_config import build_mcp_config

from aworld.runner import Runners


async def run(user_input: str):
    # 1. build agent
    agent = build_gaia_agent(llm_model_name=os.getenv("LLM_MODEL_NAME"),
                             llm_base_url=os.getenv("LLM_BASE_URL"),
                             llm_api_key=os.getenv("LLM_API_KEY"),
                             mcp_config=build_mcp_config())

    # 2. build task
    task = await build_gaia_task(user_input=user_input, target=agent, timeout=1200)

    # 3. run task
    try:
        result = await Runners.run_task(task=task)
        print(result)
    except Exception as err:
        print(f"err is {err}, trace is {traceback.format_exc()}")


if __name__ == '__main__':
    query = "In July 2, 1959 United States standards for grades of processed fruits, vegetables, and certain other products listed as dehydrated, consider the items in the \"dried and dehydrated section\" specifically marked as dehydrated along with any items in the Frozen/Chilled section that contain the whole name of the item, but not if they're marked Chilled. As of August 2023, what is the percentage (to the nearest percent) of those standards that have been superseded by a new version since the date given in the 1959 standards?"
    asyncio.run(run(user_input=query))
