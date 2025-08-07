import abc
import statistics
import asyncio
from typing import Any, Iterable, Optional, List
from dataclasses import dataclass, field
from itertools import chain, repeat


@dataclass
class EvaluationResultRow:
    index: int = 0
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


class NoActionEvaluatable(Evaluatable):
    async def predict(self, input: dict) -> dict:
        return {}


class Scorer(abc.ABC):
    '''
    The base class of scorer.
    '''

    def __init__(self, name: str = None):
        self.name = name or self.__class__.__name__

    def __str__(self) -> str:
        return self.name

    @abc.abstractmethod
    async def score(self, index: int, input: dict, output: dict) -> Any:
        """score the execute result.

        Args:
            index: the index of the example.
            input: the input of the example.
            output: the output of the example.

        Returns:
            score: the score of the example.
        """
        raise NotImplementedError

    def summarize(self, result_rows: list[EvaluationResultRow]) -> Optional[dict]:
        '''
            summarize the score rows.
        '''
        if not result_rows or not result_rows[0].score_rows or self.name not in result_rows[0].score_rows:
            return {}
        my_scores = [result.score_rows[self.name] for result in result_rows]
        score_dict = {}
        score = my_scores[0]
        if isinstance(score, bool):
            score_dict['true_count'] = my_scores.count(True)
            score_dict['true_rate'] = my_scores.count(True) / len(my_scores)
        elif isinstance(score, (int, float)):
            score_dict['mean'] = sum(my_scores) / len(my_scores)
            score_dict['min'] = min(my_scores)
            score_dict['max'] = max(my_scores)
            score_dict['std'] = statistics.stdev(my_scores)
        elif isinstance(score, dict):
            all_keys = list(
                dict.fromkeys([k for score in my_scores if isinstance(score, dict) for k in score.keys()])
            )
            for k in all_keys:
                score_dict[k] = self.summarize([score[k] for score in my_scores if k in score])
        return score_dict


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

    async def _evaluate_in_task(self, evaluatable: Evaluatable, dataset: Iterable[dict], evaluate_fun: callable[int, Evaluatable, dict]):
        # create a semaphore to limit the parallelism
        semaphore: asyncio.Semaphore = asyncio.Semaphore(self.eval_parallelism)
        dataset_iter = iter(dataset)
        running_tasks = []
        index = 0

        async def __evaluate_fun(index: int, evaluatable: Evaluatable, input: dict) -> dict:
            async with semaphore:
                return await evaluate_fun(index, evaluatable, input)

        def __create_eval_task():
            nonlocal dataset_iter
            nonlocal index
            try:
                input = next(dataset_iter)
                running_tasks.append(asyncio.create_task(__evaluate_fun(index, evaluatable, input)))
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

    async def run_single_case(self, index: int, evaluatable: Evaluatable, input: dict) -> EvaluationResultRow:
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
            score_rows[scorer.name] = await scorer.score(index, input, output)
        return EvaluationResultRow(index=index, input=input, output=output, score_rows=score_rows)

    async def evaluate(self, dataset: Dataset, evaluatable: Evaluatable = NoActionEvaluatable()) -> EvaluationResult:
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
        async for result_row in self._evaluate_in_task(evaluatable, input_dataset_chain, self.run_single_case):
            details.append(result_row)

        details.sort(key=lambda x: x.index)
        summary = {}
        for scorer in self.scorers:
            summary[scorer.name] = scorer.summarize(details)
        return EvaluationResult(summary=summary, details=details)
