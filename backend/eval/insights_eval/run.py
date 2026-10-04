import argparse
import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import orjson

from insights.analytics import ANALYTICS_VERSION
from insights.config import Settings
from insights.logging import configure_logging
from insights.narrative.evidence import build_evidence_pack
from insights.narrative.llm import BedrockClient, LLMClient, LLMReply, LLMUsage
from insights.narrative.prompt import PROMPT_VERSION
from insights.narrative.service import generate as narrate
from insights.narrative.validator import validate
from insights_eval.generator import AS_OF, generate
from insights_eval.metrics import gates, metrics, recheck
from insights_eval.pipeline import build_snapshot_from_repo
from insights_eval.scenarios import EXPECTED, SCENARIOS
from insights_eval.stub_llm import StubLLMClient


class TracedClient:
    def __init__(self, client: LLMClient) -> None:
        self.client, self.model_id = client, client.model_id
        self.usage = LLMUsage()

    @property
    def first(self) -> LLMReply | None:
        return self.usage.first

    @property
    def input_tokens(self) -> int:
        return self.usage.input_tokens

    @property
    def output_tokens(self) -> int:
        return self.usage.output_tokens

    async def submit(
        self, *, system: str, messages: list[dict[str, Any]], tool_spec: dict[str, Any]
    ) -> LLMReply:
        self.usage.attempts += 1
        reply = await self.client.submit(system=system, messages=messages, tool_spec=tool_spec)
        self.usage.record(reply)
        return reply


async def evaluate(
    client: LLMClient, *, seeds: list[int], scenarios: list[str]
) -> list[dict[str, Any]]:
    runs = []
    print("scenario          seed audience/lang top hypothesis         level   hit fallback")
    for scenario in scenarios:
        for seed in seeds:
            snapshot = build_snapshot_from_repo(generate(SCENARIOS[scenario], seed))
            expected_id, expected_location = EXPECTED[scenario]
            for audience in ("director", "manager"):
                started = perf_counter()
                traced = TracedClient(client)
                pack, _ = build_evidence_pack(snapshot, audience, True)
                result = await narrate(
                    snapshot, audience=audience, llm=traced, ci_complete=True, now=AS_OF
                )
                payload, meta = result.payload, result.payload["meta"]
                hypotheses = payload["hypotheses"]
                top = hypotheses[0] if hypotheses else {}
                hit = (
                    (
                        top.get("id") == expected_id
                        and (expected_location is None or top.get("location") == expected_location)
                    )
                    if expected_id
                    else payload["abstained"] and not hypotheses
                )
                first_errors = (
                    validate(traced.first.tool_input, pack, snapshot, audience=audience)
                    if traced.first is not None
                    else []
                )
                row = {
                    "scenario": scenario,
                    "seed": seed,
                    "audience": audience,
                    "lang": "en",
                    "expected": {"id": expected_id, "location": expected_location},
                    "generated_by": meta["generated_by"],
                    "validation": meta["validation"],
                    "attempts": meta["attempts"],
                    "fallback_reason": meta["fallback_reason"],
                    "violations": meta["violations"],
                    "top_hypothesis": top.get("id"),
                    "top_level": top.get("confidence_level"),
                    "abstained": payload["abstained"],
                    "hit": hit,
                    "duration_ms": round((perf_counter() - started) * 1000, 2),
                    "input_tokens": traced.input_tokens,
                    "output_tokens": traced.output_tokens,
                    "narrative": payload["narrative"],
                    "hypotheses": [
                        {"id": h["id"], "level": h["confidence_level"], "location": h["location"]}
                        for h in hypotheses
                    ],
                    "first_attempt_valid": traced.first is not None and not first_errors,
                    "first_attempt_violations": [
                        {"code": v.code, "message": v.message} for v in first_errors
                    ],
                    "recheck_violations": recheck(payload, pack, snapshot),
                }
                runs.append(row)
                print(
                    f"{scenario:17} {seed:4} {audience + '/en':13} "
                    f"{top.get('id', '-')!s:22} {top.get('confidence_level', '-')!s:7} "
                    f"{hit!s:5} {meta['generated_by'] == 'template'}"
                )
    return runs


def previous_report(out: Path, mode: str) -> dict[str, Any] | None:
    for path in sorted(out.glob(f"eval-*-{mode}.json"), reverse=True):
        try:
            result: dict[str, Any] = orjson.loads(path.read_bytes())
            if result.get("llm") == mode and "metrics" in result:
                return result
        except (OSError, orjson.JSONDecodeError):
            continue
    return None


async def run(mode: str, seeds: list[int], scenarios: list[str], out: Path) -> int:
    settings = Settings()
    if mode == "bedrock" and not settings.aws_bearer_token_bedrock:
        print("AWS_BEARER_TOKEN_BEDROCK is not set")
        return 2
    client = BedrockClient(settings) if mode == "bedrock" else StubLLMClient()
    started = datetime.now(UTC)
    previous = await asyncio.to_thread(previous_report, out, mode)
    try:
        results = await evaluate(client, seeds=seeds, scenarios=scenarios)
    finally:
        if isinstance(client, BedrockClient):
            client.client.close()
    values = metrics(results)
    checked = gates(values)
    report = {
        "started_at": started.isoformat(),
        "llm": mode,
        "model": client.model_id,
        "prompt_version": PROMPT_VERSION,
        "analytics_version": ANALYTICS_VERSION,
        "seeds": seeds,
        "runs": results,
        "metrics": values,
        "gates": checked,
        "passed": all(g["passed"] for g in checked.values()),
    }
    await asyncio.to_thread(out.mkdir, parents=True, exist_ok=True)
    path = out / f"eval-{started:%Y%m%dT%H%M%SZ}-{mode}.json"
    await asyncio.to_thread(
        path.write_bytes,
        orjson.dumps(report, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS) + b"\n",
    )
    print("\nmetric                       value  threshold  passed   change vs previous")
    for name, gate in checked.items():
        value = gate["value"]
        prior = previous["metrics"].get(name) if previous else None
        delta = f"{value - prior:+.3f}" if value is not None and prior is not None else "-"
        display = f"{value:.3f}" if value is not None else "null"
        print(
            f"{name:28} {display:>5}  {gate['threshold']:.2f}       {gate['passed']!s:5}    {delta}"
        )
    print(
        "\nSynthetic calibration only; confidence is an evidence-strength score, not a probability."
    )
    for level, row in values["calibration"].items():
        print(f"{level}: {row['hits']}/{row['hypotheses']} correct ({row['hit_rate']})")
    if values["high_precision"] is None:
        print("Warning: no high-confidence hypotheses; high_precision gate is not exercised.")
    print(f"Report: {await asyncio.to_thread(path.resolve)}")
    return 0 if report["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate evidence-grounded narratives on synthetic data."
    )
    parser.add_argument("--llm", choices=("stub", "bedrock"), required=True)
    parser.add_argument("--seeds", default="101,202")
    parser.add_argument("--scenarios", default=",".join(SCENARIOS))
    parser.add_argument("--out", type=Path, default=Path("reports"))
    args = parser.parse_args()
    try:
        seeds = list(dict.fromkeys(int(s) for s in args.seeds.split(",")))
    except ValueError:
        parser.error("--seeds must contain comma-separated integers")
    scenarios = list(dict.fromkeys(args.scenarios.split(",")))
    if not seeds or any(seed < 0 for seed in seeds):
        parser.error("--seeds must contain nonnegative integers")
    if any(name not in SCENARIOS for name in scenarios):
        parser.error("--scenarios contains an unknown scenario")
    configure_logging()
    logging.getLogger().setLevel(logging.WARNING)
    return asyncio.run(run(args.llm, seeds, scenarios, args.out))


if __name__ == "__main__":
    raise SystemExit(main())
