"""In-process graph tasks. Search waits only for work that has already started."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from timem.memory_graph.build import ingest_l2_rows, l2_children, safe_embed
from timem.memory_graph.extract import default_complete
from timem.memory_graph.store import get_store
from timem.utils.logging import get_logger

logger = get_logger(__name__)

GRAPH_INGEST_WAIT_SECONDS = 120.0
_inflight: Dict[str, List[asyncio.Task]] = {}


def scope_key(user_id: str, expert_id: str) -> str:
    return f"{(user_id or '').strip()}:{(expert_id or '').strip() or 'default'}"


def report_date_of(task: Any, memory: Any) -> str:
    window = getattr(task, "time_window", None) or {}
    start = window.get("start_time") if isinstance(window, dict) else None
    if hasattr(start, "date"):
        return start.date().isoformat()
    if isinstance(start, str) and start.strip():
        return start.strip()[:10]
    raw = None
    if isinstance(memory, dict):
        raw = memory.get("timestamp") or memory.get("created_at")
    else:
        raw = getattr(memory, "timestamp", None) or getattr(memory, "created_at", None)
    if raw is None:
        raw = getattr(task, "timestamp", None)
    if hasattr(raw, "date"):
        return raw.date().isoformat()
    if isinstance(raw, str) and raw.strip():
        return raw.strip()[:10]
    return datetime.now().date().isoformat()


def _layer(task: Any) -> str:
    return str(getattr(task, "layer", "") or "")


async def _default_load(memory_id: str):
    from storage.memory_storage_manager import get_memory_storage_manager_async

    manager = await get_memory_storage_manager_async()
    return await manager.get_memory(memory_id)


def _track(key: str, task: asyncio.Task) -> None:
    bucket = _inflight.setdefault(key, [])
    bucket.append(task)

    def _drop(done: asyncio.Task) -> None:
        current = _inflight.get(key) or []
        _inflight[key] = [item for item in current if item is not done]
        if not _inflight[key]:
            _inflight.pop(key, None)

    task.add_done_callback(_drop)


async def schedule_after_l3(
    task: Any,
    memory: Any,
    *,
    store=None,
    complete=None,
    load_memory=None,
    embed=None,
    enabled: Optional[bool] = None,
) -> Optional[str]:
    """Enqueue ingest after L3 is stored. Returns the scope key, or None when skipped."""
    if _layer(task) != "L3":
        return None
    graph = store if store is not None else get_store()
    if enabled is None:
        enabled = bool(getattr(graph, "enabled", False))
    if not enabled:
        return None
    user_id = str(getattr(task, "user_id", "") or "").strip()
    expert_id = str(getattr(task, "expert_id", "") or "default").strip() or "default"
    if not user_id:
        return None
    report_date = report_date_of(task, memory)
    key = scope_key(user_id, expert_id)

    async def _run() -> None:
        try:
            rows = await l2_children(memory, load_memory or _default_load)
            await ingest_l2_rows(
                user_id=user_id,
                expert_id=expert_id,
                report_date=report_date,
                rows=rows,
                store=graph,
                complete=complete or default_complete,
                embed=embed,
            )
        except Exception as exc:
            logger.warning("memory graph ingest failed user_id=%s: %s", user_id, exc)

    _track(key, asyncio.create_task(_run()))
    return key


async def safe_schedule_after_l3(task: Any, memory: Any) -> None:
    """Scheduling errors stay here. The L3 write result is unchanged."""
    try:
        await schedule_after_l3(task, memory, embed=safe_embed)
    except Exception as exc:
        logger.warning(
            "memory graph schedule failed user_id=%s: %s",
            getattr(task, "user_id", None),
            exc,
        )


async def wait_inflight(user_id: str, expert_id: str, timeout: float = GRAPH_INGEST_WAIT_SECONDS) -> None:
    key = scope_key(user_id, expert_id)
    pending = [task for task in _inflight.get(key, []) if not task.done()]
    if not pending:
        return
    try:
        await asyncio.wait_for(
            asyncio.gather(*(asyncio.shield(task) for task in pending), return_exceptions=True),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("memory graph wait timed out user_id=%s", user_id)
