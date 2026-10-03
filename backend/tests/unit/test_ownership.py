from unittest.mock import AsyncMock

from insights.analytics.classify import ownership_counts
from insights.config import Settings
from insights.domain import RepoRef
from insights.sources.github.adapter import GitHubAdapter
from insights.sources.github.client import GitHubClient
from insights.sources.github.ownership import parse_area_owners, parse_codeowners


def test_codeowners_comments_escaped_hash_and_last_pattern_counts():
    rules = parse_codeowners(
        "# Heading\n/src/ @one @one @two # inline\n/src/ @last\n"
        r"/hash\#name @hash" + "\n/no-owner"
    )
    assert [r.pattern for r in rules] == ["/src/", "/src/", r"/hash\#name", "/no-owner"]
    assert rules[0].owners == ("@one", "@two") and rules[0].line_no == 2
    assert dict(ownership_counts(rules))["codeowners:/src/"] == 1


def test_area_table_reads_only_owner_columns_and_unions_duplicates():
    rules = parse_area_owners(
        "| Area | Lead | Owners | Other |\n| --- | --- | --- | --- |\n"
        "| area-A | @team/one | @one, @one | @ignored |\n"
        "| area-A | @two | @one |\n| invalid | @wrong | @no |\n"
        "| area-B | [@b](link) | |"
    )
    counts = dict(ownership_counts(rules))
    assert counts == {"area-A": 3, "area-B": 1}
    assert "@ignored" not in rules[0].owners


def test_ownership_strings_are_cleaned():
    assert parse_codeowners("/s\x00rc/ @te\x00am/one") == parse_codeowners("/src/ @team/one")
    assert parse_area_owners("| area-F\x00oo | @te\x00am/one |") == parse_area_owners(
        "| area-Foo | @team/one |"
    )


async def test_codeowners_priority_raw_accept_and_missing_area_file(respx_mock):
    first = respx_mock.get("https://api.github.com/repos/a/b/contents/.github/CODEOWNERS")
    first.respond(404)
    root = respx_mock.get("https://api.github.com/repos/a/b/contents/CODEOWNERS")
    root.respond(200, text="/src/ @one")
    area = respx_mock.get("https://api.github.com/repos/a/b/contents/docs/area-owners.md")
    area.respond(404)
    client = GitHubClient(Settings(), sleep=AsyncMock())
    try:
        rules = await GitHubAdapter(client).ownership_rules(RepoRef("a", "b"))
    finally:
        await client.aclose()
    assert len(rules) == 1 and rules[0].owners == ("@one",)
    assert root.calls[0].request.headers["Accept"] == "application/vnd.github.raw+json"
    assert len(respx_mock.calls) == 3
