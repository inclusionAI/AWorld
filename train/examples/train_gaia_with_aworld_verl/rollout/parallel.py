"""
简化的并行评估执行器
"""
import logging
import os
import traceback
from datetime import datetime

from aworld.core.agent.swarm import Swarm
from aworld.core.task import TaskResponse, Task
from aworld.evaluations.base import EvalTarget, EvalDataCase
from aworld.runner import Runners
from train.examples.train_gaia_with_aworld_verl.env import build_mcp_config
from train.examples.train_gaia_with_aworld_verl.rollout import build_gaia_agent


logging.basicConfig(level=logging.INFO, force=True, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

log_path = os.path.join("logs", "eval_digest.log")

# Use RotatingFileHandler for size-based rotation (100MB per file, keep 10 files)
from logging.handlers import RotatingFileHandler

file_handler = RotatingFileHandler(
    log_path,
    maxBytes=30 * 1024 * 1024,  # 100MB per file
    backupCount=10,  # Keep 10 backup files
    encoding='utf-8'
)
eval_digest_logger = logging.getLogger("eval_digest")
eval_digest_logger.setLevel(level=logging.INFO)

eval_digest_logger.addHandler(file_handler)


class ParallelGaiaEvalTarget(EvalTarget[dict]):
    """简化的并行 Gaia 评估目标"""

    def __init__(
            self
    ):
        super().__init__()

    async def build_common_gaia_task(self, user_input: str, session_id, task_id):
        if 'screen_shot' in os.getenv("ENV_PLUGINS", ""):
            from ..env.hooks import PostLLMCallRolloutHook, PostToolCallRolloutHook

        swarm = Swarm(build_gaia_agent(llm_model_name=os.getenv("LLM_MODEL_NAME"),
                                       llm_base_url=os.getenv("LLM_BASE_URL"),
                                       llm_api_key=os.getenv("LLM_API_KEY"),
                                       mcp_config=build_mcp_config()))
        return Task(id=task_id, session_id=session_id, input=user_input, swarm=swarm, timeout=1200)


    async def predict(self, index: int, o_input: EvalDataCase[dict]) -> dict:
        batch_id = o_input.run_id
        input = o_input.case_data
        session_id = f"{batch_id}_session#{input['id']}"
        task_id = f"{batch_id}_task#{input['id']}"
        task = await self.build_common_gaia_task(user_input=input['prompt'], session_id=session_id, task_id=task_id)
        task_id = task.id

        try:
            result = await Runners.run_task(task=task)
            os.makedirs(f"logs/trajectory/{batch_id}", exist_ok=True)
            with open(f"logs/trajectory/{batch_id}/traj_{index}.json", "a") as f:
                f.write(str(result[task_id].trajectory))
            os.makedirs(f"results/{batch_id}", exist_ok=True)
            cur_time = datetime.now().strftime('%Y%m%d%H%M%S')
            with open(f"logs/results/{batch_id}/{task_id}_{cur_time}_{o_input.eval_case_id}.txt", "w") as f:
                f.write(result[task_id].answer)
            if isinstance(result, TaskResponse):
                return {"answer": result.answer}
            if isinstance(result, dict):
                task_result = result[task_id]
                eval_digest_logger.info(
                    f"eval_task_digest|{batch_id}|{task_id}|{task_result.time_cost:0.1f}|{task_result.usage}")
                return {"answer": task_result.answer}
            else:
                return {"answer": result}
        except Exception as err:
            print(f"err is {err}, trace is {traceback.format_exc()}")
            return {"answer": str(err)}
