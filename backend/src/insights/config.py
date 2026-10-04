import re
from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MAX_PERIOD_DAYS = 366

REPO_RE = re.compile(
    r"^(?P<owner>[A-Za-z0-9][A-Za-z0-9-]{0,38})/(?P<name>(?!\.{1,2}$)[A-Za-z0-9._-]{1,100})$"
)
OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
NAME_RE = re.compile(r"^(?!\.{1,2}$)[A-Za-z0-9._-]{1,100}$")


def split_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, env_ignore_empty=True)
    github_token: SecretStr | None = None
    github_api_url: str = "https://api.github.com"
    github_graphql_url: str = "https://api.github.com/graphql"
    tracked_repos: str = "dotnet/runtime"
    location_dimension: str = "label:area-"
    directory_depth: int = Field(default=2, ge=1, le=3)
    backfill_days: int = Field(default=120, ge=30, le=365)
    sync_interval_minutes: int = Field(default=15, ge=1, le=60)
    open_sweep_minutes: int = Field(default=60, ge=1)
    graphql_page_size: int = Field(default=25, ge=5, le=50)
    extra_bot_logins: str = ""
    database_url: str = "postgresql+asyncpg://insights:insights@postgres:5432/insights"
    redis_url: str = "redis://redis:6379/0"
    aws_bearer_token_bedrock: SecretStr | None = None
    aws_region: str = "us-west-2"
    bedrock_model_id: str = "us.anthropic.claude-sonnet-4-6"
    llm_timeout_seconds: int = Field(default=60, ge=1)
    cors_origins: str = "http://localhost:5173"
    rate_limit_per_minute: int = Field(default=120, ge=1)
    manual_sync_cooldown_seconds: int = Field(default=300, ge=1)
    max_repos_per_request: int = Field(default=20, ge=1)
    precompute_days: str = "7,30,60"
    ci_source: Literal["actions", "none"] = "actions"
    ci_complete: bool = False
    area_owners_path: str = "docs/area-owners.md"
    log_level: str = "INFO"

    @model_validator(mode="after")
    def validate_settings(self) -> Self:
        if not self.tracked_repo_list or any(
            REPO_RE.fullmatch(repo) is None for repo in self.tracked_repo_list
        ):
            raise ValueError("TRACKED_REPOS must contain valid owner/name entries")
        if 60 % self.sync_interval_minutes:
            raise ValueError("SYNC_INTERVAL_MINUTES must divide 60")
        if self.open_sweep_minutes % self.sync_interval_minutes:
            raise ValueError("OPEN_SWEEP_MINUTES must be a multiple of SYNC_INTERVAL_MINUTES")
        if self.location_dimension not in {"directory", "codeowners"} and not (
            self.location_dimension.startswith("label:") and self.location_label_prefix
        ):
            raise ValueError("LOCATION_DIMENSION must be label:<prefix>, codeowners or directory")
        if any(
            not value.isdigit() or not 1 <= int(value) <= MAX_PERIOD_DAYS
            for value in split_list(self.precompute_days)
        ):
            raise ValueError("PRECOMPUTE_DAYS must contain day counts between 1 and 366")
        return self

    @property
    def tracked_repo_list(self) -> list[str]:
        seen: dict[str, str] = {}
        for repo in split_list(self.tracked_repos):
            seen.setdefault(repo.lower(), repo)
        return list(seen.values())

    @property
    def llm_enabled(self) -> bool:
        return bool(
            self.aws_bearer_token_bedrock and self.aws_bearer_token_bedrock.get_secret_value()
        )

    @property
    def backfill_phases(self) -> list[int]:
        return sorted({7, 30, self.backfill_days})

    @property
    def location_label_prefix(self) -> str | None:
        return self.location_dimension[6:] if self.location_dimension.startswith("label:") else None


@lru_cache
def get_settings() -> Settings:
    return Settings()
