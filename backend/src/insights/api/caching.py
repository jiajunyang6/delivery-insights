import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import orjson

from insights.analytics.snapshot import canonical, digest
from insights.api.params import CURSOR_RE, PrFilters, invalid

SNAPSHOT_RETENTION = timedelta(days=7)


def matches_etag(header: str | None, etag: str) -> bool:
    if header is None:
        return False
    return any(part.strip().removeprefix("W/").strip() in {"*", etag} for part in header.split(","))


def alive(created_at: datetime, now: datetime) -> bool:
    return created_at + SNAPSHOT_RETENTION > now


def cache_ttl(created_at: datetime, now: datetime, maximum: int = 86400) -> int:
    return max(0, min(maximum, int((created_at + SNAPSHOT_RETENTION - now).total_seconds())))


@dataclass(frozen=True, slots=True)
class Reply:
    body: bytes
    status: int
    headers: dict[str, str]


def snapshot_reply(
    sid: str, body: bytes, etag: str, conditional: str | None, *, immutable: bool
) -> Reply:
    headers = {
        "ETag": etag,
        "Cache-Control": "private, max-age=86400, immutable"
        if immutable
        else "private, max-age=60",
    }
    if not immutable:
        headers.update({"X-Snapshot-Id": sid, "Content-Location": f"/v1/snapshots/{sid}"})
    matched = matches_etag(conditional, etag)
    return Reply(b"" if matched else body, 304 if matched else 200, headers)


def encode_cursor(sid: str, offset: int, filters: PrFilters) -> str:
    return (
        base64.urlsafe_b64encode(
            canonical({"v": 1, "sid": sid, "o": offset, "f": digest(filters.canonical_dict())[:12]})
        )
        .decode()
        .rstrip("=")
    )


def decode_cursor(cursor: str | None, sid: str, filters: PrFilters) -> int:
    if cursor is None:
        return 0
    try:
        if CURSOR_RE.fullmatch(cursor) is None:
            raise ValueError
        obj = orjson.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if (
            not isinstance(obj, dict)
            or set(obj) != {"v", "sid", "o", "f"}
            or type(obj["v"]) is not int
            or obj["v"] != 1
            or type(obj["o"]) is not int
            or obj["o"] < 0
            or not isinstance(obj["sid"], str)
            or not isinstance(obj["f"], str)
        ):
            raise ValueError
    except (ValueError, binascii.Error, orjson.JSONDecodeError) as exc:
        raise invalid("cursor") from exc
    if obj["sid"] != sid or obj["f"] != digest(filters.canonical_dict())[:12]:
        raise invalid("cursor", "cursor is no longer valid; restart from the first page")
    return int(obj["o"])


def page_rows(
    rows: list[dict[str, Any]],
    sid: str,
    as_of: datetime,
    filters: PrFilters,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    selected = [
        r
        for r in rows
        if r["status"] == filters.status
        and (not filters.at_risk or r["at_risk"] is not None)
        and (filters.state is None or r["current_state"] == filters.state)
        and (filters.location is None or filters.location in r["locations"])
    ]

    def score(row: dict[str, Any]) -> float:
        if filters.at_risk:
            threshold = row["at_risk"]["threshold_hours"]
            return float(row["current_state_age_hours"] / threshold) if threshold else float("inf")
        if filters.status == "closed":
            return datetime.fromisoformat(row["closed_at"]).timestamp()
        return (
            float(row["cycle_hours"] or 0)
            if filters.status == "merged"
            else float(row["current_state_age_hours"] or 0)
        )

    selected.sort(key=lambda r: (-score(r), r["repo"], r["number"]))
    offset = decode_cursor(cursor, sid, filters)
    end = offset + limit
    return {
        "snapshot_id": sid,
        "as_of": as_of,
        "status": filters.status,
        "total": len(selected),
        "items": selected[offset:end],
        "next_cursor": encode_cursor(sid, end, filters) if end < len(selected) else None,
    }
