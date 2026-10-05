"""Flow eligibility and location assignment for one PR; no I/O."""

from collections import Counter

from insights.analytics.thresholds import DIRECTORY_LOCATIONS_PER_PR
from insights.analytics.types import PrFacts
from insights.domain import PullRequestRecord


def is_flow(facts: PrFacts) -> bool:
    # Flow metrics cover human, ready-for-review PRs; bot and backport PRs are excluded.
    """Return whether the PR is ready and eligible for human, non-backport flow metrics."""
    return not facts.is_bot_author and not facts.is_backport and facts.ready_at is not None


def locations_for(pr: PullRequestRecord, dimension: str, depth: int) -> tuple[str, ...]:
    """Return matching labels, else the most-touched directories, else "unclassified".

    Directory locations are the DIRECTORY_LOCATIONS_PER_PR most-touched paths at depth.
    """
    if dimension.startswith("label:"):
        prefix = dimension[6:].lower()
        labels = tuple(sorted({label for label in pr.labels if label.lower().startswith(prefix)}))
        if labels:
            return labels
    if pr.files:
        directories = Counter(
            "dir:" + ("/".join(path.split("/")[:depth]) if "/" in path else "/")
            for path in pr.files
        )
        return tuple(
            name
            for name, _ in sorted(directories.items(), key=lambda item: (-item[1], item[0]))[
                :DIRECTORY_LOCATIONS_PER_PR
            ]
        )
    return ("unclassified",)
