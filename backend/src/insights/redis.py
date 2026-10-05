"""Redis and arq connection factories plus the key layout shared by API and worker."""

from hashlib import sha256
from typing import cast

from redis.asyncio import Redis

from insights.config import Settings


def create_redis(settings: Settings) -> Redis:
    """Create a shared Redis client with two-second connection and socket timeouts."""
    return cast(
        Redis, Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    )


def snapshot_key(snapshot_id: str) -> str:
    """Redis key for a snapshot's cached body and ETag."""
    return f"di:snap:{snapshot_id}"


def narrative_key(snapshot_id: str, prompt_version: str, model_id: str, pack_hash: str) -> str:
    """Redis key scoped by snapshot, prompt, model and evidence content."""
    return f"di:narr:{snapshot_id}:{prompt_version}:{model_id}:{pack_hash}"


def llm_error_key() -> str:
    """Latest Bedrock failure code and time, shown as a setup hint until an LLM call succeeds."""
    return "di:setup:llm_error"


def sync_lock_key(repo: str) -> str:
    """Repository-level sync mutex key, with case-insensitive repository identity."""
    return f"di:lock:sync:{repo.lower()}"


def narrative_lock_key(snapshot_id: str) -> str:
    """Generation mutex for one snapshot, shared across prompt/model variants."""
    return f"di:lock:narr:{snapshot_id}"


def rate_limit_key(client_ip: str, epoch_minute: int) -> str:
    """Client-IP request counter key for a single epoch-minute bucket."""
    return f"di:rl:{client_ip}:{epoch_minute}"


def github_etag_key(url: str) -> str:
    """Redis key derived from the complete request URL without exposing that URL in the key."""
    return f"di:gh:etag:{sha256(url.encode()).hexdigest()}"
