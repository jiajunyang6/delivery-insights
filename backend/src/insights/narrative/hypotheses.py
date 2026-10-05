"""Deterministic scoring of the two library hypotheses against evidence items.

Code decides which hypotheses qualify and their confidence band; the LLM only words them.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from insights.analytics import (
    MIN_SAMPLES_P50,
    REVIEW_CAPACITY_MIN_WAIT_SHARE,
)


@dataclass(frozen=True, slots=True)
class ConfidenceLevel:
    minimum: float
    exclusive: bool
    downgrade_cap: float


# Each downgrade_cap sits inside its own band (0.5 is still low, 0.74 still medium), so a
# capped or downgraded confidence never lands back in a higher band.
LEVELS = {
    "low": ConfidenceLevel(0.35, False, 0.5),
    "medium": ConfidenceLevel(0.5, True, 0.74),
    "high": ConfidenceLevel(0.75, False, 1.0),
}
LEVEL_ORDER = {name: index for index, name in enumerate(LEVELS)}
STEPS = ("symptom", "stage", "location", "mechanism")
# Each location reserves four IDs; slots 1 and 3 are retired, so existing IDs stay stable.
LOCATION_SLOTS = {"pickup_ratio": 0, "added_wait_share": 2}
DEFAULT_ABSTAIN_REASON = "insufficient_signal"


def location_id(index: int, slot: str) -> str:
    """Return the reserved evidence ID for one zero-based location slot."""
    return f"E{51 + 4 * index + LOCATION_SLOTS[slot]}"


def chain_ids(candidate: Mapping[str, Any]) -> list[str]:
    """Flatten the candidate's ordered evidence steps, removing duplicate IDs."""
    return list(dict.fromkeys(i for step in STEPS for i in candidate["chain"].get(step, [])))


def allowed_ids(candidate: Mapping[str, Any]) -> set[str]:
    """IDs a hypothesis statement may cite: chain, counter-evidence and ruled-out alternatives.

    Accepts both the pack shape (ruled_out/evidence_ids) and the candidate shape
    (alternatives_ruled_out/evidence).
    """
    ids = set(chain_ids(candidate)) | set(candidate["counter_evidence"])
    for alternative in candidate.get("ruled_out", candidate.get("alternatives_ruled_out", [])):
        ids.update(alternative.get("evidence_ids", alternative.get("evidence", [])))
    return ids


def abstention(pack: Mapping[str, Any]) -> tuple[str, str]:
    """Choose the abstention reason and its cycle-time or merged-count citation."""
    reason = pack.get("abstain_reason") or DEFAULT_ABSTAIN_REASON
    identifier = "E1" if any(e["id"] == "E1" for e in pack["evidence"]) else "E3"
    return reason, identifier


SERIES_KEYS = {
    "E1": "cycle_p50_hours",
    "E3": "merged",
    "E15": "pickup_p50_hours",
    "E18": "waiting_reviewer_share",
    "E30": "pr_size_p50_lines",
}
# A stage or location item belongs in the displayed chain only when it shows the added time.
ATTRIBUTION_MIN_SHARE = REVIEW_CAPACITY_MIN_WAIT_SHARE


def level(score: float) -> str | None:
    """Return the highest qualifying confidence band, or None below all band thresholds."""
    for name, band in reversed(LEVELS.items()):
        if score > band.minimum if band.exclusive else score >= band.minimum:
            return name
    return None


def changed(
    entry: Mapping[str, Any] | None,
    threshold: float,
    *,
    field: str = "change_rel",
) -> bool:
    """Whether field increased by at least threshold.

    Only an explicit significant=False rejects the change; items without the flag pass.
    """
    return bool(
        entry
        and entry.get(field) is not None
        and entry[field] >= threshold
        and entry.get("significant") is not False
    )


def flat_rel(entry: Mapping[str, Any] | None, threshold: float) -> bool:
    """Test whether the absolute relative change is strictly below the threshold."""
    return bool(
        entry and entry.get("change_rel") is not None and abs(entry["change_rel"]) < threshold
    )


def at_least(entry: Mapping[str, Any] | None, threshold: float) -> bool:
    """Test an available evidence value against an inclusive threshold."""
    return bool(entry and entry.get("value") is not None and entry["value"] >= threshold)


def effect_size(entry: Mapping[str, Any], snapshot: Mapping[str, Any]) -> tuple[float, bool]:
    """Effect in [0, 1] and whether it was measured against weekly variation.

    For items in SERIES_KEYS with at least three non-constant previous-period weeks, 2 sigma
    counts as full effect; otherwise 10 pp for shares or a 50% relative change does.
    """
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


class SignalContext:
    def __init__(self, snapshot: Mapping[str, Any], evidence: Mapping[str, dict[str, Any]]) -> None:
        """Prepare reusable source availability, localization and counter-evidence predicates."""
        self.evidence = evidence
        drivers = snapshot.get("drivers") is not None

        def available(identifier: str) -> bool:
            """Test source availability for an evidence family, independently of item presence."""
            return drivers if identifier in {"E48", "E49", "E50"} else True

        def signal(role: str, ids: tuple[str, ...], condition: bool) -> Signal:
            """Build a signal that is present only when its condition and all required IDs hold."""
            eligible = all(available(i) for i in ids)
            return Signal(
                role,
                ids,
                bool(condition and eligible and all(i in evidence for i in ids)),
                eligible,
            )

        e = evidence.get
        loc_entries = [e(location_id(i, "added_wait_share")) for i in range(5)]
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
            changed(e("E30"), 0.20) and e("E30") and evidence["E30"]["significant"] is True
        )
        size_flat = bool(
            flat_rel(e("E30"), 0.05)
            and e("E31")
            and evidence["E31"].get("change_pp") is not None
            and abs(evidence["E31"]["change_pp"]) < 2
        )
        self.available = available
        self.drivers = drivers
        self.e = e
        self.localized = localized
        self.location = location
        self.location_index = location_index
        self.signal = signal
        self.size_counter = size_counter
        self.size_flat = size_flat


def build_review_capacity(context: SignalContext) -> dict[str, Any]:
    """Define review-capacity symptoms, demand/load mechanisms and location support."""
    return {
        "localization": context.localized["value"] if context.localized else 0,
        "location": context.location,
        "stage": stage_ids(
            ("E15", changed(context.e("E15"), 0.1)),
            ("E37", at_least(context.e("E37"), ATTRIBUTION_MIN_SHARE)),
        ),
        "location_ids": tuple(
            location_id(context.location_index, slot)
            for slot in ("pickup_ratio", "added_wait_share")
        )
        if context.location_index is not None
        else (),
        "signals": [
            context.signal("symptom", ("E1",), changed(context.e("E1"), 0.1)),
            context.signal("symptom", ("E18",), changed(context.e("E18"), 3, field="change_pp")),
            context.signal(
                "mechanism",
                ("E22",),
                bool(
                    context.e("E22")
                    and context.evidence["E22"]["extra"].get("weeks_total", 0) >= 2
                    and (
                        context.evidence["E22"]["value"]
                        / context.evidence["E22"]["extra"]["weeks_total"]
                        >= 0.5
                    )
                ),
            ),
            context.signal(
                "mechanism",
                ("E24",),
                at_least(context.e("E24"), 0.6) or changed(context.e("E24"), 5, field="change_pp"),
            ),
        ],
        "counter": [("E30",)] if context.size_counter else [],
    }


def build_pr_size_growth(context: SignalContext) -> dict[str, Any]:
    """Define delivery/rework symptoms and size-growth mechanisms with descriptive support."""
    return {
        "localization": (context.e("E42") or {}).get("value", 0),
        "location": None,
        "stage": stage_ids(("E42", at_least(context.e("E42"), ATTRIBUTION_MIN_SHARE))),
        "location_ids": (),
        "signals": [
            context.signal("symptom", ("E1",), changed(context.e("E1"), 0.1)),
            context.signal(
                "symptom",
                tuple(
                    (
                        i
                        for i, yes in (
                            ("E8", changed(context.e("E8"), 0.1)),
                            ("E9", changed(context.e("E9"), 5, field="change_pp")),
                        )
                        if yes
                    )
                )
                or ("E8", "E9"),
                changed(context.e("E8"), 0.1) or changed(context.e("E9"), 5, field="change_pp"),
            ),
            context.signal("mechanism", ("E31",), changed(context.e("E31"), 5, field="change_pp")),
            context.signal("mechanism", ("E30",), changed(context.e("E30"), 0.2)),
            context.signal("mechanism", ("E48",), at_least(context.e("E48"), 2)),
        ],
        "counter": [("E30", "E31")] if context.size_flat else [],
    }


def review_actions(location: str | None) -> tuple[str, str]:
    """Return reviewer-capacity action and follow-up wording, optionally scoped to a location."""
    return (
        f"Add reviewers or code owners for {location} and enable team auto-assignment."
        if location
        else "Add reviewers to the busiest areas and enable team auto-assignment.",
        (
            "Two weeks after adding reviewers, check whether "
            f"the first-review wait in {location} has dropped."
        )
        if location
        else ("Two weeks after adding reviewers, check whether the first-review wait has dropped."),
    )


@dataclass(frozen=True, slots=True)
class Hypothesis:
    title: str
    subject: str
    main: str
    action: Callable[[str | None], tuple[str, str]]
    build: Callable[[SignalContext], dict[str, Any]]


HYPOTHESES = {
    "H_review_capacity": Hypothesis(
        "Limited review capacity",
        "Limited review capacity",
        "E15",
        review_actions,
        build_review_capacity,
    ),
    "H_pr_size_growth": Hypothesis(
        "Pull requests getting larger",
        "Growing pull request size",
        "E1",
        lambda location: (
            "Split large changes into smaller PRs and agree on the approach before coding.",
            "Over the next month, check whether the share of PRs with 500+ lines "
            "and the cycle time both fall.",
        ),
        build_pr_size_growth,
    ),
}


# The change each symptom records. A cause sentence names the changes it would explain, so a
# hypothesis triggered by reviewer wait is not read as the cause of a cycle-time change.
SYMPTOM_EFFECTS = {
    "E1": "the slower cycle time",
    "E8": "more review rounds per PR",
    "E9": "more PRs with commits after the first review",
    "E18": "the larger share of PR time waiting on reviewers",
}


def explains(chain: Mapping[str, Any]) -> str:
    """Name the changes a candidate would explain, from its present symptom evidence."""
    effects = [SYMPTOM_EFFECTS[evidence_id] for evidence_id in chain.get("symptom", [])]
    if len(effects) < 2:
        return "".join(effects)
    return ", ".join(effects[:-1]) + " and " + effects[-1]


def actions(identifier: str, location: str | None) -> tuple[str, str]:
    """Return the registered hypothesis's action and verification wording."""
    return HYPOTHESES[identifier].action(location)


def evaluate_candidate(
    identifier: str,
    hypothesis: Hypothesis,
    context: SignalContext,
    snapshot: Mapping[str, Any],
    evidence: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    """Score one hypothesis; return its candidate plus the flags select_candidates uses.

    Eligible requires an adequate main-metric sample, a symptom, a mechanism and at least
    low confidence. Signals whose data source is unavailable leave the agreement denominator,
    so missing data neither supports nor counts against a hypothesis. Confidence is a weighted
    evidence-strength score, not a calibrated probability that this hypothesis is the cause.
    """
    definition = hypothesis.build(context)
    main_id = hypothesis.main
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
    # Every library hypothesis explains a slowdown, so only an increase counts as effect.
    if main.get("change_abs") is None or main["change_abs"] <= 0:
        effect = 0
    series_key = SERIES_KEYS[main_id]
    values = [w[series_key] for w in snapshot["series"]["current"] if w.get(series_key) is not None]
    holding = sum(v > main["previous"] for v in values) if main.get("previous") is not None else 0
    persistence = holding / len(values) if values else 0
    sample = min(1, (main.get("n") or 0) / 100)
    localization = min(1, max(0, float(definition["localization"])))
    counters = definition["counter"]
    # Fixed weights summing to 1 keep scores comparable across hypotheses; each counter-evidence
    # group costs 0.15, enough to move a borderline candidate down a band.
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
    confidence = round(raw, 2)
    chain = {
        role: list(
            dict.fromkeys(i for s in signals if s.role == role and s.present for i in s.evidence)
        )
        for role in ("symptom", "mechanism")
    }
    chain.update(
        stage=[i for i in definition["stage"] if i in evidence],
        location=[i for i in definition["location_ids"] if i in evidence],
    )
    candidate = {
        "id": identifier,
        "title": hypothesis.title,
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
            "cap": None,
            "cap_reason": None,
            "llm_downgrade": None,
        },
        "alternatives_ruled_out": [],
        "alternatives_open": [],
    }
    return {
        "candidate": candidate,
        "symptom": symptom,
        "mechanism": mechanism,
        "mechanisms": mechanisms,
        "main_available": context.available(main_id),
        "sample_ok": sample_ok,
        # A symptom alone shows that something changed and a mechanism alone shows a possible
        # lever; both are required to propose an explanation for this period's change.
        "eligible": sample_ok and symptom and mechanism and confidence >= LEVELS["low"].minimum,
    }


def score_hypotheses(
    snapshot: Mapping[str, Any], evidence: Mapping[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], str | None]:
    """Return the qualifying candidates by confidence, or none and an abstain reason.

    Without a previous period the result is no_comparison straight away, because every
    symptom is a change against that period.
    """
    if not snapshot["meta"]["comparison_available"]:
        return [], "no_comparison"
    context = SignalContext(snapshot, evidence)
    evaluated = {
        identifier: evaluate_candidate(identifier, hypothesis, context, snapshot, evidence)
        for identifier, hypothesis in HYPOTHESES.items()
    }
    return select_candidates(evidence, evaluated)


def select_candidates(
    evidence: Mapping[str, dict[str, Any]], evaluated: Mapping[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], str | None]:
    """Select every eligible candidate by confidence and explain the alternatives.

    Each other hypothesis that showed a symptom is either ruled out with cited evidence
    (counter-evidence, or mechanisms checked and absent) or left open with a reason
    (no_data, insufficient_sample, below_threshold).
    """
    selected = sorted(
        (item["candidate"] for item in evaluated.values() if item["eligible"]),
        key=lambda c: (-c["confidence"], c["id"]),
    )
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
                # Symptom, mechanism and sample are all present, so the score was too low.
                reason = "below_threshold"
            if reason:
                candidate["alternatives_open"].append({"hypothesis": identifier, "reason": reason})
    return selected, None if selected else abstain_reason(evidence, evaluated)


def abstain_reason(
    evidence: Mapping[str, dict[str, Any]], evaluated: Mapping[str, Mapping[str, Any]]
) -> str:
    """Return no_slowdown when cycle time is comparable and no slowdown symptom is present.

    Otherwise insufficient_signal.
    """
    cycle = evidence.get("E1")
    comparable = bool(
        cycle
        and cycle.get("value") is not None
        and cycle.get("previous") is not None
        and (cycle.get("n") or 0) >= MIN_SAMPLES_P50
    )
    slowing = any(item["symptom"] for item in evaluated.values())
    return "no_slowdown" if comparable and not slowing else "insufficient_signal"


def stage_ids(*items: tuple[str, bool]) -> tuple[str, ...]:
    """Retain evidence IDs whose paired stage condition is true, preserving input order."""
    return tuple(identifier for identifier, shows_change in items if shows_change)
