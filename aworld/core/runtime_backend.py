# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import inspect
import os
from concurrent.futures import Future
from concurrent.futures.process import ProcessPoolExecutor
from types import MethodType
from typing import List, Callable, Any

from aworld.core.common import Config
from aworld.core.task import Task

from aworld.logs.util import logger
from aworld.utils.common import sync_exec

LOCAL = "local"
SPARK = "spark"
RAY = "ray"
ODPS = "odps"


class RuntimeBackend(object):
    """Lightweight wrapper of computing and storage engine runtime."""

    __metaclass__ = abc.ABCMeta

    def __init__(self, conf: Config):
        """Engine runtime instance initialize."""
        self.conf = conf
        self.runtime = None
        register(conf.name, self)

    def build_context(self):
        """Create computing or storage engine runtime context.

        If create more times in the same runtime instance, will get the same context instance, like getOrCreate.
        """
        if self.runtime is not None:
            return self
        self._build_context()
        return self

    @abc.abstractmethod
    def _build_context(self):
        raise NotImplementedError("Base _build_context not implemented!")

    @abc.abstractmethod
    def execute(self, tasks: List[Task]):
        raise NotImplementedError("Base execute not implemented!")


class LocalRuntime(RuntimeBackend):
    """Local runtime is used to verify or test locally."""

    def _build_context(self):
        self.runtime = self

    def func_wrapper(self, func):
        """Function is used to adapter computing form."""

        if inspect.iscoroutinefunction(func):
            res = sync_exec(func, )
        else:
            res = func()
        return res

    async def execute(self, funcs: List[Callable[..., Any]]):
        # opt of the one task process
        if len(funcs) == 1:
            func = funcs[0]
            if inspect.iscoroutinefunction(func):
                res = await func()
            else:
                res = func()
            return {res.id: res}

        num_executor = self.conf.get('num_executor', os.cpu_count() - 1)
        num_process = len(funcs)
        if num_process > num_executor:
            num_process = num_executor

        if num_process <= 0:
            num_process = 1

        futures = []
        with ProcessPoolExecutor(num_process) as pool:
            for func in funcs:
                futures.append(pool.submit(self.func_wrapper, func))

        results = {}
        for future in futures:
            future: Future = future
            res = future.result()
            results[res.id] = res
        return results


class SparkRuntime(RuntimeBackend):
    """Spark runtime must keep unique and RUNTIME key is 'spark'.

    Spark runtime must in driver end, the implement is AntSpark runtime.
    """

    def __init__(self, engine_options):
        super(SparkRuntime, self).__init__(engine_options)

    def _build_context(self):
        from pyspark.sql import SparkSession

        conf = self.conf
        is_local = getattr(conf, 'is_local', False)
        logger.info('build runtime is_local:{}'.format(is_local))
        spark_builder = SparkSession.builder
        if is_local:
            if getattr(conf, 'use_python', False) and hasattr(conf, "python_command"):
                assert (conf.python_command is
                        not None), "e.g. python_command: /anaconda2/envs/new_env/bin/python3"
                os.environ["PYSPARK_PYTHON"] = conf.python_command
            else:
                spark_builder = spark_builder.master('local[2]').config('spark.executor.instances', '1')

        self.runtime = spark_builder.appName(conf.job_name).getOrCreate()


class RayRuntime(RuntimeBackend):
    """Ray runtime is used to custom resources allocation and communication etc. advance features."""

    def __init__(self, engine_options):
        super(RayRuntime, self).__init__(engine_options)

    def _build_context(self):
        import ray

        if not ray.is_initialized():
            ray.init()

        self.runtime = ray
        self.num_executors = self.conf.get('num_executors', 1)
        logger.info("ray init finished, executor number {}".format(str(self.num_executors)))


class ODPSRuntime(RuntimeBackend):
    """ODPS runtime can reused or create more instances to use."""

    def __init__(self, engine_options):
        super(ODPSRuntime, self).__init__(engine_options)

    def _build_context(self):
        import odps

        if hasattr(self.conf, 'options'):
            for k, v in self.conf.options.items():
                odps.options.register_option(k, v)
        else:
            self.runtime = odps.ODPS(self.conf.get('accessid', self.conf.get('access_id', None)),
                                     self.conf.get('accesskey', self.conf.get('access_key', None)),
                                     self.conf.project, self.conf.endpoint)


RUNTIME = {}


def register(key, runtime_backend):
    if RUNTIME.get(key, None) is not None:
        logger.warning("{} runtime backend already exists.".format(key))
        return

    RUNTIME[key] = runtime_backend
    logger.info("register {}:{} success".format(key, runtime_backend))
