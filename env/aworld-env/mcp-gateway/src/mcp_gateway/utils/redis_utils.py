from redis.asyncio import Redis, ConnectionPool
from ..configs import redis_url


def get_redis_client() -> Redis:
    pool = ConnectionPool.from_url(redis_url)
    return Redis(connection_pool=pool)
