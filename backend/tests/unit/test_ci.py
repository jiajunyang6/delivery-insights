from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest
from tests.analytics_factory import dataset, pr
from tests.factories import at, event, record

from insights.analytics.bottlenecks import merge_blockers
from insights.analytics.ci import (
    build_ci,
    map_runs,
    mapped_flow_runs,
    overlap_hours,
    union_intervals,
)
from insights.analytics.dataset import SnapshotParams
from insights.analytics.snapshot import build_snapshot
from insights.api.schemas import Snapshot
from insights.config import Settings
from insights.domain import CiRun, RepoRef
from insights.sources.github.actions import fetch_runs
from insights.sources.github.client import GitHubClient
from insights_eval.generator import generate
from insights_eval.pipeline import build_snapshot_from_repo
from insights_eval.scenarios import SCENARIOS


def run(identifier=1, start=50, end=52, **changes):
    return replace(
        CiRun(
            identifier,
            "Tests",
            "pull_request",
            "a" * 40,
            "completed",
            "success",
            1,
            at(start),
            at(start + 0.5),
            at(end),
            (1,),
        ),
        **changes,
    )


def raw(identifier, *, start=0, event_="pull_request"):
    return {
        "id": identifier,
        "name": "Tests",
        "event": event_,
        "head_sha": "a" * 40,
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 2,
        "created_at": at(start).isoformat(),
        "run_started_at": at(start + 0.5).isoformat(),
        "updated_at": at(start + 1).isoformat(),
        "pull_requests": [{"number": 1}],
    }


def test_union_and_fork_sha_mapping_preserves_all_matching_prs():
    records = {
        11: record(events=(event("commit", 0),)),
        12: record(number=2, events=(event("commit", 0),)),
        13: record(number=3),
    }
    runs = [run(pr_numbers=()), run(2, 51, 54, pr_numbers=(3,)), run(3, 54, 55)]
    mapped = map_runs(records, runs)
    assert set(mapped) == {11, 12, 13}
    assert {r.run_id for r in mapped[11]} == {1, 2, 3}
    ranges = union_intervals(mapped[11])
    assert ranges == ((at(50), at(55)),)
    assert overlap_hours(ranges, at(52), at(56)) == 3
    assert union_intervals([run(start=50, end=50)]) == ()
    flow = mapped_flow_runs(mapped, {11: 1, 13: 3})
    assert flow[0].pr_numbers == (1,)
    assert flow[1].pr_numbers == (1, 3)


def test_ci_cohort_gates_comparison_and_workflow_sorting():
    runs = tuple(
        ("a/b", run(i, pr_numbers=(i,), run_attempt=2 if i < 10 else 1)) for i in range(30)
    )
    runs += (("a/b", run(90, start=30, end=32)),)
    runs += (("a/b", run(91, pr_numbers=())),)
    d = replace(dataset([]), ci_runs=runs)
    ci = build_ci(d, "fixed", 0.75)
    assert ci["queue_p50_minutes"]["value"] == 30
    assert ci["run_p50_minutes"]["value"] == 90
    assert ci["rerun_rate"]["value"] == pytest.approx(1 / 3)
    assert ci["flaky_rerun_rate"]["value"] == pytest.approx(1 / 3)
    assert ci["runs_per_pr_p50"]["value"] == 1
    assert ci["queue_p50_minutes"]["n_previous"] == 1
    assert ci["queue_p50_minutes"]["previous"] is None
    assert ci["top_workflows"][0]["runs"] == 30
    assert ci["coverage"] == 0.75


def test_approval_ci_uses_raw_union_even_when_state_is_waiting_merge():
    prs = [replace(pr(i, ci_covered=True), ci_intervals=((at(58), at(65)),)) for i in range(10)]
    blockers = merge_blockers(dataset(prs))
    assert blockers["ci_after_approval_p50_hours"] == 5
    assert merge_blockers(dataset(prs[:9]))["ci_after_approval_p50_hours"] is None


def test_ci_scenario_contract_finding_and_no_ci_mode():
    snapshot = build_snapshot_from_repo(generate(SCENARIOS["ci_slowdown"], 101))
    Snapshot.model_validate(snapshot)
    assert snapshot["time_ledger"]["ci_data_available"]
    assert snapshot["time_ledger"]["ci_coverage"] > 0.9
    assert snapshot["bottleneck_analysis"]["ci"]["run_p50_minutes"]["n"] > 20
    assert any(f["type"] == "ci_wait" for f in snapshot["bottlenecks"])
    assert any(w["stage"] == "ci" for w in snapshot["bottleneck_analysis"]["what_if"])
    d = dataset([pr(i) for i in range(25)])
    empty = build_snapshot(
        d, params=SnapshotParams(("a/b",), d.period_from, d.period_to, ci_source="actions")
    )
    assert empty["bottleneck_analysis"]["ci"]["coverage"] == 0
    assert not empty["time_ledger"]["ci_data_available"]


async def test_actions_splits_over_limit_days_caps_child_windows_and_deduplicates(
    respx_mock, capsys
):
    requested = []

    def respond(request):
        params = request.url.params
        created = params["created"]
        requested.append((created, int(params["page"])))
        assert params["event"] == "pull_request" and params["per_page"] == "100"
        if ".." not in created:
            return httpx.Response(200, json={"total_count": 1001, "workflow_runs": []})
        total = 1001 if "T00:00:00Z" in created else 1
        return httpx.Response(
            200,
            json={
                "total_count": total,
                "workflow_runs": [
                    raw(1, start=1),
                    raw(2, start=2, event_="push"),
                    raw(3, start=-1),
                ],
            },
        )

    respx_mock.get("https://api.github.com/repos/a/b/actions/runs").mock(side_effect=respond)
    client = GitHubClient(Settings(), sleep=AsyncMock())
    try:
        runs = await fetch_runs(client, RepoRef("a", "b"), created_from=at(0), created_to=at(23))
    finally:
        await client.aclose()
    assert [r.run_id for r in runs] == [1]
    assert len(requested) == 14
    assert len({c for c, _ in requested if ".." in c}) == 4
    assert max(page for _, page in requested) == 10
    assert "ci_window_truncated" in capsys.readouterr().out
