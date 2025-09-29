import abc
import time
import uuid
from ast import Set
import statistics
import asyncio
from typing import Any, Iterable, Optional, List, Callable, Awaitable, TypeVar, Generic, TypedDict, Union
from enum import Enum
from dataclasses import dataclass, field
from itertools import chain, repeat
from aworld.logs.util import logger
from aworld.config.conf import EvaluationConfig

# Try to import tqdm
try:
    from tqdm.asyncio import tqdm_asyncio as tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

EvalCaseDataType = TypeVar('EvalCaseDataType')


class EvalStatus(Enum):
    PASSED = 1
    FAILED = 2
    NOT_EVALUATED = 3


MetricValueType = Union[int, float, bool]


@dataclass
class EvalCriteria:
    '''
    Evaluation criteria.
    '''
    metric_name: str = field(default_factory=str)
    # full class name of scorer class, e.g. aworld.evaluations.scorers.label_distribution.LabelDistributionScorer
    # if not specified, will use the first scorer class in the registry for the metric name
    scorer_class: Optional[str] = field(default_factory=str)
    scorer_params: Optional[dict] = field(default_factory=dict)
    prompt: str = field(default_factory=str)
    max_value: float = field(default=float('inf'))
    min_value: float = field(default=-float('inf'))
    threshold: float = field(default=0.0)

    @classmethod
    def from_dict(cls, data: dict) -> 'EvalCriteria':
        from dataclasses import fields
        valid_fields = {field.name for field in fields(cls)}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered_data)

    def judge(self, value: float) -> EvalStatus:
        '''
        Judge the value against the threshold.
        '''
        if value > self.max_value or value < self.min_value:
            return EvalStatus.FAILED
        if value >= self.threshold:
            return EvalStatus.PASSED
        else:
            return EvalStatus.FAILED


@dataclass
class EvalTask:
    '''
    Evaluation task.
    '''
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    task_name: str = field(default_factory=str)
    create_time: float = field(default_factory=lambda: time.time())
    config: EvaluationConfig = field(default=None)


@dataclass
class EvalDataCase(Generic[EvalCaseDataType]):
    '''
    Evaluation data case.
    '''
    eval_case_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    eval_dataset_id: str = field(default_factory=str)
    run_id: Optional[str] = field(default_factory=str)
    case_data: EvalCaseDataType = field(default_factory=dict)
    create_time: float = field(default_factory=lambda: time.time())


@dataclass
class EvalDataset:
    '''
    Evaluation dataset.
    '''
    eval_dataset_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    eval_dataset_name: Optional[str] = field(default_factory=str)
    run_id: Optional[str] = field(default_factory=str)
    create_time: float = field(default_factory=lambda: time.time())
    eval_cases: list[EvalDataCase] = field(default_factory=list)


class MetricResult(TypedDict, total=False):
    '''
    Metric result.
    '''
    value: MetricValueType
    eval_status: EvalStatus


@dataclass
class ScorerResult:
    '''
    Scorer result. One scorer result contains multiple metric results.
    '''
    scorer_name: str = field(default_factory=str)
    # metric results, key is metric name, value is metric result or float
    metric_results: dict[str, MetricResult] = field(default_factory=dict)


@dataclass
class EvalCaseResult:
    index: int = 0
    eval_case_id: str = field(default_factory=str)
    eval_dataset_id: str = field(default_factory=str)
    input: dict = field(default_factory=dict)
    output: dict = field(default_factory=dict)
    # score results, key is scorer name, value is ScorerResult obj
    score_rows: dict[str, ScorerResult] = field(default_factory=dict)
    create_time: float = field(default_factory=lambda: time.time())


@dataclass
class EvalResult:
    '''
    Evaluation result.
    '''
    eval_result_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    eval_dataset_id: str = field(default_factory=str)
    run_id: str = field(default_factory=str)
    create_time: float = field(default_factory=lambda: time.time())
    summary: dict = field(default_factory=dict)
    eval_case_results: list[EvalCaseResult] = field(default_factory=list)


class EvalTarget(abc.ABC, Generic[EvalCaseDataType]):
    '''
    The base class of evaluated object.
    '''

    def __init__(self, eval_config: EvaluationConfig = None):
        self.eval_config = eval_config or EvaluationConfig()

    @abc.abstractmethod
    async def predict(self, index: int, input: EvalDataCase[EvalCaseDataType]) -> dict:
        """execute the llm/agent.

        Returns:
            execute result
        """
        raise NotImplementedError


class NoActionEvalTarget(EvalTarget[EvalCaseDataType]):
    async def predict(self, index: int, input: EvalDataCase[EvalCaseDataType]) -> dict:
        return {}


class Scorer(abc.ABC, Generic[EvalCaseDataType]):
    '''
    The base class of scorer.
    '''

    def __init__(self, name: str = None, eval_config: EvaluationConfig = None):
        self.name = name or self.__class__.__name__
        self.eval_config = eval_config or EvaluationConfig()
        self.eval_criterias = self.eval_config.eval_criterias or {}

    def __str__(self) -> str:
        return self.name

    def add_eval_criteria(self, eval_criteria: EvalCriteria) -> None:
        '''
            Add eval criteria.
        '''
        self.eval_criterias[eval_criteria.metric_name] = eval_criteria

    def list_eval_metrics(self) -> list[str]:
        '''
            List eval metric names.
        '''
        return list(self.eval_criterias.keys())

    async def scorer_and_judge(self, index: int, input: EvalDataCase[EvalCaseDataType], output: dict) -> ScorerResult:
        '''
            Judge the status.
        '''
        scorer_result = await self.score(index, input, output)
        for metric_name, metric_result in scorer_result.metric_results.items():
            if metric_name in self.eval_criterias:
                metric_result['eval_status'] = self.eval_criterias[metric_name].judge(metric_result['value'])
        return scorer_result

    @abc.abstractmethod
    async def score(self, index: int, input: EvalDataCase[EvalCaseDataType], output: dict) -> ScorerResult:
        """score the execute result.

        Args:
            index: the index of the example.
            input: the input of the example.
            output: the output of the example.

        Returns:
            score: the score of the example.
        """
        raise NotImplementedError

    def _do_summarize(self, scores: list[Any]) -> dict:
        score_dict = {}
        score = scores[0]
        if isinstance(score, bool):
            score_dict['true_count'] = scores.count(True)
            score_dict['true_rate'] = scores.count(True) / len(scores)
        elif isinstance(score, (int, float)):
            score_dict['mean'] = sum(scores) / len(scores)
            score_dict['min'] = min(scores)
            score_dict['max'] = max(scores)
            # Only calculate standard deviation if there are at least 2 data points
            if len(scores) >= 2:
                score_dict['std'] = statistics.stdev(scores)
            else:
                score_dict['std'] = 0.0
        elif isinstance(score, dict):
            all_keys = list(
                dict.fromkeys([k for score in scores if isinstance(score, dict) for k in score.keys()])
            )
            for k in all_keys:
                score_dict[k] = self._do_summarize([score[k] for score in scores if k in score])
        return score_dict

    def summarize(self, eval_case_results: list[EvalCaseResult], repeat_times: int) -> Optional[dict]:
        '''
        Summarize the scores for all metrics.
        '''
        logger.info(f"eval_case_results: {eval_case_results}")
        if not eval_case_results or not eval_case_results[0].score_rows or self.name not in eval_case_results[0].score_rows:
            return {}

        # my all metric score results of all cases
        metric_scores = {}
        # group eval case results by eval_case_id
        case_groups = {}
        for result in eval_case_results:
            case_groups.setdefault(result.eval_case_id, []).append(result)

            scorer_result = result.score_rows.get(self.name)
            if not scorer_result or not hasattr(scorer_result, 'metric_results'):
                continue
            for metric_name, metric_result in scorer_result.metric_results.items():
                if metric_name not in metric_scores:
                    metric_scores[metric_name] = []
                if isinstance(metric_result, dict) and 'value' in metric_result:
                    metric_scores[metric_name].append(metric_result['value'])
                elif isinstance(metric_result, MetricValueType):
                    metric_scores[metric_name].append(metric_result)

        summary = {}
        for metric_name, scores in metric_scores.items():
            if scores:
                metric_summary = self._do_summarize(scores)
                if metric_name in self.eval_criterias:
                    eval_criteria = self.eval_criterias[metric_name]
                    if 'mean' in metric_summary:
                        metric_summary['eval_status'] = eval_criteria.judge(metric_summary['mean']).name
                    elif 'true_rate' in metric_summary:
                        metric_summary['eval_status'] = eval_criteria.judge(metric_summary['true_rate']).name

                # If there are repeated cases (same eval_case_id appears multiple times), calculate pass@k
                if repeat_times > 1:
                    # For each k value between 2 and repeat_times
                    for k in range(1, repeat_times + 1):
                        # Calculate pass@k
                        passed_count = 0
                        for _, results in case_groups.items():
                            # Check if any of the first k runs passed
                            if len(results) >= k:
                                # Take the first k runs
                                k_results = results[:k]
                                # Check if any of them passed
                                for result in k_results:
                                    scorer_result = result.score_rows.get(self.name)
                                    if scorer_result and hasattr(scorer_result, 'metric_results'):
                                        metric_result = scorer_result.metric_results.get(metric_name)
                                        if isinstance(metric_result, dict) and 'eval_status' in metric_result:
                                            if metric_result['eval_status'] == EvalStatus.PASSED:
                                                passed_count += 1
                                                break

                        # Add pass@k to the summary
                        pass_at_k = passed_count / len(case_groups) if case_groups else 0
                        metric_summary[f'pass@{k}'] = pass_at_k

                summary[metric_name] = metric_summary
        return summary


class Evaluator(Generic[EvalCaseDataType]):
    '''
    The base class of evaluator.
    '''

    def __init__(self,
                 scorers: list[Scorer] = None,
                 prepare_dataset: Optional[Callable[[EvalDataset], List[dict]]] = None,
                 repeat_times: int = 1,
                 parallel_num: int = 1,
                 skip_passed_cases: bool = False,
                 skip_passed_on_metrics: list[str] = None):
        self.scorers = scorers or []
        # preprocess the dataset
        self.prepare_dataset = prepare_dataset
        # repeat run example times
        self.repeat_times = repeat_times
        # evaluate parallelism
        self.parallel_num = parallel_num
        # whether to skip cases that have already passed
        self.skip_passed_cases = skip_passed_cases
        # Skip cases when the metric in the list have passed, if None, skip cases only all metrics have passed
        self.skip_passed_on_metrics = skip_passed_on_metrics or []
        # set to track eval_case_ids that have already passed, key is metric_name, value is a set of eval_case_ids that have already passed on this metric
        self._passed_cases = dict[str, set[str]]()
        # lock to protect access to _passed_cases in async environment
        self._passed_cases_lock = asyncio.Lock()

    def _default_prepare_dataset(self, dataset: EvalDataset) -> List[EvalDataCase[EvalCaseDataType]]:
        return dataset.eval_cases

    async def _evaluate_in_task(self, eval_target: EvalTarget[EvalCaseDataType], dataset: Iterable[EvalDataCase[EvalCaseDataType]], evaluate_fun: Callable[[int, EvalTarget[EvalCaseDataType], EvalDataCase[EvalCaseDataType]], Awaitable[dict]]):
        # create a semaphore to limit the parallelism
        semaphore: asyncio.Semaphore = asyncio.Semaphore(self.parallel_num)
        dataset_iter = iter(dataset)
        running_tasks: Set[asyncio.Task] = set()
        index = 0

        async def __evaluate_fun(index: int, eval_target: EvalTarget[EvalCaseDataType], input: EvalDataCase[EvalCaseDataType]) -> dict:
            async with semaphore:
                return await evaluate_fun(index, eval_target, input)

        def __create_eval_task():
            nonlocal dataset_iter
            nonlocal index
            nonlocal running_tasks
            try:
                input = next(dataset_iter)
                running_tasks.add(asyncio.create_task(__evaluate_fun(index, eval_target, input)))
                index += 1
            except StopIteration:
                return None

        try:
            for _ in range(self.parallel_num):
                __create_eval_task()

            while running_tasks:
                done, running_tasks = await asyncio.wait(running_tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    result = task.result()
                    yield result
                    __create_eval_task()
        except Exception as e:
            for task in running_tasks:
                task.cancel()
            raise e

    async def _should_skip_predict(self, input: EvalDataCase[EvalCaseDataType]) -> bool:
        """Check if we should skip this case because it has already passed.

        Args:
            input: the input data.

        Returns:
            True if we should skip this case, False otherwise.
        """
        should_skip = False
        if self.skip_passed_cases:
            # If skip_passed_on_metrics is empty, we need to check all metrics have passed
            # If skip_passed_on_metrics is specified, check only those metrics
            metrics_to_check = self.skip_passed_on_metrics
            if not metrics_to_check:
                for scorer in self.scorers:
                    metrics_to_check.extend(scorer.list_eval_metrics())
            print(f"metrics_to_check: {metrics_to_check}, passed_cases:{self._passed_cases}, id={input.eval_case_id}")
            async with self._passed_cases_lock:
                # If there are metrics to check, verify if all of them have passed for this case
                if metrics_to_check:
                    # Check if all metrics_to_check have passed
                    if all(input.eval_case_id in self._passed_cases.get(metric_name, set()) for metric_name in metrics_to_check):
                        should_skip = True
                        logger.warning(f"Skipping case {input.eval_case_id} which has already passed all required metrics")
        return should_skip

    async def run_single_case(self, index: int, eval_target: EvalTarget[EvalCaseDataType], input: EvalDataCase[EvalCaseDataType]) -> EvalCaseResult:
        """Run a single case.

        Args:
            eval_target: the evaluated object.
            input: the input data.

        Returns:
            execute result
        """
        # Check if we should skip this case because it has already passed

        if await self._should_skip_predict(input):
            # Return an empty result with the same case ID
            return EvalCaseResult(index=index,
                                  input=input,
                                  eval_case_id=input.eval_case_id,
                                  eval_dataset_id=input.eval_dataset_id,
                                  output={},
                                  score_rows={})

        # Proceed with normal evaluation if not skipped
        start_time = time.time()
        output = await eval_target.predict(index, input)
        end_time = time.time()
        time_cost_ms = (end_time - start_time) * 1000
        output['_time_cost_ms'] = time_cost_ms
        score_rows = {}

        for scorer in self.scorers:
            scorer_result = await scorer.scorer_and_judge(index, input, output)
            score_rows[scorer.name] = scorer_result

            # Record passed metrics if skip_passed_on_metrics is specified
            if self.skip_passed_cases:
                for metric_name, metric_result in scorer_result.metric_results.items():
                    if metric_result.get('eval_status') == EvalStatus.PASSED:
                        # Add to passed cases if it passed on this metric
                        async with self._passed_cases_lock:
                            self._passed_cases.setdefault(metric_name, set()).add(input.eval_case_id)

        return EvalCaseResult(index=index,
                              input=input,
                              eval_case_id=input.eval_case_id,
                              eval_dataset_id=input.eval_dataset_id,
                              output=output,
                              score_rows=score_rows)

    async def evaluate(self, dataset: EvalDataset, eval_target: EvalTarget[EvalCaseDataType] = NoActionEvalTarget()) -> EvalResult:
        """Evaluate the dataset/llm/agent.

        Returns:
            EvaluationResult
        """
        if self.prepare_dataset:
            input_dataset = self.prepare_dataset(dataset)
        else:
            input_dataset = self._default_prepare_dataset(dataset)

        input_dataset_chain = chain.from_iterable(repeat(input_dataset, self.repeat_times))
        details = []

        # Calculate total number of cases for progress bar
        total_cases = len(input_dataset) * self.repeat_times

        # Use tqdm if available
        if HAS_TQDM:
            progress_bar = tqdm(total=total_cases, desc="Evaluating", unit="case")
            async for result_row in self._evaluate_in_task(eval_target, input_dataset_chain, self.run_single_case):
                details.append(result_row)
                progress_bar.update(1)
            progress_bar.close()
        else:
            # Fallback without progress bar
            async for result_row in self._evaluate_in_task(eval_target, input_dataset_chain, self.run_single_case):
                details.append(result_row)

        details.sort(key=lambda x: x.index)
        summary = {}
        for scorer in self.scorers:
            summary[scorer.name] = scorer.summarize(details, self.repeat_times)
        return EvalResult(
            eval_dataset_id=dataset.eval_dataset_id,
            run_id=dataset.run_id,
            summary=summary,
            eval_case_results=details,
        )
