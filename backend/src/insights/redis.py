from typing import cast

from redis.asyncio import Redis

from insights.config import Settings


def create_redis(settings: Settings) -> Redis:
    return cast(
        Redis, Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    )
