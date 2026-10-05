from copy import deepcopy

import pytest
from tests.narrative_factory import entry, validation_fixture

from insights.narrative.validator import check_numbers, sentences, validate


def codes(output, pack):
    return {v.code for v in validate(output, pack)}


def test_valid_and_abbreviation_decimal_splitting():
    pack, output = validation_fixture()
    assert not codes(output, pack)
    assert len(sentences("It was 41.3 h vs. 35.1 h, e.g. early work [E1]. Next [E1].")) == 2


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("It was 41.3 h [E1].", None),
        ("It rose 18% from 35.1 to 41.3 h [E1].", None),
        ("It was 1.7 days [E1].", None),
        ("It was 9 different weeks [E22].", None),
        ("It was 63% [E53].", None),
        ("There were 10 of 13 weeks [E15].", None),
        ("It was 512 h [E1].", "V5:unit_mismatch"),
        ("It was 41.3% [E1].", "V5:unit_mismatch"),
        ("It was 29 h [E1].", "V5:number_not_in_evidence"),
        ("It was 41.3 h [E15].", "V5:number_not_in_evidence"),
        ("It was 999 h [E1].", "V5:number_not_in_evidence"),
        ("It was 29 h.", "V5:number_not_in_evidence"),
        ("It fell to 41.3 h [E1].", "V5:direction_mismatch"),
        ("It fell from 35.1 to 41.3 h [E1].", "V5:direction_mismatch"),
        ("It rose 18%, while another fell [E1].", None),
        ("There were 9 days [E22].", "V5:unit_mismatch"),
        ("It was 63 hours [E53].", "V5:unit_mismatch"),
        ("There were 30 days [E1].", None),
        ("example/repo and area-Foo had 41.3 h [E1].", None),
    ],
)
def test_sentence_local_numbers_units_rounding_and_direction(text, expected):
    pack, _ = validation_fixture()
    evidence = {e["id"]: e for e in pack["evidence"]}
    actual = {v.code for v in check_numbers(text, pack, evidence)}
    assert actual == ({expected} if expected else set())


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("unknown", "V4:unknown_citation"),
        ("missing_cite", "V4:sentence_without_citation"),
        ("schema", "V1:schema"),
        ("null", "V1:schema"),
        ("length", "V2:length"),
        ("sentences", "V2:sentence_count"),
        ("non_english", "V3:language"),
        ("personal", "V10:personal_name"),
        ("unknown_h", "V6:unknown_hypothesis"),
        ("duplicate", "V6:duplicate_hypothesis"),
        ("missing_h", "V6:missing_required_hypothesis"),
        ("outside_chain", "V6:citation_outside_chain"),
        ("definite", "V7b:overclaim"),
        ("no_hedge", "V7:hedge_mismatch"),
        ("unlikely", "V7:hedge_mismatch"),
        ("bad_downgrade", "V8:invalid_downgrade"),
    ],
)
def test_validation_rules(mutation, expected):
    pack, output = validation_fixture()
    if mutation == "unknown":
        output["narrative"] += " Unknown [E999]."
    elif mutation == "missing_cite":
        output["narrative"] += " No evidence."
    elif mutation == "schema":
        output["extra"] = 1
    elif mutation == "null":
        output["llm_hypothesis"] = None
    elif mutation == "length":
        output["narrative"] += "a" * 1201
    elif mutation == "sentences":
        output["narrative"] = "One [E1]."
    elif mutation == "non_english":
        output["hypotheses"][0]["statement"] += chr(0x4E00)
    elif mutation == "personal":
        output["narrative"] += " @someone [E1]."
    elif mutation == "unknown_h":
        output["hypotheses"][0]["id"] = "H_fake"
    elif mutation == "duplicate":
        output["hypotheses"] *= 2
    elif mutation == "missing_h":
        output["hypotheses"] = []
    elif mutation == "outside_chain":
        pack["evidence"].append(entry("E99", 10))
        output["hypotheses"][0]["statement"] += " Also [E99]."
    elif mutation == "definite":
        output["narrative"] = output["narrative"].replace("likely", "definitely")
    elif mutation in {"no_hedge", "unlikely"}:
        output["hypotheses"][0]["statement"] = output["hypotheses"][0]["statement"].replace(
            "likely", "" if mutation == "no_hedge" else "unlikely"
        )
    elif mutation == "bad_downgrade":
        output["hypotheses"][0]["downgrade"] = {"level": "medium", "reason": "No citation."}
    assert expected in codes(output, pack)


def test_downgrade_counterevidence_body_causal_ceiling_and_abstain():
    pack, output = validation_fixture()
    h = output["hypotheses"][0]
    h["downgrade"] = {"level": "low", "reason": "The first-review evidence is limited [E15]."}
    h["statement"] = "There are early signs that review capacity is the main cause [E1][E15]."
    assert "V7b:overclaim" in codes(output, pack)
    output["narrative"] = (
        "Median cycle time rose 18% [E1]. There are early signs of a review capacity cause [E15]."
    )
    assert not codes(output, pack)
    pack["hypotheses"][0]["counter_evidence"] = ["E53"]
    assert "V6:counter_evidence_not_cited" in codes(output, pack)
    h["statement"] += " There is counter-evidence [E53]."
    assert not codes(output, pack)
    pack["hypotheses"] = []
    output = {
        "narrative": "There are insufficient signals to establish a root cause [E1].",
        "hypotheses": [],
    }
    assert not codes(output, pack)
    output["narrative"] += " Review capacity may be the cause [E15]."
    assert "V12:abstain" in codes(output, pack)


def test_outside_library_requires_significant_evidence_on_both_sides():
    pack, output = validation_fixture()
    output["llm_hypothesis"] = {
        "statement": "There are early signs that scheduling may explain the delay [E1][E15].",
        "evidence_ids": ["E1", "E15"],
    }
    assert "V9:invalid_llm_hypothesis" in codes(output, pack)  # medium hedge is too strong
    output["llm_hypothesis"]["statement"] = (
        "There are early signs of a scheduling constraint [E1][E15]."
    )
    assert not codes(output, pack)
    pack["evidence"][1]["significant"] = False
    pack["observations"] = []
    assert "V9:invalid_llm_hypothesis" in codes(output, pack)


def test_extra_number_units_labels_and_multiple_direction_entries():
    pack, _ = validation_fixture()
    evidence = {e["id"]: e for e in pack["evidence"]}
    evidence["E90"] = entry(
        "E90", 90, 120, unit="minutes", extra={"n_days": 3}, label="500 lines within 10 minutes"
    )
    for sentence in (
        "It was 1.5 h [E90].",
        "Within 3 days and 500 lines [E90].",
        "Within 10 minutes [E90].",
    ):
        assert not check_numbers(sentence, pack, evidence)
    assert (
        any(
            v.code == "V5:direction_mismatch"
            for v in check_numbers("It rose 18% with 90 minutes [E1][E90].", pack, evidence)
        )
        is False
    )
    mixed = deepcopy(evidence)
    mixed["E90"]["previous"] = 100
    mixed["E90"]["change_abs"] = -10
    assert any(
        v.code == "V5:direction_mismatch"
        for v in check_numbers("It rose 18% and 10 minutes [E1][E90].", pack, mixed)
    )


@pytest.mark.parametrize(
    "text",
    [
        "Median was 41 h [E1].",
        "Median rose 17.5% [E1].",
        "The p50, E12 and v1 refer to 41.3 hours [E1].",
        "There were 1,234 reviews [E80].",
        "There were 3 different areas [E81].",
        "There were 500 or more lines [E81].",
        "Share was 42% [E82].",
        "It was 2.4x vs. the rest [E83].",
        "Merged within 3 days [E5].",
    ],
)
def test_explicit_plan_numeric_examples(text):
    pack, _ = validation_fixture()
    evidence = {e["id"]: e for e in pack["evidence"]}
    evidence.update(
        {
            e["id"]: e
            for e in [
                entry("E80", 1234, unit="count"),
                entry("E81", 3, unit="count", label="500 or more lines"),
                entry("E82", 0.4213, unit="share"),
                entry("E83", 2.4, unit="ratio"),
                entry("E5", 0.8, unit="share", extra={"n_days": 3}),
            ]
        }
    )
    assert not check_numbers(text, pack, evidence)


def test_direction_only_checks_reported_changes_and_statement_numbers():
    pack, output = validation_fixture()
    evidence = {e["id"]: e for e in pack["evidence"]}
    evidence["E19"] = entry("E19", 0.21, 0.25, unit="share")
    assert not check_numbers(
        "Cycle was 41.3 h while author waiting decreased 4 pp [E1][E19].", pack, evidence
    )
    output["hypotheses"][0]["statement"] += " It took 999 hours [E1]."
    assert "V5:number_not_in_evidence" in codes(output, pack)
    assert "V5:number_not_in_evidence" in {
        v.code for v in check_numbers("It was 42 h [E1].", pack, evidence)
    }
    assert "V5:number_not_in_evidence" in {
        v.code for v in check_numbers("It lasted 10 weeks [E19].", pack, evidence)
    }


def test_all_low_omitted_has_no_causal_license():
    pack, output = validation_fixture()
    pack["hypotheses"][0]["level"] = "low"
    output["hypotheses"] = []
    assert "V7b:overclaim" in codes(output, pack)
    output["narrative"] = (
        "Median cycle time was 41.3 h [E1]. "
        "The signals are not strong enough to support a root cause [E1]."
    )
    assert not codes(output, pack)


@pytest.mark.parametrize(
    "sentence",
    [
        "Insufficient review capacity in area-Foo causes the slower cycle time [E15][E22].",
        "Not enough review capacity causes the slower cycle time [E15][E22].",
        "Review capacity is not strong enough and causes the slower cycle time [E15][E22].",
    ],
)
def test_abstention_words_do_not_exempt_claims_with_a_low_hypothesis(sentence):
    pack, output = validation_fixture()
    pack["hypotheses"][0]["level"] = "low"
    output["hypotheses"][0]["statement"] = (
        "There are early signs that review capacity is the main cause [E1][E15]."
    )
    output["narrative"] = "Median cycle time was 41.3 h [E1]. " + sentence
    assert codes(output, pack) == {"V7b:overclaim"}


def test_schema_repair_feedback_identifies_field_and_limit_without_echoing_input():
    pack, output = validation_fixture()
    output["hypotheses"][0]["statement"] = "untrusted-payload" * 30
    errors = validate(output, pack)
    schema = [v.message for v in errors if v.code == "V1:schema"]
    assert any("hypotheses.0.statement" in message and "400" in message for message in schema)
    assert all("untrusted-payload" not in message for message in schema)


def test_medium_wording_and_same_level_downgrade():
    pack, output = validation_fixture()
    pack["hypotheses"][0]["level"] = "medium"
    output["narrative"] = "Cycle time was 41.3 h [E1]. Review capacity may be the cause [E15]."
    assert "V7:hedge_mismatch" in codes(output, pack)
    output["hypotheses"][0]["statement"] = "Review capacity may be the cause [E15]."
    assert not codes(output, pack)
    output["hypotheses"][0]["downgrade"] = {"level": "medium", "reason": "Limited signals [E15]."}
    assert "V8:invalid_downgrade" in codes(output, pack)


@pytest.mark.parametrize("stronger", ["likely", "may", "might", "possibly", "could"])
def test_low_statement_rejects_stronger_words_even_with_a_high_primary(stronger):
    pack, output = validation_fixture()
    secondary = deepcopy(pack["hypotheses"][0])
    secondary.update(id="H_pr_size_growth", level="low")
    pack["hypotheses"].append(secondary)
    statement = (
        "There are early signs that larger PRs are the main cause of slower cycle time [E1]."
    )
    output["hypotheses"].append({"id": secondary["id"], "statement": statement})
    assert not codes(output, pack)
    output["hypotheses"][1]["statement"] = statement.replace(
        "larger PRs are", f"larger PRs {stronger} drive"
    )
    assert "V7:hedge_mismatch" in codes(output, pack)


@pytest.mark.parametrize("field", ["narrative", "statement", "downgrade", "outside"])
def test_non_english_text_is_rejected_in_every_generated_field(field):
    pack, output = validation_fixture()
    unsupported_character = chr(0x4E00)
    if field == "narrative":
        output["narrative"] += unsupported_character
    elif field == "statement":
        output["hypotheses"][0]["statement"] += unsupported_character
    elif field == "downgrade":
        output["hypotheses"][0]["downgrade"] = {
            "level": "medium",
            "reason": unsupported_character + " [E15].",
        }
    else:
        output["llm_hypothesis"] = {
            "statement": "Early signs of " + unsupported_character + " [E1][E15].",
            "evidence_ids": ["E1", "E15"],
        }
    assert "V3:language" in codes(output, pack)
