import logging
import sys
from importlib.resources import files
from typing import Any

import orjson
import structlog

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


def json_formatter() -> structlog.stdlib.ProcessorFormatter:
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=SHARED,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(serializer=json_dumps),
        ],
    )


def configure_logging(level: str = "INFO") -> None:
    structlog.configure(
        processors=[*SHARED, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(json_formatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
    for name in ("arq", "uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("httpx", "httpcore", "botocore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
