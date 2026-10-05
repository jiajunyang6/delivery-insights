"""Evidence pack construction: numbered evidence items, observations and scored hypotheses.
The pack is the only snapshot data the LLM receives.
"""

import re
from collections.abc import Mapping
from typing import Any

from insights.analytics.pointer import resolve_pointer
from insights.analytics.thresholds import CI_COVERAGE_MIN
from insights.narrative.catalog import CATALOG
from insights.narrative.hypotheses import (
    CI_EVIDENCE_IDS,
    CI_RUN_IDS,
    changed,
    effect_size,
    explains,
    location_id,
    score_hypotheses,
)

SAFE_LOCATION = re.compile(r"^[A-Za-z0-9._:/+#-]{1,120}$")
WEIGHTS = {
    "E1": 1.0,
    "E15": 0.9,
    "E18": 0.8,
    "E20": 0.7,
    "E3": 0.6,
    "E8": 0.6,
    "E30": 0.6,
    "E19": 0.6,
    "E21": 0.6,
    "E24": 0.5,
}


def read(snapshot: Mapping[str, Any], pointer: str) -> Any:
    """Resolve optional snapshot evidence; return None for missing or structurally absent data."""
    try:
        return resolve_pointer(snapshot, pointer)
    except (KeyError, IndexError, TypeError):
        return None


def extract_evidence(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Resolve catalog and location items from the snapshot.

    Items whose pointer is missing or whose value is None are skipped, so IDs can be sparse.
    E20 and E39 are omitted when the snapshot has no CI data. Up to five locations other than
    "other" get a first-review ratio and an added-wait share (see location_id). Entries keep
    their pointer for the API response; the pack strips it.
    """
    entries: list[dict[str, Any]] = []

    def add(
        identifier: str,
        key: str,
        label: str,
        ref: str,
        unit: str,
        side: str = "bottleneck",
        location: str | None = None,
    ) -> None:
        """Append a catalog item with normalized values; skip absent values."""
        raw = read(snapshot, ref)
        if raw is None:
            return
        entry: dict[str, Any] = {
            "id": identifier,
            "key": key,
            "label": label,
            "unit": unit,
            "value": raw,
            "previous": None,
            "change_abs": None,
            "change_rel": None,
            "change_pp": None,
            "significant": None,
            "n": None,
            "side": side,
            "baseline": None,
            "location": location,
            "extra": {},
            "ref": ref,
        }
        if isinstance(raw, dict):
            if "share" in raw:
                entry.update(
                    value=raw["share"],
                    previous=raw["previous_share"],
                    change_pp=raw["change_pp"],
                    n=snapshot["time_ledger"]["merged_prs"],
                )
                if entry["previous"] is not None:
                    entry["change_abs"] = entry["value"] - entry["previous"]
            else:
                for field in (
                    "value",
                    "previous",
                    "change_abs",
                    "change_rel",
                    "significant",
                    "n",
                    "extra",
                ):
                    if field in raw:
                        entry[field] = raw[field]
        entry["extra"] = {
            key: value
            for key, value in entry["extra"].items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        if entry["value"] is None:
            return
        if identifier == "E22":
            entry["extra"] = {
                "weeks_total": snapshot["bottleneck_analysis"]["review_queue"]["weeks_total"]
            }
        if unit == "share" and entry["change_abs"] is not None:
            entry["change_pp"] = round(entry["change_abs"] * 100, 2)
        if entry["previous"] is not None:
            entry["baseline"] = "previous_period"
        if key == "loc_pickup_ratio":
            entry["baseline"] = "rest_of_repo"
        entries.append(entry)

    for identifier, key, label, ref, unit, side in CATALOG:
        if (
            identifier in CI_EVIDENCE_IDS - CI_RUN_IDS
            and not snapshot["time_ledger"]["ci_data_available"]
        ):
            continue
        add(identifier, key, label, ref, unit, side)
    locations = [
        (i, loc)
        for i, loc in enumerate(snapshot["bottleneck_analysis"]["locations"])
        if loc["location"] != "other"
    ][:5]
    for i, (idx, loc) in enumerate(locations):
        name = loc["location"]
        root = f"/bottleneck_analysis/locations/{idx}"
        add(
            location_id(i, "pickup_ratio"),
            "loc_pickup_ratio",
            f"First-review wait in {name} vs the rest of the repo",
            f"{root}/pickup_ratio_vs_rest",
            "ratio",
            location=name,
        )
        add(
            location_id(i, "added_wait_share"),
            "loc_added_wait_share",
            f"Share of the added time that is reviewer wait in {name}",
            f"/trend/attribution/locations/{idx}/share_of_increase",
            "share",
            location=name,
        )
    return entries


def observations(
    snapshot: Mapping[str, Any], evidence: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Rank notable changes and one fixed contradiction into at most eight observations.

    Salience is effect size x per-metric weight (WEIGHTS, default 0.4) x sample factor
    (n / 100 capped at 1, or 0.5 when n is unknown). Items without a previous value or flagged
    non-significant are skipped, and changes below 0.15 are dropped. A contradiction scores
    0.2 above its strongest component, so it ranks ahead of that component's own change.
    """
    candidates: list[dict[str, Any]] = []
    scores: dict[str, float] = {}
    entries = {e["id"]: e for e in evidence}
    for e in evidence:
        if e["previous"] is None or e["significant"] is False:
            continue
        effect, sigma = effect_size(e, snapshot)
        score = (
            effect
            * WEIGHTS.get(e["id"], 0.4)
            * (min(1, e["n"] / 100) if e["n"] is not None else 0.5)
        )
        scores[e["id"]] = score
        if score >= 0.15:
            candidates.append(
                {
                    "kind": "anomaly" if sigma and effect >= 1 else "change",
                    "evidence_ids": [e["id"]],
                    "direction": "up" if e["change_abs"] > 0 else "down",
                    "salience": score,
                }
            )
    # More merged PRs alongside a slower cycle time reads as mixed, not as a plain slowdown.
    if changed(entries.get("E3"), 0.1) and changed(entries.get("E1"), 0.1):
        ids = ["E3", "E1"]
        candidates.append(
            {
                "kind": "contradiction:throughput_up_cycle_up",
                "evidence_ids": ids,
                "direction": "mixed",
                "salience": min(1, max(scores.get(i, 0) for i in ids) + 0.2),
            }
        )
    order = {"contradiction": 0, "anomaly": 1, "change": 2}
    candidates.sort(
        key=lambda o: (
            -o["salience"],
            order[o["kind"].split(":")[0]],
            int(o["evidence_ids"][0][1:]),
        )
    )
    return [
        {"id": f"O{i + 1}", **{k: v for k, v in o.items() if k != "salience"}}
        for i, o in enumerate(candidates[:8])
    ]


def build_evidence_pack(
    snapshot: Mapping[str, Any],
    ci_complete: bool,
    *,
    evidence: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return the LLM-facing pack and the full hypothesis candidates.

    The pack drops pointers, replaces location names outside SAFE_LOCATION
    with placeholders, and gives hypotheses only their level band, not the numeric confidence.
    The candidates keep the scoring detail that assemble() needs. Pass evidence to reuse an
    earlier extract_evidence() result.
    """
    if evidence is None:
        evidence = extract_evidence(snapshot)
    candidates, abstain = score_hypotheses(
        snapshot, {e["id"]: e for e in evidence}, ci_complete=ci_complete
    )
    locations: dict[str, str] = {}

    def sanitize(name: str | None) -> str | None:
        """Keep allowed location names or assign a stable placeholder; preserve None."""
        if name is None:
            return None
        if name not in locations:
            locations[name] = (
                name if SAFE_LOCATION.fullmatch(name) else f"location-{len(locations) + 1}"
            )
        return locations[name]

    # Register locations in snapshot order so placeholder numbers do not depend on which
    # evidence item mentions a location first.
    for loc in snapshot["bottleneck_analysis"]["locations"]:
        sanitize(loc["location"])
    safe_evidence = []
    for e in evidence:
        safe = {k: v for k, v in e.items() if k != "ref"}
        safe["location"] = sanitize(e["location"])
        if e["location"]:
            safe["label"] = safe["label"].replace(e["location"], safe["location"])
        safe_evidence.append(safe)
    gaps = []
    if not snapshot["meta"]["comparison_available"]:
        gaps.append("no_comparison")
    if not (
        snapshot["time_ledger"]["ci_data_available"]
        and snapshot["time_ledger"]["ci_coverage"] >= CI_COVERAGE_MIN
        and ci_complete
    ):
        gaps.append("ci_data_incomplete")
    if snapshot["meta"]["sample"]["merged_prs"] < 30:
        gaps.append("few_samples")
    pack = {
        "pack_version": "1",
        "lang": "en",
        "period": {k: snapshot["period"][k] for k in ("from", "to", "days", "compared_to")},
        "scope": {
            "repos": snapshot["repos"],
            "location_dimension": snapshot["meta"]["location_dimension"],
        },
        "comparison_available": snapshot["meta"]["comparison_available"],
        "data_gaps": gaps,
        "evidence": safe_evidence,
        "observations": observations(snapshot, evidence),
        "hypotheses": [
            {
                "id": c["id"],
                "title": c["title"],
                "level": c["confidence_level"],
                "location": sanitize(c["location"]),
                "explains": explains(c["chain"]),
                "chain": c["chain"],
                "counter_evidence": c["counter_evidence"],
                "persistence": c["persistence"],
                "ruled_out": [
                    {"id": a["hypothesis"], "evidence_ids": a["evidence"]}
                    for a in c["alternatives_ruled_out"]
                ],
            }
            for c in candidates
        ],
        "abstain_reason": abstain,
    }
    return pack, candidates
