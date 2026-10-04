import logging
import sys
from importlib.resources import files
from logging.config import dictConfig
from types import TracebackType
from typing import Any

import orjson
import structlog
from structlog.typing import EventDict

SHARED: list[Any] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]
LOGGING_CONFIG: dict[str, Any] = orjson.loads(
    files("insights").joinpath("logging.json").read_bytes()
)


def json_dumps(value: Any, **kwargs: Any) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()


def sanitize_exception(logger: Any, method: str, event: EventDict) -> EventDict:
    info = event.pop("exc_info", None)
    event.pop("stack_info", None)
    if info:
        if info is True:
            info = sys.exc_info()
        kind = info[0] if isinstance(info, tuple) else type(info)
        event["error_type"] = getattr(kind, "__name__", "Exception")
        event["event"] = "unhandled_exception"
    return event


def log_uncaught(
    kind: type[BaseException], error: BaseException, traceback: TracebackType | None
) -> None:
    logging.getLogger("insights.process").error(
        "unhandled_exception", exc_info=(kind, error, traceback)
    )


def json_formatter() -> structlog.stdlib.ProcessorFormatter:
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=SHARED,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            sanitize_exception,
            structlog.processors.JSONRenderer(serializer=json_dumps),
        ],
    )


def configure_logging(level: str = "INFO") -> None:
    sys.excepthook = log_uncaught
    structlog.configure(
        processors=[*SHARED, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    dictConfig({**LOGGING_CONFIG, "root": {**LOGGING_CONFIG["root"], "level": level}})
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("httpx", "httpcore", "botocore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
