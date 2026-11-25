"""Keeps the most recent log lines in memory so the Logs page can show them without reading files."""

import logging
import threading
from collections import deque
from datetime import datetime

LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}


class RingHandler(logging.Handler):
    def __init__(self, capacity: int = 2000) -> None:
        super().__init__(level=logging.DEBUG)
        self._lines: deque[dict] = deque(maxlen=capacity)
        self._lock_lines = threading.Lock()
        self._seq = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + (self.formatter or logging.Formatter()).formatException(record.exc_info)
            with self._lock_lines:
                self._seq += 1
                self._lines.append({
                    "seq": self._seq,
                    "at": datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="seconds"),
                    "level": record.levelname.lower(),
                    "logger": record.name,
                    "message": message[:4000],
                })
        except Exception:  # a log handler must never raise
            self.handleError(record)

    def lines(self, level: str = "info", query: str = "", limit: int = 500) -> list[dict]:
        floor = LEVELS.get(level, logging.INFO)
        q = query.lower()
        with self._lock_lines:
            snapshot = list(self._lines)
        picked = [
            line for line in snapshot
            if LEVELS.get(line["level"], logging.CRITICAL) >= floor
            and (not q or q in line["message"].lower() or q in line["logger"].lower())
        ]
        return picked[-limit:]


_handler: RingHandler | None = None


def install() -> RingHandler:
    """Attach one buffer to the `pal` and `uvicorn.error` loggers (idempotent)."""
    global _handler
    if _handler is None:
        _handler = RingHandler()
        for name in ("pal", "uvicorn.error"):
            logger = logging.getLogger(name)
            logger.addHandler(_handler)
            if logger.level == logging.NOTSET or logger.level > logging.INFO:
                logger.setLevel(logging.INFO)
    return _handler
