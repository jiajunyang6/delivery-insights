from functools import lru_cache

import pytest
from tests.narrative_factory import golden

from insights.narrative.evidence import build_evidence_pack
from insights.narrative.template import build_template
from insights.narrative.validator import validate
from insights_eval.generator import generate
from insights_eval.pipeline import build_snapshot_from_repo
from insights_eval.scenarios import SCENARIOS


@lru_cache
def snapshot_for(name):
    return (
        golden() if name == "golden" else build_snapshot_from_repo(generate(SCENARIOS[name], 101))
    )


@pytest.mark.parametrize(
    "name",
    ["golden", "review_capacity", "pr_size_growth", "ci_slowdown", "quality_tradeoff", "no_signal"],
)
@pytest.mark.parametrize("audience", ["director", "manager"])
def test_all_templates_pass_the_same_validator(name, audience):
    snapshot = snapshot_for(name)
    pack, _ = build_evidence_pack(snapshot, audience, False)
    output = build_template(pack, snapshot)
    assert validate(output, pack, snapshot, audience=audience) == []


def test_sparse_snapshot_and_no_comparison_templates():
    snapshot = golden()
    snapshot["meta"]["comparison_available"] = False
    snapshot["efficiency"]["cycle_time_p50_hours"]["previous"] = None
    snapshot["efficiency"]["cycle_time_p50_hours"]["change_rel"] = None
    for audience in ("director", "manager"):
        pack, _ = build_evidence_pack(snapshot, audience, False)
        assert not pack["hypotheses"] and pack["abstain_reason"] == "no_comparison"
        assert not validate(build_template(pack, snapshot), pack, snapshot, audience=audience)


def test_too_few_merged_and_real_no_comparison():
    from datetime import UTC, datetime

    snapshot = build_snapshot_from_repo(
        generate(SCENARIOS["no_signal"], 101), covered_since=datetime(2026, 1, 19, tzinfo=UTC)
    )
    for sparse in (False, True):
        if sparse:
            snapshot["efficiency"]["cycle_time_p50_hours"]["value"] = None
        for audience in ("director", "manager"):
            pack, _ = build_evidence_pack(snapshot, audience, False)
            output = build_template(pack, snapshot)
            assert not validate(output, pack, snapshot, audience=audience)
            assert pack["abstain_reason"] == "no_comparison"
            if sparse:
                assert "E3" in output["narrative"]


@pytest.mark.parametrize("audience", ["director", "manager"])
def test_no_slowdown_template_states_it_and_shows_where_time_goes(audience):
    snapshot = snapshot_for("no_signal")
    pack, _ = build_evidence_pack(snapshot, audience, False)
    assert pack["abstain_reason"] == "no_slowdown"
    output = build_template(pack, snapshot)
    assert "There is no slowdown to explain this period" in output["narrative"]
    assert "insufficient" not in output["narrative"]
    assert not validate(output, pack, snapshot, audience=audience)
    pack["top_bottlenecks"] = []
    output = build_template(pack, snapshot)
    assert "the largest share of PR time" in output["narrative"]
    assert not validate(output, pack, snapshot, audience=audience)
    output["narrative"] = output["narrative"].replace(
        "There is no slowdown to explain this period", "Delivery was steady"
    )
    assert "V12:abstain" in [v.code for v in validate(output, pack, snapshot, audience=audience)]


def test_explains_names_only_the_changes_each_symptom_records():
    from insights.narrative.hypotheses import explains

    assert explains("H_review_capacity", {"symptom": ["E18"]}) == (
        "the larger share of PR time waiting on reviewers"
    )
    assert explains("H_quality_tradeoff", {"symptom": ["E1"]}) == "the faster cycle time"
    assert explains("H_pr_size_growth", {"symptom": ["E1", "E8", "E9"]}) == (
        "the slower cycle time, more review rounds per PR "
        "and more PRs with commits after the first review"
    )


@pytest.mark.parametrize("name", ["review_capacity", "pr_size_growth", "ci_slowdown"])
def test_cause_sentences_name_what_the_hypothesis_explains(name):
    snapshot = snapshot_for(name)
    pack, _ = build_evidence_pack(snapshot, "director", False)
    output = build_template(pack, snapshot)
    assert pack["hypotheses"]
    for candidate, statement in zip(pack["hypotheses"], output["hypotheses"], strict=True):
        assert candidate["explains"]
        assert f"main cause of {candidate['explains']} [" in statement["statement"]
        if "E1" not in candidate["chain"]["symptom"]:
            assert "cycle time" not in statement["statement"]
