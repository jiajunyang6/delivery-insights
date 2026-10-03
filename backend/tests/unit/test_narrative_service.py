from copy import deepcopy
from datetime import UTC, datetime

import pytest
from tests.narrative_factory import golden, scoring_fixture, validation_fixture

from insights.analytics.snapshot import digest
from insights.api.schemas import Narrative
from insights.narrative.evidence import build_evidence_pack
from insights.narrative.hypotheses import score_hypotheses
from insights.narrative.llm import FakeLLMClient, LLMReply, LLMUnavailable
from insights.narrative.service import assemble, generate
from insights.narrative.template import build_template

NOW = datetime(2026, 3, 2, tzinfo=UTC)


@pytest.mark.parametrize("mode", ["disabled", "first", "repair", "failed", "error", "second_error"])
async def test_generation_repair_fallback_and_grounded_output(mode):
    snapshot = golden()
    pack, _ = build_evidence_pack(snapshot, "director", False)
    valid = build_template(pack, snapshot)
    bad = {"narrative": "Definitely 999 hours.", "hypotheses": []}
    script = {
        "first": [valid],
        "repair": [bad, valid],
        "failed": [bad, bad],
        "error": [LLMUnavailable("ThrottlingException")],
        "second_error": [bad, LLMUnavailable("ReadTimeout")],
    }
    llm = FakeLLMClient(script[mode]) if mode != "disabled" else None
    result = await generate(snapshot, audience="director", llm=llm, ci_complete=False, now=NOW)
    Narrative.model_validate(result.payload)
    meta = result.payload["meta"]
    assert meta["pack_hash"] == digest(pack)[:16]
    assert meta["generated_at"] == "2026-03-02T00:00:00Z"
    assert result.persist is (mode in {"disabled", "first", "repair"})
    assert meta["generated_by"] == ("llm" if mode in {"first", "repair"} else "template")
    if mode in {"repair", "second_error"}:
        content = llm.calls[1]["messages"][-1]["content"][0]["toolResult"]
        assert content["status"] == "error" and content["toolUseId"] == "fake-1"
        assert "V5:number_not_in_evidence" in content["content"][0]["text"]
        assert meta["attempts"] == 2
    if mode == "failed":
        assert meta["validation"] == "failed" and meta["violations"]
    if mode in {"error", "second_error"}:
        assert meta["fallback_reason"] == "llm_error" and meta["validation"] == "not_run"
    if llm:
        request = str(llm.calls[0]["messages"])
        assert all(
            p["title"] not in request and p["author"] not in request
            for p in snapshot["at_risk_prs"]
        )
    ids = {e["id"] for e in result.payload["evidence"]}
    assert ids == {"E1", "E71"}


async def test_missing_tool_use_repairs_with_text_message():
    snapshot = golden()
    pack, _ = build_evidence_pack(snapshot, "director", False)
    valid = build_template(pack, snapshot)

    class NoToolFirst(FakeLLMClient):
        async def submit(self, **kwargs):
            if not self.calls:
                self.calls.append(deepcopy(kwargs))
                return LLMReply(
                    None,
                    None,
                    {"role": "assistant", "content": [{"text": "No tool"}]},
                    1,
                    2,
                    "end_turn",
                )
            return await super().submit(**kwargs)

    llm = NoToolFirst([valid])
    result = await generate(snapshot, audience="director", llm=llm, ci_complete=False, now=NOW)
    assert result.payload["meta"]["validation"] == "passed"
    assert "text" in llm.calls[-1]["messages"][-1]["content"][0]


@pytest.mark.parametrize(("target", "expected"), [("medium", 0.74), ("low", 0.5)])
async def test_generate_downgrade_is_code_owned(target, expected, monkeypatch):
    snapshot, pack, output = validation_fixture()
    synthetic, evidence = scoring_fixture()
    candidates, _ = score_hypotheses(synthetic, evidence, ci_complete=False)
    candidates[0]["chain"] = pack["hypotheses"][0]["chain"]
    pack.update(audience="director", lang="en", abstain_reason=None, top_bottlenecks=[])
    import insights.narrative.service as service

    monkeypatch.setattr(service, "build_evidence_pack", lambda *args: (pack, candidates))
    h = output["hypotheses"][0]
    h["downgrade"] = {"level": target, "reason": "The location evidence remains limited [E53]."}
    h["statement"] = (
        "Review capacity may be the main cause [E1][E15]."
        if target == "medium"
        else "There are early signs that review capacity is the main cause [E1][E15]."
    )
    output["narrative"] = "Median cycle time rose 18% [E1]. " + h["statement"]
    result = await generate(
        snapshot, audience="director", llm=FakeLLMClient([output]), ci_complete=False, now=NOW
    )
    assert result.payload["meta"]["generated_by"] == "llm"
    final = result.payload["hypotheses"][0]
    assert final["confidence"] == expected
    assert final["confidence_basis"]["llm_downgrade"]["to"] == target


def test_assembly_resorts_after_downgrade_and_keeps_outside_score_fixed():
    snapshot, evidence = scoring_fixture()
    candidates, _ = score_hypotheses(snapshot, evidence, ci_complete=False)
    second = deepcopy(candidates[0])
    second.update(
        id="H_pr_size_growth",
        title="Pull requests getting larger",
        confidence=0.63,
        confidence_level="medium",
    )
    candidates.append(second)
    pack, _ = build_evidence_pack(snapshot, "director", False)
    output = {
        "narrative": "Cycle data [E1].",
        "hypotheses": [
            {
                "id": "H_review_capacity",
                "statement": "Early signs [E1].",
                "downgrade": {"level": "low", "reason": "Limited [E15]."},
            },
            {"id": "H_pr_size_growth", "statement": "May matter [E30]."},
        ],
        "llm_hypothesis": {"statement": "Early signs [E1][E15].", "evidence_ids": ["E1", "E15"]},
    }
    result = assemble(output, snapshot, pack, candidates, {})
    assert [h["id"] for h in result["hypotheses"]] == [
        "H_pr_size_growth",
        "H_review_capacity",
        "H_llm",
    ]
    outside = result["hypotheses"][-1]
    assert (
        outside["confidence"] == 0.35
        and outside["confidence_basis"]["cap_reason"] == "outside_library"
    )
    assert outside["action"] is None
    assert result["hypotheses"][1]["alternatives_open"] == candidates[0]["alternatives_open"]
