"""Unit tests for llm.queue_manager — bounded concurrency + fail-fast TTL.

The queue guards how many LLM calls run at once. When all slots are taken and a
new caller can't get one within the TTL, it must raise QueueFullError rather
than hang the web request.
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from llm import queue_manager
from llm.queue_manager import slot, QueueFullError


class TestSlot:
    def test_acquire_and_release(self):
        with slot("unit-test"):
            pass  # a single acquire/release cycle must not raise

    def test_reentrant_sequential_acquire(self):
        # Two sequential acquisitions reuse the same freed slot.
        with slot("first"):
            pass
        with slot("second"):
            pass

    def test_queue_full_raises_when_no_slot(self, monkeypatch):
        """Exhaust the semaphore, force a 0s TTL, expect a fast failure."""
        # Shrink the pool to a single slot and make timeout instant.
        small_sem = threading.BoundedSemaphore(1)
        monkeypatch.setattr(queue_manager, "_semaphore", small_sem)
        monkeypatch.setattr(queue_manager, "QUEUE_TTL_SECS", 0)

        # Hold the only slot in a background thread.
        holding = threading.Event()
        release = threading.Event()

        def _hold():
            small_sem.acquire()
            holding.set()
            release.wait(timeout=5)
            small_sem.release()

        t = threading.Thread(target=_hold)
        t.start()
        try:
            assert holding.wait(timeout=2)
            with pytest.raises(QueueFullError):
                with slot("blocked"):
                    pass
        finally:
            release.set()
            t.join(timeout=5)

    def test_slot_released_on_exception(self, monkeypatch):
        """An exception inside the block must still release the slot."""
        sem = threading.BoundedSemaphore(1)
        monkeypatch.setattr(queue_manager, "_semaphore", sem)

        with pytest.raises(ValueError):
            with slot("boom"):
                raise ValueError("inner")

        # If the slot leaked, this second acquire would block; it should not.
        acquired = sem.acquire(timeout=1)
        assert acquired
        sem.release()
