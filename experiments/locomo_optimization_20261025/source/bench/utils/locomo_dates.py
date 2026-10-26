"""Parse LoCoMo session date/time strings into ISO 8601 timestamps."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

TURN_TIMESTAMP_STEP_SEC = 60

# e.g. "1:56 pm on 8 May, 2023" or "1:56 pm on 8 May 2023"
_LOCOMO_DATETIME_RE = re.compile(
    r"^\s*(\d{1,2}):(\d{2})\s*(am|pm)\s+on\s+(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})\s*$",
    re.IGNORECASE,
)

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def parse_locomo_datetime(raw: str | None) -> str | None:
    """Parse LoCoMo ``session_N_date_time`` into ISO 8601 (naive local)."""
    if not raw or not str(raw).strip():
        return None
    text = str(raw).strip()
    match = _LOCOMO_DATETIME_RE.match(text)
    if not match:
        logger.warning("Failed to parse LoCoMo datetime: %r", raw)
        return None
    hour, minute, ampm, day, month_name, year = match.groups()
    month = _MONTHS.get(month_name.lower())
    if month is None:
        logger.warning("Unknown month in LoCoMo datetime: %r", raw)
        return None
    h = int(hour) % 12
    if ampm.lower() == "pm":
        h += 12
    try:
        dt = datetime(int(year), month, int(day), h, int(minute), 0)
    except ValueError as exc:
        logger.warning("Invalid LoCoMo datetime %r: %s", raw, exc)
        return None
    return dt.isoformat(timespec="seconds")


def message_timestamps(
    started_at: str,
    turn_count: int,
    *,
    step_sec: int = TURN_TIMESTAMP_STEP_SEC,
) -> list[str]:
    """Return ISO timestamps for each turn, monotonically increasing within a session."""
    if turn_count <= 0:
        return []
    base = datetime.fromisoformat(started_at)
    return [
        (base + timedelta(seconds=i * step_sec)).isoformat(timespec="seconds")
        for i in range(turn_count)
    ]
