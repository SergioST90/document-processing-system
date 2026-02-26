"""SLA deadline calculation and checking utilities."""

from datetime import datetime, timedelta, timezone


def calculate_deadline(sla_seconds: int, from_time: datetime | None = None) -> datetime:
    """Calculate the absolute deadline from an SLA duration."""
    base = from_time or datetime.now(timezone.utc)
    return base + timedelta(seconds=sla_seconds)


def remaining_seconds(deadline: datetime) -> float:
    """Seconds remaining until deadline. Negative means overdue."""
    now = datetime.now(timezone.utc)
    return (deadline - now).total_seconds()


def is_at_risk(deadline: datetime, warn_threshold_pct: int = 70, sla_seconds: int = 60) -> bool:
    """Check if a job is at risk of missing its SLA.

    Returns True if more than warn_threshold_pct of the SLA time has elapsed.
    """
    remaining = remaining_seconds(deadline)
    threshold_remaining = sla_seconds * (1 - warn_threshold_pct / 100)
    return remaining <= threshold_remaining


def is_breached(deadline: datetime) -> bool:
    """Check if the SLA deadline has passed."""
    return remaining_seconds(deadline) <= 0
