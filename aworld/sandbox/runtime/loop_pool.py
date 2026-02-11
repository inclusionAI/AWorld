import asyncio
import threading
from concurrent.futures import Future
from typing import Dict, List, Optional


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
        loop.run_forever()

    def get_loop_for_sandbox_id(self, sandbox_id: str) -> asyncio.AbstractEventLoop:
        """
        Deterministically pick a loop for a given sandbox_id.

        A simple hash-based sharding is sufficient here.
        """
        if not self._loops:
            raise RuntimeError("SandboxLoopPool is not initialized")
        index = hash(sandbox_id) % len(self._loops)
        return self._loops[index]

    def submit_to_loop(
        self, loop: asyncio.AbstractEventLoop, coro: "asyncio.Future"
    ) -> Future:
        """
        Submit a coroutine to the given loop from any thread.

        Returns a concurrent.futures.Future that can be awaited/waited on
        in the caller's context.
        """
        return asyncio.run_coroutine_threadsafe(coro, loop)

