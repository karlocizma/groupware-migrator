"""Minimal 5-field cron expression parser."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _matches(field: str, value: int, min_val: int, _max_val: int) -> bool:
    if field == "*":
        return True
    for part in field.split(","):
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            start = min_val if base in ("*", "") else int(base.split("-")[0])
            if value >= start and (value - start) % step == 0:
                return True
        elif "-" in part:
            lo, hi = part.split("-", 1)
            if int(lo) <= value <= int(hi):
                return True
        elif int(part) == value:
            return True
    return False


def cron_next(expr: str, *, after: datetime) -> datetime:
    """Return the next UTC datetime matching the 5-field cron expression.

    Fields: minute hour dom month dow  (cron convention: dow 0=Sunday)
    """
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError(f"Cron expression must have 5 fields: {expr!r}")
    f_min, f_hour, f_dom, f_month, f_dow = fields

    dt = after.astimezone(timezone.utc).replace(second=0, microsecond=0)
    dt += timedelta(minutes=1)

    dom_restricted = f_dom != "*"
    dow_restricted = f_dow not in ("*", "?")

    for _ in range(366 * 5 * 24 * 60):
        # cron: 0=Sun…6=Sat; Python weekday(): 0=Mon…6=Sun
        cron_dow = (dt.weekday() + 1) % 7

        if not _matches(f_month, dt.month, 1, 12):
            if dt.month == 12:
                dt = dt.replace(year=dt.year + 1, month=1, day=1, hour=0, minute=0)
            else:
                dt = dt.replace(month=dt.month + 1, day=1, hour=0, minute=0)
            continue

        if dom_restricted and dow_restricted:
            day_ok = _matches(f_dom, dt.day, 1, 31) or _matches(f_dow, cron_dow, 0, 6)
        elif dom_restricted:
            day_ok = _matches(f_dom, dt.day, 1, 31)
        elif dow_restricted:
            day_ok = _matches(f_dow, cron_dow, 0, 6)
        else:
            day_ok = True

        if not day_ok:
            dt = (dt + timedelta(days=1)).replace(hour=0, minute=0)
            continue

        if not _matches(f_hour, dt.hour, 0, 23):
            dt = (dt + timedelta(hours=1)).replace(minute=0)
            continue

        if not _matches(f_min, dt.minute, 0, 59):
            dt += timedelta(minutes=1)
            continue

        return dt

    raise ValueError(f"No matching datetime found for cron expression: {expr!r}")


def parse_interval_seconds(expr: str) -> int:
    """Parse a simple interval string like '30m', '6h', '1d' into seconds."""
    expr = expr.strip().lower()
    if expr.endswith("d"):
        return int(expr[:-1]) * 86400
    if expr.endswith("h"):
        return int(expr[:-1]) * 3600
    if expr.endswith("m"):
        return int(expr[:-1]) * 60
    if expr.endswith("s"):
        return int(expr[:-1])
    raise ValueError(f"Unknown interval format: {expr!r} (expected e.g. '30m', '6h', '1d')")
