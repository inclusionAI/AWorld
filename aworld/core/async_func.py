# coding: utf-8
import asyncio
from functools import wraps
from typing import Callable, Optional, Union, Any, Dict

from core.task import Task


class FunctionTask(Task):
    def __init__(self, function: Callable[..., Any], *args: Any, **kwargs: Dict[str, Any]) -> None:
        super(FunctionTask, self).__init__(None, *args, **kwargs)
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.done: bool = False
        self.error: bool = False
        self.result: Optional[Any] = None
        self.exception: Optional[Exception] = None

    def __call__(self) -> None:
        try:
            self.result = self.function(*self.args, **self.kwargs)
        except Exception as e:
            self.error = True
            self.exception = e
        self.done = True

    def run(self):
        self.__call__()


def async_decorator(*func, delay: Optional[Union[int, float]] = 0.5) -> Callable:
    def wrapper(function: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(function)
        async def inner_wrapper(*args: Any, **kwargs: Any) -> Any:
            sleep_time = 0 if delay is None else delay
            task = FunctionTask(function, *args, **kwargs)
            # TODO: Use thread pool to process task
            task.start()
            if task.error:
                raise task.exception
            await asyncio.sleep(sleep_time)
            return task.result

        return inner_wrapper

    if not func:
        return wrapper
    else:
        if asyncio.iscoroutinefunction(func[0]):
            # coroutine function, return itself
            return func[0]
        return wrapper(func[0])

def async_func(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    async def inner_wrapper(*args: Any, **kwargs: Any) -> Any:
        task = FunctionTask(function, *args, **kwargs)
        task.start()
        if task.error:
            raise task.exception
        return task.result

    if asyncio.iscoroutinefunction(function):
        # coroutine function, return itself
        return function
    return inner_wrapper
