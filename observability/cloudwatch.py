"""CloudWatch log handler.

Enabled by setting LOG_TO_CLOUDWATCH=true (and installing `watchtower`). Ships
the same JSON lines written to logs/app.jsonl to a CloudWatch log group, which
Grafana can then read via the CloudWatch data source.

Group, stream, region and retention come from config (env-overridable). The
handler is best-effort: any failure returns None so the app keeps logging
locally without interruption.
"""
from __future__ import annotations

import logging
import socket
from datetime import datetime
from typing import Optional

from config import (
    CLOUDWATCH_LOG_GROUP, CLOUDWATCH_LOG_STREAM, CLOUDWATCH_REGION,
    CLOUDWATCH_RETENTION_DAYS,
)


def _default_stream_name() -> str:
    """Per-host, per-day stream name so runs stay separable in the console."""
    return f"{socket.gethostname()}-{datetime.utcnow():%Y-%m-%d}"


def build_cloudwatch_handler() -> Optional[logging.Handler]:
    """Return a CloudWatch logging handler, or None if unavailable.

    Requires the optional `watchtower` package and valid AWS credentials.
    Returns None (and does not raise) if either is missing.
    """
    log = logging.getLogger("cloudwatch")
    try:
        import boto3  # type: ignore
        import watchtower  # type: ignore
    except ImportError as exc:
        log.warning("cloudwatch.disabled", extra={"reason": f"{exc} — pip install watchtower"})
        return None

    try:
        session = boto3.session.Session(region_name=CLOUDWATCH_REGION or None)
        return watchtower.CloudWatchLogHandler(
            log_group_name=CLOUDWATCH_LOG_GROUP,
            log_stream_name=CLOUDWATCH_LOG_STREAM or _default_stream_name(),
            boto3_client=session.client("logs"),
            log_group_retention_days=CLOUDWATCH_RETENTION_DAYS,
            create_log_group=True,
            send_interval=5,      # seconds between batched uploads
            max_batch_count=1000,
        )
    except Exception as exc:  # credentials missing, region unset, no permission…
        log.warning("cloudwatch.setup_failed", extra={"error": str(exc)})
        return None
