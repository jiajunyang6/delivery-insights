import json
from copy import deepcopy
from pathlib import Path

import pytest


@pytest.fixture
def github_page():
    return deepcopy(
        json.loads(
            (Path(__file__).parent / "fixtures/github/page.json").read_text(encoding="utf-8")
        )
    )
