import asyncio
from contextlib import asynccontextmanager
import logging
from datetime import timedelta
import traceback
from typing import AsyncIterator
import uuid
from ..utils.redis_utils import get_redis_client


logger = logging.getLogger(__name__)


@asynccontextmanager
async def distribute_lock(key: str, timeout_sec: int = 30) -> AsyncIterator[bool]:
    """
    Distributed lock based on Redis
    """
    assert key is not None and key != "", "Key cannot be empty"
    assert timeout_sec is not None and timeout_sec > 0, "Timeout must be positive"

    redis_client = get_redis_client()
    await redis_client.initialize()

    lock_value = uuid.uuid4().hex
    watchdog_stop_event = asyncio.Event()
    lock_acquired = False

    async def _try_lock() -> bool:
        try:
            before_value = await redis_client.get(key)
            result = await redis_client.set(
                key, lock_value, nx=True, ex=timedelta(seconds=timeout_sec)
            )
            after_value = await redis_client.get(key)
            logger.info(
                f"Try lock: lock_key={key}, timeout_sec={timeout_sec}, before_value={before_value}, after_value={after_value}, result={result}"
            )
            return bool(result)
        except Exception as e:
            logger.error(f"Redis SET error: {e}")
            return False

    async def _release_lock() -> bool:
        try:
            release_lua_script = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
    """
            r = await redis_client.eval(release_lua_script, 1, key, lock_value)  # type: ignore
            return bool(r)
        except:
            logger.error(
                f"Release lock error: lock_key={key}\n{traceback.format_exc()}"
            )
            return False

    async def _start_lock_watchdog():
        async def _watch_dog():
            while not watchdog_stop_event.is_set():
                await asyncio.sleep(timeout_sec / 3)
                if watchdog_stop_event.is_set():
                    break

                try:
                    extend_lua_script = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""
                    result = await redis_client.eval(extend_lua_script, 1, key, lock_value, str(timeout_sec))  # type: ignore

                    if bool(result):
                        logger.info(f"Watchdog extended lock: {key}")
                    else:
                        # Lock may have been released or expired, check if it still exists
                        current_value = await redis_client.get(key)
                        logger.warning(
                            f"Watchdog failed to extend lock: lock_key={key}, current lock value={current_value}, expected lock value={lock_value}"
                        )
                except:
                    logger.error(
                        f"Watchdog error: lock_key={key}\n{traceback.format_exc()}"
                    )
                    continue

            logger.info(f"Watchdog stopped: lock_key={key}")

        asyncio.create_task(_watch_dog())

    async def _stop_lock_watchdog():
        try:
            watchdog_stop_event.set()
        except:
            logger.error(
                f"Stop watchdog error: lock_key={key}\n{traceback.format_exc()}"
            )

    try:
        result = await _try_lock()
        if result:
            logger.info(f"Acquired lock: lock_key={key}, result={result}")
            lock_acquired = True
            await _start_lock_watchdog()
            yield True
        else:
            logger.info(f"Failed to acquire lock: lock_key={key}, result={result}")
            yield False
    except:
        logger.error(f"Acquire lock error: lock_key={key}\n{traceback.format_exc()}")
        yield False
    finally:
        try:
            await _stop_lock_watchdog()
        except:
            logger.error(f"Stop watchdog error: {traceback.format_exc()}")

        if lock_acquired:
            try:
                r = await _release_lock()
                if r == 1:
                    logger.info(f"Released lock: {key}")
                else:
                    logger.warning(f"Failed to release lock: {key}")
            except:
                logger.error(f"Release lock error: {traceback.format_exc()}")

        try:
            await redis_client.aclose()
        except:
            logger.error(f"Close redis client error: {traceback.format_exc()}")
