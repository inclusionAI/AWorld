import asyncio
import os
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

    Key idea: all coroutines bound to this sandbox (including MCP
    connect / list_tools / call_tool / cleanup) are executed
    sequentially inside the same worker_task. This guarantees that
    anyio cancel scopes and async generators are always entered and
    exited from the same asyncio Task.
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

    Compared to a simple sandbox_id -> loop mapping, this manager also
    maintains a single worker Task per sandbox. All MCP-related
    operations are funneled through that worker and executed
    sequentially. This avoids situations where:

    - Task A enters an async generator or anyio cancel scope, but
    - Task B tries to exit or aclose the same object.

    Those cross-task enter/exit patterns are what cause anyio / MCP
    errors like:
    `Attempted to exit cancel scope in a different task than it was entered in`
    and `aclose(): asynchronous generator is already running`.
    """
    
    _instance: Optional["SandboxManager"] = None
    
    def __init__(self) -> None:
        self._loop_pool = SandboxLoopPool.get_instance()
        # sandbox_id -> _SandboxContext
        self._registry: Dict[str, _SandboxContext] = {}
        # sandbox_id -> Sandbox instance (registered on creation, unregistered on
        # cleanup, used by get_sandbox_instances)
        self._sandbox_instances: Dict[str, Any] = {}

    def register_sandbox(self, sandbox_id: str, sandbox: Any) -> None:
        """Register a Sandbox instance when it is created."""
        if sandbox_id:
            self._sandbox_instances[sandbox_id] = sandbox

    def unregister_sandbox(self, sandbox_id: str) -> None:
        """Unregister a Sandbox instance after its cleanup completes."""
        if sandbox_id:
            self._sandbox_instances.pop(sandbox_id, None)

    @classmethod
    def get_instance(cls) -> "SandboxManager":
        if cls._instance is None:
            cls._instance = SandboxManager()
        return cls._instance

    def get_sandbox_count(self) -> int:
        """Return the number of sandbox IDs that currently have a context."""
        return len(self._registry)

    def get_registered_sandbox_ids(self) -> List[str]:
        """Return a snapshot list of all sandbox_ids with a registered context."""
        return list(self._registry.keys())

    def get_sandbox_instances(self) -> List[Any]:
        """Return a snapshot list of all registered Sandbox instances."""
        return list(self._sandbox_instances.values())

    def _context_key(self, sandbox_id: str, server_name: Optional[str] = None) -> str:
        """Key for registry: sandbox_id only, or sandbox_id:server_name when server affinity is used."""
        if server_name:
            return f"{sandbox_id}:{server_name}"
        return sandbox_id

    async def _ensure_context(self, sandbox_id: str, server_name: Optional[str] = None) -> _SandboxContext:
        """
        Get (or create) the runtime context for the given sandbox_id (and optionally server_name).
        When server_name is set, key is "sandbox_id:server_name" so each server gets its own worker/loop.
        """
        key = self._context_key(sandbox_id, server_name)
        ctx = self._registry.get(key)
        if ctx is not None:
            return ctx

        loop = self._loop_pool.get_loop_for_key(key)
        
        async def _init_worker() -> _SandboxContext:
            # Create the queue and worker_task on the sandbox loop
            queue: "asyncio.Queue[Tuple[Callable[..., Awaitable[Any]], Tuple[Any, ...], Dict[str, Any], Future]]" = asyncio.Queue()
            
            async def _worker() -> None:
                while True:
                    func, args, kwargs, fut = await queue.get()
                    try:
                        # None is used as a sentinel to eventually support shutting down the worker
                        if func is None:
                            queue.task_done()
                            # Stop accepting new jobs
                            break
                        
                        # If the caller already cancelled the Future, skip executing the job
                        if fut.cancelled():
                            queue.task_done()
                            continue
                        
                        try:
                            result = await func(*args, **kwargs)
                        except Exception as e:  # noqa: BLE001
                            # Propagate the exception back to the caller's thread/loop
                            if not fut.done():
                                fut.set_exception(e)
                        else:
                            if not fut.done():
                                fut.set_result(result)
                        finally:
                            queue.task_done()
                    except Exception:
                        # Do not let exceptions in the worker crash the loop; log & continue
                        queue.task_done()
                        continue
            
            worker_task = asyncio.create_task(_worker())
            ctx_inner = _SandboxContext(loop=loop, queue=queue, worker_task=worker_task)
            # Update registry on the sandbox loop; GIL prevents cross-thread races here
            self._registry[key] = ctx_inner
            # bound = first time this key gets a worker/event-loop (after create)
            logger.info(
                f"[sandbox bound] key={key} pid={os.getpid()} tid={threading.get_ident()} at={datetime.now().isoformat(timespec='milliseconds')}"
            )
            return ctx_inner
        
        # Initialize worker and queue on the target loop
        init_future = self._loop_pool.submit_to_loop(loop, _init_worker())
        ctx = await asyncio.wrap_future(init_future)
        return ctx

    async def run_on_sandbox(
        self,
        sandbox_id: str,
        func: Callable[..., Awaitable[Any]],
        *args: Any,
        server_name: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """
        Execute a coroutine function on the Task/loop bound to a sandbox (and optionally a server).

        When server_name is provided, the key is "sandbox_id:server_name" so each server
        has its own worker/loop (for testing: same endpoint, different thread per server).
        """
        if not sandbox_id:
            # No sandbox_id (rare tool-only calls): execute directly on current loop
            return await func(*args, **kwargs)

        ctx = await self._ensure_context(sandbox_id, server_name)
        loop = ctx.loop
        
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        
        # If we're already on the sandbox loop *and* inside its worker_task,
        # execute directly to avoid deadlocks (e.g. recursive run_on_sandbox)
        if current_loop is loop:
            current_task = asyncio.current_task()
            if current_task is ctx.worker_task:
                key = self._context_key(sandbox_id, server_name)
                logger.debug(f"[sandbox] run_on_sandbox direct key={key} tid={threading.get_ident()}")
                return await func(*args, **kwargs)

        # Normal path: build a Future, enqueue the job, let worker_task execute it
        key = self._context_key(sandbox_id, server_name)
        logger.debug(f"[sandbox] run_on_sandbox forwarded key={key} caller_tid={threading.get_ident()}")
        fut: Future = Future()
        job = (func, args, kwargs, fut)
        
        # Enqueue the job onto the sandbox loop from any thread safely
        loop.call_soon_threadsafe(ctx.queue.put_nowait, job)
        
        # Wait for the result on the caller's loop/thread
        return await asyncio.wrap_future(fut)
    
    async def cleanup_all(self) -> None:
        """
        Placeholder for unified cleanup support.

        At present SandboxManager only knows how to dispatch work to the
        correct worker_task. It does not own or enumerate Sandbox
        instances; higher layers are responsible for tracking sandboxes
        and calling per-sandbox cleanup explicitly.
        """
        return None

