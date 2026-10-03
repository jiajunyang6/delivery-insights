from dataclasses import replace
from datetime import date

from insights.analytics.dataset import Dataset, PrData, RepoData, Review
from insights.analytics.timeline import Interval
from insights.analytics.types import PrFacts
from tests.factories import at


def pr(identifier, *, offset=24, reviewer=20, author=5, merge=5, locations=("area-A",), **facts):
    ready = at(offset + 10)
    reviewed = at(offset + 10 + reviewer)
    approved = at(offset + 10 + reviewer + author)
    merged = at(offset + 10 + reviewer + author + merge)
    f = PrFacts(
        number=identifier,
        ready_at=ready,
        first_review_at=reviewed,
        first_approval_at=approved,
        approved_at=approved,
        merged_at=merged,
        end_at=merged,
        coding_hours=10,
        pickup_hours=reviewer,
        review_hours=author,
        merge_hours=merge,
        cycle_hours=10 + reviewer + author + merge,
        locations=locations,
        location_source="label",
        human_reviews=2,
        size_lines=100,
        size_bucket="M",
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
        f"PR {identifier}",
        f"https://github.com/a/b/pull/{identifier}",
        "author",
        False,
        at(offset),
    )


def dataset(prs, *, repos=None, reviews=None, **changes):
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
        (),
        date(2026, 1, 3),
        date(2026, 1, 3),
    )
    return replace(base, **changes)
