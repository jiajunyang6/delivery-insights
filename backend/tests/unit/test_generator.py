from insights.analytics.timeline import build_timeline, check_invariants, pr_input
from insights_eval.generator import AS_OF, ScenarioSpec, generate
from insights_eval.scenarios import SCENARIOS


def test_generator_deterministic_bounded_and_legal():
    syn = generate(ScenarioSpec.baseline(), 42)
    assert syn == generate(ScenarioSpec.baseline(), 42)
    assert len({p.number for p in syn.records}) == len(syn.records)
    for record in syn.records:
        assert all(e.occurred_at < AS_OF for e in record.events)
        result = build_timeline(pr_input(record), record.events, (), AS_OF)
        assert not check_invariants(result, pr_input(record)), record.number
    assert set(SCENARIOS) == {
        "review_capacity",
        "ci_slowdown",
        "pr_size_growth",
        "no_signal",
    }


def test_generator_reviewers_reverts_and_normal_relands_match_source_contract():
    syn = generate(ScenarioSpec.baseline(), 42)
    by_number = {p.number: p for p in syn.records}
    relands = []
    for p in syn.records:
        reviews = [e for e in p.events if e.kind == "review"]
        assert all(e.actor.login != "rev-x5" for e in reviews)
        if p.body_excerpt.startswith("Reverts synthetic/repo#"):
            original = by_number[int(p.body_excerpt.split("#")[1])]
            assert p.author.login != original.author.login and not p.author.is_bot
            if p.merged_at:
                assert (p.merged_at - p.created_at).total_seconds() <= 3 * 3600
        if p.title.startswith("Reland "):
            relands.append(p)
    assert relands and any(
        p.merged_at and (p.merged_at - p.created_at).total_seconds() > 3 * 3600 for p in relands
    )
