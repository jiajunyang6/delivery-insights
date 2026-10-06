"""Shared fixtures for unit and integration tests."""

import json
from copy import deepcopy
from pathlib import Path

import pytest


@pytest.fixture
def github_page():
    """A fresh copy of the recorded GraphQL PR page fixture."""
    return deepcopy(
        json.loads(
            (Path(__file__).parent / "fixtures/github/page.json").read_text(encoding="utf-8")
        )
    )
