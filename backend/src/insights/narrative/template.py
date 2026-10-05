"""Deterministic template narrative, used whenever no validated LLM answer is available.

It follows the hedge bands and abstention sentences that the validator enforces on the LLM.
"""

from collections.abc import Mapping
from typing import Any

from insights.narrative.hypotheses import HYPOTHESES, abstention, chain_ids, explains
from insights.narrative.validator import ABSTAIN_SENTENCES

WAITING = {
    "E18": "waiting on reviewers",
    "E19": "waiting on authors",
    "E20": "waiting on CI",
    "E21": "waiting to merge after approval",
}


def percent(value: float) -> str:
    """Format a fractional share as percent, keeping one decimal below ten percent in magnitude."""
    return f"{value * 100:.1f}%" if abs(value * 100) < 10 else f"{value * 100:.0f}%"


def phrase(candidate: Mapping[str, Any]) -> str:
    """Render a candidate's location, band wording and the changes it would explain."""
    if candidate["id"] == "H_review_capacity":
        location = candidate["location"]
        subject = (
            "Limited review capacity" if not location else f"Limited review capacity in {location}"
        )
    else:
        subject = HYPOTHESES[candidate["id"]].subject
    level = candidate["level"]
    effect = explains(candidate["chain"])
    cause = f"the main cause of {effect}" if effect else "the main cause"
    return (
        f"{subject} is likely {cause}"
        if level == "high"
        else f"{subject} may be {cause}"
        if level == "medium"
        else f"There are early signs that {subject[0].lower() + subject[1:]} is {cause}"
    )


def build_template(pack: Mapping[str, Any]) -> dict[str, Any]:
    """Return output in the submit_narrative shape, built from the pack alone.

    Three sentences: the key metric, the top cause or the abstention, and where PR time goes
    now. Each sentence cites at most three evidence IDs.
    """
    evidence = {e["id"]: e for e in pack["evidence"]}
    candidates = pack["hypotheses"]

    def hour(value: float) -> str:
        """Format an elapsed-hour value with one decimal and its unit."""
        return f"{value:.1f} h"

    def sentence(text: str, ids: list[str]) -> str:
        """Append up to three evidence citations and a final period to the supplied text."""
        return f"{text} {''.join(f'[{i}]' for i in ids[:3])}" + "."

    cycle = evidence.get("E1")
    if cycle is None:
        count = evidence["E3"]["value"]
        metric = sentence(
            f"Only {count} PRs were merged, too few for reliable cycle-time statistics", ["E3"]
        )
    elif cycle["previous"] is None:
        metric = sentence(
            f"Median cycle time was {hour(cycle['value'])}; there is no previous period to compare",
            ["E1"],
        )
    elif cycle["significant"] is True and cycle["change_rel"] is not None:
        delta = percent(abs(cycle["change_rel"]))
        current, previous = (hour(cycle["value"]), hour(cycle["previous"]))
        verb = "rose" if cycle["change_rel"] > 0 else "fell"
        metric = sentence(f"Median cycle time {verb} {delta} to {current} from {previous}", ["E1"])
    else:
        metric = sentence(
            (
                f"Median cycle time was {hour(cycle['value'])}, "
                "with no significant change from the previous period"
            ),
            ["E1"],
        )
    parts = [metric]
    if candidates:
        parts.append(sentence(phrase(candidates[0]), chain_ids(candidates[0])))
    else:
        reason, identifier = abstention(pack)
        parts.append(sentence(ABSTAIN_SENTENCES[reason], [identifier]))
    if waits := [i for i in WAITING if evidence.get(i, {}).get("value") is not None]:
        largest = max(waits, key=lambda i: (evidence[i]["value"], -int(i[1:])))
        share = percent(evidence[largest]["value"])
        parts.append(
            sentence(
                f"The largest share of PR time, {share}, is spent {WAITING[largest]}", [largest]
            )
        )
    hypotheses = []
    for c in candidates:
        text = phrase(c) + " " + "".join(f"[{i}]" for i in chain_ids(c)[:3])
        if c["counter_evidence"]:
            text += ", although there is counter-evidence "
            text += f"[{c['counter_evidence'][0]}]"
        hypotheses.append({"id": c["id"], "statement": text + "."})
    return {"narrative": " ".join(parts), "hypotheses": hypotheses}
