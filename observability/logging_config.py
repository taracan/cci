"""Structured JSON logging with rotating 10MB files + console.

Every log record is emitted as one JSON line and automatically enriched with the
current trace_id and span_id, so the whole request lifecycle is correlatable.
A CloudWatch handler is stubbed and only attached when LOG_TO_CLOUDWATCH is on.
"""
from __future__ import annotations

import json
import logging
import re
from logging.handlers import RotatingFileHandler
from datetime import datetime

from config import (
    LOG_DIR, LOG_MAX_BYTES, LOG_BACKUP_COUNT, LOG_LEVEL, LOG_TO_CLOUDWATCH,
    CLOUDWATCH_REDACT,
)

_CONFIGURED = False

# Third-party libraries that spam INFO/DEBUG logs — raise them to WARNING so only
# real problems from them appear, keeping app.jsonl focused on our own events.
_NOISY_LOGGERS = [
    "httpx", "httpcore", "urllib3", "requests",
    "sentence_transformers", "SentenceTransformer",
    "transformers", "huggingface_hub", "filelock",
    "chromadb", "chromadb.telemetry", "chromadb.segment",
    "PIL", "matplotlib", "werkzeug", "asyncio", "sqlalchemy.engine",
    "datasets", "fsspec", "numexpr", "torch",
]


class _JsonFormatter(logging.Formatter):
    """Format each log record as a single JSON line with trace/span context."""

    # Standard LogRecord attributes we don't want duplicated in "extra".
    _RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {
        "message", "asctime", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a record to JSON, injecting trace_id/span_id."""
        from observability.tracing import get_trace_id, get_span_id

        payload = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "trace_id": get_trace_id(),
            "span_id": getattr(record, "span", None) or get_span_id(),
        }
        # Merge any custom fields passed via extra=...
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and key not in payload and key != "span":
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class _RedactingJsonFormatter(_JsonFormatter):
    """_JsonFormatter with PII masked — used for logs leaving the machine.

    Local files keep the full values; only the CloudWatch copy is redacted, so
    debugging stays easy here while AWS holds no candidate identities.
    """

    # Field names whose values are replaced wholesale, wherever they are nested.
    _PII_KEYS = {"email", "name", "full_name", "phone", "address", "raw_text", "summary"}
    _EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

    @classmethod
    def _scrub(cls, value):
        """Recursively mask PII keys and any stray email addresses."""
        if isinstance(value, dict):
            return {
                k: ("***" if k.lower() in cls._PII_KEYS else cls._scrub(v))
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [cls._scrub(v) for v in value]
        if isinstance(value, str):
            return cls._EMAIL_RE.sub("***", value)
        return value

    def format(self, record: logging.LogRecord) -> str:
        """Serialize as JSON, then mask PII before the line leaves the host."""
        payload = json.loads(super().format(record))
        return json.dumps(self._scrub(payload), default=str)


def setup_logging() -> None:
    """Configure root logging once: JSON to rotating file + console."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger()
    root.setLevel(LOG_LEVEL)

    formatter = _JsonFormatter()

    file_handler = RotatingFileHandler(
        filename=str(LOG_DIR / "app.jsonl"),
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    # Quiet noisy third-party libraries so app.jsonl only carries our events.
    for noisy in _NOISY_LOGGERS:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if LOG_TO_CLOUDWATCH:
        try:
            from observability.cloudwatch import build_cloudwatch_handler
            cw = build_cloudwatch_handler()
            if cw is not None:
                # Same events as the local file, with PII masked (unless opted out).
                cw.setFormatter(
                    _RedactingJsonFormatter() if CLOUDWATCH_REDACT else formatter
                )
                root.addHandler(cw)
        except Exception as exc:  # never let logging setup break the app
            logging.getLogger("logging_config").warning(
                "cloudwatch.setup_failed", extra={"error": str(exc)}
            )

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger; ensures setup_logging() has run."""
    if not _CONFIGURED:
        setup_logging()
    return logging.getLogger(name)
