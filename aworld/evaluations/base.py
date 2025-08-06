import abc
import statistics
import asyncio
from typing import Any, Iterable, Optional, List
from dataclasses import dataclass, field
from itertools import chain, repeat


class Evaluatable(abc.ABC):
    '''
    The base class of evaluated object.
    '''

    @abc.abstractmethod
    async def predict(self, input: dict) -> dict:
        """execute the llm/agent.

        Returns:
            execute result
        """
        raise NotImplementedError


class Scorer(abc.ABC):
    '''
    The base class of scorer.
    '''

    def __init__(self, name: str = None):
        self.name = name or self.__class__.__name__

    def __str__(self) -> str:
        return self.name

    @abc.abstractmethod
    async def score(self, input: dict, output: dict) -> Any:
        """score the execute result.

        Returns:
            score
        """
        raise NotImplementedError

    def summarize(self, score_rows: list) -> Optional[dict]:
        '''
            summarize the score rows.
        '''
        if not score_rows:
            return {}
        score_dict = {}
        score = score_rows[0]
        if isinstance(score, bool):
            score_dict['true_count'] = score_rows.count(True)
            score_dict['true_rate'] = score_rows.count(True) / len(score_rows)
        elif isinstance(score, (int, float)):
            score_dict['mean'] = sum(score_rows) / len(score_rows)
            score_dict['min'] = min(score_rows)
            score_dict['max'] = max(score_rows)
            score_dict['std'] = statistics.stdev(score_rows)
        elif isinstance(score, dict):
            all_keys = list(
                dict.fromkeys([k for score in score_rows if isinstance(score, dict) for k in score.keys()])
            )
            for k in all_keys:
                score_dict[k] = self.summarize([score[k] for score in score_rows if k in score])
        return score_dict


@dataclass
class EvaluationResultRow:
    input: dict = field(default_factory=dict)
    output: dict = field(default_factory=dict)
    score_rows: dict = field(default_factory=dict)


@dataclass
class EvaluationResult:
    '''
    Evaluation result.
    '''
    summary: dict = field(default_factory=dict)
    details: list[EvaluationResultRow] = field(default_factory=list)


@dataclass
class Dataset(abc.ABC):
    '''
    The base class of dataset.
    '''
    rows: List[dict] = field(default_factory=list)


class Evaluator(abc.ABC):
    '''
    The base class of evaluator.
    '''

    def __init__(self,
                 scorers: list[Scorer],
                 prepare_dataset: Optional[callable[Dataset, List[dict]]] = None,
                 repeat_times: int = 1,
                 eval_parallelism: int = 1):
        self.scorers = scorers
        # preprocess the dataset
        self.prepare_dataset = prepare_dataset
        # repeat run example times
        self.repeat_times = repeat_times
        # evaluate parallelism
        self.eval_parallelism = eval_parallelism

    def _default_prepare_dataset(self, dataset: Dataset) -> List[dict]:
        return dataset.rows

    async def _evaluate_in_task(self, evaluatable: Evaluatable, dataset: Iterable[dict], evaluate_fun: callable[Evaluatable, dict]):
        # create a semaphore to limit the parallelism
        semaphore: asyncio.Semaphore = asyncio.Semaphore(self.eval_parallelism)
        dataset_iter = iter(dataset)
        running_tasks = []

        async def __evaluate_fun(evaluatable: Evaluatable, input: dict) -> dict:
            async with semaphore:
                return await evaluate_fun(evaluatable, input)

        def __create_eval_task():
            nonlocal dataset_iter
            try:
                input = next(dataset_iter)
                running_tasks.append(asyncio.create_task(__evaluate_fun(evaluatable, input)))
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

    async def run_single_case(self, evaluatable: Evaluatable, input: dict) -> EvaluationResultRow:
        """Run a single case.

        Args:
            evaluatable: the evaluated object.
            input: the input data.

        Returns:
            execute result
        """
        output = await evaluatable.predict(input)
        score_rows = {}
        for scorer in self.scorers:
            score_rows[scorer.name] = await scorer.score(input, output)
        return EvaluationResultRow(input=input, output=output, score_rows=score_rows)

    async def evaluate(self, evaluatable: Evaluatable, dataset: Dataset) -> EvaluationResult:
        """Evaluate the dataset/task.

        Returns:
            EvaluationResult
        """
        if self.prepare_dataset:
            input_dataset = self.prepare_dataset(dataset)
        else:
            input_dataset = self._default_prepare_dataset(dataset)

        input_dataset_chain = chain.from_iterable(repeat(input_dataset, self.repeat_times))
        details = []
        async for result_row in self._evaluate_in_task(evaluatable, input_dataset_chain, self.run_single_case):
            details.append(result_row)

        summary = {}
        for scorer in self.scorers:
            summary[scorer.name] = scorer.summarize([result_row.score_rows[scorer.name] for result_row in details])
        return EvaluationResult(summary=summary, details=details)
