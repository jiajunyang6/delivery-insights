from copy import deepcopy

import pytest
from tests.analytics_factory import dataset, pr
from tests.unit.test_bottlenecks import half_unreviewed_dataset
from tests.unit.test_snapshot import params

from insights.analytics.dataset import closed, hours
from insights.analytics.findings import build_findings, headline, resolve_pointer
from insights.analytics.snapshot import build_snapshot
from insights_eval.generator import generate
from insights_eval.pipeline import build_snapshot_from_repo, dataset_from_repo
from insights_eval.scenarios import SCENARIOS


def base():
    d = dataset([pr(i) for i in range(30)])
    s = build_snapshot(d, params=params(d))
    s["efficiency"]["review_concentration_top_k"]["value"] = 0
    s["time_ledger"]["states"]["waiting_merge"]["share"] = 0
    return d, s


@pytest.mark.parametrize(
    ("kind", "path", "value"),
    [
        ("review_concentration", "/efficiency/review_concentration_top_k/value", 0.60),
        ("merge_blocked", "/time_ledger/states/waiting_merge/share", 0.15),
        ("rework_high", "/efficiency/avg_review_rounds/value", 2.5),
        ("waste_high", "/efficiency/waste_share/value", 0.15),
        ("external_contributor_wait", "/signals/external_pickup_ratio/value", 2.0),
    ],
)
def test_finding_threshold_boundaries(kind, path, value):
    d, s = base()
    parent, key = path.rsplit("/", 1)
    node = resolve_pointer(s, parent)
    node[key] = value - 0.001
    assert kind not in {f["type"] for f in build_findings(s, d)}
    node[key] = value
    assert kind in {f["type"] for f in build_findings(s, d)}
    node[key] = None
    assert kind not in {f["type"] for f in build_findings(s, d)}


def test_capacity_queue_guardrail_sort_and_pointers():
    d, s = base()
    loc = s["bottleneck_analysis"]["locations"][0]
    loc.update(pickup_ratio_vs_rest=1.5, waiting_reviewer_share=0.3)
    queue = s["bottleneck_analysis"]["review_queue"]
    queue.update(weeks_total=4, weeks_inflow_exceeds_outflow=2, net_inflow_share=0.5)
    s["guardrail"]["verdict"] = "tradeoff_suspected"
    findings = build_findings(s, d)
    assert {f["type"] for f in findings} == {
        "review_capacity",
        "review_queue_growth",
        "quality_guardrail",
    }
    assert [f["rank"] for f in findings] == [1, 2, 3]
    assert findings[-1]["type"] == "quality_guardrail"
    for f in findings:
        assert f["impact_share"] == f["impact_pr_hours"] / s["time_ledger"]["total_pr_hours"]
        for evidence in f["evidence"]:
            resolve_pointer(s, evidence["ref"])
    s["efficiency"]["merged_prs"]["value"] = 19
    assert build_findings(s, d) == []


def test_headline_all_efficiency_forms_and_optional_clauses():
    d, s = base()
    c = s["efficiency"]["cycle_time_p50_hours"]
    c.update(value=40, previous=20, change_rel=1, significant=True)
    s["bottlenecks"] = []
    assert headline(s).startswith("Median cycle time rose 100%")
    c.update(change_rel=-0.5, previous=80)
    assert "fell 50%" in headline(s)
    c.update(significant=False, n=38)
    assert "-50% vs previous period, within normal variation for 38 merged PRs" in headline(s)
    c["change_rel"] = -0.05
    assert "-5% vs previous period, no meaningful change" in headline(s)
    c["previous"] = None
    assert "no previous period" in headline(s)
    c["value"] = None
    assert headline(s) == "Not enough merged PRs for a reliable cycle time."
    s["bottleneck_analysis"]["locations"][0].update(
        pickup_ratio_vs_rest=2, waiting_reviewer_share=0.5
    )
    s["bottlenecks"] = build_findings(s, d)
    assert "Capping pickup" in headline(s)
    without = deepcopy(s)
    without["bottlenecks"][0]["what_if"] = None
    assert "Capping" not in headline(without)


def test_balanced_review_flow_does_not_report_growing_period_queue():
    d, s = base()
    s["bottleneck_analysis"]["review_queue"].update(
        weeks=[
            {"inflow": 10, "outflow": 10, "open_at_week_end": size} for size in (10, 20, 30, 40)
        ],
        weeks_total=4,
        weeks_inflow_exceeds_outflow=0,
        open_growth_rel=3,
        net_inflow_share=0,
    )
    assert "review_queue_growth" not in {f["type"] for f in build_findings(s, d)}
    # Even many imbalanced weeks cannot overcome a balanced total flow.
    s["bottleneck_analysis"]["review_queue"]["weeks_inflow_exceeds_outflow"] = 3
    assert "review_queue_growth" not in {f["type"] for f in build_findings(s, d)}


@pytest.mark.parametrize(
    ("unserved", "weeks", "severity"),
    [
        (None, 2, None),
        (-0.1, 2, None),
        (0.1999, 2, None),
        (0.2, 1, None),
        (0.2, 2, "medium"),
        (0.4999, 2, "medium"),
        (0.5, 2, "high"),
    ],
)
def test_queue_finding_uses_unserved_demand_boundaries(unserved, weeks, severity):
    d, s = base()
    s["bottleneck_analysis"]["review_queue"].update(
        weeks_total=4,
        weeks_inflow_exceeds_outflow=weeks,
        net_inflow_share=unserved,
        open_growth_rel=None,
    )
    queue = [f for f in build_findings(s, d) if f["type"] == "review_queue_growth"]
    assert (queue[0]["severity"] if queue else None) == severity


def test_half_unreviewed_prs_trigger_high_demand_finding():
    d = half_unreviewed_dataset()
    snapshot = build_snapshot(d, params=params(d))
    finding = next(f for f in snapshot["bottlenecks"] if f["type"] == "review_queue_growth")
    assert finding["severity"] == "high"
    assert finding["title"] == "Review demand exceeds first reviews"
    assert finding["evidence"][1] == {
        "label": "Share of new review demand not yet served",
        "value": 0.5,
        "unit": "share",
        "ref": "/bottleneck_analysis/review_queue/net_inflow_share",
    }


@pytest.mark.parametrize("seed", [101, 202])
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_finding_shares_use_all_finished_pr_time_and_never_exceed_one(name, seed):
    syn = generate(SCENARIOS[name], seed)
    data = dataset_from_repo(syn)
    snapshot = build_snapshot_from_repo(syn)
    finished = snapshot["time_ledger"]["total_pr_hours"] + sum(
        sum(hours(p, end=data.as_of).values()) for p in closed(data, data.current)
    )
    for finding in snapshot["bottlenecks"]:
        assert 0 <= finding["impact_share"] <= 1
        assert finding["impact_share"] == pytest.approx(
            finding["impact_pr_hours"] / finished, abs=1e-3
        )
