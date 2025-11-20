import logging
import os
import traceback
from datetime import datetime

from aworld.core.task import TaskResponse
from aworld.evaluations.base import EvalTarget, EvalDataCase
from aworld.runner import Runners
from aworld.runners.state_manager import RuntimeStateManager
from train.examples.train_gaia_with_aworld_verl.mcp import build_mcp_config
from train.examples.train_gaia_with_aworld_verl.mcp.ip_pool import release_proxy_by_task_id
from train.examples.train_gaia_with_aworld_verl.rollout import build_gaia_agent, build_gaia_task

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

    def __init__(
            self
    ):
        super().__init__()

    async def build_gaia_task(self, user_input: str, session_id, task_id):
        if 'screen_shot' in os.getenv("ENV_PLUGINS", ""):
            from ..mcp.hooks import PostToolCallRolloutHook

        if 'xiecheng_ck' in os.getenv("ENV_PLUGINS", ""):
            from ..mcp.xiecheng_hook import PostLLMCallRolloutHook

        agent = build_gaia_agent(llm_model_name=os.getenv("LLM_MODEL_NAME"),
                                       llm_base_url=os.getenv("LLM_BASE_URL"),
                                       llm_api_key=os.getenv("LLM_API_KEY"),
                                       mcp_config=await build_mcp_config(user_input=user_input, session_id=session_id, task_id=task_id))
        return await build_gaia_task(user_input=user_input, target=agent, timeout=1200,
                                     session_id=session_id, task_id=task_id)


    async def predict(self, index: int, o_input: EvalDataCase[dict]) -> dict:
        batch_id = o_input.run_id
        input = o_input.case_data
        session_id = f"{batch_id}_session#{input['id']}"
        task_id = f"{batch_id}_task#{input['id']}"
        task = await self.build_gaia_task(user_input=input['prompt'], session_id=session_id, task_id=task_id)
        task_id = task.id

        try:
            result = await Runners.run_task(task=task)
            os.makedirs(f"logs/trajectory/{batch_id}", exist_ok=True)
            with open(f"logs/trajectory/{batch_id}/traj_{index+1}.json", "a") as f:
                f.write(str(result[task_id].trajectory))
            os.makedirs(f"logs/results/{batch_id}", exist_ok=True)
            cur_time = datetime.now().strftime('%Y%m%d%H%M%S')
            with open(f"logs/results/{batch_id}/{task_id}_{cur_time}_{o_input.eval_case_id}.txt", "w") as f:
                f.write(result[task_id].answer)
            
            # 任务结束后，查询state_manager获取所有节点并绘制火焰图
            try:
                state_manager = RuntimeStateManager.instance()
                if state_manager:
                    nodes = state_manager.query_by_task(task_id)
                    if nodes:
                        os.makedirs(f"logs/flame_graphs/{batch_id}", exist_ok=True)
                        flame_graph_path = f"logs/flame_graphs/{batch_id}/flame_{task_id}_{cur_time}.html"
                        from train.examples.train_gaia_with_aworld_verl.log_processor.analyze_state_manager import \
                            plot_flame_graph
                        plot_flame_graph(nodes, task_id, flame_graph_path)
            except Exception as flame_err:
                logging.warning(f"绘制火焰图失败: {flame_err}, trace: {traceback.format_exc()}")
            
            if isinstance(result, TaskResponse):
                return {"answer": result[task_id].answer, "trajectory": result[task_id].trajectory}
            if isinstance(result, dict):
                task_result = result[task_id]
                eval_digest_logger.info(
                    f"eval_task_digest|{batch_id}|{task_id}|{task_result.time_cost:0.1f}|{task_result.usage}")
                return {"answer": task_result.answer, "trajectory": task_result.trajectory}
            else:
                return {"answer": result}
        except Exception as err:
            print(f"err is {err}, trace is {traceback.format_exc()}")
            return {"answer": str(err)}
        finally:
            # 任务执行结束后释放 IP 回 IP 池
            if os.getenv("IP_POOL_ENABLE", "False") == "True":
                release_proxy_by_task_id(task_id)
