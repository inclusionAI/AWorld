import asyncio
import threading
from concurrent.futures import Future
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from aworld.logs.util import logger
from .loop_pool import SandboxLoopPool


class _SandboxContext:
    """
    Runtime context for a single sandbox:
    - loop: the dedicated asyncio loop chosen for this sandbox
    - queue: an async job queue processed on that loop
    - worker_task: single task that runs all jobs sequentially
    
    关键点：所有与该 sandbox 绑定的协程（包括 MCP connect / list_tools /
    call_tool / cleanup）都会在同一个 worker_task 里顺序执行，从而保证
    anyio cancel scope / async generator 的 enter / exit 发生在同一个 Task 上。
    """
    
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: "asyncio.Queue[Tuple[Callable[..., Awaitable[Any]], Tuple[Any, ...], Dict[str, Any], Future]]",
        worker_task: "asyncio.Task[Any]",
    ) -> None:
        self.loop = loop
        self.queue = queue
        self.worker_task = worker_task


class SandboxManager:
    """
    Manage sandbox-to-loop affinity and provide helpers to run work
    on the correct event loop for a given sandbox.
    
    与最初版本相比，这里不仅仅记录 sandbox -> loop 的映射，还为每个
    sandbox 维护一个“单 worker Task”，所有 MCP 相关操作都通过这个
    worker 顺序执行，避免了：
    
    - 在 Task A 上 enter async generator / cancel scope
    - 在 Task B 上 exit / aclose 同一个对象
    
    这正是 anyio / MCP 报
    `Attempted to exit cancel scope in a different task than it was entered in`
    和 `aclose(): asynchronous generator is already running` 的根源。
    """
    
    _instance: Optional["SandboxManager"] = None
    
    def __init__(self) -> None:
        self._loop_pool = SandboxLoopPool.get_instance()
        # sandbox_id -> _SandboxContext
        self._registry: Dict[str, _SandboxContext] = {}
        # sandbox_id -> Sandbox 实例（创建时注册，cleanup 时注销，用于 get_sandbox_instances）
        self._sandbox_instances: Dict[str, Any] = {}

    def register_sandbox(self, sandbox_id: str, sandbox: Any) -> None:
        """由 Sandbox 在创建时调用，登记实例以便 get_sandbox_instances 可返回。"""
        if sandbox_id:
            self._sandbox_instances[sandbox_id] = sandbox

    def unregister_sandbox(self, sandbox_id: str) -> None:
        """由 Sandbox 在 cleanup 完成后调用，从实例表中移除。"""
        if sandbox_id:
            self._sandbox_instances.pop(sandbox_id, None)

    @classmethod
    def get_instance(cls) -> "SandboxManager":
        if cls._instance is None:
            cls._instance = SandboxManager()
        return cls._instance

    def get_sandbox_count(self) -> int:
        """返回当前已注册的 sandbox 数量（至少被调度过一次的 sandbox_id 个数）。"""
        return len(self._registry)

    def get_registered_sandbox_ids(self) -> List[str]:
        """返回当前已注册的所有 sandbox_id 列表（快照）。"""
        return list(self._registry.keys())

    def get_sandbox_instances(self) -> List[Any]:
        """返回当前已登记的 Sandbox 实例列表（快照），可用于统一 cleanup 等。"""
        return list(self._sandbox_instances.values())

    async def _ensure_context(self, sandbox_id: str) -> _SandboxContext:
        """
        获取（或创建）指定 sandbox 的运行上下文：
        - 选择一个 loop
        - 在该 loop 上启动一个单独的 worker_task，串行执行该 sandbox 的所有工作
        """
        ctx = self._registry.get(sandbox_id)
        if ctx is not None:
            return ctx
        
        loop = self._loop_pool.get_loop_for_sandbox_id(sandbox_id)
        
        async def _init_worker() -> _SandboxContext:
            # 注意：在 sandbox loop 上创建 queue 和 worker_task
            queue: "asyncio.Queue[Tuple[Callable[..., Awaitable[Any]], Tuple[Any, ...], Dict[str, Any], Future]]" = asyncio.Queue()
            
            async def _worker() -> None:
                while True:
                    func, args, kwargs, fut = await queue.get()
                    try:
                        # None 作为哨兵用于未来扩展“关闭 worker”
                        if func is None:
                            queue.task_done()
                            # 不再接受新任务
                            break
                        
                        # 如果调用方那边已经取消，不必再执行
                        if fut.cancelled():
                            queue.task_done()
                            continue
                        
                        try:
                            result = await func(*args, **kwargs)
                        except Exception as e:  # noqa: BLE001
                            # 将异常传回调用方所在的线程/loop
                            if not fut.done():
                                fut.set_exception(e)
                        else:
                            if not fut.done():
                                fut.set_result(result)
                        finally:
                            queue.task_done()
                    except Exception:
                        # worker 内部异常不能让整个 loop 崩溃，简单吞掉并继续
                        queue.task_done()
                        continue
            
            worker_task = asyncio.create_task(_worker())
            ctx_inner = _SandboxContext(loop=loop, queue=queue, worker_task=worker_task)
            # 在 sandbox loop 上更新 registry，没有跨线程竞态问题（GIL 保护）
            self._registry[sandbox_id] = ctx_inner
            logger.info(
                f"[sandbox] bound sandbox_id={sandbox_id} loop_tid={threading.get_ident()} at={datetime.now().isoformat(timespec='milliseconds')}"
            )
            return ctx_inner
        
        # 在目标 loop 上初始化 worker 和 queue
        init_future = self._loop_pool.submit_to_loop(loop, _init_worker())
        ctx = await asyncio.wrap_future(init_future)
        return ctx
    
    async def run_on_sandbox(
        self,
        sandbox_id: str,
        func: Callable[..., Awaitable[Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        在与指定 sandbox 绑定的单一 Task/loop 上执行协程函数。
        
        设计要点：
        - 每个 sandbox 只有一个 worker_task，顺序执行所有提交的协程；
        - connect / list_tools / call_tool / cleanup 等都在这个 Task 里执行，
          确保 anyio 的 cancel scope 与 async generator 的 enter/exit 发生在
          同一个 Task 上；
        - 从任意线程/loop 调用本方法都是安全的，通过 Future 把结果回传。
        """
        if not sandbox_id:
            # 如果没有 sandbox_id（极少数工具级调用），直接本地执行
            return await func(*args, **kwargs)
        
        ctx = await self._ensure_context(sandbox_id)
        loop = ctx.loop
        
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        
        # 如果当前就在 sandbox loop 且已经处于该 sandbox 的 worker_task 内，
        # 直接执行以避免死锁（例如 worker 内部递归调用 run_on_sandbox）
        if current_loop is loop:
            current_task = asyncio.current_task()
            if current_task is ctx.worker_task:
                logger.debug(f"[sandbox] run_on_sandbox direct sandbox_id={sandbox_id} tid={threading.get_ident()}")
                return await func(*args, **kwargs)
        
        # 一般情况：构造一个 Future，塞到 sandbox 的队列里，由 worker_task 执行
        logger.debug(f"[sandbox] run_on_sandbox forwarded sandbox_id={sandbox_id} caller_tid={threading.get_ident()}")
        fut: Future = Future()
        job = (func, args, kwargs, fut)
        
        # 从任意线程安全地往 sandbox loop 的队列里塞任务
        loop.call_soon_threadsafe(ctx.queue.put_nowait, job)
        
        # 在当前调用方所在的 loop/线程上等待结果
        return await asyncio.wrap_future(fut)
    
    async def cleanup_all(self) -> None:
        """
        Placeholder for unified cleanup support.
        
        当前实现中，SandboxManager 只负责调度到正确的 worker_task，
        不负责枚举/持有所有 sandbox 实例的引用；统一 cleanup 仍由上层
        明确调用 per-sandbox cleanup 实现。
        """
        return None

