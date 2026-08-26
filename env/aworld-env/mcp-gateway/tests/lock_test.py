import asyncio
from mcp_gateway.utils.lock import distribute_lock

import logging

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def test_distribute_lock():
    async with distribute_lock(key="test_lock", timeout_sec=1) as locked:
        logger.info(f"Lock enter: {locked}")
        await asyncio.sleep(10)
        logger.info(f"Lock exit: {locked}")


if __name__ == "__main__":

    async def main():
        await asyncio.gather(test_distribute_lock(), test_distribute_lock())
        await asyncio.sleep(10)

    asyncio.run(main())
