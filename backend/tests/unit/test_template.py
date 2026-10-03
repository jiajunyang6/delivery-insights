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
@pytest.mark.parametrize("lang", ["en", "zh"])
def test_all_templates_pass_the_same_validator(name, audience, lang):
    snapshot = snapshot_for(name)
    pack, _ = build_evidence_pack(snapshot, audience, lang, False)
    output = build_template(pack, snapshot)
    assert validate(output, pack, snapshot, audience=audience, lang=lang) == []


def test_sparse_snapshot_and_no_comparison_templates():
    snapshot = golden()
    snapshot["meta"]["comparison_available"] = False
    snapshot["efficiency"]["cycle_time_p50_hours"]["previous"] = None
    snapshot["efficiency"]["cycle_time_p50_hours"]["change_rel"] = None
    for audience in ("director", "manager"):
        for lang in ("en", "zh"):
            pack, _ = build_evidence_pack(snapshot, audience, lang, False)
            assert not pack["hypotheses"] and pack["abstain_reason"] == "no_comparison"
            assert not validate(
                build_template(pack, snapshot), pack, snapshot, audience=audience, lang=lang
            )


def test_too_few_merged_and_real_no_comparison():
    from datetime import UTC, datetime

    snapshot = build_snapshot_from_repo(
        generate(SCENARIOS["no_signal"], 101), covered_since=datetime(2026, 1, 19, tzinfo=UTC)
    )
    for sparse in (False, True):
        if sparse:
            snapshot["efficiency"]["cycle_time_p50_hours"]["value"] = None
        for audience in ("director", "manager"):
            for lang in ("en", "zh"):
                pack, _ = build_evidence_pack(snapshot, audience, lang, False)
                output = build_template(pack, snapshot)
                assert not validate(output, pack, snapshot, audience=audience, lang=lang)
                assert pack["abstain_reason"] == "no_comparison"
                if sparse:
                    assert "E3" in output["narrative"]
