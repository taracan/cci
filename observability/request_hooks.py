"""Flask request lifecycle hooks — trace every HTTP request end to end.

Registers before/after/teardown handlers that:
  - create a trace_id per request
  - log request.received and request.completed (with latency + status)
  - persist a RequestTrace row to the DB
"""
from __future__ import annotations

import time
from datetime import datetime

from flask import Flask, request, session, g

from observability.logging_config import get_logger, setup_logging
from observability.tracing import new_trace_id, get_trace_id

logger = get_logger("request")

# Form fields never written to logs.
_REDACT = {"password", "confirm_password", "admin_key", "token", "secret"}


def _safe_form() -> dict:
    """Return submitted form fields with sensitive values redacted."""
    out = {}
    for key, value in request.form.items():
        out[key] = "***" if key.lower() in _REDACT else value
    return out


def _action_name(path: str) -> str:
    """Derive a readable action name from a request path.

    Examples:
        /admin/jds/jd_001/deactivate  -> admin.jds.deactivate
        /candidate/upload-resume      -> candidate.upload-resume
    """
    parts = [p for p in path.strip("/").split("/") if p]
    # Drop id-like segments (they contain '_' or are long hex) to keep names stable.
    cleaned = [p for p in parts if not (("_" in p) or (len(p) >= 12 and p.isalnum()))]
    return ".".join(cleaned) if cleaned else "request"


def register_request_hooks(app: Flask) -> None:
    """Attach lifecycle logging + DB tracing to a Flask app.

    Args:
        app: The Flask application to instrument.
    """
    setup_logging()

    @app.before_request
    def _start_trace() -> None:
        """Open a trace, stash start time, log request.received."""
        g._trace_id = new_trace_id()
        g._start = time.perf_counter()
        logger.info(
            "request.received",
            extra={
                "route": request.path,
                "method": request.method,
                "user_id": session.get("user_id", ""),
                "role": session.get("role", ""),
            },
        )

    @app.after_request
    def _end_trace(response):
        """Log request.completed + action audit, and persist the trace."""
        start = getattr(g, "_start", None)
        latency_ms = round((time.perf_counter() - start) * 1000, 2) if start else 0.0

        # AUTO-AUDIT: any state-changing request (POST/PUT/DELETE) is a user action.
        # This guarantees EVERY admin/candidate action is logged without per-route code.
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            logger.info(
                "action",
                extra={
                    "action": _action_name(request.path),
                    "actor": session.get("user_id", "anonymous"),
                    "role": session.get("role", ""),
                    "route": request.path,
                    "method": request.method,
                    "form": _safe_form(),
                    "status_code": response.status_code,
                    "latency_ms": latency_ms,
                },
            )

        logger.info(
            "request.completed",
            extra={
                "route": request.path,
                "method": request.method,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
                "user_id": session.get("user_id", ""),
                "role": session.get("role", ""),
            },
        )
        _persist_trace(request.path, request.method, response.status_code, latency_ms)
        return response

    @app.teardown_request
    def _log_exceptions(exc: BaseException | None) -> None:
        """Log any unhandled exception raised during the request."""
        if exc is not None:
            logger.error("request.exception", extra={"route": request.path, "error": str(exc)})


def _persist_trace(route: str, method: str, status_code: int, latency_ms: float) -> None:
    """Write a RequestTrace row; never raise (logging must not break requests)."""
    try:
        from db.database import get_session
        from db.models_orm import RequestTrace
        with get_session() as s:
            s.add(RequestTrace(
                trace_id=get_trace_id(),
                route=route,
                method=method,
                user_id=session.get("user_id", ""),
                role=session.get("role", ""),
                started_at=datetime.utcnow(),
                ended_at=datetime.utcnow(),
                total_latency_ms=latency_ms,
                status_code=status_code,
            ))
    except Exception as exc:
        logger.warning("request.trace_persist_failed", extra={"error": str(exc)})
