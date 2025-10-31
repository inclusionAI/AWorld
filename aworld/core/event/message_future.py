import asyncio


class MessageFuture:
    """Async message Future class, similar to JavaScript Promise.

    Core features:
    - Can await anywhere to get results
    - Returns immediately if result ready
    - Blocks at await point if result not ready until completion or timeout
    - Supports timeout handling
    - Supports try-catch error handling
    - Multiple locations can await same Future and get same result

    Example:
        # Send message, immediately returns Future
        future = await send_message_with_future(msg)

        # Main thread continues...
        await do_other_work()

        # Get result when needed
        try:
            result = await future.wait(timeout=10)
            print("Success:", result.status)
        except TimeoutError:
            print("Wait timeout")
    """

    def __init__(self, msg_id: str):
        """Initialize MessageFuture.

        Args:
            msg_id: Message ID to track
        """
        self.msg_id = msg_id
        from aworld.runners.state_manager import RuntimeStateManager
        self.state_mng = RuntimeStateManager.instance()

        # asyncio.Future() is the core - a waitable object
        # When set_result() is called, all await locations wake up
        self.future: asyncio.Future = asyncio.Future()

        # Record polling task for management
        self._task = None

        # Track if polling has been started to avoid duplicate polling
        self._polling_started = False

    def _start_polling(self, timeout: float = None):
        """Start background polling task.
        
        Args:
            timeout: Timeout in seconds, None for infinite wait
        """
        if not self._polling_started:
            self._task = asyncio.create_task(self._wait_internal(timeout))
            self._polling_started = True

    async def wait(self, timeout: float = None):
        """Wait for message completion and return result.

        Args:
            timeout: Timeout in seconds, None for infinite wait

        Returns:
            RunNode object containing:
            - node.status: RunNodeStatus execution status
            - node.results: List[HandleResult] results
            - node.result_msg: str result message

        Raises:
            TimeoutError: If wait times out
            Exception: If message execution failed or other error

        Example:
            try:
                result = await future.wait(timeout=30)
                print(f"Status: {result.status}")
                print(f"Results: {result.results}")
            except TimeoutError:
                print("Wait timeout")
        """
        # Start polling with the specified timeout
        self._start_polling(timeout)
        
        try:
            if timeout is not None:
                return await asyncio.wait_for(self.future, timeout=timeout)
            else:
                return await self.future
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Waiting for message {self.msg_id} timed out after {timeout} seconds"
            )
        except asyncio.CancelledError:
            raise RuntimeError(f"Wait for message {self.msg_id} was cancelled")

    def done(self) -> bool:
        """Check if message completed.

        Returns:
            True if completed (success/failed), False if still processing
        """
        return self.future.done()

    def result(self):
        """Get result without waiting (raises if not completed).

        Returns:
            RunNode object if completed

        Raises:
            asyncio.InvalidStateError: If message not yet completed
        """
        return self.future.result()

    async def _wait_internal(self, timeout: float = 60.0) -> None:
        """Background polling task.

        Args:
            timeout: Timeout in seconds(defaults to 60s)

        Workflow:
        1. Check message status every 100ms
        2. If completed, call set_result() on Future
        3. set_result() wakes up all await locations
        4. All await future.wait() calls receive result
        5. After timeout seconds, set timeout exception
        """
        from aworld.runners.state_manager import RuntimeStateManager
        from aworld.logs.util import logger

        # Use 60 seconds as default if timeout not specified
        actual_timeout = timeout if timeout is not None else 60
        max_retries = int(actual_timeout * 10)  # 0.1s per retry
        retry_count = 0

        while retry_count < max_retries:
            try:
                node = self.state_mng.get_node(self.msg_id)

                # Node not yet created
                if node is None:
                    await asyncio.sleep(0.1)
                    retry_count += 1
                    continue

                # Node completed
                if node.has_finished():
                    logger.info(
                        f"Message {self.msg_id} finished with status: {node.status}"
                    )

                    # Critical: set result
                    # This wakes up all await locations
                    if not self.future.done():
                        self.future.set_result(node)
                    return

                # Continue waiting
                await asyncio.sleep(0.1)
                retry_count += 1

            except Exception as e:
                logger.error(f"Error in _wait_internal for {self.msg_id}: {e}", exc_info=True)
                if not self.future.done():
                    self.future.set_exception(e)
                return

        # Timeout
        timeout_error = TimeoutError(
            f"Message {self.msg_id} did not complete within {actual_timeout} seconds"
        )
        if not self.future.done():
            self.future.set_exception(timeout_error)
