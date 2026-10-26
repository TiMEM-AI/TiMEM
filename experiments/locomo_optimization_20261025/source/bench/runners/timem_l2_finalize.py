"""Ensure TiMEM L2 session memories exist after ingest (before T0 retrieval)."""



from __future__ import annotations



import asyncio

import logging

import time

from typing import Any



from adapters.timem_adapter import TiMEMAdapter

from benchmark_data.locomo_loader import LoCoMoPersona

from utils.config import make_user_id

from utils.ingest_scheduler import run_bounded_tasks



logger = logging.getLogger(__name__)



_BACKFILL_NOOP_HINT = (

    "backfill noop (total_tasks=0): TiMEM memory_sessions not registered for this user_id. "

    "Re-ingest with scoped session_id ({run_id}_{{source_session_id}})."

)





async def _wait_l2_ready(

    adapter: TiMEMAdapter,

    user_id: str,

    *,

    expected_sessions: int,

    timeout_sec: int,

    l2_floor: int = 0,

) -> tuple[float, dict[str, int]]:

    started = time.perf_counter()

    elapsed = 0

    interval = 5

    counts: dict[str, int] = {}

    target = max(1, expected_sessions)

    while elapsed < timeout_sec:

        counts = await adapter.count_layers(user_id)

        l2_count = max(int(counts.get("L2", 0) or 0), l2_floor)

        if l2_count >= target:

            wait_ms = (time.perf_counter() - started) * 1000

            logger.info("L2 ready for %s: %s (target>=%s)", user_id, counts, target)

            return wait_ms, counts

        await asyncio.sleep(interval)

        elapsed += interval

    wait_ms = (time.perf_counter() - started) * 1000

    logger.warning(

        "L2 wait timeout for %s after %ss (counts=%s, target>=%s)",

        user_id,

        timeout_sec,

        counts,

        target,

    )

    return wait_ms, counts





def _backfill_stats_from_result(result: Any) -> dict[str, Any]:

    raw = result.raw if isinstance(getattr(result, "raw", None), dict) else {}

    stats = raw.get("stats")

    return dict(stats) if isinstance(stats, dict) else {}





def _l2_ready_from_backfill(

    *,

    l2_before: int,

    expected_sessions: int,

    backfill_stats: dict[str, Any],

) -> tuple[bool, int]:

    """Prefer backfill stats over list API when judging L2 completeness."""

    generated = int(backfill_stats.get("generated_memories") or 0)

    successful = int(backfill_stats.get("successful_tasks") or 0)

    failed = int(backfill_stats.get("failed_tasks") or 0)

    total_tasks = int(backfill_stats.get("total_tasks") or 0)

    missing = max(0, expected_sessions - l2_before)



    if failed > 0:

        return False, l2_before + generated



    if total_tasks == 0:

        return l2_before >= expected_sessions, l2_before



    l2_estimated = l2_before + generated

    if successful >= missing or l2_estimated >= expected_sessions:

        return True, max(l2_estimated, l2_before + successful)



    return l2_estimated >= expected_sessions, l2_estimated





async def _finalize_one_persona(

    persona: LoCoMoPersona,

    *,

    adapter: TiMEMAdapter,

    run_id: str,

    timeout_sec: int,

) -> dict[str, Any]:

    user_id = make_user_id("timem", run_id, persona.persona_id)

    expected_sessions = len(persona.sessions)

    counts_before = await adapter.count_layers(user_id)

    l2_before = int(counts_before.get("L2", 0) or 0)

    if l2_before >= expected_sessions:

        return {

            "persona_id": persona.persona_id,

            "user_id": user_id,

            "success": True,

            "skipped": True,

            "expected_sessions": expected_sessions,

            "l2_count": l2_before,

            "layer_counts": counts_before,

        }



    result = await adapter.backfill(user_id, ["L2"])

    backfill_stats = _backfill_stats_from_result(result)

    total_tasks = int(backfill_stats.get("total_tasks") or 0)

    failed_tasks = int(backfill_stats.get("failed_tasks") or 0)

    row: dict[str, Any] = {

        "persona_id": persona.persona_id,

        "user_id": user_id,

        "success": False,

        "skipped": False,

        "expected_sessions": expected_sessions,

        "l2_count_before": l2_before,

        "backfill_api_ms": result.latency_ms,

        "backfill_stats": backfill_stats,

        "error": result.error,

    }



    if total_tasks == 0 and l2_before < expected_sessions:

        row["error"] = result.error or _BACKFILL_NOOP_HINT.format(run_id=run_id)

        row["layer_counts"] = counts_before

        return row



    if failed_tasks > 0:

        row["error"] = result.error or f"backfill failed_tasks={failed_tasks}"

        row["layer_counts"] = counts_before

        return row



    ready, l2_estimated = _l2_ready_from_backfill(

        l2_before=l2_before,

        expected_sessions=expected_sessions,

        backfill_stats=backfill_stats,

    )

    if ready:

        counts_after = await adapter.count_layers(user_id)

        l2_after = max(l2_estimated, int(counts_after.get("L2", 0) or 0))

        row["backfill_wait_ms"] = 0.0

        row["l2_count"] = l2_after

        row["layer_counts"] = counts_after

        row["success"] = l2_after >= expected_sessions or l2_estimated >= expected_sessions

        if row["success"]:

            row["error"] = None

        return row



    if not result.success:

        row["layer_counts"] = counts_before

        return row



    wait_ms, counts_after = await _wait_l2_ready(

        adapter,

        user_id,

        expected_sessions=expected_sessions,

        timeout_sec=timeout_sec,

        l2_floor=l2_estimated,

    )

    l2_after = max(l2_estimated, int(counts_after.get("L2", 0) or 0))

    row["backfill_wait_ms"] = wait_ms

    row["l2_count"] = l2_after

    row["layer_counts"] = counts_after

    row["success"] = l2_after >= expected_sessions

    return row





async def finalize_timem_l2_sessions(

    adapter: TiMEMAdapter,

    run_id: str,

    personas: list[LoCoMoPersona],

    *,

    timeout_sec: int = 600,

    concurrency: int = 3,

) -> dict[str, Any]:

    """Trigger L2 backfill per persona and wait until session-level L2 memories are searchable."""

    started = time.perf_counter()

    tasks = [

        (

            persona.persona_id,

            lambda p=persona: _finalize_one_persona(

                p,

                adapter=adapter,

                run_id=run_id,

                timeout_sec=timeout_sec,

            ),

        )

        for persona in personas

    ]

    results = await run_bounded_tasks(tasks, concurrency=concurrency)

    rows = [row for _, row in results]

    rows.sort(key=lambda r: r.get("persona_id") or "")

    ready = sum(1 for r in rows if r.get("success"))

    return {

        "persona_count": len(personas),

        "l2_ready_count": ready,

        "wall_ms": (time.perf_counter() - started) * 1000,

        "details": rows,

    }





async def ensure_timem_l2_before_retrieval(

    adapter: TiMEMAdapter,

    run_id: str,

    personas: list[LoCoMoPersona],

    *,

    timeout_sec: int = 600,

    concurrency: int = 3,

) -> dict[str, Any]:

    """Safety net when retrieve runs without a fresh ingest (e.g. ingest-only then retrieve job)."""

    return await finalize_timem_l2_sessions(

        adapter,

        run_id,

        personas,

        timeout_sec=timeout_sec,

        concurrency=concurrency,

    )


