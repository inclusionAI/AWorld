import os
import time
import json
import uuid
import asyncio
import logging
import traceback
import functools
from typing import Callable
from asyncio import Queue

from aworld.utils.diagnostic_tools.configs import settings
from aworld.utils.diagnostic_tools.diagnostic_data import DiagnosticData, RenderData
from aworld.utils.diagnostic_tools.utils import get_logger, convert_dict_2_str
from aworld.core.common import Observation

diagnostic_logger = get_logger(name=__name__,
                               log_file=settings.diagnostic_log_file)


class Diagnostic:
    _queue = Queue(maxsize=500)

    def __init__(self,
                 component_name: str | None = None,
                 description: str | None = None,
                 exclude_args: set[str] | None = None,
                 exclude_results: set[str] | None = None,
                 max_arg_length: int = settings.default_max_arg_length,
                 max_result_length: int = settings.default_max_result_length):
        self.component_name = component_name or ""
        self.description = description or ""
        self.exclude_args = exclude_args or set()
        self.exclude_args.add('self')
        self.exclude_results = exclude_results or set()
        self.max_arg_length = max_arg_length
        self.max_result_length = max_result_length

    def __call__(self, func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):
            return self._async_wrapper(func)
        return self._sync_wrapper(func)

    def _async_wrapper(self, func: Callable) -> Callable:

        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> any:
            return await self._execute(func, args, kwargs)

        return wrapper

    def _sync_wrapper(self, func: Callable) -> Callable:

        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> any:
            return self._execute(func, args, kwargs)

        return wrapper

    def _execute(self, func: Callable, args: tuple, kwargs: dict) -> any:
        if asyncio.iscoroutinefunction(func):
            return self._execute_async(func, args, kwargs)

        diagnostic = DiagnosticData()
        module_name = func.__module__ if func.__module__ != '__main__' else os.path.splitext(
            os.path.basename(func.__code__.co_filename))[0]
        diagnostic.componentName = self.component_name or f"{module_name}-{func.__name__}"
        info = {'args': self._process_args(func, args, kwargs)}
        try:
            result = func(*args, **kwargs)
            info["result"] = self._process_result(result)
            return result
        except Exception as e:
            diagnostic.success = False
            info["error"] = {
                "type": type(e).__name__,
                "message": str(e),
                "traceback": traceback.format_exc()
            }
            logging.warning(f'{e}')
            raise
        finally:
            diagnostic.endTime = int(time.time() * 1000)
            diagnostic.info = convert_dict_2_str(info)
            self.record_diagnostic(diagnostic)

    async def _execute_async(self, func: Callable, args: tuple,
                             kwargs: dict) -> any:
        diagnostic = DiagnosticData()
        module_name = func.__module__ if func.__module__ != '__main__' else os.path.splitext(
            os.path.basename(func.__code__.co_filename))[0]
        diagnostic.componentName = self.component_name or f"{module_name}-{func.__name__}"
        info = {'args': self._process_args(func, args, kwargs)}

        try:
            result = await func(*args, **kwargs)
            info["result"] = self._process_result(result)
            return result
        except Exception as e:
            diagnostic.success = False
            info["error"] = {
                "type": type(e).__name__,
                "message": str(e),
                "traceback": traceback.format_exc()
            }
            logging.warning(f'{e}')
            raise
        finally:
            diagnostic.endTime = int(time.time() * 1000)
            diagnostic.info = convert_dict_2_str(info)
            self.record_diagnostic(diagnostic)

    def _process_args(self, func: Callable, args: tuple, kwargs: dict) -> dict:
        processed_args = {}
        try:
            func_params = func.__code__.co_varnames[:func.__code__.co_argcount]
            for i, arg in enumerate(args):
                if i < len(func_params):
                    arg_name = func_params[i]
                    if arg_name not in self.exclude_args:
                        processed_args[arg_name] = self._truncate_value(arg)
            for key, value in kwargs.items():
                if key not in self.exclude_args:
                    processed_args[key] = self._truncate_value(value)
        except Exception as e:
            logging.warning(f'{e}')
        return processed_args

    def _process_result(self, result: any) -> any:
        try:
            if isinstance(result, dict):
                return {
                    k: self._truncate_value(v)
                    for k, v in result.items() if k not in self.exclude_results
                }
            elif isinstance(result, (list, tuple)):
                return [self._truncate_value(v) for v in result]
            return self._truncate_value(result)
        except Exception as e:
            logging.warning(f'{e}')
        return f'{result}'

    def _truncate_value(self, value: any) -> str:
        try:
            if isinstance(value, (str, bytes)):
                str_value = str(value)
                if len(str_value) > self.max_arg_length:
                    return f"{str_value[:self.max_arg_length]}... (truncated)"
            elif isinstance(value, (list, tuple)):
                return [self._truncate_value(v) for v in value]
            elif isinstance(value, dict):
                return {
                    k: self._truncate_value(v)
                    for k, v in value.items() if k not in self.exclude_results
                }
            elif value and hasattr(value, 'model_dump_json'):
                return value.model_dump_json()
            else:
                return f'{value}'
        except Exception as e:
            logging.warning(f'{e}')
        return f'{value}'

    @classmethod
    def record_diagnostic(cls, diagnostic: DiagnosticData) -> None:
        try:
            if isinstance(diagnostic, DiagnosticData):
                if diagnostic.success:
                    diagnostic_logger.info(f'{diagnostic.model_dump_json()}')
                else:
                    diagnostic_logger.warning(f'{diagnostic.model_dump_json()}')
                try:
                    # Use put_nowait to avoid blocking
                    cls._queue.put_nowait(diagnostic)
                except asyncio.QueueFull:
                    logging.warning(
                        "Diagnostic queue is full. Discarding diagnostic data."
                    )
            else:
                diagnostic_logger.warning('record diagnostic failed.')
        except Exception as e:
            logging.warning(f'{e}')

    @classmethod
    async def get_diagnostics(cls,
                              timeout: float = None) -> list | None:
        """
        Get all diagnostic data from the queue.
        
        Args:
            timeout: Timeout duration in seconds for waiting for the first data item. None means wait indefinitely until data is available
            
        Returns:
            list[DiagnosticData]: List of diagnostic data, returns empty list if timeout or queue not initialized
        """
        if not cls._queue:
            return None

        try:
            results = []
            # Wait for the first data item (supports timeout)
            if timeout is not None:
                first_item = await asyncio.wait_for(cls._queue.get(),
                                                    timeout=timeout)
                render_data = Diagnostic.convert_diagnostic(first_item)
                if render_data:
                    results.append(render_data)
            else:
                first_item = await cls._queue.get()
                render_data = Diagnostic.convert_diagnostic(first_item)
                if render_data:
                    results.append(render_data)

            # Get all remaining data in a non-blocking way
            while True:
                try:
                    item = cls._queue.get_nowait()
                    render_data = Diagnostic.convert_diagnostic(item)
                    if render_data:
                        results.append(render_data)
                except asyncio.QueueEmpty:
                    break
            return [result.model_dump() for result in results]
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            logging.warning(f"Error getting diagnostic data from queue: {e}")
            return None

    @classmethod
    def get_diagnostic_nowait(cls) -> str | None:
        """
        Get diagnostic data from the queue in a non-blocking way.
        
        Returns:
            DiagnosticData: Diagnostic data, returns None if queue is empty or not initialized
        """
        if not cls._queue:
            return None
        try:
            diagnostic_data = cls._queue.get_nowait()
            return diagnostic_data.model_dump_json()
        except asyncio.QueueEmpty:
            return None
        except Exception as e:
            logging.warning(f"Error getting diagnostic data from queue: {e}")
            return None

    @classmethod
    def has_items(cls) -> bool:
        """
        Check if queue has diagnostic data.

        Returns:
            bool: Returns True if queue has data, otherwise False
        """
        if not cls._queue:
            return False
        return not cls._queue.empty()

    @classmethod
    def activate_queue(cls,
                       queue_id: str = f'{uuid.uuid4().hex}',
                       maxsize: str = 100) -> None:
        """
        Activate diagnostic data queue
        """
        cls.reset_queue()

    @classmethod
    def reset_queue(cls):
        """
        Reset diagnostic data queue
        """
        cls._queue.empty()

    @staticmethod
    def convert_diagnostic(diagnostic: DiagnosticData) -> RenderData | None:
        if diagnostic.componentName.endswith("agent-policy"):
            return Diagnostic.convert_policy_diagnostic(diagnostic)
        elif diagnostic.componentName.endswith("-step"):
            return Diagnostic.convert_tool_diagnostic(diagnostic)

    @staticmethod
    def convert_policy_diagnostic(diagnostic: DiagnosticData) -> RenderData:
        diagnostic_info = json.loads(diagnostic.info) if diagnostic.info else {}
        results = diagnostic_info.get('result', [])
        if not results:
            return None
        args_info = diagnostic_info.get('args', {}).get('info', {})
        args_info = json.loads(args_info) if isinstance(args_info, str) else args_info
        render_data = RenderData(type=args_info.get('source_type', 'agent'),
                                 agent_name=args_info.get('agent_name', '') if args_info.get('agent_name',
                                                                                             '') else 'PlanAgent',
                                 tool_name=args_info.get('tool_name', ''),
                                 action_name=args_info.get('action_name', ''),
                                 result=[],
                                 status='success' if diagnostic.success else 'error'
                                 )
        for result_str in results:
            result = json.loads(result_str)
            tool_name = result.get('tool_name')
            action_name = result.get('action_name')
            agent_name = result.get('agent_name')
            params = result.get('params', {})
            policy_info = result.get('policy_info', "")
            render_data.result.append({'info': policy_info,
                                       'tool_name': tool_name,
                                       'action_name': action_name,
                                       'agent_name': agent_name,
                                       'params': params})
        return render_data

    @staticmethod
    def convert_tool_diagnostic(diagnostic: DiagnosticData) -> RenderData | None:
        diagnostic_info = json.loads(diagnostic.info) if diagnostic.info else {}
        results = diagnostic_info.get('result', [])
        if not results:
            return None
        observation_str = results[0]
        observation = json.loads(observation_str) if isinstance(observation_str, str) else observation_str
        args_info_str = diagnostic_info.get('args', {}).get('info', {})
        args_info = json.loads(args_info_str) if isinstance(args_info_str, str) else args_info_str
        return RenderData(type=args_info.get('source_type', 'action'),
                          agent_name=args_info.get('agent_name', ''),
                          tool_name=args_info.get('tool_name', ''),
                          action_name=args_info.get('action_name', ''),
                          result=[observation],
                          status='success' if diagnostic.success else 'error'
                          )
