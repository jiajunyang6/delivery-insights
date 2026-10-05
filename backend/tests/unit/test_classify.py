from dataclasses import replace

from tests.factories import at, record

from insights.analytics.facts import PrFacts, is_flow, locations_for


def test_location_labels_then_directories_then_unclassified():
    pr = record(labels=("Area-Z", "area-A", "area-A"))
    assert locations_for(pr, "label:area-", 2) == ("Area-Z", "area-A")
    paths = ("a/x/1", "a/x/2", "b/y/1", "c/z/1", "README", "README2")
    # The three most-touched directories at depth 2; root files count as "/".
    assert locations_for(replace(pr, files=paths), "directory", 2) == (
        "dir:/",
        "dir:a/x",
        "dir:b/y",
    )
    assert locations_for(replace(pr, labels=(), files=paths), "label:area-", 2) == (
        "dir:/",
        "dir:a/x",
        "dir:b/y",
    )
    assert locations_for(replace(pr, labels=(), files=()), "label:area-", 2) == ("unclassified",)


def test_flow_scope():
    assert is_flow(PrFacts(ready_at=at(0)))
    assert not is_flow(PrFacts())
    assert not is_flow(PrFacts(ready_at=at(0), is_bot_author=True))
    assert not is_flow(PrFacts(ready_at=at(0), is_backport=True))
