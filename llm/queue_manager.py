"""Bounded-concurrency queue manager for LLM requests.

Limits how many LLM calls run at once (QUEUE_MAX_CONCURRENT). If a caller cannot
acquire a slot within QUEUE_TTL_SECS, it fails fast (rather than hanging), which
is the right behaviour for a synchronous web request.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from config import QUEUE_MAX_CONCURRENT, QUEUE_TTL_SECS
from observability.logging_config import get_logger

_logger = get_logger("queue")
_semaphore = threading.BoundedSemaphore(QUEUE_MAX_CONCURRENT)


class QueueFullError(Exception):
    """Raised when no slot becomes free within the TTL."""


@contextmanager
def slot(task: str) -> Iterator[None]:
    """Acquire a concurrency slot for the duration of a block.

    Args:
        task: Name of the task requesting a slot (for logging).

    Yields:
        None once a slot is held.

    Raises:
        QueueFullError: If no slot frees within QUEUE_TTL_SECS.
    """
    acquired = _semaphore.acquire(timeout=QUEUE_TTL_SECS)
    if not acquired:
        _logger.warning("queue.timeout", extra={"task": task, "ttl_secs": QUEUE_TTL_SECS})
        raise QueueFullError(
            f"Server busy: no LLM slot within {QUEUE_TTL_SECS}s (max {QUEUE_MAX_CONCURRENT})"
        )
    _logger.info("queue.acquired", extra={"task": task})
    try:
        yield
    finally:
        _semaphore.release()
        _logger.info("queue.released", extra={"task": task})
