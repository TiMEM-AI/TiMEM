"""Search-then-judge retrieval pipeline with bounded concurrency."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from utils.ingest_scheduler import run_bounded_tasks

T = TypeVar("T")


async def run_search_judge_pipeline(
    tasks: list[tuple[Any, Callable[[], Awaitable[dict[str, Any]]]]],
    *,
    query_concurrency: int,
    judge_concurrency: int,
    judge_row_fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
) -> list[tuple[Any, dict[str, Any]]]:
    """Run search then judge per QA with overlapping phases.

    Each task acquires a search slot, runs search, releases it, then acquires a
    judge slot. This overlaps search and judge work instead of batching phases.
    """
    if not tasks:
        return []

    search_sem = asyncio.Semaphore(max(1, query_concurrency))
    judge_sem = asyncio.Semaphore(max(1, judge_concurrency or 1))
    ordered: list[tuple[Any, dict[str, Any] | None]] = [(key, None) for key, _ in tasks]

    async def _run_index(
        idx: int,
        key: Any,
        search_fn: Callable[[], Awaitable[dict[str, Any]]],
    ) -> None:
        async with search_sem:
            row = await search_fn()
        if judge_row_fn is not None:
            async with judge_sem:
                row = await judge_row_fn(row)
        else:
            row.pop("_memory_records", None)
            row["judge"] = {}
        ordered[idx] = (key, row)

    await asyncio.gather(
        *[_run_index(i, key, fn) for i, (key, fn) in enumerate(tasks)]
    )
    return [(key, row) for key, row in ordered if row is not None]


async def run_search_judge_two_phase(
    tasks: list[tuple[Any, Callable[[], Awaitable[dict[str, Any]]]]],
    *,
    query_concurrency: int,
    judge_concurrency: int,
    judge_row_fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
) -> list[tuple[Any, dict[str, Any]]]:
    """Legacy two-phase: all searches, then all judges."""
    search_results = await run_bounded_tasks(tasks, concurrency=query_concurrency)
    rows = [row for _, row in search_results]
    if judge_row_fn is None:
        for row in rows:
            row.pop("_memory_records", None)
            row["judge"] = {}
        return search_results

    judge_tasks = [
        (key, lambda r=row: judge_row_fn(r))
        for (key, row) in search_results
    ]
    return await run_bounded_tasks(judge_tasks, concurrency=judge_concurrency)
