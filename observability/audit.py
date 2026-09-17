"""Automatic action auditing.

`@audit_action("name")` wraps any Flask view so that EVERY invocation is logged
with the actor, role, route, url args, form fields, trace_id, and outcome —
without having to add a log line inside each view by hand .
"""
from __future__ import annotations

import functools
from typing import Callable

from flask import request, session

from observability.logging_config import get_logger

_logger = get_logger("action")

# Form fields we must never write to logs.
_REDACT = {"password", "confirm_password", "admin_key", "token", "secret"}


def _safe_form() -> dict:
    """Return submitted form fields with sensitive values redacted."""
    out = {}
    for key, value in request.form.items():
        out[key] = "***" if key.lower() in _REDACT else value
    return out


def audit_action(action: str) -> Callable:
    """Decorator: log an action's start and outcome around a Flask view.

    Args:
        action: Stable action name (e.g. 'jd.deactivate', 'resume.upload').

    Returns:
        A decorator that wraps the view function with audit logging.
    """
    def decorator(view: Callable) -> Callable:
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            base = {
                "action": action,
                "actor": session.get("user_id", "anonymous"),
                "role": session.get("role", ""),
                "route": request.path,
                "method": request.method,
                "url_args": dict(kwargs),
                "form": _safe_form() if request.method == "POST" else {},
            }
            _logger.info(f"{action}.attempt", extra=base)
            try:
                result = view(*args, **kwargs)
            except Exception as exc:
                _logger.error(f"{action}.failed", extra={**base, "error": str(exc)})
                raise
            _logger.info(f"{action}.ok", extra=base)
            return result
        return wrapper
    return decorator
