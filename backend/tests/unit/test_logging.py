import json
import logging

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
