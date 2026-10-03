from copy import deepcopy

import pytest
from tests.analytics_factory import dataset, pr
from tests.unit.test_snapshot import params

from insights.analytics.findings import build_findings, headline, resolve_pointer
from insights.analytics.snapshot import build_snapshot


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
    queue.update(weeks_total=4, weeks_inflow_exceeds_outflow=2, open_growth_rel=0.5)
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
    c["significant"] = False
    assert "not significant" in headline(s)
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
