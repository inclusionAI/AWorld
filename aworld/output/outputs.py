import abc
import asyncio
import traceback
from abc import abstractmethod
from dataclasses import field, dataclass
from typing import AsyncIterator, Any, Union, Iterator

from aworld.logs.util import logger
from aworld.output import Output
from aworld.output.base import RUN_FINISHED_SIGNAL


@dataclass
class Outputs(abc.ABC):
    """Base class for managing output streams in the AWorld framework.
    Provides abstract methods for adding and streaming outputs both synchronously and asynchronously.
    reference: https://github.com/openai/openai-agents-python/blob/main/src/agents/result.py
    """
    _metadata: dict = field(default_factory=dict)

    @abstractmethod
    async def add_output(self, output: Output):
        """Add an output asynchronously to the output stream.
        
        Args:
            output (Output): The output to be added
        """
        pass

    @abstractmethod
    def sync_add_output(self, output: Output):
        """Add an output synchronously to the output stream.
        
        Args:
            output (Output): The output to be added
        """
        pass

    @abstractmethod
    async def stream_events(self) -> Union[AsyncIterator[Output], list]:
        """Stream outputs asynchronously.
        
        Returns:
            AsyncIterator[Output]: An async iterator of outputs
        """
        pass

    @abstractmethod
    def sync_stream_events(self) -> Union[Iterator[Output], list]:
        """Stream outputs synchronously.
        
        Returns:
            Iterator[Output]: An iterator of outputs
        """
        pass

    @abstractmethod
    async def mark_completed(self):
        pass

    async def get_metadata(self) -> dict:
        return self._metadata

    async def set_metadata(self, metadata: dict):
        self._metadata = metadata

@dataclass
class AsyncOutputs(Outputs):
    """Intermediate class that implements the Outputs interface with async support.
    This class serves as a base for more specific async output implementations."""

    async def add_output(self, output: Output):
        pass

    def sync_add_output(self, output: Output):
        pass

    async def stream_events(self) -> Union[AsyncIterator[Output], list]:
        pass

    def sync_stream_events(self) -> Union[Iterator[Output]]:
        pass


@dataclass
class DefaultOutputs(Outputs):
    """DefaultAsyncOutputs """

    _outputs: list = field(default_factory=list)

    async def add_output(self, output: Output):
        self._outputs.append(output)

    def sync_add_output(self, output: Output):
        self._outputs.append(output)

    async def stream_events(self) -> Union[AsyncIterator[Output], list]:
        return self._outputs

    def sync_stream_events(self) -> Union[Iterator[Output], list]:
        return self._outputs

    async def mark_completed(self):
        pass


@dataclass
class StreamingOutputs(AsyncOutputs):
    """Concrete implementation of AsyncOutputs that provides streaming functionality.
    Manages a queue of outputs and handles streaming with error checking and task management."""

    # Task and input related fields
    # task: Task = Field(default=None)  # The task associated with these outputs
    input: Any = field(default=None)  # Input data for the task
    usage: dict = field(default=None)  # Usage statistics
    task_id: str = field(default=None)  # Input data for the task

    # State tracking
    is_complete: bool = field(default=False)  # Flag indicating if streaming is complete

    # Queue for managing outputs
    _output_queue: asyncio.Queue[Output] = field(
        default_factory=asyncio.Queue, repr=False
    )

    # Internal state management
    _visited_outputs: list[Output] = field(default_factory=list)
    _stored_exception: Exception | None = field(default=None, repr=False)  # Stores any exceptions that occur
    _run_impl_task: asyncio.Task[Any] | None = field(default=None, repr=False)  # The running task

    async def add_output(self, output: Output):
        """Add an output to the queue asynchronously.
        
        Args:
            output (Output): The output to be added to the queue
        """
        logger.info(f"StreamingOutputs|add_output|{self.task_id}|{output}")
        # if different task take same Output instance, they will output to the same queue
        # if self.task_id != output.task_id:
        #     logger.warning(f"{self.task_id} unequals {output.task_id}, add ignored.")
        self._output_queue.put_nowait(output)

    async def stream_events(self) -> AsyncIterator[Output]:
        """Stream outputs asynchronously, handling cached outputs and new outputs from the queue.
        Includes error checking and task cleanup.
        
        Yields:
            Output: The next output in the stream
            
        Raises:
            Exception: Any stored exception that occurred during streaming
        """
        # First yield any cached outputs
        for output in self._visited_outputs:
            if output == RUN_FINISHED_SIGNAL:
                self._output_queue.task_done()
                return
            # if self.task_id != output.task_id:
            #     logger.warning(f"{self.task_id} unequals {output.task_id}")
            #     return
            yield output

        # Main streaming loop
        while True:
            self._check_errors()
            if self._stored_exception:
                logger.info("Breaking due to stored exception")
                self.is_complete = True
                break

            if self.is_complete and self._output_queue.empty():
                logger.info(f"StreamingOutputs|stream_events|finished|{self.task_id}")
                break

            try:
                output = await self._output_queue.get()
                logger.info("Outputs got output: {}".format(output.output_type()))
                self._visited_outputs.append(output)

            except asyncio.CancelledError as err:
                logger.info(f"StreamingOutputs|stream_events|CancelledError|{self.task_id}|{err}")
                break

            if output == RUN_FINISHED_SIGNAL:
                logger.info(f"StreamingOutputs|stream_events|RUN_FINISHED_SIGNAL|{self.task_id}|{output}")
                self._output_queue.task_done()
                self._check_errors()
                break

            yield output
            self._output_queue.task_done()

        self._cleanup_tasks()

        if self._stored_exception:
            logger.info(f"StreamingOutputs|stream_events|stored_exception|{self.task_id}|{self._stored_exception}")
            raise self._stored_exception

    def _check_errors(self):
        """Check for errors in the streaming process.
        Verifies step count and checks for exceptions in the running task.
        """
        # Check the task for any exceptions
        if self._run_impl_task and self._run_impl_task.done():
            exc = self._run_impl_task.exception()
            if exc and isinstance(exc, Exception):
                self._stored_exception = exc

    def _cleanup_tasks(self):
        """Clean up any running tasks by cancelling them if they're not done."""
        if self._run_impl_task and not self._run_impl_task.done():
            self._run_impl_task.cancel()

    async def mark_completed(self) -> None:
        """Mark the streaming process as completed by adding a RUN_FINISHED_SIGNAL to the queue."""
        await self._output_queue.put(RUN_FINISHED_SIGNAL)
