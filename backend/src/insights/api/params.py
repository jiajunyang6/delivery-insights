"""Strict parsing of query and path parameters; failures raise problem+json errors."""

import re
from datetime import date, datetime, timedelta

from starlette.datastructures import QueryParams

from insights.analytics.dataset import SnapshotParams
from insights.api.errors import ProblemError, invalid_many
from insights.config import MAX_PERIOD_DAYS, REPO_RE, Settings
from insights.snapshots.errors import invalid as invalid

DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
SNAPSHOT_RE = re.compile(r"^s_[0-9a-f]{16}$")


def tracked_repo(repo: str, settings: Settings) -> str:
    """Return the configured spelling of the repo; 403 if it is not tracked."""
    allowed = {name.lower(): name for name in settings.tracked_repo_list}
    if repo.lower() not in allowed:
        raise ProblemError(
            403,
            "not-tracked",
            "Repository not tracked",
            "The requested repository is not tracked.",
            extensions={"repos": [repo]},
        )
    return allowed[repo.lower()]


def parse_params(query: QueryParams, settings: Settings, now: datetime) -> SnapshotParams:
    """Resolve one repo and the date range, reporting all invalid fields in one 422.

    Dates default to the 30 days ending on now.date(); the range must end by that date, span at
    most MAX_PERIOD_DAYS and start within the backfill horizon.
    """
    errors = []
    repos = query.getlist("repo")
    if len(repos) != 1:
        errors.append({"param": "repo", "message": "provide exactly one repo"})
    elif REPO_RE.fullmatch(repos[0]) is None:
        errors.append({"param": "repo", "message": "must be a valid owner/name"})
    today = now.date()
    dates: dict[str, date] = {}
    for name in ("to", "from"):
        raw = query.get(name)
        if raw is None:
            dates[name] = today if name == "to" else dates.get("to", today) - timedelta(days=29)
            continue
        try:
            if DATE_RE.fullmatch(raw) is None:
                raise ValueError
            dates[name] = date.fromisoformat(raw)
        except ValueError:
            errors.append({"param": name, "message": "must be an ISO date (YYYY-MM-DD)"})
    if "from" in dates and "to" in dates:
        start, end = dates["from"], dates["to"]
        if start > end:
            errors.append({"param": "from", "message": "must not be later than 'to'"})
        if (end - start).days + 1 > MAX_PERIOD_DAYS:
            errors.append({"param": "to", "message": "period must not exceed 366 days"})
        if end > today:
            errors.append({"param": "to", "message": "must not be later than today"})
        if start < today - timedelta(days=settings.backfill_days):
            errors.append(
                {"param": "from", "message": "must be within the configured backfill horizon"}
            )
    if errors:
        raise invalid_many(errors)
    return SnapshotParams(
        (tracked_repo(repos[0], settings),),
        dates["from"],
        dates["to"],
        settings.location_dimension,
        settings.directory_depth,
        settings.ci_source,
    )


def validate_snapshot_id(value: str) -> str:
    """Return a valid snapshot identifier unchanged, or raise a 422 parameter problem."""
    if SNAPSHOT_RE.fullmatch(value) is None:
        raise invalid("snapshot_id")
    return value
