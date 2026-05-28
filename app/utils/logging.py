"""NDJSON structured logging for the FHIR REST layer.

Set LOG_FILE to a file path to append logs there; leave unset to write to stdout.
Each log record is emitted as a single JSON object followed by a newline.
"""

from __future__ import annotations

import atexit
import json
import logging
import sys
from datetime import UTC, datetime
from typing import TextIO

from app.config import LOG_FILE as _LOG_FILE

# Fields that belong to LogRecord internals — not forwarded as extra payload keys.
_LOGRECORD_BUILTINS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


class NdjsonHandler(logging.StreamHandler):
    """Emits one JSON object per log record to the configured stream."""

    def emit(self, record: logging.LogRecord) -> None:
        payload: dict = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = logging.Formatter().formatException(record.exc_info)
        for key, log_value in record.__dict__.items():
            if key not in _LOGRECORD_BUILTINS and not key.startswith("_"):
                payload[key] = log_value
        try:
            self.stream.write(json.dumps(payload) + "\n")
            self.flush()
        except Exception:
            self.handleError(record)


def configure_logging() -> logging.Logger:
    """Configure root logging with NdjsonHandler and return the 'fhir' logger."""
    # ``open(..., "a", encoding=...)`` returns ``TextIOWrapper``; ``sys.stdout``
    # is the abstract ``TextIO``. Annotate the wider type up front so both
    # branches assign compatibly.
    stream: TextIO
    if _LOG_FILE:
        stream = open(_LOG_FILE, "a", encoding="utf-8")
        atexit.register(stream.close)
    else:
        stream = sys.stdout
    handler = NdjsonHandler(stream)
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    return logging.getLogger("fhir")
