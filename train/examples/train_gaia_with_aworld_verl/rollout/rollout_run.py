import asyncio
import logging
import os
import traceback
from datetime import datetime

from dotenv import load_dotenv

load_dotenv('.env')

from train.examples.train_gaia_with_aworld_verl.mcp_tools.ip_pool import release_proxy_by_task_id
from aworld.core.task import TaskResponse
from aworld.evaluations.base import EvalTarget, EvalDataCase
from aworld.runner import Runners
from aworld.runners.state_manager import RuntimeStateManager
from train.examples.train_gaia_with_aworld_verl.rollout import *

from aworld.logs.util import logger

from aworld.config import EvaluationConfig, DataLoaderConfig
from aworld.evaluations.base import EvalResult, EvalTask
from aworld.runners.evaluate_runner import EvaluateRunner

# Import scorer to register it with the global scorer registry

class ParallelGaiaEvalTarget(EvalTarget[dict]):

    def __init__(
            self
    ):
        super().__init__()

    async def build_gaia_task(self, user_input: str, session_id, task_id):
        if 'screen_shot' in os.getenv("ENV_PLUGINS", ""):
            from train.examples.train_gaia_with_aworld_verl.mcp_tools.hooks import PostToolCallRolloutHook

        agent = build_context_aware_agent(llm_model_name=os.getenv("LLM_MODEL_NAME"),
                                          llm_base_url=os.getenv("LLM_BASE_URL"),
                                          llm_api_key=os.getenv("LLM_API_KEY"),
                                          mcp_config=await build_mcp_config())
        return await build_task(user_input=user_input, target=agent, timeout=1200,
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
                logger.info(
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


async def batch_run():
    logger.info(f"runner_log|pid={os.getpid()}|ppid={os.getppid()}")
    eval_target = ParallelGaiaEvalTarget()
    task_id = f"eval_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    result: EvalResult = await EvaluateRunner(
        task=EvalTask(task_id=task_id),
        config=EvaluationConfig(
            eval_target=eval_target,
            eval_dataset_query_column="prompt",
            eval_criterias=[
                {
                    "metric_name": "flight_judge",
                    "threshold": 0.5,
                }
            ] if os.getenv('ENABLE_SCORE', 'True') == 'True' else [],
            eval_dataset_id_or_file_path=os.getenv(
                'EVAL_DATASET_PATH',
                os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gaia_datasets', 'DeepSearch_decrypted.csv')
            ),
            eval_dataset_load_config=DataLoaderConfig(),
            # eval_dataset_load_config=DataLoaderConfig(sampler=RangeSampler(start_index=50, end_index=100)),
            # eval_dataset_load_config=DataLoaderConfig(sampler=FixedSampler(ids = [12,14,16,24,25,26])),
            repeat_times=1,
            parallel_num=10,
            skip_passed_cases=True,
        )).run()

    # ============= SAVE RESULT TO FILE =============
    result_file_path = f"logs/results/{task_id}/"
    if not os.path.exists("logs/results"):
        os.mkdir("logs/results")
    if not os.path.exists(result_file_path):
        os.mkdir(result_file_path)
    with open(f"{result_file_path}/results.txt", "w") as f:
        f.write(f"{result.run_id}\n")
        f.write(f"START: {datetime.fromtimestamp((int(result.create_time))).strftime('%Y%m%d %H%M%S')}\n")
        f.write(f"END: {datetime.now().strftime('%Y%m%d %H%M%S')}\n")

        f.write(f"---------- EVAL RESULT --------------\n")
        f.write(f"{result.summary.get('FlightJudgeLLMScorer')}\n\n")

        f.write("---------- DETAIL -------------\n")
        for case_result in result.eval_case_results:
            if not case_result.score_rows or not case_result.score_rows.get('FlightJudgeLLMScorer'):
                continue
            answer_acc = case_result.score_rows.get('FlightJudgeLLMScorer').metric_results.get('flight_judge')
            time_cost_scorer = case_result.score_rows.get('TimeCostScorer')
            cost_time = time_cost_scorer.metric_results.get('predict_time_cost_ms') if time_cost_scorer and time_cost_scorer.metric_results else None

            # resolve None
            # answer_status = answer_acc.get('eval_status') if answer_acc else 'N/A'
            cost_time_value = int(cost_time.get('value')/1000) if cost_time and cost_time.get('value') else 0

            f.write(f"{case_result.eval_case_id}|{case_result.input.case_data.get('id')}|{answer_acc}|{cost_time_value}\n")


if __name__ == '__main__':
    asyncio.run(batch_run())

