
from dotenv import load_dotenv
# init env
load_dotenv()

import asyncio
import logging
import os
import sys
import traceback
from datetime import datetime
from typing import Iterator

from dotenv import load_dotenv
load_dotenv()

from aworld.core.context.amni import TaskInput, ApplicationContext
from aworld.core.context.amni.config import init_middlewares, AmniConfigFactory, AmniConfigLevel
from aworld.core.context.amni.worksapces import workspace_repo
from aworld.dataset.sampler import RangeSampler, Sampler, FixedSampler
from aworld.output import WorkSpace
from aworld.runners.evaluate_runner import EvaluateRunner
from examples.xbench.agents.swarm import build_xbench_swarm
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

    async def predict(self, index: int, o_input: EvalDataCase[dict]) -> dict:
        batch_id = o_input.run_id
        input = o_input.case_data
        session_id = f"{batch_id}_session#{input['id']}"
        task_id = f"{batch_id}_task#{input['id']}"

        task = await self.build_task(input['prompt'], session_id=session_id, task_id=task_id)
        try:
            result = await Runners.run_task(task=task)
            if not os.path.exists(f"results/{batch_id}"):
                os.mkdir(f"results/{batch_id}")
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
            eval_dataset_load_config=DataLoaderConfig(sampler=FixedSampler(ids=[3])),
            # eval_dataset_load_config=DataLoaderConfig(sampler=RangeSampler(start_index=50, end_index=100)),
            # eval_dataset_load_config=DataLoaderConfig(sampler=FixedSampler(ids = [12,14,16,24,25,26])),
            repeat_times=1,
            parallel_num=3,
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
            cost_time = case_result.score_rows.get('TimeCostScorer').metric_results.get('predict_time_cost_ms')
            f.write(f"{case_result.eval_case_id}|{case_result.input.case_data.get('id')}|{answer_acc.get('eval_status')}|{int(cost_time.get('value')/1000)}\n")


if __name__ == '__main__':
    asyncio.run(evaluate())
