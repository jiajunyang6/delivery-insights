import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from insights.narrative.hypotheses import LEVEL_ORDER

CITATION = re.compile(r"\[(E\d+)\]")
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:,\d{3})*(?:\.\d+)?")
CJK = re.compile(r"[\u4e00-\u9fff]")
DEFINITE = re.compile(
    r"\b(definitely|certainly|clearly|undoubtedly|proves?|proved|proven|confirms?|confirmed)\b"
    r"|(?<!不)一定(?!程度)|(?<!不)肯定|(?<!不)必然|毫无疑问|证明了|确定是",
    re.I,
)
CAUSAL = re.compile(
    r"\b(cause[sd]?|causing|because|due to|driven by|drives|driving|leads? to|led to|"
    r"results? in|resulted in|responsible for|explains?|explained)\b"
    r"|原因|导致|由于|造成|引起|归因|因为",
    re.I,
)
ABSTAIN = re.compile(r"\binsufficient\b|\bnot (?:strong )?enough\b|信号不足|不足以", re.I)
UP = re.compile(r"\b(rose|increased|grew|went up|climbed)\b|上升|增加|增长|变长", re.I)
DOWN = re.compile(r"\b(fell|decreased|dropped|declined|went down)\b|下降|减少|缩短", re.I)
SUFFIXES = (
    ("percent", r"^\s*(?:%|pp\b|percent\b|percentage points?\b|个百分点)"),
    ("hours", r"^\s*(?:h\b|hrs?\b|hours?\b|小时)"),
    ("days", r"^\s*(?:d\b|days?\b|天)"),
    ("minutes", r"^\s*(?:min\b|minutes?\b|分钟)"),
    ("ratio", r"^\s*(?:x\b|×|times\b|倍)"),
)
STEPS = ("symptom", "stage", "location", "mechanism")


class ToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="before")
    @classmethod
    def no_explicit_null(cls, value: Any) -> Any:
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
    cleaned = re.sub(r"\b(?:vs\.|e\.g\.|i\.e\.)", lambda m: m[0].replace(".", ""), text, flags=re.I)
    return [
        s.strip() for s in re.split(r"(?<=[.!?])\s+|(?<=[。！？])", cleaned.strip()) if s.strip()
    ]


def citations(text: str) -> set[str]:
    return set(CITATION.findall(text))


def chain_ids(candidate: Mapping[str, Any]) -> list[str]:
    return list(dict.fromkeys(i for step in STEPS for i in candidate["chain"].get(step, [])))


def hedge_levels(text: str, lang: str) -> set[int]:
    patterns = (
        (r"\blikely\b", r"\b(may|might|possibly|could)\b", r"\bearly signs?\b")
        if lang == "en"
        else (r"很可能", r"(?<!很)可能", r"初步迹象")
    )
    return {2 - i for i, pattern in enumerate(patterns) if re.search(pattern, text, re.I)}


def hedge_ok(text: str, level: str, lang: str) -> bool:
    levels = hedge_levels(text, lang)
    target = LEVEL_ORDER[level]
    return target in levels and max(levels) <= target


def numeric_tokens(text: str) -> list[tuple[float, int, str]]:
    matches = list(NUMBER.finditer(text))
    result = []
    for i, match in enumerate(matches):
        category = unit(text[match.end() :])
        if category == "plain" and i + 1 < len(matches):
            following = matches[i + 1]
            if re.fullmatch(
                r"\s*(?:to|and|-|–|→|至|到)\s*", text[match.end() : following.start()], re.I
            ):
                category = unit(text[following.end() :])
        raw = match[0].replace(",", "")
        result.append((float(raw), len(raw.split(".")[1]) if "." in raw else 0, category))
    return result


def unit(suffix: str) -> str:
    return next((name for name, pattern in SUFFIXES if re.match(pattern, suffix, re.I)), "plain")


@dataclass(frozen=True, slots=True)
class AllowedNumber:
    value: float
    category: str
    evidence: str = ""
    field: str = ""


def allowed_numbers(entry: Mapping[str, Any]) -> list[AllowedNumber]:
    result: list[AllowedNumber] = []
    identifier = entry["id"]

    def add(value: Any, category: str, field: str = "", divisor: float = 1) -> None:
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


def check_numbers(
    sentence: str, pack: Mapping[str, Any], evidence: Mapping[str, dict[str, Any]]
) -> list[Violation]:
    cited = citations(sentence) & evidence.keys()
    allowed = [number for i in cited for number in allowed_numbers(evidence[i])]
    if cited:
        days = pack["period"]["days"]
        allowed += [AllowedNumber(days, "plain"), AllowedNumber(days, "days")]
        for hypothesis in pack["hypotheses"]:
            if cited & set(chain_ids(hypothesis)):
                allowed += [AllowedNumber(v, "plain") for v in hypothesis["persistence"].values()]
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


def validate(
    output: dict[str, Any] | None,
    pack: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    audience: str,
    lang: str,
) -> list[Violation]:
    errors: list[Violation] = []

    def fail(code: str, message: str) -> None:
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
    low = (2 if candidates else 1) if audience == "director" else (3 if candidates else 2)
    high = 4 if audience == "director" else 6
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
    if (lang == "zh" and len(CJK.findall(body)) < 10) or (
        lang == "en" and any(CJK.search(t) for t in texts)
    ):
        fail("V3:language", "Text does not match the requested language.")
    for i, sentence in enumerate(body_sentences, 1):
        if not citations(sentence):
            fail("V4:sentence_without_citation", f"Body sentence {i} needs evidence.")
    logins = {r["reviewer"] for r in snapshot["bottleneck_analysis"]["review_load"]["distribution"]}
    logins.update(p["author"] for p in snapshot["at_risk_prs"] if p["author"])
    for text in texts:
        for identifier in sorted(citations(text) - evidence.keys()):
            fail("V4:unknown_citation", f"Unknown citation {identifier}.")
        for sentence in sentences(text):
            errors.extend(check_numbers(sentence, pack, evidence))
        if DEFINITE.search(text):
            fail("V7b:overclaim", "Definite causal language is not supported.")
        if re.search(r"@[A-Za-z0-9]", text) or any(
            re.search(r"(?<![A-Za-z0-9-])" + re.escape(name) + r"(?![A-Za-z0-9-])", text, re.I)
            for name in logins
            if len(name) >= 3
        ):
            fail("V10:personal_name", "Personal login names must not appear.")
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
        allowed = set(chain_ids(candidate)) | set(candidate["counter_evidence"])
        allowed.update(i for a in candidate["ruled_out"] for i in a["evidence_ids"])
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
        if not hedge_ok(statement, final_level, lang):
            fail("V7:hedge_mismatch", f"Statement must use {final_level} language.")
    for identifier, candidate in candidates.items():
        if candidate["level"] in {"high", "medium"} and identifier not in seen:
            fail("V6:missing_required_hypothesis", f"Required hypothesis {identifier} is missing.")
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
            or not hedge_ok(outside["statement"], "low", lang)
        ):
            fail(
                "V9:invalid_llm_hypothesis",
                "Outside hypothesis needs significant evidence from both sides, all statement "
                "citations in evidence_ids, and low wording only: early signs / 初步迹象, "
                "without may, might, possibly, could, likely, 可能 or 很可能. "
                "Omit this optional hypothesis if those requirements cannot be met.",
            )
        levels.append(0)
    for sentence in body_sentences:
        if CAUSAL.search(sentence) and (levels or not ABSTAIN.search(sentence)):
            hedges = hedge_levels(sentence, lang)
            if not levels or not hedges or max(hedges) > max(levels):
                fail("V7b:overclaim", "Body causal language exceeds the supported level.")
    if not candidates:
        required = (
            re.search(r"\binsufficient\b|\bnot enough\b", body, re.I)
            if lang == "en"
            else re.search(r"信号不足|不足以", body)
        )
        if (
            hypotheses
            or outside
            or not required
            or any(CAUSAL.search(s) and not ABSTAIN.search(s) for s in body_sentences)
        ):
            fail("V12:abstain", "Insufficient signals require an explicit abstention.")
    return list(dict.fromkeys(errors))
