from datetime import UTC, datetime
from functools import lru_cache

import pytest
from tests.narrative_factory import golden

from insights.narrative.evidence import build_evidence_pack
from insights.narrative.hypotheses import explains
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
    "name", ["golden", "review_capacity", "pr_size_growth", "ci_slowdown", "no_signal"]
)
def test_all_templates_pass_the_same_validator(name):
    pack, _ = build_evidence_pack(snapshot_for(name), False)
    output = build_template(pack)
    assert validate(output, pack) == []
    assert len(output["narrative"].split(". ")) == 3


def test_sparse_snapshot_and_no_comparison_templates():
    snapshot = golden()
    snapshot["meta"]["comparison_available"] = False
    snapshot["efficiency"]["cycle_time_p50_hours"]["previous"] = None
    snapshot["efficiency"]["cycle_time_p50_hours"]["change_rel"] = None
    pack, _ = build_evidence_pack(snapshot, False)
    assert not pack["hypotheses"] and pack["abstain_reason"] == "no_comparison"
    assert not validate(build_template(pack), pack)


def test_too_few_merged_and_real_no_comparison():
    snapshot = build_snapshot_from_repo(
        generate(SCENARIOS["no_signal"], 101), covered_since=datetime(2026, 1, 19, tzinfo=UTC)
    )
    for sparse in (False, True):
        if sparse:
            snapshot["efficiency"]["cycle_time_p50_hours"]["value"] = None
        pack, _ = build_evidence_pack(snapshot, False)
        output = build_template(pack)
        assert not validate(output, pack)
        assert pack["abstain_reason"] == "no_comparison"
        if sparse:
            assert "E3" in output["narrative"]


def test_no_slowdown_template_states_it_and_shows_where_time_goes():
    pack, _ = build_evidence_pack(snapshot_for("no_signal"), False)
    assert pack["abstain_reason"] == "no_slowdown"
    output = build_template(pack)
    assert "There is no slowdown to explain this period" in output["narrative"]
    assert "insufficient" not in output["narrative"]
    assert "The largest share of PR time" in output["narrative"]
    assert not validate(output, pack)
    output["narrative"] = output["narrative"].replace(
        "There is no slowdown to explain this period", "Delivery was steady"
    )
    assert "V12:abstain" in [v.code for v in validate(output, pack)]


def test_explains_names_only_the_changes_each_symptom_records():
    assert explains({"symptom": ["E18"]}) == "the larger share of PR time waiting on reviewers"
    assert explains({"symptom": ["E1", "E8", "E9"]}) == (
        "the slower cycle time, more review rounds per PR "
        "and more PRs with commits after the first review"
    )


@pytest.mark.parametrize("name", ["review_capacity", "pr_size_growth", "ci_slowdown"])
def test_cause_sentences_name_what_the_hypothesis_explains(name):
    pack, _ = build_evidence_pack(snapshot_for(name), False)
    output = build_template(pack)
    assert pack["hypotheses"]
    for candidate, statement in zip(pack["hypotheses"], output["hypotheses"], strict=True):
        assert candidate["explains"]
        assert f"main cause of {candidate['explains']} [" in statement["statement"]
        if "E1" not in candidate["chain"]["symptom"]:
            assert "cycle time" not in statement["statement"]
