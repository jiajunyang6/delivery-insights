"""Strict parsing of query and path parameters; failures raise problem+json errors."""

import re
from datetime import date, datetime, timedelta
from uuid import UUID

from starlette.datastructures import QueryParams

from insights.analytics.dataset import SnapshotParams
from insights.api.errors import ProblemError, invalid_many
from insights.config import MAX_PERIOD_DAYS, NAME_RE, OWNER_RE, REPO_RE, Settings
from insights.snapshots.errors import invalid as invalid
from insights.snapshots.filters import CURSOR_RE
from insights.snapshots.filters import PrFilters as PrFilters

DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
SNAPSHOT_RE = re.compile(r"^s_[0-9a-f]{16}$")
LOCATION_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,200}$")


def tracked_repos(repos: list[str], settings: Settings) -> tuple[str, ...]:
    """Return configured spellings of the repos, de-duplicated; 403 if any is not tracked."""
    allowed = {repo.lower(): repo for repo in settings.tracked_repo_list}
    unknown = [repo for repo in repos if repo.lower() not in allowed]
    if unknown or not repos:
        raise ProblemError(
            403,
            "not-tracked",
            "Repository not tracked",
            "The requested repository or organization is not tracked.",
            extensions={"repos": unknown},
        )
    return tuple(allowed[key] for key in dict.fromkeys(repo.lower() for repo in repos))


def parse_params(query: QueryParams, settings: Settings, now: datetime) -> SnapshotParams:
    """Resolve repo/org and the date range, reporting all invalid fields in one 422.

    Dates default to the 30 days ending on now.date(); the range must end by that date, span at
    most MAX_PERIOD_DAYS and start within the backfill horizon. An org expands to the tracked
    repos under that owner.
    """
    errors = []
    repos, org = query.getlist("repo"), query.get("org")
    if bool(repos) == (org is not None):
        errors.append({"param": "repo", "message": "provide exactly one of repo or org"})
    for repo in repos:
        if REPO_RE.fullmatch(repo) is None:
            errors.append({"param": "repo", "message": "must be a valid owner/name"})
    if org is not None and OWNER_RE.fullmatch(org) is None:
        errors.append({"param": "org", "message": "must be a valid organization name"})
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
    if org is not None:
        repos = [
            repo for repo in settings.tracked_repo_list if repo.split("/")[0].lower() == org.lower()
        ]
    repos = list(dict.fromkeys(repo.lower() for repo in repos))
    if len(repos) > settings.max_repos_per_request:
        raise invalid("repo", "too many repositories")
    resolved = tracked_repos(repos, settings)
    return SnapshotParams(
        tuple(sorted(resolved)),
        dates["from"],
        dates["to"],
        settings.location_dimension,
        settings.directory_depth,
        settings.ci_source,
    )


def parse_repo_path(owner: str, name: str, settings: Settings) -> str:
    """Validate owner/name syntax and return its tracked repository, or raise a problem."""
    if OWNER_RE.fullmatch(owner) is None:
        raise invalid("owner")
    if NAME_RE.fullmatch(name) is None:
        raise invalid("name")
    return tracked_repos([f"{owner}/{name}"], settings)[0]


def validate_snapshot_id(value: str) -> str:
    """Return a valid snapshot identifier unchanged, or raise a 422 parameter problem."""
    if SNAPSHOT_RE.fullmatch(value) is None:
        raise invalid("snapshot_id")
    return value


def validate_job_id(value: str) -> UUID:
    """Parse a canonical lowercase, hyphenated UUID, or raise a 422 parameter problem."""
    try:
        parsed = UUID(value)
        if str(parsed) != value:
            raise ValueError
        return parsed
    except ValueError as exc:
        raise invalid("job_id") from exc


def parse_filters(query: QueryParams) -> tuple[PrFilters, int, str | None]:
    """Parse PR list filters; at_risk requires open PRs and state applies only to open PRs."""
    risk = query.get("at_risk", "false")
    if risk not in {"true", "false"}:
        raise invalid("at_risk")
    status = query.get("status", "open" if risk == "true" else "merged")
    if status not in {"open", "merged", "closed"} or (risk == "true" and status != "open"):
        raise invalid("status")
    state = query.get("state")
    if state is not None and (
        state not in {"waiting_reviewer", "waiting_author", "waiting_ci", "waiting_merge"}
        or status != "open"
    ):
        raise invalid("state")
    location = query.get("location")
    if location is not None and LOCATION_RE.fullmatch(location) is None:
        raise invalid("location")
    raw_limit = query.get("limit", "50")
    if not raw_limit.isascii() or not raw_limit.isdecimal() or not 1 <= int(raw_limit) <= 200:
        raise invalid("limit")
    cursor = query.get("cursor")
    if cursor is not None and CURSOR_RE.fullmatch(cursor) is None:
        raise invalid("cursor")
    return PrFilters(status, risk == "true", state, location), int(raw_limit), cursor
