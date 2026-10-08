"""Unit tests for observability.tracing — trace/span id semantics."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from observability.tracing import new_trace_id, get_trace_id, get_span_id, span


class TestTraceId:
    def test_new_trace_id_is_hex_and_set(self):
        tid = new_trace_id()
        assert len(tid) == 32
        assert get_trace_id() == tid

    def test_get_trace_id_autocreates(self):
        assert get_trace_id() is not None


class TestSpan:
    def test_span_id_absent_outside_span(self):
        new_trace_id()
        assert get_span_id() == "-"

    def test_span_sets_and_restores(self):
        new_trace_id()
        with span("stage.a") as sid:
            assert get_span_id() == sid
        assert get_span_id() == "-"   # restored after block

    def test_two_spans_have_different_ids(self):
        new_trace_id()
        with span("stage.a") as a:
            pass
        with span("stage.b") as b:
            pass
        assert a != b

    def test_trace_id_constant_across_spans(self):
        tid = new_trace_id()
        with span("a"):
            assert get_trace_id() == tid
        with span("b"):
            assert get_trace_id() == tid

    def test_span_propagates_exception(self):
        new_trace_id()
        import pytest
        with pytest.raises(ValueError):
            with span("boom"):
                raise ValueError("x")
        assert get_span_id() == "-"   # still restored on error
