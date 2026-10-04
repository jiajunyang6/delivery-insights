from hashlib import sha256
from typing import cast

from arq.connections import ArqRedis, RedisSettings, create_pool
from redis.asyncio import Redis

from insights.config import Settings


def create_redis(settings: Settings) -> Redis:
    return cast(
        Redis, Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    )


def snapshot_key(snapshot_id: str) -> str:
    return f"di:snap:{snapshot_id}"


def rows_key(snapshot_id: str) -> str:
    return f"di:rows:{snapshot_id}"


def narrative_key(
    snapshot_id: str, audience: str, lang: str, prompt_version: str, model_id: str, pack_hash: str
) -> str:
    return f"di:narr:{snapshot_id}:{audience}:{lang}:{prompt_version}:{model_id}:{pack_hash}"


def sync_lock_key(repo: str) -> str:
    return f"di:lock:sync:{repo.lower()}"


def narrative_lock_key(snapshot_id: str, audience: str, lang: str) -> str:
    return f"di:lock:narr:{snapshot_id}:{audience}:{lang}"


def sync_cooldown_key(repo: str) -> str:
    return f"di:cooldown:sync:{repo.lower()}"


def rate_limit_key(client_ip: str, epoch_minute: int) -> str:
    return f"di:rl:{client_ip}:{epoch_minute}"


def github_etag_key(url: str) -> str:
    return f"di:gh:etag:{sha256(url.encode()).hexdigest()}"


async def connect_arq(url: str) -> ArqRedis:
    settings = RedisSettings.from_dsn(url)
    settings.conn_retries = 0
    settings.conn_timeout = 2
    return await create_pool(settings)
