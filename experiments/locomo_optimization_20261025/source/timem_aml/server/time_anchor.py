"""Give the memory layer an absolute time anchor.

AML messages may carry `timestamp` (Unix milliseconds). The memory pipeline only
keeps the text, so "yesterday" / "last Saturday" end up with no anchor and the
answer model can never convert them into the absolute date the judge expects.

`stamp_messages` rewrites every message content as `[YYYY-MM-DD] text`, which
survives summarisation because the level prompts are told to preserve dates.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _to_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
        if isinstance(value, str):
            text = value.strip().replace("Z", "+00:00")
            return datetime.fromisoformat(text)
    except (ValueError, OSError, OverflowError):
        return None
    return None


def stamp_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Prefix each message with the date derived from its timestamp."""
    if not messages:
        return messages

    stamps = [_to_datetime(m.get("timestamp")) for m in messages]
    if not any(stamps):
        return messages

    # a single date for the whole chunk keeps the narration readable
    known = [s for s in stamps if s]
    chunk_date = known[0].strftime("%Y-%m-%d")

    out: List[Dict[str, Any]] = []
    for message, stamp in zip(messages, stamps):
        content = str(message.get("content") or "")
        if not content.strip():
            out.append(message)
            continue
        date_text = (stamp or known[0]).strftime("%Y-%m-%d")
        # if the writer already dated the line, leave it alone
        if content.lstrip().startswith("[") and "]" in content[:12]:
            out.append(message)
            continue
        new_message = dict(message)
        new_message["content"] = f"[{date_text}] {content}"
        out.append(new_message)

    if chunk_date:
        # keep the date visible even when every message had its own stamp
        for message in out:
            if "content" in message:
                break
    return out


_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_PREFIX_RE = __import__("re").compile(r"^\[\d{4}-\d{2}-\d{2}\]\s*")


def _last_weekday(anchor, target: int):
    from datetime import timedelta

    delta = (anchor.weekday() - target) % 7 or 7
    return anchor - timedelta(days=delta)


def _resolve_relative(text: str, anchor) -> str:
    """Rewrite common relative expressions into `phrase (YYYY-MM-DD)`."""
    import re
    from datetime import timedelta

    day = lambda d: d.strftime("%Y-%m-%d")

    def sub(pattern: str, fn) -> None:
        nonlocal text
        text = re.sub(pattern, lambda m: f"{m.group(0)} ({fn(m)})", text, flags=re.IGNORECASE)

    # order matters: longer phrases first
    def _last_weekend(d):
        monday = d - timedelta(days=d.weekday())
        return monday - timedelta(days=2), monday - timedelta(days=1)

    sub(
        r"\blast\s+weekend\b(?!\s*\()",
        lambda m: "{0} to {1}".format(day(_last_weekend(anchor)[0]), day(_last_weekend(anchor)[1])),
    )
    sub(r"\blast\s+week\b(?!\s*\()", lambda m: f"{day(anchor - timedelta(days=anchor.weekday() + 7))} to {day(anchor - timedelta(days=anchor.weekday() + 1))}")
    sub(r"\bnext\s+month\b(?!\s*\()", lambda m: (anchor.replace(day=1) + timedelta(days=32)).strftime("%Y-%m"))
    sub(r"\blast\s+month\b(?!\s*\()", lambda m: (anchor.replace(day=1) - timedelta(days=1)).strftime("%Y-%m"))
    sub(r"\bthis\s+month\b(?!\s*\()", lambda m: anchor.strftime("%Y-%m"))
    sub(
        r"\blast\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b(?!\s*\()",
        lambda m: day(_last_weekday(anchor, _WEEKDAYS[m.group(1).lower()])),
    )
    sub(r"\blast\s+night\b(?!\s*\()", lambda m: day(anchor - timedelta(days=1)))
    sub(r"\byesterday\b(?!\s*\()", lambda m: day(anchor - timedelta(days=1)))
    sub(r"\btoday\b(?!\s*\()", lambda m: day(anchor))
    sub(r"\b(\d{1,2})\s+days?\s+ago\b(?!\s*\()", lambda m: day(anchor - timedelta(days=int(m.group(1)))))
    return text


def stamp_temporal(user_id: str, window_seconds: int = 180) -> int:
    """Stamp L1..L5 rows with their date and resolve relative time expressions.

    The generator tends to drop the `[date]` prefix from the prompt, so this is
    applied deterministically after the row exists. Idempotent: already
    annotated phrases and prefixes are not touched twice. Returns rows updated.
    """
    import os

    import psycopg2

    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "timem_user"),
        password=os.getenv("POSTGRES_PASSWORD", "timem_password"),
        dbname=os.getenv("POSTGRES_DATABASE", "timem_db"),
        connect_timeout=5,
    )
    updated = 0
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                select id, content, time_window_start
                  from core_memories
                 where user_id = %s and level in ('L1','L2','L3','L4','L5')
                """,
                (user_id,),
            )
            rows = cur.fetchall()
            for row_id, content, window_start in rows:
                content = content or ""
                anchor = (window_start.date() if hasattr(window_start, "date") else window_start)
                body = _PREFIX_RE.sub("", content)
                resolved = _resolve_relative(body, anchor)
                new_content = f"[{anchor.strftime('%Y-%m-%d')}] {resolved}"
                if new_content != content:
                    cur.execute(
                        "update core_memories set content = %s where id = %s",
                        (new_content, row_id),
                    )
                    updated += 1
        return updated
    finally:
        conn.close()


# backward-compatible alias (older call sites / notebooks)
prefix_l1_dates = stamp_temporal
