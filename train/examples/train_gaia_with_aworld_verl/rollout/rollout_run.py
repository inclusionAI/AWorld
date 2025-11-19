import asyncio
import logging
import os
from datetime import datetime

from dotenv import load_dotenv

load_dotenv('.env')

from aworld.logs.util import logger


from train.examples.train_gaia_with_aworld_verl.rollout.parallel import ParallelGaiaEvalTarget


from aworld.config import EvaluationConfig, DataLoaderConfig
from aworld.evaluations.base import EvalResult, EvalTask
from aworld.runners.evaluate_runner import EvaluateRunner

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
            ] if os.getenv('ENABLE_SCORE', 'True') == 'True' else [],
            eval_dataset_id_or_file_path=os.getenv(
                'EVAL_DATASET_PATH',
                os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gaia_datasets', 'DeepSearch_decrypted.csv')
            ),
            eval_dataset_load_config=DataLoaderConfig(),
            # eval_dataset_load_config=DataLoaderConfig(sampler=RangeSampler(start_index=50, end_index=100)),
            # eval_dataset_load_config=DataLoaderConfig(sampler=FixedSampler(ids = [12,14,16,24,25,26])),
            repeat_times=1,
            parallel_num=20,
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

