import subprocess
import sys
from pathlib import Path


def test_api_import_does_not_load_source_adapters():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import insights.main,sys; "
            "assert not any(m.startswith('insights.sources') for m in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_environment_and_local_artifacts_are_ignored():
    root = Path(__file__).parents[3]
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignored and ".venv" in ignored
