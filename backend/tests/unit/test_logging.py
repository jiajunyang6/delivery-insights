"""JSON logging and exception sanitizing for API, worker and CLI."""

import json
import logging
import subprocess
import sys

import pytest
import structlog

from insights.logging import configure_logging


def test_structured_and_standard_logs(capsys):
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    try:
        configure_logging()
        structlog.get_logger("unit").info("structured", field=3)
        logging.getLogger("standard").info("standard")
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert len(rows) == 2
        assert all({"timestamp", "level", "event", "logger"} <= row.keys() for row in rows)
        assert rows[0]["field"] == 3
        assert logging.getLogger("httpx").level == logging.WARNING
    finally:
        root.handlers = old_handlers


def test_cli_bootstrap_and_worker_records_are_single_json_lines(capsys):
    import logging.config

    from insights.logging import LOGGING_CONFIG

    root = logging.getLogger()
    previous = root.handlers[:]
    try:
        logging.config.dictConfig(LOGGING_CONFIG)
        logging.getLogger("arq.worker").info("startup")
        configure_logging()
        logging.getLogger("arq.worker").info("job done")
        logging.getLogger("uvicorn.error").info("ready")
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert [r["event"] for r in rows] == ["startup", "job done", "ready"]
    finally:
        root.handlers = previous


@pytest.mark.parametrize("structured", [False, True])
def test_exception_logs_are_json_without_upstream_messages(capsys, structured):
    root = logging.getLogger()
    previous, hook = root.handlers[:], sys.excepthook
    try:
        configure_logging()
        logger = structlog.get_logger("unit") if structured else logging.getLogger("unit")
        try:
            raise ConnectionError("untrusted upstream message")
        except ConnectionError:
            logger.exception("untrusted upstream message")
        captured = capsys.readouterr()
        row = json.loads(captured.out)
        assert row["event"] == "unhandled_exception"
        assert row["error_type"] == "ConnectionError"
        assert "untrusted" not in captured.out
        assert not captured.err
    finally:
        root.handlers = previous
        sys.excepthook = hook


def test_process_exit_emits_sanitized_json_instead_of_traceback():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from insights.logging import configure_logging; "
            "configure_logging(); raise RuntimeError('untrusted upstream message')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert not result.stderr
    row = json.loads(result.stdout)
    assert row["event"] == "unhandled_exception"
    assert row["error_type"] == "RuntimeError"
    assert "untrusted" not in result.stdout
