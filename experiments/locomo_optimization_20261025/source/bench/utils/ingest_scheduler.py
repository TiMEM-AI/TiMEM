"""Concurrent ingest task scheduling."""



from __future__ import annotations



import asyncio

from collections.abc import Awaitable, Callable

from typing import Any, TypeVar



T = TypeVar("T")





async def run_bounded_tasks(

    tasks: list[tuple[Any, Callable[[], Awaitable[T]]]],

    *,

    concurrency: int,

) -> list[tuple[Any, T]]:

    """Run async callables with a semaphore; preserve input order in results."""

    sem = asyncio.Semaphore(max(1, concurrency))

    ordered: list[tuple[Any, T | None]] = [(key, None) for key, _ in tasks]



    async def _run_index(idx: int, key: Any, fn: Callable[[], Awaitable[T]]) -> None:

        async with sem:

            ordered[idx] = (key, await fn())



    await asyncio.gather(

        *[_run_index(i, key, fn) for i, (key, fn) in enumerate(tasks)]

    )

    return [(key, result) for key, result in ordered if result is not None]





async def run_bounded_tasks_per_key(

    tasks: list[tuple[Any, str, Callable[[], Awaitable[T]]]],

    *,

    concurrency: int,

    key_concurrency: int = 1,

) -> list[tuple[Any, T]]:

    """Run tasks with global and per-key concurrency limits; preserve input order."""

    global_sem = asyncio.Semaphore(max(1, concurrency))

    key_sems: dict[str, asyncio.Semaphore] = {}

    ordered: list[tuple[Any, T | None]] = [(key, None) for key, _, _ in tasks]



    def _key_sem(group_key: str) -> asyncio.Semaphore:

        sem = key_sems.get(group_key)

        if sem is None:

            sem = asyncio.Semaphore(max(1, key_concurrency))

            key_sems[group_key] = sem

        return sem



    async def _run_index(

        idx: int,

        key: Any,

        group_key: str,

        fn: Callable[[], Awaitable[T]],

    ) -> None:

        async with global_sem:

            async with _key_sem(group_key):

                ordered[idx] = (key, await fn())



    await asyncio.gather(

        *[

            _run_index(i, key, group_key, fn)

            for i, (key, group_key, fn) in enumerate(tasks)

        ]

    )

    return [(key, result) for key, result in ordered if result is not None]

