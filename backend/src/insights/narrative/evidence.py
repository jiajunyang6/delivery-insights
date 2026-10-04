"""Evidence pack construction: numbered evidence items, observations, top bottlenecks and
scored hypotheses. The pack is the only snapshot data the LLM receives.
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
    "E10": 0.9,
    "E18": 0.8,
    "E5": 0.8,
    "E6": 0.8,
    "E4": 0.8,
    "E7": 0.7,
    "E20": 0.7,
    "E3": 0.6,
    "E2": 0.6,
    "E8": 0.6,
    "E30": 0.6,
    "E17": 0.6,
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
    """Resolve catalog, location and top-finding items from the snapshot.

    Items whose pointer is missing or whose value is None are skipped, so IDs can be sparse.
    E20, E39 and E47 are omitted when the snapshot has no CI data. Up to five locations other
    than "other" get four slots each (see location_id); the top three findings become E71-E73.
    Entries keep their pointer and example URLs for the API response; the pack strips both.
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
        """Append a catalog item with normalized values and safe examples; skip absent values."""
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
            "examples": [],
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
        if identifier == "E13":
            entry.update(
                previous=read(snapshot, "/efficiency/survival/previous/median_hours"),
                n=read(snapshot, "/efficiency/survival/current/n"),
            )
        elif identifier == "E22":
            entry["extra"] = {
                "weeks_total": snapshot["bottleneck_analysis"]["review_queue"]["weeks_total"]
            }
        elif identifier == "E25":
            entry["extra"] = {"critical": snapshot["at_risk_summary"]["critical"]}
        elif identifier == "E36":
            entry.update(
                previous=read(snapshot, "/trend/attribution/cycle_mean_hours/previous"),
                change_abs=read(snapshot, "/trend/attribution/cycle_mean_hours/change"),
            )
        if unit == "share" and entry["change_abs"] is not None:
            entry["change_pp"] = round(entry["change_abs"] * 100, 2)
        if entry["previous"] is not None:
            entry["baseline"] = "previous_period"
        if key == "loc_pickup_ratio":
            entry["baseline"] = "rest_of_repo"
        elif identifier in {"E11", "E25"}:
            entry["baseline"] = "team_history"
        elif identifier == "E27":
            entry["baseline"] = "internal_contributors"
        examples: list[str] = []
        if location is not None or identifier == "E25":
            examples = [
                p["url"]
                for p in snapshot["at_risk_prs"]
                if location is None or location in p["locations"]
            ][:3]
        elif identifier in {"E10", "E4"}:
            examples = [c["revert"]["url"] for c in snapshot["rework"]["revert_chains"][:3]]
        entry["examples"] = [url for url in examples if url.startswith("https://github.com/")]
        entries.append(entry)

    for identifier, key, label, ref, unit, side in CATALOG:
        if (
            identifier in CI_EVIDENCE_IDS - CI_RUN_IDS
            and not snapshot["time_ledger"]["ci_data_available"]
        ):
            continue
        if identifier == "E48":
            features = read(snapshot, "/drivers/slowest_decile/features") or []
            index = next(
                (i for i, f in enumerate(features) if f["feature"] == "size_lines_p50"), None
            )
            if index is None:
                continue
            ref = f"/drivers/slowest_decile/features/{index}/ratio"
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
            location_id(i, "waiting_share"),
            "loc_waiting_share",
            f"Share of reviewer-waiting time in {name}",
            f"{root}/waiting_reviewer_share",
            "share",
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
        add(
            location_id(i, "owners"),
            "loc_owners",
            f"Owners for {name}",
            f"{root}/owners_count",
            "count",
            location=name,
        )
    for i, finding in enumerate(snapshot["bottlenecks"][:3]):
        add(
            f"E{71 + i}",
            "finding_impact",
            f"Share of finished PR waiting time (merged and closed unmerged): {finding['id']}",
            f"/bottlenecks/{i}/impact_share",
            "share",
            location=finding["location"],
        )
    return entries


def observations(
    snapshot: Mapping[str, Any], evidence: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Rank notable changes and two fixed contradictions into at most eight observations.

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
    for kind, ids, condition in (
        (
            "throughput_up_cycle_up",
            ["E3", "E1"],
            changed(entries.get("E3"), 0.1) and changed(entries.get("E1"), 0.1),
        ),
        (
            "faster_but_more_reverts",
            ["E1", "E10"],
            changed(entries.get("E1"), 0.1, direction=-1)
            and changed(entries.get("E10"), 1, field="change_pp"),
        ),
    ):
        if condition:
            candidates.append(
                {
                    "kind": f"contradiction:{kind}",
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
    audience: str,
    ci_complete: bool,
    *,
    evidence: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return the LLM-facing pack and the full hypothesis candidates.

    The pack drops pointers and example URLs, replaces location names outside SAFE_LOCATION
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
        safe = {k: v for k, v in e.items() if k not in {"ref", "examples"}}
        safe["location"] = sanitize(e["location"])
        if e["location"]:
            safe["label"] = safe["label"].replace(e["location"], safe["location"])
        safe_evidence.append(safe)
    available_ids = {e["id"] for e in evidence}
    top = []
    selected_locs = [
        loc["location"]
        for loc in snapshot["bottleneck_analysis"]["locations"]
        if loc["location"] != "other"
    ][:5]
    for i, finding in enumerate(snapshot["bottlenecks"][:3]):
        name = finding["location"]
        ids = [f"E{71 + i}"]
        if name in selected_locs:
            idx = selected_locs.index(name)
            ids += [location_id(idx, "pickup_ratio"), location_id(idx, "waiting_share")]
        top.append(
            {
                "id": finding["id"].replace(name, sanitize(name)) if name else finding["id"],
                "type": finding["type"],
                "severity": finding["severity"],
                "location": sanitize(name),
                "evidence_ids": [i for i in ids if i in available_ids],
            }
        )
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
        "audience": audience,
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
        "top_bottlenecks": top,
        "hypotheses": [
            {
                "id": c["id"],
                "title": c["title"],
                "level": c["confidence_level"],
                "location": sanitize(c["location"]),
                "explains": explains(c["id"], c["chain"]),
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
