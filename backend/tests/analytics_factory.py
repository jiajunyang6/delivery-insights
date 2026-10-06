"""Builders for analytics inputs: PrData with fixed intervals and Datasets."""

from dataclasses import replace
from datetime import date

from insights.analytics.dataset import Dataset, PrData, RepoData, Review
from insights.analytics.facts import PrFacts
from insights.analytics.timeline import Interval
from tests.factories import at


def pr(identifier, *, offset=24, reviewer=20, author=5, merge=5, locations=("area-A",), **facts):
    """Build a merged flow PR whose intervals follow reviewer, author and merge hours."""
    ready = at(offset + 10)
    reviewed = at(offset + 10 + reviewer)
    approved = at(offset + 10 + reviewer + author)
    merged = at(offset + 10 + reviewer + author + merge)
    f = PrFacts(
        number=identifier,
        ready_at=ready,
        first_review_at=reviewed,
        merged_at=merged,
        end_at=merged,
        coding_hours=10,
        pickup_hours=reviewer,
        cycle_hours=10 + reviewer + author + merge,
        locations=locations,
        size_lines=100,
    )
    f = replace(f, **facts)
    intervals = tuple(
        Interval(state, start, end)
        for state, start, end in [
            ("coding", at(offset), ready),
            ("waiting_reviewer", ready, reviewed),
            ("waiting_author", reviewed, approved),
            ("waiting_merge", approved, merged),
        ]
        if start < end
    )
    return PrData(
        identifier,
        "a/b",
        f,
        intervals,
        identifier,
        at(offset),
        human_activity_at=tuple(
            at
            for at in (f.ready_at, f.first_review_at, approved, f.merged_at, f.closed_at)
            if at is not None
        ),
    )


def dataset(prs, *, repos=None, reviews=None, **changes):
    """Build a one-repo Dataset for 2026-01-03, with one review per reviewed PR."""
    base = Dataset(
        repos or (RepoData("a/b", 1, at(-5000), at(72)),),
        tuple(prs),
        tuple(
            reviews
            or [
                Review("reviewer", p.facts.first_review_at, p.pr_id)
                for p in prs
                if p.facts.first_review_at
            ]
        ),
        date(2026, 1, 3),
        date(2026, 1, 3),
    )
    return replace(base, **changes)
