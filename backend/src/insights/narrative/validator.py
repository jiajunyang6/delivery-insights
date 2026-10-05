"""Validation of LLM tool output against the evidence pack.

Checks schema, length, language (CJK characters), citations, numbers, hedge wording, personal
names and abstention, and returns violations for the repair prompt; output is never modified.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from insights.narrative.hypotheses import LEVEL_ORDER, abstention, allowed_ids, chain_ids

CITATION = re.compile(r"\[(E\d+)\]")
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:,\d{3})*(?:\.\d+)?")
CJK = re.compile(r"[\u4e00-\u9fff]")
DEFINITE = re.compile(
    r"\b(definitely|certainly|clearly|undoubtedly|proves?|proved|proven|confirms?|confirmed)\b",
    re.I,
)
CAUSAL = re.compile(
    r"\b(cause[sd]?|causing|because|due to|driven by|drives|driving|leads? to|led to|"
    r"results? in|resulted in|responsible for|explains?|explained)\b",
    re.I,
)
ABSTAIN = re.compile(r"\binsufficient\b|\bnot (?:strong )?enough\b|\bno slowdown\b", re.I)
# The one sentence an abstained narrative must contain, by abstain_reason.
ABSTAIN_SENTENCES = {
    "no_slowdown": "There is no slowdown to explain this period",
    "insufficient_signal": (
        "The signals are insufficient to support a specific root cause this period"
    ),
    "no_comparison": (
        "Without a previous period, the signals are insufficient to support a root cause"
    ),
}
ABSTAIN_REQUIRED = {
    "no_slowdown": re.compile(r"\bno slowdown\b", re.I),
    "insufficient_signal": re.compile(r"\binsufficient\b|\bnot enough\b", re.I),
    "no_comparison": re.compile(r"\binsufficient\b|\bnot enough\b", re.I),
}
UP = re.compile(r"\b(rose|increased|grew|went up|climbed)\b", re.I)
DOWN = re.compile(r"\b(fell|decreased|dropped|declined|went down)\b", re.I)
SUFFIXES = (
    ("percent", r"^\s*(?:%|pp\b|percent\b|percentage points?\b)"),
    ("hours", r"^\s*(?:h\b|hrs?\b|hours?\b)"),
    ("days", r"^\s*(?:d\b|days?\b)"),
    ("minutes", r"^\s*(?:min\b|minutes?\b)"),
    ("ratio", r"^\s*(?:x\b|×|times\b)"),
)


class ToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="before")
    @classmethod
    def no_explicit_null(cls, value: Any) -> Any:
        """Reject explicitly null fields; optional tool fields must be omitted instead."""
        if isinstance(value, dict) and any(v is None for v in value.values()):
            raise ValueError("Optional fields must be omitted")
        return value


class Downgrade(ToolModel):
    level: Literal["medium", "low"]
    reason: str = Field(min_length=1, max_length=300)


class HypothesisOutput(ToolModel):
    id: str
    statement: str = Field(min_length=1, max_length=400)
    downgrade: Downgrade | None = None


class OutsideHypothesis(ToolModel):
    statement: str = Field(min_length=1, max_length=400)
    evidence_ids: list[Annotated[str, Field(pattern=r"^E[0-9]+$")]] = Field(
        min_length=2, max_length=8
    )


class ToolOutput(ToolModel):
    narrative: str = Field(min_length=1, max_length=1200)
    hypotheses: list[HypothesisOutput] = Field(max_length=3)
    llm_hypothesis: OutsideHypothesis | None = None


@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    message: str


def sentences(text: str) -> list[str]:
    """Split prose at sentence punctuation after normalizing common dotted abbreviations."""
    cleaned = re.sub(r"\b(?:vs\.|e\.g\.|i\.e\.)", lambda m: m[0].replace(".", ""), text, flags=re.I)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned.strip()) if s.strip()]


def citations(text: str) -> set[str]:
    """Extract the unique numbered evidence IDs appearing in bracketed citations."""
    return set(CITATION.findall(text))


def hedge_levels(text: str) -> set[int]:
    """Detect supported uncertainty phrases and return their low/medium/high numeric bands."""
    patterns = (r"\blikely\b", r"\b(may|might|possibly|could)\b", r"\bearly signs?\b")
    return {2 - i for i, pattern in enumerate(patterns) if re.search(pattern, text, re.I)}


def hedge_ok(text: str, level: str) -> bool:
    """Require wording for the target band with no detected hedge stronger than that band."""
    levels = hedge_levels(text)
    target = LEVEL_ORDER[level]
    return target in levels and max(levels) <= target


def numeric_tokens(text: str) -> list[tuple[float, int, str]]:
    """Extract numeric magnitudes, displayed precision and units, including shared-unit ranges."""
    matches = list(NUMBER.finditer(text))
    result = []
    for i, match in enumerate(matches):
        category = unit(text[match.end() :])
        if category == "plain" and i + 1 < len(matches):
            following = matches[i + 1]
            if re.fullmatch(r"\s*(?:to|and|-|–|→)\s*", text[match.end() : following.start()], re.I):
                category = unit(text[following.end() :])
        raw = match[0].replace(",", "")
        result.append((float(raw), len(raw.split(".")[1]) if "." in raw else 0, category))
    return result


def unit(suffix: str) -> str:
    """Classify the unit immediately following a number, defaulting to plain when unrecognized."""
    return next((name for name, pattern in SUFFIXES if re.match(pattern, suffix, re.I)), "plain")


@dataclass(frozen=True, slots=True)
class AllowedNumber:
    value: float
    category: str
    evidence: str = ""
    field: str = ""


def allowed_numbers(entry: Mapping[str, Any]) -> list[AllowedNumber]:
    """Numbers a sentence citing this item may contain, in each accepted unit.

    Hours may also appear as days, minutes as hours and shares as percent. Previous and change
    values are accepted even though the prompt asks for current values only, and numbers in
    the label itself (such as 500 in a PR-size label) are allowed too.
    """
    result: list[AllowedNumber] = []
    identifier = entry["id"]

    def add(value: Any, category: str, field: str = "", divisor: float = 1) -> None:
        """Append a scaled numeric allowance linked to this item/field; exclude booleans."""
        if isinstance(value, (float, int)) and not isinstance(value, bool):
            result.append(AllowedNumber(float(value) / divisor, category, identifier, field))

    for field in ("value", "previous", "change_abs"):
        value = entry.get(field)
        kind = entry["unit"]
        if kind == "hours":
            add(value, "hours", field)
            add(value, "days", field, 24)
        elif kind == "minutes":
            add(value, "minutes", field)
            add(value, "hours", field, 60)
        elif kind in {"share", "change"}:
            add(value, "percent", field, 0.01)
        elif kind == "ratio" and field != "change_abs":
            add(value, "ratio", field)
        elif kind in {"count", "lines", "rounds", "coefficient"}:
            add(value, "plain", field)
    add(entry.get("change_pp"), "percent", "change_pp")
    add(entry.get("change_rel"), "percent", "change_rel", 0.01)
    add(entry.get("n"), "plain", "n")
    for key, value in entry.get("extra", {}).items():
        add(value, "plain")
        for ending in ("days", "hours", "minutes"):
            if key.endswith(f"_{ending}"):
                add(value, ending)
    for value, _, category in numeric_tokens(entry["label"]):
        add(value, category)
    return result


def match_numbers(
    sentence: str,
    pack: Mapping[str, Any],
    evidence: Mapping[str, dict[str, Any]],
    allowed: list[AllowedNumber],
) -> tuple[list[Violation], dict[str, set[str]]]:
    """Match sentence numbers to allowed magnitudes/units and return violations plus matched fields.

    Remove citation IDs, dates and scope names before tokenizing; tolerance follows text precision.
    """
    cleaned = CITATION.sub("", sentence)
    remove = [*pack["scope"]["repos"], *(e["location"] for e in evidence.values() if e["location"])]
    period = pack["period"]
    remove += [period["from"], period["to"]]
    if period.get("compared_to"):
        remove += list(period["compared_to"].values())
    for text in sorted(set(remove), key=len, reverse=True):
        cleaned = cleaned.replace(text, "")
    errors = []
    matched: dict[str, set[str]] = {}
    for value, decimals, category in numeric_tokens(cleaned):
        # Accept rounding to the precision used in the sentence, then check units separately:
        # equal magnitudes in hours and percentages do not support the same claim.
        tolerance = 0.5 * 10 ** (-decimals) + 1e-9
        values = [a for a in allowed if abs(value - abs(a.value)) <= tolerance]
        compatible = [
            a
            for a in values
            if a.category == category or (category == "plain" and a.category == "ratio")
        ]
        if not compatible:
            name = "unit_mismatch" if values else "number_not_in_evidence"
            errors.append(Violation(f"V5:{name}", f"Unsupported {category} number {value:g}."))
        for a in compatible:
            if a.evidence:
                matched.setdefault(a.evidence, set()).add(a.field)
    return errors, matched


def check_numbers(
    sentence: str, pack: Mapping[str, Any], evidence: Mapping[str, dict[str, Any]]
) -> list[Violation]:
    """Check one sentence's numbers and stated change direction against cited evidence.

    Numbers must come from the cited items, the period length, or persistence counts of a
    hypothesis whose chain is cited; repo names, locations and dates are ignored. When the
    sentence uses only rising or only falling verbs, the matched (or sole changing) cited item
    must have moved that way.
    """
    cited = citations(sentence) & evidence.keys()
    allowed = [number for i in cited for number in allowed_numbers(evidence[i])]
    if cited:
        days = pack["period"]["days"]
        allowed += [AllowedNumber(days, "plain"), AllowedNumber(days, "days")]
        for hypothesis in pack["hypotheses"]:
            if cited & set(chain_ids(hypothesis)):
                allowed += [AllowedNumber(v, "plain") for v in hypothesis["persistence"].values()]
    errors, matched = match_numbers(sentence, pack, evidence, allowed)
    up, down = bool(UP.search(sentence)), bool(DOWN.search(sentence))
    if up != down:
        changed = {
            i
            for i, fields in matched.items()
            if fields & {"change_abs", "change_rel", "change_pp"} or {"value", "previous"} <= fields
        }
        if not changed:
            changing = {
                i
                for i in cited
                if evidence[i].get("change_rel") is not None
                or evidence[i].get("change_abs") is not None
            }
            if len(changing) == 1:
                changed = changing
        for identifier in changed:
            entry = evidence[identifier]
            delta = entry.get("change_rel")
            if delta is None:
                delta = entry.get("change_abs")
            if delta is not None and (delta <= 0 if up else delta >= 0):
                errors.append(
                    Violation(
                        "V5:direction_mismatch", f"Change direction disagrees with {identifier}."
                    )
                )
    return errors


def validate_hypotheses(
    hypotheses: list[dict[str, Any]],
    candidates: Mapping[str, dict[str, Any]],
    evidence: Mapping[str, dict[str, Any]],
    body: str,
    errors: list[Violation],
) -> list[int]:
    """Check library hypothesis statements, appending violations to errors.

    Returns the final band order (0 low to 2 high, after a valid downgrade) of each statement
    for a known candidate, for the body's causal-wording ceiling.
    """

    def fail(code: str, message: str) -> None:
        """Collect a hypothesis-contract violation without interrupting the remaining checks."""
        errors.append(Violation(code, message))

    seen: set[str] = set()
    levels: list[int] = []
    for hypothesis in hypotheses:
        identifier, statement = hypothesis["id"], hypothesis["statement"]
        if identifier in seen:
            fail("V6:duplicate_hypothesis", f"Duplicate hypothesis {identifier}.")
        seen.add(identifier)
        if identifier not in candidates:
            fail("V6:unknown_hypothesis", "Hypothesis is not a candidate.")
            continue
        candidate = candidates[identifier]
        final_level = candidate["level"]
        allowed = allowed_ids(candidate)
        cites = citations(statement)
        if not cites or cites - allowed:
            fail("V6:citation_outside_chain", f"Statement must cite the chain for {identifier}.")
        if candidate["counter_evidence"] and not set(candidate["counter_evidence"]) & (
            cites | citations(body)
        ):
            fail(
                "V6:counter_evidence_not_cited", f"Counter-evidence for {identifier} must be cited."
            )
        downgrade = hypothesis.get("downgrade")
        if isinstance(downgrade, dict):
            proposed = downgrade.get("level")
            reason = downgrade.get("reason", "")
            if (
                proposed not in {"medium", "low"}
                or LEVEL_ORDER[proposed] >= LEVEL_ORDER[final_level]
                or not isinstance(reason, str)
                or not citations(reason) & evidence.keys()
            ):
                fail("V8:invalid_downgrade", f"Invalid downgrade for {identifier}.")
            else:
                final_level = proposed
        levels.append(LEVEL_ORDER[final_level])
        if not hedge_ok(statement, final_level):
            fail("V7:hedge_mismatch", f"Statement must use {final_level} language.")
    for identifier, candidate in candidates.items():
        if candidate["level"] in {"high", "medium"} and identifier not in seen:
            fail("V6:missing_required_hypothesis", f"Required hypothesis {identifier} is missing.")
    return levels


def validate(output: dict[str, Any] | None, pack: Mapping[str, Any]) -> list[Violation]:
    """Return every violation in one tool output, deduplicated; an empty list means valid.

    Schema errors do not stop the semantic checks, so a single repair message can list all
    problems. A None output (no tool call) yields schema violations only. Numeric support is
    scoped to each sentence's citations, not to every number available anywhere in the pack.
    """
    errors: list[Violation] = []

    def fail(code: str, message: str) -> None:
        """Collect a schema or semantic violation for the complete repair feedback."""
        errors.append(Violation(code, message))

    try:
        model = ToolOutput.model_validate(output)
    except ValidationError as exc:
        for error in exc.errors(include_input=False, include_url=False):
            path = ".".join(str(part) for part in error["loc"]) or "output"
            fail("V1:schema", f"{path}: {error['msg']}.")
        # Semantic checks still run for structurally accessible fields.
        if not isinstance(output, dict):
            return errors
        body = output.get("narrative", "")
        body = body if isinstance(body, str) else ""
        raw_hypotheses = output.get("hypotheses", [])
        hypotheses = (
            [
                h
                for h in raw_hypotheses
                if isinstance(h, dict)
                and isinstance(h.get("statement"), str)
                and isinstance(h.get("id"), str)
            ]
            if isinstance(raw_hypotheses, list)
            else []
        )
        outside = output.get("llm_hypothesis")
        outside = (
            outside
            if isinstance(outside, dict) and isinstance(outside.get("statement"), str)
            else None
        )
    else:
        parsed = model.model_dump(exclude_none=True)
        body, hypotheses, outside = (
            parsed["narrative"],
            parsed["hypotheses"],
            parsed.get("llm_hypothesis"),
        )
    candidates = {c["id"]: c for c in pack["hypotheses"]}
    evidence = {e["id"]: e for e in pack["evidence"]}
    body_sentences = sentences(body)
    low, high = (2 if candidates else 1), 4
    if len(body) > 1200:
        fail("V2:length", "Narrative exceeds 1200 characters.")
    if not low <= len(body_sentences) <= high:
        fail("V2:sentence_count", f"Narrative requires {low}-{high} sentences.")
    texts = [body]
    for h in hypotheses:
        texts.append(h["statement"])
        if isinstance(h.get("downgrade"), dict) and isinstance(h["downgrade"].get("reason"), str):
            texts.append(h["downgrade"]["reason"])
    if outside:
        texts.append(outside["statement"])
    if any(CJK.search(t) for t in texts):
        fail("V3:language", "Narrative text must be in English.")
    for i, sentence in enumerate(body_sentences, 1):
        if not citations(sentence):
            fail("V4:sentence_without_citation", f"Body sentence {i} needs evidence.")
    # Narratives discuss areas and teams, never individuals. Logins never enter the pack, so
    # an @mention can only be a guessed name.
    for text in texts:
        for identifier in sorted(citations(text) - evidence.keys()):
            fail("V4:unknown_citation", f"Unknown citation {identifier}.")
        for sentence in sentences(text):
            errors.extend(check_numbers(sentence, pack, evidence))
        if DEFINITE.search(text):
            fail("V7b:overclaim", "Definite causal language is not supported.")
        if re.search(r"@[A-Za-z0-9]", text):
            fail("V10:personal_name", "Personal login names must not appear.")
    levels = validate_hypotheses(hypotheses, candidates, evidence, body, errors)
    if outside:
        ids = outside.get("evidence_ids", [])
        ids = set(ids) if isinstance(ids, list) and all(isinstance(i, str) for i in ids) else set()
        observed = {i for o in pack["observations"] for i in o["evidence_ids"]}
        significant = {
            e["side"]
            for i, e in evidence.items()
            if i in ids and (e["significant"] is True or i in observed)
        }
        if (
            not candidates
            or not ids <= evidence.keys()
            or significant != {"efficiency", "bottleneck"}
            or not citations(outside["statement"])
            or not citations(outside["statement"]) <= ids
            or not hedge_ok(outside["statement"], "low")
        ):
            fail(
                "V9:invalid_llm_hypothesis",
                "Outside hypothesis needs significant evidence from both sides, all statement "
                "citations in evidence_ids, and low wording only: early signs, "
                "without may, might, possibly, could or likely. "
                "Omit this optional hypothesis if those requirements cannot be met.",
            )
        levels.append(0)
    # Abstention words exempt a causal sentence only when no hypothesis is output; otherwise
    # "insufficient" could slip past the wording ceiling of a low-band hypothesis.
    for sentence in body_sentences:
        if CAUSAL.search(sentence) and (levels or not ABSTAIN.search(sentence)):
            hedges = hedge_levels(sentence)
            if not levels or not hedges or max(hedges) > max(levels):
                fail("V7b:overclaim", "Body causal language exceeds the supported level.")
    if not candidates:
        pattern = ABSTAIN_REQUIRED.get(abstention(pack)[0], ABSTAIN_REQUIRED["insufficient_signal"])
        required = pattern.search(body)
        if (
            hypotheses
            or outside
            or not required
            or any(CAUSAL.search(s) and not ABSTAIN.search(s) for s in body_sentences)
        ):
            fail(
                "V12:abstain",
                "Without hypotheses, include the required abstention sentence and state no cause.",
            )
    return list(dict.fromkeys(errors))
