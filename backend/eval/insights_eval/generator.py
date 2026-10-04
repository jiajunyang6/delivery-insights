"""Deterministic source records with planted process changes."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np

from insights.domain import Actor, CiRun, Event, EventKind, PullRequestRecord
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
    first_approval_bonus: float
    rubber_stamp_large_share: float
    revert_rate: tuple[float, float]
    ci_enabled: bool
    ci_queue_mult: float
    ci_run_mult: float
    flaky_rate: tuple[float, float]
    reviewers_wait_for_ci: bool

    @classmethod
    def baseline(cls) -> "ScenarioSpec":
        """Return the no-intervention spec; normal closures, reverts and random variation remain."""
        return cls(
            "no_signal",
            {},
            {},
            {},
            1.0,
            0.0,
            0.0,
            (0.02, 0.02),
            False,
            1.0,
            1.0,
            (0.03, 0.03),
            False,
        )


@dataclass(frozen=True, slots=True)
class SyntheticRepo:
    """Observed records/runs plus inclusive reporting dates and the UTC observation cutoff."""

    repo: str
    default_branch: str
    records: tuple[PullRequestRecord, ...]
    ci_runs: tuple[CiRun, ...]
    period_from: date
    period_to: date
    as_of: datetime


def generate(spec: ScenarioSpec, seed: int) -> SyntheticRepo:
    """Build seeded GitHub-like records and optional CI runs without network or database I/O.

    History starts at START, interventions start at CURRENT, and observations stop at AS_OF.
    The returned report period ends on the preceding UTC date, inclusive. The same spec/seed
    reproduces the records with this generator; changing a spec can change later RNG draws.
    """
    rng = np.random.default_rng(seed)
    records: list[PullRequestRecord] = []
    runs: list[CiRun] = []
    next_number = 1000
    pending_supersedes: list[int] = []

    def ln(median: float, sigma: float) -> float:
        """Draw a positive lognormal value with the given median and log-scale dispersion."""
        return float(median * np.exp(sigma * rng.normal()))

    def later(at: datetime, hours: float) -> datetime:
        """Shift a timestamp by elapsed hours; negative values represent earlier authored work."""
        return at + timedelta(hours=hours)

    def make(
        number: int,
        created: datetime,
        area: str,
        *,
        title: str | None = None,
        body: str = "",
        author_override: Actor | None = None,
        simple: bool = False,
        force_merge: bool = False,
    ) -> PullRequestRecord:
        """Construct one lifecycle and append its CI runs and possible supersession candidate.

        simple creates a short approval/merge path; force_merge bypasses random closure/open
        outcomes. Either can still be open when its planned merge falls beyond AS_OF.
        """
        current = created >= CURRENT
        size = max(1, round(ln(80, 1.1) * (spec.size_mult if current else 1)))
        external = rng.random() < 0.2
        if author_override and author_override.login:
            external = author_override.login.startswith("ext")
        author = author_override or Actor(
            f"ext{rng.integers(1, 31):02}" if external else f"dev{rng.integers(1, 41):02}", False
        )
        if author_override is None and rng.random() < 0.05:
            author = Actor("dependabot[bot]", True)
        base = "release/9.0" if rng.random() < 0.03 else "main"
        if simple or force_merge:
            base = "main"
        draft = not simple and rng.random() < 0.15
        ready = later(created, ln(10, 0.8)) if draft else created
        # Controlled size intervention: larger changes take longer to code and revise.
        # The square-root coupling is a documented synthetic assumption, not a fitted effect.
        size_time_factor = float(np.sqrt(spec.size_mult)) if current else 1.0
        authored = later(created, -ln(4 if draft else 12, 1.0) * size_time_factor)
        events: list[Event] = []
        local_runs: list[CiRun] = []
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
            """Add a unique commit and, when enabled, a CI run linked by its SHA and PR number.

            Flakiness is encoded as two attempts and longer runtime, with success if finished.
            Runs still executing at AS_OF retain partial timestamps rather than a future result.
            """
            oid = f"{number:020x}{len(events):020x}"
            add(
                EventKind.COMMIT,
                at,
                author,
                oid=oid,
                authored_at=(authored_at or at).isoformat(),
                committed_at=at.isoformat(),
                reverts=[],
            )
            if spec.ci_enabled:
                queued = max(later(ready, 1 / 60), later(at, 1 / 60))
                queue = ln(10 / 60, 0.8) * (spec.ci_queue_mult if queued >= CURRENT else 1)
                duration = ln(1, 0.5) * (spec.ci_run_mult if queued >= CURRENT else 1)
                flaky = rng.random() < spec.flaky_rate[int(queued >= CURRENT)]
                started = later(queued, queue)
                ended = later(started, duration * (2 if flaky else 1))
                if queued < AS_OF:
                    local_runs.append(
                        CiRun(
                            number * 100 + len(local_runs),
                            "Build and test",
                            "pull_request",
                            oid,
                            "completed" if ended <= AS_OF else "in_progress",
                            "success" if ended <= AS_OF else None,
                            2 if flaky else 1,
                            queued,
                            started if started <= AS_OF else None,
                            min(ended, AS_OF),
                            (number,),
                        )
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
        first_review = later(ready, min(pickup, 1) if simple else pickup)
        if spec.reviewers_wait_for_ci and local_runs:
            first_review = max(
                first_review, later(max(run.updated_at for run in local_runs), ln(0.5, 0.5))
            )
        probability = 0.55 if size < 100 else 0.35 if size < 500 else 0.20
        probability = min(0.95, probability + (spec.first_approval_bonus if current else 0))
        rubber = current and size >= 500 and rng.random() < spec.rubber_stamp_large_share
        if rubber:
            first_review = later(ready, ln(0.1, 0.3))
        approved = bool(rubber or simple or author.is_bot or rng.random() < probability)
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
            if spec.reviewers_wait_for_ci and local_runs:
                clock = max(clock, later(max(run.updated_at for run in local_runs), ln(0.5, 0.5)))
            approved = round_number == 5 or rng.random() < min(0.95, 0.6 + 0.1 * round_number)
            add(
                EventKind.REVIEW,
                clock,
                reviewer,
                state="APPROVED" if approved else "CHANGES_REQUESTED",
            )
        if not simple and rng.random() < 0.3 and not local_only:
            clock = later(clock, ln(4, 1))
            reviewer = Actor(
                next(f"rev-x{i}" for i in range(1, 5) if f"rev-x{i}" != reviewer.login), False
            )
            add(EventKind.REVIEW, clock, reviewer, state="APPROVED")
        planned_merge = later(clock, min(1.5, ln(3, 1)) if simple else ln(3, 1))
        merge_at: datetime | None = planned_merge
        if rng.random() < 0.1:
            commit(clock + (planned_merge - clock) / 2)
        end = planned_merge
        outcome = 0.0 if simple or force_merge or author.is_bot else float(rng.random())
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
                pending_supersedes.append(number)
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
        # snapshot. Limit CI to the retained commit horizon after choosing a closure/open path.
        events = [e for e in events if e.occurred_at < AS_OF]
        local_runs = [
            run
            for run in local_runs
            if run.created_at
            <= max((e.occurred_at for e in events if e.kind == EventKind.COMMIT), default=ready)
            + timedelta(minutes=2)
            or run.created_at <= ready + timedelta(minutes=2)
        ]
        runs.extend(local_runs)
        label_draw = rng.random()
        labels = [area] if label_draw < 0.95 else []
        if label_draw < 0.05:
            labels.append(str(rng.choice([a for a in AREAS if a != area])))
        file_count = int(rng.integers(1, 6))
        closed_at = end if state != "OPEN" else None
        return PullRequestRecord(
            number=number,
            title=title or f"Change {number} in {area}",
            body_excerpt=body,
            url=f"https://github.com/synthetic/repo/pull/{number}",
            state=state,
            is_draft=draft and ready >= AS_OF,
            author=author,
            author_association="CONTRIBUTOR" if external else "MEMBER",
            base_ref=base,
            head_ref=f"change-{number}",
            created_at=created,
            updated_at=max([created, *(e.occurred_at for e in events)]),
            closed_at=closed_at,
            merged_at=merge_at,
            merge_commit_oid=f"{number:040x}" if merge_at else None,
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
    by_number = {p.number: i for i, p in enumerate(records)}
    for number in pending_supersedes:
        index = by_number[number]
        original = records[index]
        if original.closed_at is None:
            continue
        created = original.closed_at + timedelta(hours=float(rng.uniform(-12, 12)))
        if created >= AS_OF:
            continue
        successor = make(
            next_number,
            created,
            original.labels[0] if original.labels else "area-A",
            author_override=original.author,
            simple=True,
        )
        next_number += 1
        stable = f"syn-{number}-cross-{successor.number}"
        at = max(created, original.created_at)
        payload = {
            "source_repo": "synthetic/repo",
            "source_number": successor.number,
            "source_state": successor.state,
            "source_merged_at": successor.merged_at.isoformat() if successor.merged_at else None,
            "source_author": successor.author.login,
            "will_close": False,
        }
        cross = Event(
            EventKind.CROSS_REFERENCED,
            at,
            original.author,
            payload,
            dedup_key(EventKind.CROSS_REFERENCED, at, original.author.login, stable),
        )
        records[index] = replace(
            original,
            events=tuple(
                sorted(
                    (*original.events, cross), key=lambda e: (e.occurred_at, e.kind, e.dedup_key)
                )
            ),
            updated_at=max(original.updated_at, at),
        )
        records.append(successor)
    # Iterate a frozen set of originals so newly appended reverts are not themselves randomly
    # reverted in the same pass. Relands are added explicitly below when the revert has merged.
    for original in tuple(records):
        if (
            original.merged_at is None
            or rng.random() >= spec.revert_rate[int(original.merged_at >= CURRENT)]
        ):
            continue
        created = later(original.merged_at, ln(30, 0.8))
        if created >= AS_OF:
            continue
        area = original.labels[0] if original.labels else "area-A"
        revert = make(
            next_number,
            created,
            area,
            title=f'Revert "{original.title}"',
            body=f"Reverts synthetic/repo#{original.number}",
            author_override=Actor(
                next(f"dev{i:02}" for i in range(1, 41) if f"dev{i:02}" != original.author.login),
                False,
            ),
            simple=True,
        )
        next_number += 1
        records.append(revert)
        if revert.merged_at and rng.random() < 0.5:
            reland_at = later(revert.merged_at, ln(72, 0.5))
            if reland_at < AS_OF:
                reland = make(
                    next_number,
                    reland_at,
                    area,
                    title=f'Reland "{original.title}"',
                    body=f"Reland #{original.number}",
                    author_override=Actor(
                        next(
                            f"dev{i:02}"
                            for i in range(1, 41)
                            if f"dev{i:02}" != revert.author.login
                        ),
                        False,
                    ),
                    force_merge=True,
                )
                records.append(reland)
                next_number += 1
    return SyntheticRepo(
        "synthetic/repo",
        "main",
        tuple(records),
        tuple(sorted(runs, key=lambda r: r.run_id)),
        CURRENT.date(),
        (AS_OF - timedelta(days=1)).date(),
        AS_OF,
    )
