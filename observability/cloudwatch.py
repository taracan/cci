"""CloudWatch log handler stub.

Disabled by default. When AWS is ready, set LOG_TO_CLOUDWATCH=true and install
`watchtower`; this returns a handler that ships JSON logs to a CloudWatch log
group, which Grafana can then read via the CloudWatch data source.
"""
from __future__ import annotations

import logging
from typing import Optional


def build_cloudwatch_handler() -> Optional[logging.Handler]:
    """Return a CloudWatch logging handler, or None if unavailable.

    Requires the optional `watchtower` package and valid AWS credentials.
    Returns None (and does not raise) if either is missing, so the app keeps
    logging locally without interruption.
    """
    try:
        import watchtower  # type: ignore
    except ImportError:
        logging.getLogger("cloudwatch").warning(
            "cloudwatch.disabled", extra={"reason": "watchtower not installed"}
        )
        return None

    return watchtower.CloudWatchLogHandler(
        log_group="resume-jd-portal",
        stream_name="app",
    )
