"""Deterministic source records with planted process changes."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np

from insights.domain import Actor, Event, EventKind, PullRequestRecord
from insights.sources.github.normalize import dedup_key

AREAS = ("area-A", "area-B", "area-C", "area-D", "area-E")
WEIGHTS = (0.30, 0.25, 0.20, 0.15, 0.10)
START = datetime(2025, 9, 8, tzinfo=UTC)
CURRENT = datetime(2026, 1, 19, tzinfo=UTC)
AS_OF = datetime(2026, 3, 2, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    """Synthetic interventions applied after CURRENT, with baseline behavior before it.

    Multipliers scale arrivals, PR size or elapsed hours; rates are (baseline, current).
    This describes planted assumptions, not effect sizes inferred from a real repository.
    """

    name: str
    pickup_mult: Mapping[str, float]
    arrival_mult: Mapping[str, float]
    area_reviewers: Mapping[str, int]
    size_mult: float

    @classmethod
    def baseline(cls) -> "ScenarioSpec":
        """Return the no-intervention spec; normal closures and random variation remain."""
        return cls("no_signal", {}, {}, {}, 1.0)


@dataclass(frozen=True, slots=True)
class SyntheticRepo:
    """Observed records plus inclusive reporting dates and the UTC observation cutoff."""

    repo: str
    default_branch: str
    records: tuple[PullRequestRecord, ...]
    period_from: date
    period_to: date
    as_of: datetime


def generate(spec: ScenarioSpec, seed: int) -> SyntheticRepo:
    """Build seeded GitHub-like records without network or database I/O.

    History starts at START, interventions start at CURRENT, and observations stop at AS_OF.
    The returned report period ends on the preceding UTC date, inclusive. The same spec/seed
    reproduces the records with this generator; changing a spec can change later RNG draws.
    """
    rng = np.random.default_rng(seed)
    records: list[PullRequestRecord] = []
    next_number = 1000

    def ln(median: float, sigma: float) -> float:
        """Draw a positive lognormal value with the given median and log-scale dispersion."""
        return float(median * np.exp(sigma * rng.normal()))

    def later(at: datetime, hours: float) -> datetime:
        """Shift a timestamp by elapsed hours; negative values represent earlier authored work."""
        return at + timedelta(hours=hours)

    def make(number: int, created: datetime, area: str) -> PullRequestRecord:
        """Construct one PR lifecycle."""
        current = created >= CURRENT
        size = max(1, round(ln(80, 1.1) * (spec.size_mult if current else 1)))
        external = rng.random() < 0.2
        author = Actor(
            f"ext{rng.integers(1, 31):02}" if external else f"dev{rng.integers(1, 41):02}", False
        )
        if rng.random() < 0.05:
            author = Actor("dependabot[bot]", True)
        base = "release/9.0" if rng.random() < 0.03 else "main"
        draft = rng.random() < 0.15
        ready = later(created, ln(10, 0.8)) if draft else created
        # Controlled size intervention: larger changes take longer to code and revise.
        # The square-root coupling is a documented synthetic assumption, not a fitted effect.
        size_time_factor = float(np.sqrt(spec.size_mult)) if current else 1.0
        authored = later(created, -ln(4 if draft else 12, 1.0) * size_time_factor)
        events: list[Event] = []
        area_letter = area[-1].lower()

        def add(kind: EventKind, at: datetime, actor: Actor, **payload: Any) -> Event:
            """Append an event with a stable synthetic ID and review metadata when applicable."""
            stable = f"syn-{number}-{len(events)}"
            if kind == EventKind.REVIEW:
                payload.update(review_id=stable, dismissed=False)
            ev = Event(kind, at, actor, payload, dedup_key(kind, at, actor.login, stable))
            events.append(ev)
            return ev

        def commit(at: datetime, authored_at: datetime | None = None) -> None:
            """Add a unique commit with authored and committed timestamps."""
            oid = f"{number:020x}{len(events):020x}"
            add(
                EventKind.COMMIT,
                at,
                author,
                oid=oid,
                authored_at=(authored_at or at).isoformat(),
                committed_at=at.isoformat(),
            )

        commit(max(created, later(authored, ln(0.5, 0.5))), authored)
        if draft:
            add(EventKind.READY_FOR_REVIEW, ready, author)
        reviewer_count = spec.area_reviewers.get(area, 4) if current else 4
        local_only = current and area == "area-B" and spec.name == "review_capacity"
        cross_area = rng.random() < 0.2 and not local_only
        reviewer = Actor(
            f"rev-x{rng.integers(1, 5)}"
            if cross_area
            else f"rev-{area_letter}{rng.integers(1, max(1, reviewer_count) + 1)}",
            False,
        )
        if rng.random() < 0.7:
            add(
                EventKind.REVIEW_REQUESTED,
                ready,
                author,
                reviewer=reviewer.login,
                reviewer_type="User",
            )
        base_events = tuple(events)
        area_mult = {"area-B": 1.2, "area-C": 0.9, "area-D": 1.1}.get(area, 1.0)
        pickup = ln(6 * area_mult * (spec.pickup_mult.get(area, 1) if current else 1), 1.0)
        first_review = later(ready, pickup)
        probability = 0.55 if size < 100 else 0.35 if size < 500 else 0.20
        approved = bool(author.is_bot or rng.random() < probability)
        first_state = (
            "APPROVED" if approved else ("CHANGES_REQUESTED" if rng.random() < 0.6 else "COMMENTED")
        )
        add(EventKind.REVIEW, first_review, reviewer, state=first_state)
        clock = first_review
        for round_number in range(1, 6):
            if approved:
                break
            clock = later(clock, ln(8, 1) * size_time_factor)
            if rng.random() < 0.8:
                commit(clock)
            else:
                add(EventKind.COMMENT, clock, author)
            clock = later(clock, ln(5, 1))
            approved = round_number == 5 or rng.random() < min(0.95, 0.6 + 0.1 * round_number)
            add(
                EventKind.REVIEW,
                clock,
                reviewer,
                state="APPROVED" if approved else "CHANGES_REQUESTED",
            )
        if rng.random() < 0.3 and not local_only:
            clock = later(clock, ln(4, 1))
            reviewer = Actor(
                next(f"rev-x{i}" for i in range(1, 5) if f"rev-x{i}" != reviewer.login), False
            )
            add(EventKind.REVIEW, clock, reviewer, state="APPROVED")
        planned_merge = later(clock, ln(3, 1))
        merge_at: datetime | None = planned_merge
        if rng.random() < 0.1:
            commit(clock + (planned_merge - clock) / 2)
        end = planned_merge
        outcome = 0.0 if author.is_bot else float(rng.random())
        state = "MERGED"
        if 0.85 <= outcome < 0.95:
            state = "CLOSED"
            ending = float(rng.random())
            if ending < 0.3:
                events = list(base_events)
                end = later(ready, ln(120, 0.6))
                closer = author
            elif ending < 0.85:
                events = list(base_events)
                add(EventKind.REVIEW, first_review, reviewer, state="CHANGES_REQUESTED")
                end = later(first_review, ln(8, 1))
                closer = reviewer if ending < 0.6 else author
            else:
                events = list(base_events)
                end = later(first_review, ln(8, 1))
                closer = author
            merge_at = None
            add(EventKind.CLOSED, end, closer)
        elif outcome >= 0.95:
            state, merge_at = "OPEN", None
            stop = [ready, first_review, clock][int(rng.integers(0, 3))]
            events = [e for e in events if e.occurred_at <= stop]
            end = AS_OF
        else:
            add(EventKind.MERGED, planned_merge, reviewer)
        if end >= AS_OF:
            state, merge_at = "OPEN", None
        # Keep only observed events: future approvals/merges must not leak into the historical
        # snapshot.
        events = [e for e in events if e.occurred_at < AS_OF]
        label_draw = rng.random()
        labels = [area] if label_draw < 0.95 else []
        if label_draw < 0.05:
            labels.append(str(rng.choice([a for a in AREAS if a != area])))
        file_count = int(rng.integers(1, 6))
        closed_at = end if state != "OPEN" else None
        return PullRequestRecord(
            number=number,
            title=f"Change {number} in {area}",
            url=f"https://github.com/synthetic/repo/pull/{number}",
            state=state,
            is_draft=draft and ready >= AS_OF,
            author=author,
            base_ref=base,
            created_at=created,
            updated_at=max([created, *(e.occurred_at for e in events)]),
            closed_at=closed_at,
            merged_at=merge_at,
            additions=round(0.7 * size),
            deletions=size - round(0.7 * size),
            labels=tuple(sorted(set(labels))),
            files=tuple(f"src/{area[-1]}/file{k}.cs" for k in range(file_count)),
            events=tuple(sorted(events, key=lambda e: (e.occurred_at, e.kind, e.dedup_key))),
        )

    day = START
    while day < AS_OF:
        current = day >= CURRENT
        weights = np.array(
            [
                weight * (spec.arrival_mult.get(area, 1) if current and day.weekday() < 5 else 1)
                for area, weight in zip(AREAS, WEIGHTS, strict=True)
            ]
        )
        daily = int(rng.poisson((15 if day.weekday() < 5 else 5) * weights.sum()))
        for _ in range(daily):
            created = day + timedelta(seconds=float(rng.uniform(0, 86400)))
            area = str(rng.choice(AREAS, p=weights / weights.sum()))
            records.append(make(next_number, created, area))
            next_number += 1
        day += timedelta(days=1)
    return SyntheticRepo(
        "synthetic/repo",
        "main",
        tuple(records),
        CURRENT.date(),
        (AS_OF - timedelta(days=1)).date(),
        AS_OF,
    )
