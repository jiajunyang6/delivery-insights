import os
from dataclasses import replace
from pathlib import Path

import orjson
import pytest
from tests.analytics_factory import dataset, pr
from tests.factories import at

from insights.analytics import ANALYTICS_VERSION, derive_key
from insights.analytics.dataset import SnapshotParams
from insights.analytics.insight import insight_view
from insights.analytics.snapshot import build_snapshot, canonical, etag, identifiers, rounded
from insights.api.schemas import Insight
from insights_eval.generator import ScenarioSpec, generate
from insights_eval.pipeline import dataset_from_repo

GOLDEN = Path(__file__).parents[1] / "golden/snapshot_seed42.json"


@pytest.fixture(scope="module")
def synthetic():
    return dataset_from_repo(generate(ScenarioSpec.baseline(), 42))


def params(d):
    return SnapshotParams(tuple(r.repo for r in d.repos), d.period_from, d.period_to)


def test_golden_determinism_contract_and_independent_accounting(synthetic):
    payload = build_snapshot(synthetic, params=params(synthetic))
    again = build_snapshot(synthetic, params=params(synthetic))
    assert canonical(payload) == canonical(again)
    Insight.model_validate(insight_view(payload))
    assert set(payload["time_ledger"]) == {"merged_prs", "total_pr_hours", "states"}
    assert set(payload["time_ledger"]["states"]) == {
        "waiting_reviewer",
        "waiting_author",
        "waiting_merge",
    }
    assert "ci" not in payload["bottleneck_analysis"]
    assert "ci_source" not in payload["meta"]
    if os.getenv("UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_bytes(
            orjson.dumps(payload, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2) + b"\n"
        )
    assert payload == orjson.loads(GOLDEN.read_bytes())
    selected = [
        p
        for p in synthetic.prs
        if not p.facts.is_bot_author
        and not p.facts.is_backport
        and p.facts.ready_at
        and p.facts.merged_at
        and synthetic.start <= p.facts.merged_at < synthetic.as_of
    ]
    total = sum(
        (i.end_at - i.start_at).total_seconds() / 3600
        for p in selected
        for i in p.intervals
        if i.state not in {"coding", "closed"} and i.end_at
    )
    assert payload["meta"]["sample"]["merged_prs"] == len(selected)
    assert payload["time_ledger"]["total_pr_hours"] == round(total, 2)
    allocated = sum(
        p["waiting_reviewer_pr_hours"] for p in payload["bottleneck_analysis"]["locations"]
    )
    assert allocated == pytest.approx(
        payload["time_ledger"]["states"]["waiting_reviewer"]["pr_hours"], abs=0.04
    )


def test_identity_and_rounding():
    d = dataset([pr(1)])
    p = params(d)
    assert identifiers(d, p) == identifiers(d, p)
    assert identifiers(d, replace(p, directory_depth=3))[0] != identifiers(d, p)[0]
    assert (
        identifiers(replace(d, repos=(replace(d.repos[0], data_version=2),)), p)[0]
        != identifiers(d, p)[0]
    )
    assert etag(b"a") != etag(b"b")
    assert derive_key("label:area-", 3) == f"{ANALYTICS_VERSION}|1.0.0|label:area-|depth=3"
    assert rounded({"hours": 1.23456, "share": 0.123456, "ratio": 1.23456}) == {
        "hours": 1.23,
        "share": 0.1235,
        "ratio": 1.23,
    }
    assert rounded(at(1.5)) == "2026-01-01T01:30:00Z"
    with pytest.raises(ValueError):
        rounded(float("nan"))


def test_partial_period_and_multi_repo_watermark():
    d = dataset(
        [pr(i) for i in range(30)], repos=(replace(dataset([]).repos[0], last_synced_at=at(60)),)
    )
    payload = build_snapshot(d, params=params(d))
    assert not payload["period"]["complete"]
    assert payload["efficiency"]["merged_prs"]["value"] == 0
    second = replace(d.repos[0], repo="c/d", last_synced_at=at(65))
    d = replace(d, repos=(d.repos[0], second))
    payload = build_snapshot(d, params=params(d))
    assert payload["repos"] == ["a/b", "c/d"]
    assert "per_repo" not in payload
    assert payload["as_of"] == "2026-01-03T12:00:00Z"
