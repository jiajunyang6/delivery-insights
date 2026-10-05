from copy import deepcopy

import orjson
from tests.narrative_factory import golden

from insights.narrative.evidence import build_evidence_pack
from insights.narrative.prompt import SYSTEM_PROMPT, TOOL_SPEC, user_message
from insights.narrative.service import generate
from insights.narrative.validator import validate
from insights_eval.generator import AS_OF
from insights_eval.metrics import gates, metrics, recheck
from insights_eval.run import evaluate, previous_report, run
from insights_eval.scenarios import SCENARIOS
from insights_eval.stub_llm import StubLLMClient


async def test_stub_reads_only_pack_and_produces_valid_tool_output():
    snapshot = golden()
    pack, _ = build_evidence_pack(snapshot, True)
    client = StubLLMClient()
    reply = await client.submit(
        system=SYSTEM_PROMPT, messages=[user_message(pack)], tool_spec=TOOL_SPEC
    )
    assert reply == await client.submit(
        system=SYSTEM_PROMPT, messages=[user_message(pack)], tool_spec=TOOL_SPEC
    )
    assert not validate(reply.tool_input, pack)
    assert reply.tool_use_id and reply.input_tokens == reply.output_tokens == 0


async def test_final_recheck_detects_assembly_numeric_and_citation_corruption():
    snapshot = golden()
    pack, _ = build_evidence_pack(snapshot, True)
    result = await generate(snapshot, llm=StubLLMClient(), ci_complete=True, now=AS_OF)
    assert recheck(result.payload, pack) == []
    corrupted = deepcopy(result.payload)
    corrupted["narrative"] = "Median cycle time was 99999 hours [E1]. Nothing changed [E999]."
    violations = recheck(corrupted, pack)
    assert any(v.startswith("V5:") for v in violations)
    assert any(v.startswith("V4:") for v in violations)


def test_metrics_fail_bad_runs_and_empty_denominators_do_not_pass():
    common = {
        "attempts": 1,
        "first_attempt_valid": True,
        "generated_by": "llm",
        "recheck_violations": [],
        "hit": True,
        "abstained": False,
    }
    good = {
        **common,
        "expected": {"id": "H_review_capacity"},
        "scenario": "review_capacity",
        "hypotheses": [{"id": "H_review_capacity", "level": "high"}],
    }
    no_signal = {
        **common,
        "expected": {"id": None},
        "scenario": "no_signal",
        "hypotheses": [],
        "abstained": True,
    }
    values = metrics([good, no_signal])
    assert all(g["passed"] for g in gates(values).values())
    bad = {
        **good,
        "first_attempt_valid": False,
        "hit": False,
        "hypotheses": [{"id": "H_wrong", "level": "high"}],
        "recheck_violations": [
            "V5:number_not_in_evidence",
            "V6:unknown_hypothesis",
            "V7:hedge_mismatch",
        ],
    }
    failed = gates(metrics([bad, no_signal]))
    assert not failed["numeric_consistency"]["passed"]
    assert not failed["citation_validity"]["passed"]
    assert not failed["hedge_consistency"]["passed"]
    assert not failed["high_precision"]["passed"]
    assert not gates(metrics([]))["first_attempt_valid_rate"]["passed"]
    fallback = {**good, "generated_by": "template"}
    assert not gates(metrics([fallback, no_signal]))["fallback_rate"]["passed"]


async def test_default_twenty_run_offline_gates(capsys):
    seeds = [101, 202, 303, 404, 505]
    results = await evaluate(StubLLMClient(), seeds=seeds, scenarios=list(SCENARIOS))
    assert len(results) == 20
    checked = gates(metrics(results))
    assert all(g["passed"] for g in checked.values()), checked
    assert all(not r["recheck_violations"] for r in results)
    assert all(r["attempts"] == 1 and r["generated_by"] == "llm" for r in results)
    assert "review_capacity" in capsys.readouterr().out


async def test_bedrock_missing_key_exits_two_without_contacting_sdk(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    assert await run("bedrock", [101], ["no_signal"], tmp_path) == 2
    assert "AWS_BEARER_TOKEN_BEDROCK is not set" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_report_comparison_uses_latest_valid_matching_mode(tmp_path):
    (tmp_path / "eval-20200101T000000Z-stub.json").write_bytes(
        orjson.dumps({"llm": "stub", "metrics": {"fallback_rate": 0}})
    )
    (tmp_path / "eval-20210101T000000Z-bedrock.json").write_bytes(
        orjson.dumps({"llm": "bedrock", "metrics": {}})
    )
    (tmp_path / "eval-20220101T000000Z-stub.json").write_text("{", encoding="utf-8")
    assert previous_report(tmp_path, "stub")["metrics"]["fallback_rate"] == 0
