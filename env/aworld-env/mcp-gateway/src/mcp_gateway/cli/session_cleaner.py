import asyncio
from contextlib import AbstractAsyncContextManager
import logging
import traceback

from ..utils.lock import distribute_lock
from ..sessions import (
    SessionConnectionManager,
    session_connection_manager_builder,
)
from ..containers import (
    ContainerServerManager,
    container_server_manager_builder,
)

from ..configs import cluster_name, SESSION_CLEAN_INTERVAL_SEC

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s SESSION_CLEANER_LOG - %(name)s - %(levelname)s - %(message)s",
)

logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


class MCPSessionCleaner(AbstractAsyncContextManager):
    """
    MCP Session Cleaner service that cleans up expired sessions.
    """

    def __init__(self):
        self.session_connection_manager: SessionConnectionManager
        self.container_server_manager: ContainerServerManager

    async def __aenter__(self):
        await self.startup()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.shutdown()

    async def startup(self):
        """Initialize the session cleaner service"""
        logger.info("Starting MCP Session Cleaner")

        self.container_server_manager = await container_server_manager_builder()
        self.session_connection_manager = await session_connection_manager_builder(
            self.container_server_manager
        )

    async def shutdown(self):
        """Cleanup resources"""
        logger.info("Shutting down MCP Session Cleaner")
        await self.session_connection_manager.shutdown()
        await self.container_server_manager.shutdown()

    async def clean_sessions(self):
        while True:
            try:
                lock_key = f"session_clean_task_lock.{cluster_name}"
                async with distribute_lock(key=lock_key) as locked:
                    if locked:
                        logger.info(f"Sessions clean task start! lock={locked}")
                        await self.session_connection_manager.cleanup_expired_sessions()
                        logger.info("Sessions clean task end!")
                    else:
                        logger.info(f"Sessions clean task skipped! lock={locked}")
            except:
                logger.error(f"Sessions clean task error: {traceback.format_exc()}")
            finally:
                await asyncio.sleep(SESSION_CLEAN_INTERVAL_SEC)


async def main():
    try:
        async with MCPSessionCleaner() as session_cleaner:
            await session_cleaner.clean_sessions()
    except:
        logger.error(f"Session cleaner error: {traceback.format_exc()}")
        exit(1)


def cli():
    """Console script entry point"""
    asyncio.run(main())


if __name__ == "__main__":
    cli()
