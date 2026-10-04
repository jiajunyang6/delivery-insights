import re
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from pathspec import PathSpec

from insights.analytics.thresholds import (
    DIRECTORY_LOCATIONS_PER_PR,
    LATE_REJECTION_DAYS,
    LATE_REJECTION_ROUNDS,
    SUPERSEDE_WINDOW_DAYS,
)
from insights.analytics.timeline import human_event
from insights.analytics.types import PrFacts
from insights.domain import EventKind, OwnershipRule, PullRequestRecord

LINK_FIELDS = (
    "is_revert",
    "reverts_pr_id",
    "reverted_by_pr_id",
    "reverted_at",
    "reland_of_pr_id",
    "close_class",
    "late_rejection",
)


def is_flow(facts: PrFacts) -> bool:
    return not facts.is_bot_author and not facts.is_backport and facts.ready_at is not None


def locations_for(
    pr: PullRequestRecord, dimension: str, depth: int, rules: Sequence[OwnershipRule]
) -> tuple[tuple[str, ...], str]:
    if dimension.startswith("label:"):
        prefix = dimension[6:].lower()
        labels = tuple(sorted({label for label in pr.labels if label.lower().startswith(prefix)}))
        if labels:
            return labels, "label"
    if dimension != "directory":
        locations: set[str] = set()
        compiled = [
            (rule, PathSpec.from_lines("gitwildmatch", [rule.pattern]))
            for rule in sorted(rules, key=lambda r: r.line_no)
            if rule.source == "codeowners"
        ]
        for path in pr.files:
            match = next(
                (rule for rule, pattern in reversed(compiled) if pattern.match_file(path)), None
            )
            if match:
                locations.add("codeowners:" + match.pattern)
        if locations:
            return tuple(sorted(locations)), "codeowners"
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
        ), "directory"
    return ("unclassified",), "unclassified"


@dataclass(frozen=True, slots=True)
class LinkInput:
    pr_id: int
    record: PullRequestRecord
    facts: PrFacts


class HeadIndex:
    """Range minimum over creation times; avoids quadratic supersession scans."""

    def __init__(self, items: Sequence[LinkInput]) -> None:
        ordered = sorted(items, key=lambda p: (p.record.created_at, p.pr_id))
        self.dates = [p.record.created_at for p in ordered]
        self.size = 1 << max(0, (len(ordered) - 1).bit_length())
        self.tree: list[tuple[datetime, int] | None] = [None] * (2 * self.size)
        for index, item in enumerate(ordered):
            self.tree[self.size + index] = (
                item.record.merged_at or item.record.created_at,
                item.pr_id,
            )
        for index in range(self.size - 1, 0, -1):
            self.tree[index] = self.minimum(self.tree[index * 2], self.tree[index * 2 + 1])

    @staticmethod
    def minimum(
        a: tuple[datetime, int] | None, b: tuple[datetime, int] | None
    ) -> tuple[datetime, int] | None:
        return b if a is None else (a if b is None else min(a, b))

    def earliest(self, start: datetime, end: datetime) -> tuple[datetime, int] | None:
        left = self.size + bisect_left(self.dates, start)
        right = self.size + bisect_right(self.dates, end)
        result = None
        while left < right:
            if left % 2:
                result = self.minimum(result, self.tree[left])
                left += 1
            if right % 2:
                right -= 1
                result = self.minimum(result, self.tree[right])
            left //= 2
            right //= 2
        return result


@dataclass(frozen=True, slots=True)
class LinkIndexes:
    by_number: dict[int, LinkInput]
    by_id: dict[int, LinkInput]
    merged_titles: dict[str, list[LinkInput]]
    sha_index: dict[str, list[LinkInput]]
    head_indexes: dict[tuple[str, str], HeadIndex]
    sha_dates: dict[str, list[datetime]]
    title_dates: dict[str, list[datetime]]


def build_link_indexes(prs: Sequence[LinkInput], default_branch: str) -> LinkIndexes:
    merged_titles: dict[str, list[LinkInput]] = defaultdict(list)
    sha_index: dict[str, list[LinkInput]] = defaultdict(list)
    heads: dict[tuple[str, str], list[LinkInput]] = defaultdict(list)
    for item in sorted(prs, key=lambda p: (p.record.merged_at or p.record.created_at, p.pr_id)):
        pr = item.record
        if pr.merged_at:
            if pr.base_ref == default_branch:
                merged_titles[pr.title].append(item)
            oids = {e.payload["oid"] for e in pr.events if e.kind == EventKind.COMMIT}
            if pr.merge_commit_oid:
                oids.add(pr.merge_commit_oid)
            for oid in oids:
                for length in range(7, len(oid) + 1):
                    sha_index[oid[:length]].append(item)
            if pr.author.login:
                heads[(pr.author.login.lower(), pr.head_ref)].append(item)
    head_indexes = {key: HeadIndex(items) for key, items in heads.items()}
    sha_dates = {
        sha: [p.record.merged_at for p in items if p.record.merged_at is not None]
        for sha, items in sha_index.items()
    }
    title_dates = {
        title: [p.record.merged_at for p in items if p.record.merged_at is not None]
        for title, items in merged_titles.items()
    }
    return LinkIndexes(
        {p.record.number: p for p in prs},
        {p.pr_id: p for p in prs},
        dict(merged_titles),
        dict(sha_index),
        head_indexes,
        sha_dates,
        title_dates,
    )


def link_reverts(
    prs: Sequence[LinkInput],
    output: dict[int, PrFacts],
    indexes: LinkIndexes,
    repo_full_name: str,
) -> None:
    for item in sorted(prs, key=lambda p: (p.record.created_at, p.pr_id)):
        pr, facts = item.record, output[item.pr_id]
        title = re.match(r'^Revert\s+"(?P<title>.+)"\s*$', pr.title, re.I)
        body = re.search(r"(?m)^Reverts\s+([\w.-]+/[\w.-]+)#(\d+)\b", pr.body_excerpt)
        reverts = [
            sha
            for e in pr.events
            if e.kind == EventKind.COMMIT
            for sha in e.payload.get("reverts", [])
        ]
        is_revert = bool(title or body or (pr.title.lower().startswith("revert") and reverts))
        original: LinkInput | None = None
        if body and body[1].lower() == repo_full_name.lower():
            original = indexes.by_number.get(int(body[2]))
        if original is None:
            candidates = []
            for sha in reverts:
                entries = indexes.sha_index.get(sha, [])
                index = bisect_left(indexes.sha_dates.get(sha, []), pr.created_at) - 1
                if index >= 0:
                    candidates.append(entries[index])
            original = max(
                candidates,
                key=lambda p: (p.record.merged_at or p.record.created_at, p.pr_id),
                default=None,
            )
        if original is None and title and title[1] in indexes.merged_titles:
            entries = indexes.merged_titles[title[1]]
            index = bisect_left(indexes.title_dates[title[1]], pr.created_at) - 1
            if index >= 0:
                original = entries[index]
        if is_revert:
            if original and output[original.pr_id].is_revert:
                facts = replace(facts, reland_of_pr_id=output[original.pr_id].reverts_pr_id)
            else:
                facts = replace(
                    facts,
                    is_revert=True,
                    reverts_pr_id=original.pr_id if original and pr.merged_at else None,
                )
                if original and pr.merged_at:
                    old = output[original.pr_id]
                    if old.reverted_at is None or pr.merged_at < old.reverted_at:
                        output[original.pr_id] = replace(
                            old, reverted_by_pr_id=item.pr_id, reverted_at=pr.merged_at
                        )
        output[item.pr_id] = facts


def link_relands_and_closes(
    prs: Sequence[LinkInput],
    output: dict[int, PrFacts],
    indexes: LinkIndexes,
    repo_full_name: str,
) -> None:
    reverted = {identifier: facts for identifier, facts in output.items() if facts.reverted_at}
    reverted_by_title = {
        indexes.by_id[identifier].record.title: identifier for identifier in sorted(reverted)
    }
    for item in prs:
        pr, facts = item.record, output[item.pr_id]
        if re.match(r"^(Reland|Re-land|Reapply|Re-apply)\b", pr.title, re.I):
            reference = re.search(r"#(\d+)", pr.title + "\n" + pr.body_excerpt)
            original_id = None
            if reference and int(reference[1]) in indexes.by_number:
                candidate_id = indexes.by_number[int(reference[1])].pr_id
                if candidate_id in reverted:
                    original_id = candidate_id
            quoted = re.search(r'"(.+)"', pr.title)
            raw_title = re.sub(
                r"^(Reland|Re-land|Reapply|Re-apply)\s*", "", pr.title, flags=re.I
            ).strip('" ')
            if original_id is None:
                original_id = reverted_by_title.get(quoted[1] if quoted else raw_title)
            facts = replace(facts, reland_of_pr_id=original_id)
        if pr.state == "CLOSED" and pr.closed_at and is_flow(facts):
            author = pr.author.login.lower() if pr.author.login else None
            superseded: list[tuple[datetime, int | None]] = []
            for event in pr.events:
                if event.kind != EventKind.CROSS_REFERENCED:
                    continue
                payload = event.payload
                if payload["source_repo"].lower() != repo_full_name.lower():
                    continue
                source = indexes.by_number.get(payload["source_number"])
                source_author = (
                    source.record.author.login if source else payload.get("source_author")
                )
                merged_at = (
                    source.record.merged_at
                    if source
                    else (
                        datetime.fromisoformat(payload["source_merged_at"])
                        if payload.get("source_merged_at")
                        else None
                    )
                )
                if (
                    author
                    and source_author
                    and author == source_author.lower()
                    and merged_at
                    and (
                        pr.created_at
                        <= merged_at
                        <= pr.closed_at + timedelta(days=SUPERSEDE_WINDOW_DAYS)
                    )
                ):
                    superseded.append((merged_at, source.pr_id if source else None))
            key = (author or "", pr.head_ref)
            if author and key in indexes.head_indexes:
                successor_candidate = indexes.head_indexes[key].earliest(
                    pr.closed_at, pr.closed_at + timedelta(days=SUPERSEDE_WINDOW_DAYS)
                )
                if successor_candidate:
                    superseded.append(successor_candidate)
            close_class = (
                "superseded"
                if superseded
                else ("no_review" if not facts.human_reviews else "abandoned")
            )
            if close_class == "abandoned":
                closing = [e for e in pr.events if e.kind == EventKind.CLOSED]
                final_close = max(closing, key=lambda e: e.occurred_at, default=None)
                if final_close and human_event(final_close, pr.author.login):
                    close_class = "rejected"
            late = close_class == "rejected" and (
                bool(
                    facts.ready_at
                    and pr.closed_at - facts.ready_at >= timedelta(days=LATE_REJECTION_DAYS)
                )
                or facts.review_rounds >= LATE_REJECTION_ROUNDS
            )
            facts = replace(facts, close_class=close_class, late_rejection=late)
        output[item.pr_id] = facts


def link_prs(
    prs: Sequence[LinkInput],
    *,
    repo_full_name: str,
    default_branch: str,
) -> dict[int, PrFacts]:
    output = {
        p.pr_id: replace(
            p.facts,
            is_revert=False,
            reverts_pr_id=None,
            reverted_by_pr_id=None,
            reverted_at=None,
            reland_of_pr_id=None,
            close_class=None,
            late_rejection=False,
        )
        for p in prs
    }
    indexes = build_link_indexes(prs, default_branch)
    link_reverts(prs, output, indexes, repo_full_name)
    link_relands_and_closes(prs, output, indexes, repo_full_name)
    return output


def ownership_counts(rules: Sequence[OwnershipRule]) -> tuple[tuple[str, int], ...]:
    areas: dict[str, set[str]] = {}
    code: dict[str, int] = {}
    for rule in sorted(rules, key=lambda r: (r.source, r.line_no)):
        if rule.source == "area_owners":
            areas.setdefault(rule.pattern, set()).update(rule.owners)
        elif rule.source == "codeowners":
            code["codeowners:" + rule.pattern] = len(set(rule.owners))
    return tuple(sorted({**code, **{key: len(owners) for key, owners in areas.items()}}.items()))
