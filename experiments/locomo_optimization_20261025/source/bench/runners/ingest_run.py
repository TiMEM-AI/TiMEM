"""Ingest benchmark runner."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from adapters.base import MemoryAdapter
from adapters.registry import close_adapters, get_adapters
from adapters.timem_adapter import TiMEMAdapter
from benchmark_data.locomo_loader import load_locomo_personas
from models.records import IngestResult, LoCoMoPersona, LoCoMoSession
from runners.ingest_flush import flush_mem0_pending
from utils.config import PROJECT_ROOT, get_settings, make_user_id, resolve_ingest_config
from utils.ids import new_run_id, scoped_session_id
from utils.ingest_add_metrics import aggregate_add_metrics
from runners.timem_l2_finalize import finalize_timem_l2_sessions
from utils.ingest_scheduler import run_bounded_tasks, run_bounded_tasks_per_key
from utils.message_pairs import pair_count

logger = logging.getLogger(__name__)


@dataclass
class IngestRunReport:
    run_id: str
    system: str
    persona_count: int
    session_count: int
    success_count: int
    failure_count: int
    latency: dict[str, float]
    add_count: int = 0
    add_success_count: int = 0
    add_latency: dict[str, float] = field(default_factory=dict)
    session_latency: dict[str, float] = field(default_factory=dict)
    latency_granularity: str = "add"
    add_latency_estimated: bool = False
    avg_input_tokens: float = 0.0
    run_wall_ms: float = 0.0
    sum_latency_ms: float = 0.0
    concurrency_settings: dict[str, Any] = field(default_factory=dict)
    mem0_flush: dict[str, Any] = field(default_factory=dict)
    timem_l2_finalize: dict[str, Any] = field(default_factory=dict)
    details: list[dict[str, Any]] = field(default_factory=list)


async def run_ingest(
    *,
    run_id: str | None = None,
    systems: list[str] | None = None,
    persona_count: int | None = None,
    use_fixture: bool = False,
    ingest_overrides: dict[str, Any] | None = None,
    wait_timem_l2_on_ingest: bool = False,
    memory_language: str | None = None,
) -> dict[str, IngestRunReport]:
    run_id = run_id or new_run_id()
    settings = get_settings()
    persona_count = persona_count or settings.benchmark_persona_count
    systems = systems or ["timem", "memos", "mem0"]
    ingest_cfg = resolve_ingest_config(ingest_overrides)
    lang = str(memory_language or settings.timem_memory_language or "en").strip().lower()
    if lang not in ("zh", "en"):
        lang = "en"

    personas = load_locomo_personas(persona_count=persona_count, use_fixture=use_fixture)
    adapters = get_adapters(systems)
    reports: dict[str, IngestRunReport] = {}

    try:
        if ingest_cfg.system_parallel and len(adapters) > 1:
            results = await asyncio.gather(
                *[
                    _ingest_personas(
                        adapter, system, run_id, personas, ingest_cfg,
                        wait_timem_l2_on_ingest=wait_timem_l2_on_ingest,
                        memory_language=lang,
                    )
                    for system, adapter in adapters.items()
                ]
            )
            for report in results:
                reports[report.system] = report
        else:
            for system, adapter in adapters.items():
                reports[system] = await _ingest_personas(
                    adapter, system, run_id, personas, ingest_cfg,
                    wait_timem_l2_on_ingest=wait_timem_l2_on_ingest,
                    memory_language=lang,
                )

        flush_info = await flush_mem0_pending(adapters)
        if flush_info:
            for system, report in reports.items():
                if system == "mem0":
                    report.mem0_flush = flush_info
    finally:
        await close_adapters(adapters)

    _save_reports(run_id, "ingest", {k: asdict(v) for k, v in reports.items()})
    return reports


async def _ingest_personas(
    adapter: MemoryAdapter,
    system: str,
    run_id: str,
    personas: list[LoCoMoPersona],
    ingest_cfg: Any,
    *,
    wait_timem_l2_on_ingest: bool = False,
    memory_language: str = "en",
) -> IngestRunReport:
    run_started = time.perf_counter()
    concurrency = ingest_cfg.session_concurrency_for(system)
    concurrency_settings = {
        **ingest_cfg.to_report_dict(),
        "effective_session_concurrency": concurrency,
    }
    if system == "timem":
        concurrency_settings["timem_per_user_session_serial"] = True
        concurrency_settings["memory_language"] = memory_language

    session_jobs: list[tuple[str, str, str, LoCoMoSession]] = []
    for persona in personas:
        user_id = make_user_id(system, run_id, persona.persona_id)
        for session in persona.sessions:
            session_jobs.append((persona.persona_id, user_id, session.session_id, session))

    if system == "timem" and isinstance(adapter, TiMEMAdapter):
        for persona in personas:
            user_id = make_user_id(system, run_id, persona.persona_id)
            try:
                await adapter.ensure_memory_language(user_id, memory_language)
            except Exception as exc:
                logger.warning(
                    "Failed to set memory_language=%s for user_id=%s: %s",
                    memory_language,
                    user_id,
                    exc,
                )

    async def _ingest_session(job: tuple[str, str, str, LoCoMoSession]) -> dict[str, Any]:
        persona_id, user_id, session_id, session = job
        source_session_id = session_id
        api_session_id = (
            scoped_session_id(run_id, session_id) if system == "timem" else session_id
        )
        result: IngestResult = await adapter.ingest(
            user_id=user_id,
            session_id=api_session_id,
            messages=session.messages,
        )
        token_count = float((result.metrics or {}).get("input_tokens") or 0)
        raw = result.raw or {}
        pair_details = raw.get("pair_details")
        detail = {
            "persona_id": persona_id,
            "user_id": user_id,
            "session_id": api_session_id,
            "success": result.success,
            "latency_ms": result.latency_ms,
            "input_tokens": token_count,
            "memory_count": result.memory_count,
            "api_calls": raw.get("api_calls", 1),
            "pair_count": raw.get("pair_count", pair_count(session.messages)),
            "ingest_mode": raw.get("ingest_mode"),
            "poll_mode": raw.get("poll_mode"),
            "error": result.error,
        }
        if system == "timem":
            detail["source_session_id"] = source_session_id
        if isinstance(pair_details, list):
            detail["pair_details"] = pair_details
        api_calls = int(detail.get("api_calls") or 0)
        if api_calls > 0 and detail.get("success"):
            detail["avg_add_latency_ms"] = float(detail["latency_ms"]) / api_calls
        return detail

    if system == "timem":
        # TiMEM session_id is scoped per run_id so memory_sessions align with user_id.
        # L2 is produced after ingest via backfill/manual (not by POST /memory/ alone).
        # Parallel sessions for the same user_id break L2 — serialize per user.
        task_specs = [
            (job, job[1], lambda j=job: _ingest_session(j))
            for job in session_jobs
        ]
        bounded = await run_bounded_tasks_per_key(
            task_specs,
            concurrency=concurrency,
            key_concurrency=1,
        )
    else:
        task_specs = [
            (job, lambda j=job: _ingest_session(j))
            for job in session_jobs
        ]
        bounded = await run_bounded_tasks(task_specs, concurrency=concurrency)
    details = [detail for _, detail in bounded]

    add_metrics = aggregate_add_metrics(details, estimated=False)
    details = add_metrics["details"]
    input_tokens = [float(d["input_tokens"]) for d in details]

    def _avg(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    timem_l2_finalize: dict[str, Any] = {}
    if system == "timem" and isinstance(adapter, TiMEMAdapter):
        if wait_timem_l2_on_ingest:
            settings = get_settings()
            timem_l2_finalize = await finalize_timem_l2_sessions(
                adapter,
                run_id,
                personas,
                timeout_sec=settings.benchmark_backfill_timeout_sec,
                concurrency=min(3, concurrency),
            )
        else:
            timem_l2_finalize = {
                "skipped": True,
                "reason": "wait_timem_l2_on_ingest=false",
            }

    return IngestRunReport(
        run_id=run_id,
        system=system,
        persona_count=len(personas),
        session_count=add_metrics["session_count"],
        success_count=add_metrics["success_count"],
        failure_count=add_metrics["failure_count"],
        latency=add_metrics["latency"],
        add_count=add_metrics["add_count"],
        add_success_count=add_metrics["add_success_count"],
        add_latency=add_metrics["add_latency"],
        session_latency=add_metrics["session_latency"],
        latency_granularity=add_metrics["latency_granularity"],
        add_latency_estimated=add_metrics["add_latency_estimated"],
        avg_input_tokens=_avg(input_tokens),
        sum_latency_ms=add_metrics["sum_latency_ms"],
        run_wall_ms=(time.perf_counter() - run_started) * 1000,
        concurrency_settings=concurrency_settings,
        timem_l2_finalize=timem_l2_finalize,
        details=details,
    )


def _save_reports(run_id: str, stage: str, payload: dict[str, Any]) -> Path:
    """Write stage report, merging system keys so partial re-runs keep siblings."""
    out_dir = PROJECT_ROOT / "reports" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stage}.json"
    merged = dict(payload)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                merged = {**existing, **payload}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not merge existing %s (%s); overwriting", path, exc)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    logger.info("Saved report: %s (systems=%s)", path, list(merged.keys()))
    return path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    use_fixture = "--fixture" in __import__("sys").argv
    reports = asyncio.run(run_ingest(use_fixture=use_fixture))
    for name, report in reports.items():
        print(f"\n=== {name} ingest ===")
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
