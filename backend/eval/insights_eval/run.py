"""CLI evaluation runner: trace narrative attempts, aggregate gates and save JSON reports."""

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
    """Wrap the LLM protocol to observe the first attempt and usage without changing replies."""

    def __init__(self, client: LLMClient) -> None:
        """Start a fresh trace for one narrative; repairs contribute to the same usage totals."""
        self.client, self.model_id = client, client.model_id
        self.usage = LLMUsage()

    @property
    def first(self) -> LLMReply | None:
        """Reply from attempt one, or None if that attempt has not returned successfully."""
        return self.usage.first

    @property
    def input_tokens(self) -> int:
        """Input tokens from returned replies across initial generation and any repair."""
        return self.usage.input_tokens

    @property
    def output_tokens(self) -> int:
        """Output tokens from returned replies; failed calls with no usage are not counted."""
        return self.usage.output_tokens

    async def submit(
        self, *, system: str, messages: list[dict[str, Any]], tool_spec: dict[str, Any]
    ) -> LLMReply:
        """Count the attempt before delegation, then record usage only when a reply returns."""
        self.usage.attempts += 1
        reply = await self.client.submit(system=system, messages=messages, tool_spec=tool_spec)
        self.usage.record(reply)
        return reply

    async def ping(self) -> None:
        """Delegate the settings check; evaluation runs do not call it."""
        await self.client.ping()


async def evaluate(
    client: LLMClient, *, seeds: list[int], scenarios: list[str]
) -> list[dict[str, Any]]:
    """Run each scenario/seed through production narrative generation.

    A top hit requires the expected ID and any specified location; no-signal cases require
    abstention with no hypotheses. Recheck final wording separately so successful repair
    cannot mask an invalid first reply.
    """
    runs = []
    print("scenario          seed top hypothesis         level   hit fallback")
    for scenario in scenarios:
        for seed in seeds:
            snapshot = build_snapshot_from_repo(generate(SCENARIOS[scenario], seed))
            expected_id, expected_location = EXPECTED[scenario]
            started = perf_counter()
            traced = TracedClient(client)
            pack, _ = build_evidence_pack(snapshot, True)
            result = await narrate(snapshot, llm=traced, ci_complete=True, now=AS_OF)
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
            # Generation may repair or fall back; score its original reply separately so
            # final validation success does not inflate first-attempt validity.
            first_errors = (
                validate(traced.first.tool_input, pack) if traced.first is not None else []
            )
            row = {
                "scenario": scenario,
                "seed": seed,
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
                "recheck_violations": recheck(payload, pack),
            }
            runs.append(row)
            print(
                f"{scenario:17} {seed:4} "
                f"{top.get('id', '-')!s:22} {top.get('confidence_level', '-')!s:7} "
                f"{hit!s:5} {meta['generated_by'] == 'template'}"
            )
    return runs


def previous_report(out: Path, mode: str) -> dict[str, Any] | None:
    """Read the newest usable report for this LLM mode, or None if none can be read.

    Timestamped filenames sort newest first; unreadable/invalid JSON files are skipped. This
    is a display baseline only: scenario sets, seeds and versions are not matched here.
    """
    for path in sorted(out.glob(f"eval-*-{mode}.json"), reverse=True):
        try:
            result: dict[str, Any] = orjson.loads(path.read_bytes())
            if result.get("llm") == mode and "metrics" in result:
                return result
        except (OSError, orjson.JSONDecodeError):
            continue
    return None


async def run(mode: str, seeds: list[int], scenarios: list[str], out: Path) -> int:
    """Evaluate, write a timestamped JSON report and return the gate-based CLI exit code.

    Return 0 if all gates pass, 1 for failed gates, or 2 for a missing Bedrock API key.
    Stub mode makes no model request; Bedrock mode calls the configured model and closes its
    SDK client even if evaluation raises. File operations run off the event loop.
    """
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
    """Parse CLI options, deduplicate seeds/scenarios in input order and start the async runner.

    Defaults cover four scenarios and five seeds (20 cases). Argument errors
    exit through argparse; reports go to --out, relative to the process working directory.
    """
    parser = argparse.ArgumentParser(
        description="Evaluate evidence-grounded narratives on synthetic data."
    )
    parser.add_argument("--llm", choices=("stub", "bedrock"), required=True)
    parser.add_argument("--seeds", default="101,202,303,404,505")
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
