"""Debounced L2 backfill after AML adds, with coverage-based idempotency.

The AML platform never tells us "this is the last chunk of a session", so an
add schedules a short-delay backfill for that user; a new add for the same user
cancels and re-arms the timer.

Before running the (expensive, LLM-backed) backfill we check whether an L2
already covers every L1 of that user. Without this check TiMEM happily creates
another near-identical L2 each time the window is triggered.

Disable with TIMEM_BACKFILL_ON_ADD=0.
"""
from __future__ import annotations

import asyncio
import os
from typing import Dict

from timem.utils.logging import get_logger

logger = get_logger(__name__)

_tasks: Dict[str, asyncio.Task] = {}
DELAY_SECONDS = float(os.getenv("TIMEM_BACKFILL_DELAY", "4"))
LAYERS = ["L2"]

# An L2 covers the user when it has an edge to every L1 the user owns.
_COVERED_SQL = """
select r.parent_id
from memory_child_relations r
join core_memories p on p.id = r.parent_id and p.user_id = %(uid)s and p.level = 'L2'
join core_memories c on c.id = r.child_id   and c.user_id = %(uid)s and c.level = 'L1'
group by r.parent_id
having count(distinct r.child_id) >= (
    select count(*) from core_memories where user_id = %(uid)s and level = 'L1'
)
limit 1
"""


def enabled() -> bool:
    return os.getenv("TIMEM_BACKFILL_ON_ADD", "1").strip().lower() not in {"0", "false", "no", "off"}


def _l2_already_covers(user_id: str) -> bool:
    """True when some L2 already aggregates all of this user's L1 fragments."""
    import psycopg2

    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "timem_user"),
        password=os.getenv("POSTGRES_PASSWORD", "timem_password"),
        dbname=os.getenv("POSTGRES_DATABASE", "timem_db"),
        connect_timeout=5,
    )
    try:
        with conn, conn.cursor() as cur:
            cur.execute(_COVERED_SQL, {"uid": user_id})
            return cur.fetchone() is not None
    finally:
        conn.close()


async def _run(user_id: str) -> None:
    try:
        await asyncio.sleep(DELAY_SECONDS)

        try:
            covered = await asyncio.to_thread(_l2_already_covers, user_id)
        except Exception as exc:  # guard failure must not block backfill
            logger.warning("L2 coverage check failed for %s: %s", user_id, exc)
            covered = False

        if covered:
            logger.info("L2 backfill skipped for %s: existing L2 already covers all L1s", user_id)
            return

        from services.scheduled_backfill_service import get_scheduled_backfill_service

        service = get_scheduled_backfill_service()
        result = await service.backfill_for_user(user_id, layers=list(LAYERS))
        logger.info("L2 backfill done for %s: %s", user_id, str(result)[:200])
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("L2 backfill failed for %s: %s", user_id, exc)


def schedule(user_id: str) -> None:
    """(Re)arm the per-user backfill timer."""
    if not enabled() or not user_id:
        return
    previous = _tasks.get(user_id)
    if previous and not previous.done():
        previous.cancel()
    _tasks[user_id] = asyncio.create_task(_run(user_id))
