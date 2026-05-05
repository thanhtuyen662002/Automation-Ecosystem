from __future__ import annotations

import logging
import sys

from pythonjsonlogger import jsonlogger


LOG_FIELDS = (
    "%(asctime)s %(levelname)s %(name)s %(message)s %(event)s %(job_id)s "
    "%(execution_id)s %(task_name)s %(worker_id)s %(attempt)s %(status)s "
    "%(error_type)s %(duration_ms)s"
)


def configure_json_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(jsonlogger.JsonFormatter(LOG_FIELDS))
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

