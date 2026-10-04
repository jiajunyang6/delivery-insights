from collections.abc import Mapping
from typing import Any

from insights.narrative.hypotheses import HYPOTHESES, abstention, chain_ids
from insights.narrative.validator import ABSTAIN_SENTENCES

FINDINGS = {
    "review_queue_growth": "review demand exceeding first reviews",
    "review_concentration": "reviews concentrated on a few people",
    "merge_blocked": "approved PRs waiting to merge",
    "ci_wait": "waiting on CI",
    "rework_high": "rework after review",
    "waste_high": "work that never shipped",
    "quality_guardrail": "a quality warning",
    "external_contributor_wait": "slow first reviews for external contributors",
}
WAITING = {
    "E18": "waiting on reviewers",
    "E19": "waiting on authors",
    "E20": "waiting on CI",
    "E21": "waiting to merge after approval",
}


def percent(value: float) -> str:
    return f"{value * 100:.1f}%" if abs(value * 100) < 10 else f"{value * 100:.0f}%"


def phrase(candidate: Mapping[str, Any]) -> str:
    if candidate["id"] == "H_review_capacity":
        location = candidate["location"]
        subject = (
            "Limited review capacity" if not location else f"Limited review capacity in {location}"
        )
    else:
        subject = HYPOTHESES[candidate["id"]].subject
    level = candidate["level"]
    return (
        f"{subject} is likely the main cause"
        if level == "high"
        else f"{subject} may be the main cause"
        if level == "medium"
        else f"There are early signs that {subject[0].lower() + subject[1:]} is the main cause"
    )


def build_template(pack: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
    audience = pack["audience"]
    evidence = {e["id"]: e for e in pack["evidence"]}
    candidates = pack["hypotheses"]

    def hour(value: float) -> str:
        return f"{value:.1f} h"

    def sentence(text: str, ids: list[str]) -> str:
        return f"{text} {''.join(f'[{i}]' for i in ids[:3])}" + "."

    cycle = evidence.get("E1")
    if cycle is None:
        count = evidence["E3"]["value"]
        s1 = sentence(
            f"Only {count} PRs were merged, too few for reliable cycle-time statistics", ["E3"]
        )
    elif cycle["previous"] is None:
        s1 = sentence(
            f"Median cycle time was {hour(cycle['value'])}; there is no previous period to compare",
            ["E1"],
        )
    elif cycle["significant"] is True and cycle["change_rel"] is not None:
        delta = percent(abs(cycle["change_rel"]))
        current, previous = (hour(cycle["value"]), hour(cycle["previous"]))
        verb = "rose" if cycle["change_rel"] > 0 else "fell"
        text = f"Median cycle time {verb} {delta} to {current} from {previous}"
        s1 = sentence(text, ["E1"])
    else:
        s1 = sentence(
            (
                f"Median cycle time was {hour(cycle['value'])}, "
                "with no significant change from the previous period"
            ),
            ["E1"],
        )
    parts = {"S1": s1}
    if audience == "manager" and "E6" in evidence:
        share = percent(evidence["E6"]["value"])
        parts["S2"] = sentence(
            f"PRs spent {share} of their cycle time waiting on reviewers, CI or merge", ["E6"]
        )
    if pack["top_bottlenecks"] and "E71" in evidence:
        finding = pack["top_bottlenecks"][0]
        location = finding["location"]
        if finding["type"] == "review_capacity":
            name = f"the first-review wait in {location}"
        else:
            name = FINDINGS[finding["type"]]
        share = percent(evidence["E71"]["value"])
        parts["S3"] = sentence(
            f"The largest time sink is {name}, about {share} of PR time", ["E71"]
        )
    elif waits := [i for i in WAITING if evidence.get(i, {}).get("value") is not None]:
        largest = max(waits, key=lambda i: (evidence[i]["value"], -int(i[1:])))
        share = percent(evidence[largest]["value"])
        parts["S3"] = sentence(
            f"No single bottleneck stands out; the largest share of PR time, {share}, "
            f"is spent {WAITING[largest]}",
            [largest],
        )
    if audience == "manager" and evidence.get("E25", {}).get("value", 0) > 0:
        count, critical = (evidence["E25"]["value"], evidence["E25"]["extra"]["critical"])
        parts["S4"] = sentence(
            f"{count} open PRs are waiting longer than usual, {critical} of them critically",
            ["E25"],
        )
    if candidates:
        parts["S5"] = sentence(phrase(candidates[0]), chain_ids(candidates[0]))
    else:
        reason, identifier = abstention(pack)
        parts["S5"] = sentence(ABSTAIN_SENTENCES[reason], [identifier])
    if snapshot["guardrail"]["verdict"] != "ok" and "E10" in evidence:
        share = percent(evidence["E10"]["value"])
        parts["S6"] = sentence(
            f"The revert rate is {share}, so check review depth before pushing for more speed",
            ["E10"],
        )
    order = (
        ("S1", "S5", "S3", "S6") if audience == "director" else ("S1", "S2", "S3", "S4", "S5", "S6")
    )
    hypotheses = []
    for c in candidates:
        text = phrase(c) + " " + "".join(f"[{i}]" for i in chain_ids(c)[:3])
        if c["counter_evidence"]:
            text += ", although there is counter-evidence "
            text += f"[{c['counter_evidence'][0]}]"
        hypotheses.append({"id": c["id"], "statement": text + "."})
    return {
        "narrative": " ".join(parts[k] for k in order if k in parts),
        "hypotheses": hypotheses,
    }
