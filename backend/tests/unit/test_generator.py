from insights.analytics.timeline import build_timeline, check_invariants, pr_input
from insights_eval.generator import AS_OF, ScenarioSpec, generate
from insights_eval.scenarios import SCENARIOS


def test_generator_deterministic_bounded_and_legal():
    syn = generate(ScenarioSpec.baseline(), 42)
    assert syn == generate(ScenarioSpec.baseline(), 42)
    assert len({p.number for p in syn.records}) == len(syn.records)
    for record in syn.records:
        assert all(e.occurred_at < AS_OF for e in record.events)
        result = build_timeline(pr_input(record), record.events, AS_OF)
        assert not check_invariants(result, pr_input(record)), record.number
    assert set(SCENARIOS) == {
        "review_capacity",
        "pr_size_growth",
        "no_signal",
    }


def test_generator_uses_only_known_reviewers():
    syn = generate(ScenarioSpec.baseline(), 42)
    for p in syn.records:
        reviews = [e for e in p.events if e.kind == "review"]
        assert all(e.actor.login != "rev-x5" for e in reviews)
