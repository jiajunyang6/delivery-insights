from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from insights.analytics.thresholds import (
    CI_COVERAGE_MIN,
    MIN_SAMPLES_P50,
    REVIEW_CAPACITY_MIN_WAIT_SHARE,
)

LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2}
SERIES_KEYS = {
    "E1": "cycle_p50_hours",
    "E3": "merged",
    "E15": "pickup_p50_hours",
    "E18": "waiting_reviewer_share",
    "E20": "waiting_ci_share",
    "E30": "pr_size_p50_lines",
}
TITLES = {
    "H_review_capacity": "Limited review capacity",
    "H_ci_bottleneck": "Slow or congested CI",
    "H_pr_size_growth": "Pull requests getting larger",
    "H_quality_tradeoff": "Speed gained by lighter review",
}
SLOWDOWN_HYPOTHESES = ("H_review_capacity", "H_ci_bottleneck", "H_pr_size_growth")
# A stage or location item belongs in the displayed chain only when it shows the added time.
ATTRIBUTION_MIN_SHARE = REVIEW_CAPACITY_MIN_WAIT_SHARE


def level(score: float) -> str | None:
    return (
        "high" if score >= 0.75 else "medium" if score > 0.5 else "low" if score >= 0.35 else None
    )


def rel_up(entry: Mapping[str, Any] | None, threshold: float) -> bool:
    return bool(
        entry
        and entry.get("change_rel") is not None
        and entry["change_rel"] >= threshold
        and entry.get("significant") is not False
    )


def rel_down(entry: Mapping[str, Any] | None, threshold: float) -> bool:
    return bool(
        entry
        and entry.get("change_rel") is not None
        and entry["change_rel"] <= -threshold
        and entry.get("significant") is not False
    )


def pp_up(entry: Mapping[str, Any] | None, threshold: float) -> bool:
    return bool(
        entry
        and entry.get("change_pp") is not None
        and entry["change_pp"] >= threshold
        and entry.get("significant") is not False
    )


def flat_rel(entry: Mapping[str, Any] | None, threshold: float) -> bool:
    return bool(
        entry and entry.get("change_rel") is not None and abs(entry["change_rel"]) < threshold
    )


def at_least(entry: Mapping[str, Any] | None, threshold: float) -> bool:
    return bool(entry and entry.get("value") is not None and entry["value"] >= threshold)


def effect_size(entry: Mapping[str, Any], snapshot: Mapping[str, Any]) -> tuple[float, bool]:
    delta = entry.get("change_abs")
    if delta is None:
        return 0.0, False
    series_key = SERIES_KEYS.get(entry["id"])
    previous = [
        w[series_key]
        for w in snapshot["series"]["previous"]
        if series_key and w.get(series_key) is not None
    ]
    sigma = float(np.std(previous)) if len(previous) >= 3 else 0.0
    if sigma:
        return min(1.0, abs(delta) / (2 * sigma)), True
    if entry["unit"] == "share":
        return min(1.0, abs(entry.get("change_pp") or 0) / 10), False
    return min(1.0, abs(entry.get("change_rel") or 0) / 0.5), False


@dataclass(frozen=True, slots=True)
class Signal:
    role: str
    evidence: tuple[str, ...]
    present: bool
    available: bool = True


def actions(identifier: str, location: str | None) -> tuple[str, str]:
    if identifier == "H_review_capacity":
        return (
            f"Add reviewers or code owners for {location} and enable team auto-assignment."
            if location
            else "Add reviewers to the busiest areas and enable team auto-assignment.",
            (
                "Two weeks after adding reviewers, check whether "
                f"the first-review wait in {location} has dropped."
            )
            if location
            else (
                "Two weeks after adding reviewers, check whether the first-review wait has dropped."
            ),
        )
    templates = {
        "H_ci_bottleneck": (
            "Add CI capacity or speed up the slowest workflows, and fix flaky tests.",
            "After the change, check whether the share of PR time waiting on CI falls.",
        ),
        "H_pr_size_growth": (
            "Split large changes into smaller PRs and agree on the approach before coding.",
            (
                "Over the next month, check whether the share of PRs with 500+ lines "
                "and the cycle time both fall."
            ),
        ),
        "H_quality_tradeoff": (
            "Keep the faster flow but restore review depth for large or risky changes.",
            (
                "Watch the revert rate over the next two periods; "
                "it should return to its previous level."
            ),
        ),
    }
    return templates[identifier]


def score_hypotheses(
    snapshot: Mapping[str, Any], evidence: Mapping[str, dict[str, Any]], *, ci_complete: bool
) -> tuple[list[dict[str, Any]], str | None]:
    if not snapshot["meta"]["comparison_available"]:
        return [], "no_comparison"
    ci = (
        snapshot["bottleneck_analysis"].get("ci") is not None
        and snapshot["time_ledger"]["ci_data_available"]
    )
    drivers = snapshot.get("drivers") is not None

    def available(identifier: str) -> bool:
        return (
            ci
            if identifier in {"E20", "E39", "E44", "E45", "E46"}
            else drivers
            if identifier in {"E48", "E49", "E50"}
            else True
        )

    def signal(role: str, ids: tuple[str, ...], condition: bool) -> Signal:
        eligible = all(available(i) for i in ids)
        return Signal(
            role, ids, bool(condition and eligible and all(i in evidence for i in ids)), eligible
        )

    e = evidence.get
    loc_entries = [e(f"E{53 + 4 * i}") for i in range(5)]
    localized = max(
        (entry for entry in loc_entries if entry is not None),
        key=lambda entry: (entry["value"], -int(entry["id"][1:])),
        default=None,
    )
    # The strongest location still feeds the score, but it is named only when a
    # meaningful share of the added time is reviewer wait there.
    shown = localized if at_least(localized, ATTRIBUTION_MIN_SHARE) else None
    location = shown["location"] if shown else None
    location_index = (int(shown["id"][1:]) - 53) // 4 if shown else None
    size_counter = bool(
        rel_up(e("E30"), 0.20) and e("E30") and evidence["E30"]["significant"] is True
    )
    ci_counter = flat_rel(e("E44"), 0.05) and flat_rel(e("E45"), 0.05)
    size_flat = bool(
        flat_rel(e("E30"), 0.05)
        and e("E31")
        and evidence["E31"].get("change_pp") is not None
        and abs(evidence["E31"]["change_pp"]) < 2
    )
    revert_flat = bool(
        e("E10")
        and evidence["E10"].get("change_pp") is not None
        and abs(evidence["E10"]["change_pp"]) < 0.5
    )
    definitions = {
        "H_review_capacity": {
            "main": "E15",
            "direction": 1,
            "localization": localized["value"] if localized else 0,
            "location": location,
            "stage": stage_ids(
                ("E15", rel_up(e("E15"), 0.10)), ("E37", at_least(e("E37"), ATTRIBUTION_MIN_SHARE))
            ),
            "location_ids": tuple(f"E{51 + 4 * location_index + offset}" for offset in (0, 2, 3))
            if location_index is not None
            else (),
            "signals": [
                signal("symptom", ("E1",), rel_up(e("E1"), 0.10)),
                signal("symptom", ("E18",), pp_up(e("E18"), 3)),
                signal(
                    "mechanism",
                    ("E22",),
                    bool(
                        e("E22")
                        and evidence["E22"]["extra"].get("weeks_total", 0) >= 2
                        and evidence["E22"]["value"] / evidence["E22"]["extra"]["weeks_total"]
                        >= 0.5
                    ),
                ),
                signal("mechanism", ("E26",), at_least(e("E26"), 0.50)),
                signal("mechanism", ("E24",), at_least(e("E24"), 0.60) or pp_up(e("E24"), 5)),
            ],
            "counter": [("E30",)] if size_counter else [],
        },
        "H_ci_bottleneck": {
            "main": "E20",
            "direction": 1,
            "localization": (e("E39") or {}).get("value", 0),
            "location": None,
            "stage": stage_ids(("E39", at_least(e("E39"), ATTRIBUTION_MIN_SHARE))),
            "location_ids": (),
            "signals": [
                signal("symptom", ("E20",), pp_up(e("E20"), 3)),
                signal("symptom", ("E1",), rel_up(e("E1"), 0.10)),
                signal("mechanism", ("E44",), rel_up(e("E44"), 0.20)),
                signal("mechanism", ("E45",), rel_up(e("E45"), 0.20)),
                signal("mechanism", ("E46",), pp_up(e("E46"), 2) or at_least(e("E46"), 0.10)),
            ],
            "counter": [("E44", "E45")] if ci_counter else [],
        },
        "H_pr_size_growth": {
            "main": "E1",
            "direction": 1,
            "localization": (e("E42") or {}).get("value", 0),
            "location": None,
            "stage": stage_ids(("E42", at_least(e("E42"), ATTRIBUTION_MIN_SHARE))),
            "location_ids": (),
            "signals": [
                signal("symptom", ("E1",), rel_up(e("E1"), 0.10)),
                signal(
                    "symptom",
                    tuple(
                        i
                        for i, yes in (("E8", rel_up(e("E8"), 0.10)), ("E9", pp_up(e("E9"), 5)))
                        if yes
                    )
                    or ("E8", "E9"),
                    rel_up(e("E8"), 0.10) or pp_up(e("E9"), 5),
                ),
                signal("mechanism", ("E31",), pp_up(e("E31"), 5)),
                signal("mechanism", ("E30",), rel_up(e("E30"), 0.20)),
                signal("mechanism", ("E48",), at_least(e("E48"), 2)),
            ],
            "counter": [("E30", "E31")] if size_flat else [],
        },
        "H_quality_tradeoff": {
            "main": "E1",
            "direction": -1,
            "localization": (e("E43") or {}).get("value", 0),
            "location": None,
            "stage": stage_ids(
                ("E43", at_least(e("E43"), ATTRIBUTION_MIN_SHARE)),
                ("E15", rel_down(e("E15"), 0.10)),
            ),
            "location_ids": (),
            "signals": [
                signal("symptom", ("E1",), rel_down(e("E1"), 0.10)),
                signal("mechanism", ("E10",), pp_up(e("E10"), 1)),
                signal("mechanism", ("E32",), pp_up(e("E32"), 5)),
                signal("mechanism", ("E33",), pp_up(e("E33"), 2)),
            ],
            "counter": [("E10",)] if revert_flat else [],
        },
    }
    evaluated: dict[str, dict[str, Any]] = {}
    for identifier, definition in definitions.items():
        main_id = str(definition["main"])
        main = evidence.get(main_id, {})
        signals: list[Signal] = list(definition["signals"])
        symptom = any(s.present for s in signals if s.role == "symptom")
        mechanisms = [s for s in signals if s.role == "mechanism" and s.available]
        mechanism = any(s.present for s in mechanisms)
        sample_ok = (
            main.get("value") is not None
            and main.get("previous") is not None
            and (main.get("n") or 0) >= MIN_SAMPLES_P50
        )
        total = sum(s.available for s in signals)
        present = sum(s.present for s in signals)
        agreement = present / total if total else 0
        effect, _ = effect_size(main, snapshot) if main else (0.0, False)
        direction = int(definition["direction"])
        if main.get("change_abs") is None or main["change_abs"] * direction <= 0:
            effect = 0
        series_key = SERIES_KEYS[main_id]
        values = [
            w[series_key] for w in snapshot["series"]["current"] if w.get(series_key) is not None
        ]
        holding = (
            sum((v - main["previous"]) * direction > 0 for v in values)
            if main.get("previous") is not None
            else 0
        )
        persistence = holding / len(values) if values else 0
        sample = min(1, (main.get("n") or 0) / 100)
        localization = min(1, max(0, float(definition["localization"])))
        counters = definition["counter"]
        raw = max(
            0,
            min(
                1,
                0.30 * agreement
                + 0.20 * effect
                + 0.20 * persistence
                + 0.15 * sample
                + 0.15 * localization
                - 0.15 * len(counters),
            ),
        )
        cap = (
            0.5
            if identifier == "H_ci_bottleneck"
            and not (
                ci and snapshot["time_ledger"]["ci_coverage"] >= CI_COVERAGE_MIN and ci_complete
            )
            else None
        )
        confidence = round(min(raw, cap) if cap is not None else raw, 2)
        chain = {
            role: list(
                dict.fromkeys(
                    i for s in signals if s.role == role and s.present for i in s.evidence
                )
            )
            for role in ("symptom", "mechanism")
        }
        chain.update(
            stage=[i for i in definition["stage"] if i in evidence],
            location=[i for i in definition["location_ids"] if i in evidence],
        )
        candidate = {
            "id": identifier,
            "title": TITLES[identifier],
            "location": definition["location"],
            "confidence": confidence,
            "confidence_level": level(confidence),
            "chain": chain,
            "persistence": {"weeks_holding": holding, "weeks": len(values)},
            "counter_evidence": list(dict.fromkeys(i for group in counters for i in group)),
            "confidence_basis": {
                "signal_agreement": round(agreement, 2),
                "signals_present": present,
                "signals_total": total,
                "effect_size": round(effect, 2),
                "persistence": round(persistence, 2),
                "weeks_holding": f"{holding}/{len(values)}",
                "sample_adequacy": round(sample, 2),
                "sample_size": main.get("n"),
                "localization": round(localization, 2),
                "counter_evidence": len(counters),
                "covers_both_parts": symptom and mechanism,
                "raw_score": round(raw, 2),
                "cap": cap,
                "cap_reason": "ci_data_incomplete" if cap is not None else None,
                "llm_downgrade": None,
            },
            "alternatives_ruled_out": [],
            "alternatives_open": [],
        }
        evaluated[identifier] = {
            "candidate": candidate,
            "symptom": symptom,
            "mechanism": mechanism,
            "mechanisms": mechanisms,
            "main_available": available(main_id),
            "sample_ok": sample_ok,
            "eligible": sample_ok and symptom and mechanism and confidence >= 0.35,
        }
    selected = sorted(
        (item["candidate"] for item in evaluated.values() if item["eligible"]),
        key=lambda c: (-c["confidence"], c["id"]),
    )[:3]
    selected_ids = {c["id"] for c in selected}
    for candidate in selected:
        for identifier, other in sorted(evaluated.items()):
            if identifier in selected_ids or not other["symptom"]:
                continue
            reason = None
            alternative = other["candidate"]
            if not other["main_available"] or not other["mechanisms"]:
                reason = "no_data"
            elif not other["sample_ok"]:
                reason = "insufficient_sample"
            elif alternative["counter_evidence"]:
                candidate["alternatives_ruled_out"].append(
                    {"hypothesis": identifier, "evidence": alternative["counter_evidence"]}
                )
            elif not other["mechanism"]:
                ids = list(
                    dict.fromkeys(
                        i for s in other["mechanisms"] for i in s.evidence if i in evidence
                    )
                )
                if ids:
                    candidate["alternatives_ruled_out"].append(
                        {"hypothesis": identifier, "evidence": ids}
                    )
                else:
                    reason = "no_data"
            else:
                reason = "not_selected" if other["eligible"] else "below_threshold"
            if reason:
                candidate["alternatives_open"].append({"hypothesis": identifier, "reason": reason})
    return selected, None if selected else abstain_reason(evidence, evaluated)


def abstain_reason(
    evidence: Mapping[str, dict[str, Any]], evaluated: Mapping[str, Mapping[str, Any]]
) -> str:
    """no_slowdown when cycle time is comparable and no slowdown symptom is present."""
    cycle = evidence.get("E1")
    comparable = bool(
        cycle
        and cycle.get("value") is not None
        and cycle.get("previous") is not None
        and (cycle.get("n") or 0) >= MIN_SAMPLES_P50
    )
    slowing = any(evaluated[i]["symptom"] for i in SLOWDOWN_HYPOTHESES)
    return "no_slowdown" if comparable and not slowing else "insufficient_signal"


def stage_ids(*items: tuple[str, bool]) -> tuple[str, ...]:
    return tuple(identifier for identifier, shows_change in items if shows_change)
