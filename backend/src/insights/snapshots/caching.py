"""HTTP caching (ETags, retention TTL) for snapshot replies."""

from dataclasses import dataclass
from datetime import datetime, timedelta

SNAPSHOT_RETENTION = timedelta(days=7)


def matches_etag(header: str | None, etag: str) -> bool:
    """Match comma-separated If-None-Match values, accepting weak tags and the wildcard."""
    if header is None:
        return False
    return any(part.strip().removeprefix("W/").strip() in {"*", etag} for part in header.split(","))


def alive(created_at: datetime, now: datetime) -> bool:
    """Test whether snapshot retention expires strictly after the supplied observation time."""
    return created_at + SNAPSHOT_RETENTION > now


def cache_ttl(created_at: datetime, now: datetime, maximum: int = 86400) -> int:
    """Bound cache life by both its maximum TTL and the snapshot's remaining retention."""
    return max(0, min(maximum, int((created_at + SNAPSHOT_RETENTION - now).total_seconds())))


@dataclass(frozen=True, slots=True)
class Reply:
    """A transport-neutral HTTP reply that API routes turn into a Response."""

    body: bytes
    status: int
    headers: dict[str, str]


def snapshot_reply(sid: str, body: bytes, etag: str, conditional: str | None) -> Reply:
    """Build a conditional 200/304 snapshot reply.

    Replies are param-addressed and use no-cache: the browser revalidates with the ETag every
    time, so a sync that extends coverage shows at once while unchanged data still costs a 304.
    """
    headers = {"ETag": etag, "Cache-Control": "private, no-cache", "X-Snapshot-Id": sid}
    matched = matches_etag(conditional, etag)
    return Reply(b"" if matched else body, 304 if matched else 200, headers)
