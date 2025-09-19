import abc
import time
import uuid
from ast import Set
import statistics
import asyncio
from typing import Any, Iterable, Optional, List, Callable, Awaitable, TypeVar, Generic
from enum import Enum
from dataclasses import dataclass, field
from itertools import chain, repeat
from aworld.logs.util import logger

EvalCaseDataType = TypeVar('EvalCaseDataType')


@dataclass
class EvalCriteria:
    '''
    Evaluation criteria.
    '''
    metric_name: str = field(default_factory=str)
    # full class name of scorer class, e.g. aworld.evaluations.scorers.label_distribution.LabelDistributionScorer
    # if not specified, will use the first scorer class in the registry for the metric name
    scorer_class: Optional[str] = field(default_factory=str)
    prompt: str = field(default_factory=str)
    max_value: float = field(default=float('inf'))
    min_value: float = field(default=-float('inf'))
    threshold: float = field(default=0.0)


@dataclass
class EvalRunConfig:
    '''
    Evaluation run config.
    '''
    # full class name of eval target, e.g. aworld.evaluations.base.EvalTarget
    eval_target_full_class_name: str = field(default_factory=str)
    eval_target_config: dict = field(default_factory=dict)
    eval_criterias: list[EvalCriteria] = field(default_factory=list)
    # eval dataset id or file path, file path should be a jsonl file
    eval_dataset_id_or_file_path: str = field(default_factory=str)
    repeat_times: int = field(default=1)
    eval_parallelism: int = field(default=1)


@dataclass
class EvalRun:
    '''
    Evaluation run.
    '''
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    run_name: str = field(default_factory=str)
    create_time: float = field(default_factory=lambda: time.time())
    config: EvalRunConfig = field(default=None)


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


class EvalStatus(Enum):
    PASSED = 1
    FAILED = 2
    NOT_EVALUATED = 3


@dataclass
class EvalCaseResult:
    index: int = 0
    eval_case_id: str = field(default_factory=str)
    eval_dataset_id: str = field(default_factory=str)
    input: dict = field(default_factory=dict)
    output: dict = field(default_factory=dict)
    eval_status: EvalStatus = field(default_factory=lambda: EvalStatus.NOT_EVALUATED)
    score_rows: dict = field(default_factory=dict)
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

    @abc.abstractmethod
    async def predict(self, input: EvalDataCase[EvalCaseDataType]) -> dict:
        """execute the llm/agent.

        Returns:
            execute result
        """
        raise NotImplementedError


class NoActionEvalTarget(EvalTarget[EvalCaseDataType]):
    async def predict(self, input: EvalDataCase[EvalCaseDataType]) -> dict:
        return {}


class Scorer(abc.ABC, Generic[EvalCaseDataType]):
    '''
    The base class of scorer.
    '''

    def __init__(self, name: str = None):
        self.name = name or self.__class__.__name__
        self.eval_criterias = {}

    def __str__(self) -> str:
        return self.name

    def add_eval_criteria(self, eval_criteria: EvalCriteria) -> None:
        '''
            Add eval criteria.
        '''
        self.eval_criterias[eval_criteria.metric_name] = eval_criteria

    @abc.abstractmethod
    async def score(self, index: int, input: EvalDataCase[EvalCaseDataType], output: dict) -> EvalCaseResult:
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
            score_dict['std'] = statistics.stdev(scores)
        elif isinstance(score, dict):
            all_keys = list(
                dict.fromkeys([k for score in scores if isinstance(score, dict) for k in score.keys()])
            )
            for k in all_keys:
                score_dict[k] = self._do_summarize([score[k] for score in scores if k in score])
        return score_dict

    def summarize(self, result_rows: list[EvalCaseResult]) -> Optional[dict]:
        '''
            summarize the score rows.
        '''
        logger.info(f"result_rows: {result_rows}")
        if not result_rows or not result_rows[0].score_rows or self.name not in result_rows[0].score_rows:
            return {}
        my_scores = [result.score_rows[self.name] for result in result_rows]
        return self._do_summarize(my_scores)


class Evaluator(abc.ABC, Generic[EvalCaseDataType]):
    '''
    The base class of evaluator.
    '''

    def __init__(self,
                 scorers: list[Scorer] = None,
                 prepare_dataset: Optional[Callable[[EvalDataset], List[dict]]] = None,
                 repeat_times: int = 1,
                 eval_parallelism: int = 1):
        self.scorers = scorers or []
        # preprocess the dataset
        self.prepare_dataset = prepare_dataset
        # repeat run example times
        self.repeat_times = repeat_times
        # evaluate parallelism
        self.eval_parallelism = eval_parallelism

    def _default_prepare_dataset(self, dataset: EvalDataset) -> List[EvalDataCase[EvalCaseDataType]]:
        return dataset.eval_cases

    async def _evaluate_in_task(self, eval_target: EvalTarget[EvalCaseDataType], dataset: Iterable[EvalDataCase[EvalCaseDataType]], evaluate_fun: Callable[[int, EvalTarget[EvalCaseDataType], EvalDataCase[EvalCaseDataType]], Awaitable[dict]]):
        # create a semaphore to limit the parallelism
        semaphore: asyncio.Semaphore = asyncio.Semaphore(self.eval_parallelism)
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
            for _ in range(self.eval_parallelism):
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

    async def run_single_case(self, index: int, eval_target: EvalTarget[EvalCaseDataType], input: EvalDataCase[EvalCaseDataType]) -> EvalCaseResult:
        """Run a single case.

        Args:
            eval_target: the evaluated object.
            input: the input data.

        Returns:
            execute result
        """
        output = await eval_target.predict(input)
        score_rows = {}
        for scorer in self.scorers:
            score_rows[scorer.name] = await scorer.score(index, input, output)
        return EvalCaseResult(index=index, input=input, output=output, score_rows=score_rows)

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
        async for result_row in self._evaluate_in_task(eval_target, input_dataset_chain, self.run_single_case):
            details.append(result_row)

        details.sort(key=lambda x: x.index)
        summary = {}
        for scorer in self.scorers:
            summary[scorer.name] = scorer.summarize(details)
        return EvalResult(
            eval_dataset_id=dataset.eval_dataset_id,
            run_id=dataset.run_id,
            summary=summary,
            eval_case_results=details,
        )


class EvaluateRunner(abc.ABC):

    @abc.abstractmethod
    async def eval_run(self, eval_config: EvalRunConfig) -> EvalResult:
        """Run the evaluation.

        Returns:
            EvaluationResult
        """
        raise NotImplementedError("run method not implemented")
