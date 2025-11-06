from dotenv import load_dotenv
load_dotenv()

from train.examples.train_gaia_with_aworld_verl.rollout import build_gaia_agent, build_mcp_config

from examples.xbench.agents.swarm import build_xbench_swarm

from aworld.core.agent.swarm import Swarm
from aworld.core.context.base import Context

import asyncio
import logging
import os
import traceback
from datetime import datetime

from aworld.core.context.amni import TaskInput, ApplicationContext
from aworld.core.context.amni.config import init_middlewares, AmniConfigFactory, AmniConfigLevel
from aworld.runners.evaluate_runner import EvaluateRunner
from aworld.config import TaskConfig, EvaluationConfig, DataLoaderConfig
from aworld.core.task import Task, TaskResponse
from aworld.evaluations.base import EvalTarget, EvalDataCase, EvalTask, EvalResult
from aworld.runner import Runners

logging.basicConfig(level=logging.INFO, force=True, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

log_path = os.path.join(os.path.dirname((os.path.abspath(__file__))), "logs", "eval_digest.log")

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


class AmniContextEvaluatable(EvalTarget):

    def __init__(self):
        super().__init__()

    async def build_context(self, task_input: TaskInput) -> ApplicationContext:

        context_config = AmniConfigFactory.create(AmniConfigLevel.NAVIGATOR)

        return await ApplicationContext.from_input(task_input, context_config = context_config)

    async def build_context_common(self, task_input: TaskInput) -> ApplicationContext:
        context = Context()
        context.task_input = task_input
        return context

    async def build_task(self, task_content: str, session_id: str = None, task_id: str = None) -> Task:
        if not session_id:
            session_id = f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        task_input = TaskInput(
            user_id=f"test_user",
            session_id=session_id,
            task_id=f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}" if not task_id else task_id,
            task_content=task_content,
            origin_user_input=task_content
        )

        context = await self.build_context(task_input)
        swarm = build_xbench_swarm()
        await context.build_agents_state(swarm.topology)

        return Task(
            id=context.task_id,
            user_id=context.user_id,
            session_id=context.session_id,
            input=context.task_input,
            endless_threshold=5,
            swarm=swarm,
            context=context,
            conf=TaskConfig(
                stream=False,
                exit_on_failure=True
            ),
            timeout=60 * 60
        )

    async def build_common_gaia_task(self, user_input: str, session_id, task_id):
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

        # task = await self.build_task(input['prompt'], session_id=session_id, task_id=task_id)
        task = await self.build_common_gaia_task(user_input=input['prompt'], session_id=session_id, task_id=task_id)
        try:
            result = await Runners.run_task(task=task)
            os.makedirs(f"trajectory/{batch_id}", exist_ok=True)
            with open(f"trajectory/{batch_id}/traj_{index}.json", "a") as f:
                f.write(str(result[task_id].trajectory))
            os.makedirs(f"results/{batch_id}", exist_ok=True)
            cur_time = datetime.now().strftime('%Y%m%d%H%M%S')
            with open(f"results/{batch_id}/{task_id}_{cur_time}_{o_input.eval_case_id}.txt", "w") as f:
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


async def evaluate():
    init_middlewares()
    eval_target = AmniContextEvaluatable()
    task_id = f"eval_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    # ============= RUN EVALUATION =============
    result: EvalResult = await EvaluateRunner(
        task=EvalTask(task_id=task_id),
        config=EvaluationConfig(
            eval_target=eval_target,
            eval_criterias=[
                {
                    "metric_name": "answer_accuracy",
                    "threshold": 0.5,
                }
            ],
            eval_dataset_id_or_file_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'benchmark', 'DeepSearch_decrypted.csv'),
            eval_dataset_load_config=DataLoaderConfig(),
            # eval_dataset_load_config=DataLoaderConfig(sampler=RangeSampler(start_index=50, end_index=100)),
            # eval_dataset_load_config=DataLoaderConfig(sampler=FixedSampler(ids = [12,14,16,24,25,26])),
            repeat_times=1,
            parallel_num=100,
            skip_passed_cases=True,
        )).run()

    # ============= SAVE RESULT TO FILE =============
    result_file_path = f"results/{task_id}/"
    if not os.path.exists("results"):
        os.mkdir("results")
    if not os.path.exists(result_file_path):
        os.mkdir(result_file_path)
    with open(f"{result_file_path}/results.txt", "w") as f:
        f.write(f"{result.run_id}\n")
        f.write(f"START: {datetime.fromtimestamp((int(result.create_time))).strftime('%Y%m%d %H%M%S')}\n")
        f.write(f"END: {datetime.now().strftime('%Y%m%d %H%M%S')}\n")

        f.write(f"---------- SUMMARY --------------\n")
        f.write(f"{result.summary.get('AnswerAccuracyLLMScorer')}\n\n")

        f.write("---------- DETAIL -------------\n")
        for case_result in result.eval_case_results:
            if not case_result.score_rows or not case_result.score_rows.get('AnswerAccuracyLLMScorer'):
                continue
            answer_acc = case_result.score_rows.get('AnswerAccuracyLLMScorer').metric_results.get('answer_accuracy')
            time_cost_scorer = case_result.score_rows.get('TimeCostScorer')
            cost_time = time_cost_scorer.metric_results.get('predict_time_cost_ms') if time_cost_scorer and time_cost_scorer.metric_results else None
            
            # 处理可能为 None 的情况
            answer_status = answer_acc.get('eval_status') if answer_acc else 'N/A'
            cost_time_value = int(cost_time.get('value')/1000) if cost_time and cost_time.get('value') else 0
            
            f.write(f"{case_result.eval_case_id}|{case_result.input.case_data.get('id')}|{answer_status}|{cost_time_value}\n")


if __name__ == '__main__':
    asyncio.run(evaluate())
