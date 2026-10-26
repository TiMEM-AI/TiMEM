"""Retrieval benchmark runner (T0 / T1)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from adapters.base import MemoryAdapter
from adapters.registry import close_adapters, get_adapters
from adapters.timem_adapter import TiMEMAdapter
from adapters.timem_adapter import TiMEMAdapter
from benchmark_data.locomo_loader import load_locomo_personas
from evaluators.ark_judge import ARKJudge
from evaluators.category_metrics import build_category_summary
from evaluators.latency import summarize_latencies
from evaluators.recall import recall_at_k
from evaluators.tokens import count_tokens
from models.records import LoCoMoPersona, LoCoMoQA, MemoryRecord, SearchResult
from runners.ingest_flush import flush_mem0_pending
from runners.token_compare import save_token_compare
from runners.timem_l2_finalize import ensure_timem_l2_before_retrieval
from utils.config import (
    PROJECT_ROOT,
    get_settings,
    make_user_id,
    resolve_benchmark_config,
    resolve_retrieval_config,
)
from utils.ids import new_run_id
from utils.ingest_scheduler import run_bounded_tasks
from utils.retrieval_pipeline import run_search_judge_pipeline, run_search_judge_two_phase

logger = logging.getLogger(__name__)


@dataclass
class RetrievalRunReport:
    run_id: str
    system: str
    mode: str
    query_count: int
    success_count: int
    empty_count: int
    latency: dict[str, float]
    recall_at_5: float
    recall_at_10: float
    judge_accuracy: float
    judge_avg_score: float
    preset: str = "stable"
    top_k: int = 10
    avg_recalled_tokens: float = 0.0
    p50_recalled_tokens: float = 0.0
    p95_recalled_tokens: float = 0.0
    min_recalled_tokens: float = 0.0
    max_recalled_tokens: float = 0.0
    avg_recalled_tokens_nonempty: float = 0.0
    p50_recalled_tokens_nonempty: float = 0.0
    avg_record_count: float = 0.0
    total_judge_tokens: int = 0
    total_judge_latency_ms: float = 0.0
    sum_search_latency_ms: float = 0.0
    sum_work_ms: float = 0.0
    run_wall_ms: float = 0.0
    concurrency_settings: dict[str, Any] = field(default_factory=dict)
    backfill_summary: dict[str, Any] = field(default_factory=dict)
    timem_overrides: dict[str, Any] = field(default_factory=dict)
    details: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BackfillPersonaReport:
    persona_id: str
    user_id: str
    success: bool
    backfill_api_ms: float = 0.0
    backfill_wait_ms: float = 0.0
    backfill_total_ms: float = 0.0
    layer_counts: dict[str, int] = field(default_factory=dict)
    error: str | None = None


@dataclass
class _QAJob:
    persona_id: str
    user_id: str
    qa: LoCoMoQA


async def run_retrieval(
    *,
    run_id: str,
    mode: Literal["T0", "T1"] = "T0",
    systems: list[str] | None = None,
    persona_count: int | None = None,
    use_fixture: bool = False,
    run_judge: bool = True,
    preset: str | None = None,
    timem_overrides: dict[str, Any] | None = None,
    backfill_layers: list[str] | None = None,
    retrieval_overrides: dict[str, Any] | None = None,
) -> dict[str, RetrievalRunReport]:
    settings = get_settings()
    run_cfg = resolve_benchmark_config(
        preset=preset,
        mode=mode,
        timem_overrides=timem_overrides,
        backfill_layers=backfill_layers,
    )
    ret_cfg = resolve_retrieval_config(retrieval_overrides)
    persona_count = persona_count or settings.benchmark_persona_count
    top_k = run_cfg.top_k
    systems = systems or ["timem", "memos", "mem0"]

    personas = load_locomo_personas(persona_count=persona_count, use_fixture=use_fixture)
    judge = (
        ARKJudge(
            max_retries=ret_cfg.judge_max_retries,
            retry_base_sec=ret_cfg.judge_retry_base_sec,
        )
        if run_judge
        else None
    )

    adapters = get_adapters(systems)
    backfill_report: list[dict[str, Any]] = []
    backfill_wall_ms = 0.0
    reports: dict[str, RetrievalRunReport] = {}

    try:
        if mode == "T0" and "mem0" in adapters:
            await flush_mem0_pending(adapters)

        if mode == "T0" and "timem" in adapters:
            timem_adapter = adapters["timem"]
            if isinstance(timem_adapter, TiMEMAdapter):
                l2_summary = await ensure_timem_l2_before_retrieval(
                    timem_adapter,
                    run_id,
                    personas,
                    timeout_sec=settings.benchmark_backfill_timeout_sec,
                    concurrency=min(3, ret_cfg.backfill_concurrency),
                )
                if l2_summary.get("l2_ready_count", 0) < l2_summary.get("persona_count", 0):
                    logger.warning(
                        "TiMEM L2 not fully ready before T0: %s/%s personas",
                        l2_summary.get("l2_ready_count"),
                        l2_summary.get("persona_count"),
                    )

        if mode == "T1" and "timem" in adapters:
            backfill_started = time.perf_counter()
            backfill_report = await _timem_backfill_all(
                adapters["timem"],
                run_id,
                personas,
                settings,
                run_cfg.backfill_layers,
                backfill_concurrency=ret_cfg.backfill_concurrency,
            )
            backfill_wall_ms = (time.perf_counter() - backfill_started) * 1000
            _save_backfill_report(run_id, mode, backfill_report, backfill_wall_ms=backfill_wall_ms)

        async def _run_system(system: str, adapter: MemoryAdapter) -> RetrievalRunReport:
            overrides = run_cfg.timem_overrides if system == "timem" else None
            return await _retrieve_personas(
                adapter=adapter,
                system=system,
                run_id=run_id,
                mode=mode,
                personas=personas,
                top_k=top_k,
                judge=judge,
                preset=run_cfg.preset,
                timem_overrides=overrides or {},
                backfill_summary=_backfill_summary_for_timem(
                    system,
                    backfill_report,
                    backfill_wall_ms=backfill_wall_ms,
                ),
                ret_cfg=ret_cfg,
            )

        if ret_cfg.system_parallel and len(adapters) > 1:
            results = await asyncio.gather(
                *[_run_system(system, adapter) for system, adapter in adapters.items()]
            )
            for report in results:
                reports[report.system] = report
        else:
            for system, adapter in adapters.items():
                reports[system] = await _run_system(system, adapter)
    finally:
        if judge is not None:
            await judge.close_async()
        await close_adapters(adapters)

    out_dir = PROJECT_ROOT / "reports" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"retrieval_{mode}.json"
    payload = {k: asdict(v) for k, v in reports.items()}
    # Merge with existing systems so memos/timem/mem0 can be supplemented independently.
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                payload = {**existing, **payload}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not merge existing %s (%s); overwriting", path, exc)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    logger.info("Saved report: %s (systems=%s)", path, list(payload.keys()))

    category_path = out_dir / f"summary_by_category_{mode}.json"
    with open(category_path, "w", encoding="utf-8") as f:
        json.dump(build_category_summary(payload, mode=mode), f, indent=2, ensure_ascii=False)
    logger.info("Saved category summary: %s", category_path)

    compare_path = save_token_compare(run_id, mode, payload)
    logger.info("Saved token compare: %s", compare_path)

    return reports


def _backfill_summary_for_timem(
    system: str,
    backfill_report: list[dict[str, Any]],
    *,
    backfill_wall_ms: float = 0.0,
) -> dict[str, Any]:
    if system != "timem" or not backfill_report:
        return {}
    api_vals = [float(r.get("backfill_api_ms") or 0) for r in backfill_report]
    wait_vals = [float(r.get("backfill_wait_ms") or 0) for r in backfill_report]
    total_vals = [float(r.get("backfill_total_ms") or 0) for r in backfill_report]
    sum_persona_ms = sum(total_vals)
    out: dict[str, Any] = {
        "persona_count": len(backfill_report),
        "success_count": sum(1 for r in backfill_report if r.get("success")),
        "backfill_api_ms": summarize_latencies(api_vals),
        "backfill_wait_ms": summarize_latencies(wait_vals),
        "backfill_total_ms": summarize_latencies(total_vals),
        "sum_persona_backfill_ms": sum_persona_ms,
    }
    if backfill_wall_ms > 0:
        out["backfill_wall_ms"] = backfill_wall_ms
        if sum_persona_ms > 0:
            out["backfill_parallel_efficiency"] = sum_persona_ms / backfill_wall_ms
    return out


def _save_backfill_report(
    run_id: str,
    mode: str,
    rows: list[dict[str, Any]],
    *,
    backfill_wall_ms: float = 0.0,
) -> None:
    out_dir = PROJECT_ROOT / "reports" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "backfill.json"
    sum_persona_ms = sum(float(r.get("backfill_total_ms") or 0) for r in rows)
    payload: dict[str, Any] = {
        "run_id": run_id,
        "mode": mode,
        "personas": rows,
        "backfill_wall_ms": backfill_wall_ms,
        "sum_persona_backfill_ms": sum_persona_ms,
    }
    if backfill_wall_ms > 0 and sum_persona_ms > 0:
        payload["backfill_parallel_efficiency"] = sum_persona_ms / backfill_wall_ms
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    logger.info("Saved backfill report: %s", path)


async def _backfill_one_persona(
    persona: LoCoMoPersona,
    *,
    adapter: TiMEMAdapter,
    run_id: str,
    layers: list[str],
    timeout: int,
) -> dict[str, Any]:
    user_id = make_user_id("timem", run_id, persona.persona_id)
    logger.info("Backfill %s ...", user_id)
    result = await adapter.backfill(user_id, layers)
    row = BackfillPersonaReport(
        persona_id=persona.persona_id,
        user_id=user_id,
        success=result.success,
        backfill_api_ms=result.latency_ms,
        error=result.error,
    )
    if not result.success:
        logger.warning("Backfill failed for %s: %s", user_id, result.error)
        row.backfill_total_ms = row.backfill_api_ms
        return asdict(row)
    wait_ms, counts = await _wait_backfill(adapter, user_id, layers, timeout)
    row.backfill_wait_ms = wait_ms
    row.backfill_total_ms = row.backfill_api_ms + wait_ms
    row.layer_counts = counts
    return asdict(row)


async def _timem_backfill_all(
    adapter: TiMEMAdapter,
    run_id: str,
    personas: list[LoCoMoPersona],
    settings: Any,
    layers: list[str],
    *,
    backfill_concurrency: int = 3,
) -> list[dict[str, Any]]:
    timeout = settings.benchmark_backfill_timeout_sec
    tasks = [
        (
            persona.persona_id,
            lambda p=persona: _backfill_one_persona(
                p,
                adapter=adapter,
                run_id=run_id,
                layers=layers,
                timeout=timeout,
            ),
        )
        for persona in personas
    ]
    results = await run_bounded_tasks(tasks, concurrency=backfill_concurrency)
    rows = [row for _, row in results]
    rows.sort(key=lambda r: r.get("persona_id") or "")
    return rows


async def _wait_backfill(
    adapter: TiMEMAdapter,
    user_id: str,
    layers: list[str],
    timeout_sec: int,
) -> tuple[float, dict[str, int]]:
    """Poll layer counts until L2+ exist or timeout. Returns (wait_ms, final_counts)."""
    started = time.perf_counter()
    elapsed = 0
    interval = 5
    target = [layer for layer in layers if layer != "L1"]
    counts: dict[str, int] = {}
    while elapsed < timeout_sec:
        counts = await adapter.count_layers(user_id)
        if all(counts.get(layer, 0) > 0 for layer in target):
            wait_ms = (time.perf_counter() - started) * 1000
            logger.info("Backfill ready for %s: %s", user_id, counts)
            return wait_ms, counts
        await asyncio.sleep(interval)
        elapsed += interval
    wait_ms = (time.perf_counter() - started) * 1000
    logger.warning("Backfill timeout for %s after %ss", user_id, timeout_sec)
    return wait_ms, counts


def _records_to_detail(
    records: list[MemoryRecord],
    *,
    top_k: int,
    per_record_tokens: list[int] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, r in enumerate(records[:top_k]):
        content = r.content or ""
        tc = (
            per_record_tokens[i]
            if per_record_tokens and i < len(per_record_tokens)
            else count_tokens(content)
        )
        out.append(
            {
                "content": content,
                "type": r.memory_type,
                "layer": r.layer,
                "score": r.score,
                "token_count": tc,
                "char_count": len(content),
            }
        )
    return out


async def _search_one_qa(
    job: _QAJob,
    *,
    adapter: MemoryAdapter,
    system: str,
    top_k: int,
    timem_overrides: dict[str, Any],
) -> dict[str, Any]:
    overrides = timem_overrides if system == "timem" else None
    result: SearchResult = await adapter.search(
        job.user_id,
        job.qa.question,
        top_k=top_k,
        overrides=overrides,
    )
    metrics = result.metrics or {}
    token_count = float(metrics.get("recalled_tokens") or 0)
    per_record_tokens = metrics.get("per_record_tokens") or []
    r5 = recall_at_k(result.records, job.qa.answer, 5)
    r10 = recall_at_k(result.records, job.qa.answer, 10)
    return {
        "persona_id": job.persona_id,
        "user_id": job.user_id,
        "question": job.qa.question,
        "gold": job.qa.answer,
        "category": job.qa.category,
        "latency_ms": result.latency_ms,
        "recalled_tokens": token_count,
        "recalled_chars": metrics.get("recalled_chars"),
        "layer_breakdown": metrics.get("layer_breakdown"),
        "result_count": result.result_count,
        "record_count": metrics.get("record_count"),
        "recall@5": r5,
        "recall@10": r10,
        "records": _records_to_detail(
            result.records,
            top_k=top_k,
            per_record_tokens=per_record_tokens if isinstance(per_record_tokens, list) else None,
        ),
        "error": result.error,
        "success": result.success,
        "empty": result.result_count == 0,
        "_memory_records": result.records,
    }


async def _judge_one_row(row: dict[str, Any], judge: ARKJudge) -> dict[str, Any]:
    records = row.pop("_memory_records", [])
    judge_result = await judge.judge_async(row["question"], row["gold"], records)
    row["judge"] = judge_result
    return row


async def _retrieve_personas(
    *,
    adapter: MemoryAdapter,
    system: str,
    run_id: str,
    mode: str,
    personas: list[LoCoMoPersona],
    top_k: int,
    judge: ARKJudge | None,
    preset: str,
    timem_overrides: dict[str, Any],
    backfill_summary: dict[str, Any],
    ret_cfg: Any,
) -> RetrievalRunReport:
    run_started = time.perf_counter()

    jobs: list[_QAJob] = []
    for persona in personas:
        user_id = make_user_id(system, run_id, persona.persona_id)
        for qa in persona.qa_pairs:
            jobs.append(_QAJob(persona_id=persona.persona_id, user_id=user_id, qa=qa))

    search_tasks = [
        (
            job.qa.question,
            lambda j=job: _search_one_qa(
                j,
                adapter=adapter,
                system=system,
                top_k=top_k,
                timem_overrides=timem_overrides,
            ),
        )
        for job in jobs
    ]

    query_concurrency = ret_cfg.query_concurrency_for(system)
    judge_fn = (lambda r: _judge_one_row(r, judge)) if judge else None
    if ret_cfg.pipeline_mode:
        qa_results = await run_search_judge_pipeline(
            search_tasks,
            query_concurrency=query_concurrency,
            judge_concurrency=ret_cfg.judge_concurrency if judge else 1,
            judge_row_fn=judge_fn,
        )
    else:
        qa_results = await run_search_judge_two_phase(
            search_tasks,
            query_concurrency=query_concurrency,
            judge_concurrency=ret_cfg.judge_concurrency if judge else 1,
            judge_row_fn=judge_fn,
        )
    rows = [row for _, row in qa_results]
    rows.sort(key=lambda r: (r.get("persona_id") or "", r.get("question") or ""))

    latencies = [float(r["latency_ms"]) for r in rows]
    recalled_tokens = [float(r["recalled_tokens"]) for r in rows]
    record_counts = [float(r.get("record_count") or 0) for r in rows]
    recall5 = [float(r["recall@5"]) for r in rows]
    recall10 = [float(r["recall@10"]) for r in rows]
    judge_hits = [1.0 if (r.get("judge") or {}).get("can_answer") else 0.0 for r in rows]
    judge_scores = [float((r.get("judge") or {}).get("score") or 0.0) for r in rows]
    judge_token_total = sum(int((r.get("judge") or {}).get("judge_tokens") or 0) for r in rows)
    judge_latency_total = sum(
        float((r.get("judge") or {}).get("judge_latency_ms") or 0.0) for r in rows
    )
    sum_search_ms = sum(latencies)
    sum_work_ms = sum_search_ms + judge_latency_total
    run_wall_ms = (time.perf_counter() - run_started) * 1000
    success = sum(1 for r in rows if r.get("success"))
    empty = sum(1 for r in rows if r.get("empty"))

    def _avg(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    token_stats = summarize_latencies(recalled_tokens)
    nonempty_tokens = [t for t in recalled_tokens if t > 0]
    nonempty_stats = summarize_latencies(nonempty_tokens)

    details: list[dict[str, Any]] = []
    for row in rows:
        clean = {k: v for k, v in row.items() if not k.startswith("_")}
        details.append(clean)

    return RetrievalRunReport(
        run_id=run_id,
        system=system,
        mode=mode,
        preset=preset,
        top_k=top_k,
        query_count=len(rows),
        success_count=success,
        empty_count=empty,
        latency=summarize_latencies(latencies),
        recall_at_5=_avg(recall5),
        recall_at_10=_avg(recall10),
        judge_accuracy=_avg(judge_hits),
        judge_avg_score=_avg(judge_scores),
        avg_recalled_tokens=token_stats["mean"],
        p50_recalled_tokens=token_stats["p50"],
        p95_recalled_tokens=token_stats["p95"],
        min_recalled_tokens=token_stats["min"],
        max_recalled_tokens=token_stats["max"],
        avg_recalled_tokens_nonempty=nonempty_stats["mean"],
        p50_recalled_tokens_nonempty=nonempty_stats["p50"],
        avg_record_count=_avg(record_counts),
        total_judge_tokens=judge_token_total,
        total_judge_latency_ms=judge_latency_total,
        sum_search_latency_ms=sum_search_ms,
        sum_work_ms=sum_work_ms,
        run_wall_ms=run_wall_ms,
        concurrency_settings=ret_cfg.to_report_dict(system=system),
        backfill_summary=backfill_summary,
        timem_overrides=timem_overrides,
        details=details,
    )


def main() -> None:
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run_id = None
    mode = "T0"
    use_fixture = False
    args = sys.argv[1:]
    if args and not args[0].startswith("-"):
        run_id = args[0]
    if "--T1" in args:
        mode = "T1"
    if "--fixture" in args:
        use_fixture = True
    if not run_id:
        print("Usage: python -m runners.retrieval_run <run_id> [--T0|--T1] [--fixture]")
        print("  run_id must match a prior ingest run (same user_id prefix)")
        sys.exit(1)

    reports = asyncio.run(
        run_retrieval(run_id=run_id, mode=mode, use_fixture=use_fixture)
    )
    for name, report in reports.items():
        print(f"\n=== {name} retrieval {mode} ===")
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
