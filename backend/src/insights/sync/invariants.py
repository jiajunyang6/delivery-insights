import argparse
import asyncio
from collections import defaultdict

from sqlalchemy import select

from insights.analytics.timeline import Interval, TimelineResult, check_invariants, pr_input
from insights.config import Settings
from insights.db.engine import create_database
from insights.db.models import PrFact, PrInterval, PullRequest, Repository
from insights.db.records import load_records


async def check_repository(repo_name: str) -> int:
    engine, sessions = create_database(Settings())
    try:
        async with sessions() as session:
            repo_id = await session.scalar(
                select(Repository.id).where(Repository.full_name_lower == repo_name.lower())
            )
            if repo_id is None:
                print("Repository not found")
                return 1
            prs = (
                await session.scalars(select(PullRequest).where(PullRequest.repo_id == repo_id))
            ).all()
            records = await load_records(session, prs)
            facts = {
                f.pr_id: f
                for f in await session.scalars(select(PrFact).where(PrFact.repo_id == repo_id))
            }
            intervals: dict[int, list[Interval]] = defaultdict(list)
            for row in await session.scalars(
                select(PrInterval)
                .where(PrInterval.repo_id == repo_id)
                .order_by(PrInterval.pr_id, PrInterval.seq)
            ):
                intervals[row.pr_id].append(Interval(row.state, row.start_at, row.end_at))
            violations = 0
            for pr in prs:
                if pr.id not in facts:
                    violations += 1
                    continue
                f = facts[pr.id]
                result = TimelineResult(
                    f.ready_at,
                    tuple(intervals[pr.id]),
                    f.approved_at,
                    f.review_rounds,
                    f.state_at_close,
                )
                violations += len(check_invariants(result, pr_input(records[pr.id])))
            print(f"{len(prs)} PRs, {violations} violations")
            return int(violations > 0)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check persisted timeline invariants")
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(check_repository(args.repo)))
