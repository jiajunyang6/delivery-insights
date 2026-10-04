"""Revalidate final narratives and compute synthetic evaluation rates and acceptance gates."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from insights.narrative.validator import validate


def recheck(
    payload: Mapping[str, Any], pack: dict[str, Any], snapshot: Mapping[str, Any]
) -> list[str]:
    """Reconstruct tool-shaped output from the final payload and return validator error codes.

    Use assembled confidence bands, including accepted downgrades, so hedging is checked
    against the final response. The original pack is copied and remains unchanged.
    """
    final_pack = deepcopy(pack)
    levels = {h["id"]: h["confidence_level"] for h in payload["hypotheses"]}
    for candidate in final_pack["hypotheses"]:
        candidate["level"] = levels.get(candidate["id"], candidate["level"])
    output: dict[str, Any] = {
        "narrative": payload["narrative"],
        "hypotheses": [
            {"id": h["id"], "statement": h["statement"]}
            for h in payload["hypotheses"]
            if h["source"] == "library"
        ],
    }
    outside = next((h for h in payload["hypotheses"] if h["source"] == "llm"), None)
    if outside:
        output["llm_hypothesis"] = {
            "statement": outside["statement"],
            "evidence_ids": [i for step in outside["evidence_chain"] for i in step["evidence"]],
        }
    return [v.code for v in validate(output, final_pack, snapshot, audience=payload["audience"])]


def metrics(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate evaluation rows; return None for rates with no eligible observations.

    First-attempt validity includes attempted calls, even transport failures without a reply.
    Consistency rates include only final LLM outputs; fallback rate includes all runs. Top-hit
    rate excludes no-signal cases, which have a separate abstention rate. Calibration counts
    every selected hypothesis by expected ID, not location, and is not probability calibration.
    """

    def fraction(flags: Sequence[bool]) -> float | None:
        """Return the fraction of true flags, or None when the denominator is empty."""
        return sum(flags) / len(flags) if flags else None

    llm_runs = [r for r in runs if r["generated_by"] == "llm"]

    def consistent(prefixes: tuple[str, ...]) -> float | None:
        """Fraction of final LLM outputs without a recheck violation in these code families."""
        return fraction(
            [not any(v.startswith(prefixes) for v in r["recheck_violations"]) for r in llm_runs]
        )

    calibration = {}
    for level in ("high", "medium", "low"):
        selected = [(r, h) for r in runs for h in r["hypotheses"] if h["level"] == level]
        hits = [h["id"] == r["expected"]["id"] for r, h in selected]
        calibration[level] = {
            "hypotheses": len(hits),
            "hits": sum(hits),
            "hit_rate": fraction(hits),
        }
    return {
        "first_attempt_valid_rate": fraction(
            [r["first_attempt_valid"] for r in runs if r["attempts"]]
        ),
        "numeric_consistency": consistent(("V5:",)),
        "citation_validity": consistent(("V4:", "V6:")),
        "hedge_consistency": consistent(("V7:", "V7b:", "V12:")),
        "root_cause_hit_rate": fraction([r["hit"] for r in runs if r["expected"]["id"]]),
        "abstention_rate": fraction(
            [r["abstained"] and not r["hypotheses"] for r in runs if r["scenario"] == "no_signal"]
        ),
        "high_precision": calibration["high"]["hit_rate"],
        "fallback_rate": fraction([r["generated_by"] == "template" for r in runs]),
        "calibration": calibration,
    }


def gates(values: Mapping[str, Any]) -> dict[str, Any]:
    """Apply minimum rate thresholds, except fallback_rate which has a maximum.

    An empty high-confidence set passes high_precision as unexercised; all other None rates
    fail. The runner prints a warning for that exception so it is not reported as evidence.
    """
    limits = {
        "first_attempt_valid_rate": 0.9,
        "numeric_consistency": 1.0,
        "citation_validity": 1.0,
        "hedge_consistency": 1.0,
        "root_cause_hit_rate": 0.8,
        "abstention_rate": 0.8,
        "high_precision": 0.8,
        "fallback_rate": 0.1,
    }
    result = {}
    for name, threshold in limits.items():
        value = values[name]
        passed = (
            name == "high_precision"
            if value is None
            else (value <= threshold if name == "fallback_rate" else value >= threshold)
        )
        result[name] = {"value": value, "threshold": threshold, "passed": passed}
    return result
