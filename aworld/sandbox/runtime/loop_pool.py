import asyncio
import threading
from concurrent.futures import Future
from typing import List, Optional


class SandboxLoopPool:
    """
    Manage a small pool of dedicated asyncio event loops for sandbox work.

    Each loop runs forever in its own daemon thread. Coroutines can be
    submitted from any thread and will be executed on the chosen loop.
    """

    _instance: Optional["SandboxLoopPool"] = None
    _instance_lock = threading.Lock()

    def __init__(self, num_loops: int = 4) -> None:
        if num_loops <= 0:
            raise ValueError("num_loops must be positive")

        self._loops: List[asyncio.AbstractEventLoop] = []
        self._threads: List[threading.Thread] = []

        for _ in range(num_loops):
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=self._run_loop, args=(loop,), daemon=True
            )
            self._loops.append(loop)
            self._threads.append(thread)
            thread.start()

    @classmethod
    def get_instance(cls) -> "SandboxLoopPool":
        """
        Lazily create and return the global loop pool instance.
        """
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = SandboxLoopPool()
        return cls._instance

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
        """
        Thread target: run an event loop forever.
        """
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            try:
                from aworld.sandbox.run.mcp_servers import (
                    cleanup_provider_calls_for_loop,
                )

                loop.run_until_complete(cleanup_provider_calls_for_loop(loop))
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    def get_loop_for_key(self, key: str) -> asyncio.AbstractEventLoop:
        """
        Deterministically pick a loop for a given key (e.g. sandbox_id or "sandbox_id:server_name").
        """
        if not self._loops:
            raise RuntimeError("SandboxLoopPool is not initialized")
        index = hash(key) % len(self._loops)
        return self._loops[index]

    def get_loop_for_sandbox_id(self, sandbox_id: str) -> asyncio.AbstractEventLoop:
        """
        Deterministically pick a loop for a given sandbox_id.

        A simple hash-based sharding is sufficient here.
        """
        return self.get_loop_for_key(sandbox_id)

    def submit_to_loop(
        self, loop: asyncio.AbstractEventLoop, coro: "asyncio.Future"
    ) -> Future:
        """
        Submit a coroutine to the given loop from any thread.

        Returns a concurrent.futures.Future that can be awaited/waited on
        in the caller's context.
        """
        return asyncio.run_coroutine_threadsafe(coro, loop)

    def shutdown(self) -> None:
        """Stop all pool loops and wait until their owner threads close them."""

        loops = list(self._loops)
        threads = list(self._threads)
        for loop in loops:
            if not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:
                    pass
        current = threading.current_thread()
        for thread in threads:
            if thread is not current and thread.is_alive():
                thread.join(timeout=5)
        self._loops.clear()
        self._threads.clear()
