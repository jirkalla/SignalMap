"""Structured stdout logging setup.

Design decision (docs/TASKS_HARDENING.md, decision 1): stdlib `logging` only,
writing JSON lines to stdout. No new dependency (structlog/loguru rejected as
unnecessary weight for this project's size). Log rotation is Docker's job
(docker-compose.yaml `logging.driver: json-file`), not the application's —
the app never writes a log file inside the container.
"""

import json
import logging
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Renders one log record as a single JSON line.

    Includes the standard fields (timestamp, level, logger name, message)
    plus any extra data passed via `logger.info(..., extra={"extra_data": {...}})`,
    and the exception traceback when the record was logged with `exc_info`.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra_data = getattr(record, "extra_data", None)
        if extra_data:
            payload["extra"] = extra_data
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the root logger to emit JSON lines to stdout.

    Must be called once, at import time, before the FastAPI app is created —
    see app/main.py. Replaces any handlers already attached to the root
    logger so repeated calls (e.g. under the reloader) don't duplicate output.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
