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

from aworld.logs.util import logger
from aworld.utils.common import sync_exec

LOCAL = "local"
SPARK = "spark"
RAY = "ray"
ODPS = "odps"
K8S = "k8s"


class RuntimeBackend(object):
    """Lightweight wrapper of computing engine runtime."""

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


class TaskRuntimeBackend(RuntimeBackend):
    """The runtime base class for task execution."""

    @abc.abstractmethod
    def execute(self, funcs: List[Callable[..., Any]], *args, **kwargs):
        raise NotImplementedError("Base task execute not implemented!")


class LocalRuntime(TaskRuntimeBackend):
    """Local runtime key is 'local', and execute tasks in local machine.

    Local runtime is used to verify or test locally.
    """

    def _build_context(self):
        self.runtime = self

    def func_wrapper(self, func, *args, **kwargs):
        """Function is used to adapter computing form."""

        if inspect.iscoroutinefunction(func):
            res = sync_exec(func, *args, **kwargs)
        else:
            res = func(*args, **kwargs)
        return res

    async def execute(self, funcs: List[Callable[..., Any]], *args, **kwargs):
        # opt of the one task process
        if len(funcs) == 1:
            func = funcs[0]
            if inspect.iscoroutinefunction(func):
                res = await func(*args, **kwargs)
            else:
                res = func(*args, **kwargs)
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
                futures.append(pool.submit(self.func_wrapper, func, *args, **kwargs))

        results = {}
        for future in futures:
            future: Future = future
            res = future.result()
            results[res.id] = res
        return results


class K8sRuntime(LocalRuntime):
    """K8s runtime key is 'k8s', and execute tasks in kubernetes cluster."""


class KubernetesRuntime(LocalRuntime):
    """kubernetes runtime key is 'kubernetes', and execute tasks in kubernetes cluster."""


class SparkRuntime(TaskRuntimeBackend):
    """Spark runtime key is 'spark', and execute tasks in spark cluster.

    Note: Spark runtime must in driver end.
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
            if 'PYSPARK_PYTHON' not in os.environ:
                raise Exception('`PYSPARK_PYTHON` need to set first in environment variables.')

            spark_builder = spark_builder.master('local[2]').config('spark.executor.instances', '1')

        self.runtime = spark_builder.appName(conf.job_name).getOrCreate()

    def args_process(self, *args):
        re_args = []
        for arg in args:
            if arg:
                options = self.runtime.sparkContext.broadcast(arg)
                arg = options.value
            re_args.append(arg)
        return re_args

    async def execute(self, funcs: List[Callable[..., Any]], *args, **kwargs):
        re_args = self.args_process(*args)
        res_rdd = self.runtime.sparkContext.parallelize(funcs, len(funcs)).map(
            lambda func: func(*re_args, **kwargs))

        res_list = res_rdd.collect()
        results = {res.id: res for res in res_list}
        return results


class RayRuntime(TaskRuntimeBackend):
    """Ray runtime key is 'ray', and execute tasks in ray cluster.

    Ray runtime in TaskRuntimeBackend only execute function (stateless), can be used to custom
    resource allocation and communication etc. advanced features.
    """

    def __init__(self, engine_options):
        super(RayRuntime, self).__init__(engine_options)

    def _build_context(self):
        import ray

        if not ray.is_initialized():
            ray.init()

        self.runtime = ray
        self.num_executors = self.conf.get('num_executors', 1)
        logger.info("ray init finished, executor number {}".format(str(self.num_executors)))

    def execute(self, funcs: List[Callable[..., Any]], *args, **kwargs):
        @self.runtime.remote
        def fn_wrapper(fn, *args):
            real_args = [arg for arg in args if not isinstance(arg, MethodType)]
            return fn(*real_args, **kwargs)

        params = []
        for arg in args:
            params.append([arg] * len(funcs))

        ray_map = lambda func, fn: [func.remote(x, *y) for x, *y in zip(fn, *params)]
        return self.runtime.get(ray_map(fn_wrapper, funcs))


class ODPSRuntime(TaskRuntimeBackend):
    """ODPS runtime key is 'odps', and execute tasks in ODPS cluster.

    ODPS runtime can reused or create more instances to use.
    """

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
