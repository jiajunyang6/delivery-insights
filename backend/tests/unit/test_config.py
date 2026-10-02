import pytest
from pydantic import SecretStr, ValidationError

from insights.config import Settings


def test_lists_and_defaults(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "")
    settings = Settings(tracked_repos=" A/B, ,a/b, C/D ", backfill_days=30)
    assert settings.tracked_repo_list == ["A/B", "C/D"]
    assert settings.backfill_phases == [7, 30]
    assert settings.github_token is None
    assert not settings.llm_enabled
    assert settings.location_label_prefix == "area-"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tracked_repos": "../bad"},
        {"tracked_repos": "a/.."},
        {"sync_interval_minutes": 7},
        {"open_sweep_minutes": 17},
        {"backfill_days": 29},
        {"backfill_days": 366},
        {"directory_depth": 4},
        {"location_dimension": "label:"},
        {"precompute_days": "a,10"},
    ],
)
def test_invalid_config(kwargs):
    with pytest.raises(ValidationError):
        Settings(**kwargs)


def test_secrets_are_masked():
    token = "ghp_" + "x" * 36
    settings = Settings(github_token=SecretStr(token), aws_bearer_token_bedrock=SecretStr(token))
    assert token not in repr(settings)
    assert settings.llm_enabled
