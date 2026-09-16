"""Trace/span context management for request lifecycle tracing.

A trace_id is created per HTTP request; span_ids are created per stage
(embedding, llm call, etc.). Both are stored in context variables so any
function deep in the call stack can log with the correct IDs without threading
them through every signature.
"""
from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

_trace_id: ContextVar[Optional[str]] = ContextVar("trace_id", default=None)
_span_id: ContextVar[Optional[str]] = ContextVar("span_id", default=None)


def new_trace_id() -> str:
    """Generate and set a new trace_id for the current context."""
    tid = uuid.uuid4().hex
    _trace_id.set(tid)
    return tid


def get_trace_id() -> str:
    """Return the current trace_id, creating one if none is set."""
    tid = _trace_id.get()
    if tid is None:
        tid = new_trace_id()
    return tid


def get_span_id() -> str:
    """Return the current span_id, or '-' if not inside a span."""
    return _span_id.get() or "-"


@contextmanager
def span(name: str) -> Iterator[str]:
    """Open a timed span; logs start/end with latency via the logger.

    Args:
        name: Human-readable stage name (e.g. 'match.embed').

    Yields:
        The generated span_id string.
    """
    from observability.logging_config import get_logger
    logger = get_logger("span")

    sid = uuid.uuid4().hex
    token = _span_id.set(sid)
    start = time.perf_counter()
    logger.info(f"{name}.start", extra={"span": sid, "stage": name})
    try:
        yield sid
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000
        logger.error(
            f"{name}.error",
            extra={"span": sid, "stage": name, "latency_ms": round(elapsed, 2), "error": str(exc)},
        )
        raise
    else:
        elapsed = (time.perf_counter() - start) * 1000
        logger.info(
            f"{name}.end",
            extra={"span": sid, "stage": name, "latency_ms": round(elapsed, 2)},
        )
    finally:
        _span_id.reset(token)
