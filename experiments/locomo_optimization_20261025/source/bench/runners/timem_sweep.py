"""TiMEM-only parameter sweep runner."""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from adapters.registry import close_adapters, get_adapters
from adapters.timem_adapter import TiMEMAdapter
from benchmark_data.locomo_loader import load_locomo_personas
from runners.ingest_run import run_ingest
from runners.retrieval_run import _timem_backfill_all
from utils.config import PROJECT_ROOT, get_settings, resolve_benchmark_config, resolve_retrieval_config
from utils.ids import new_run_id

logger = logging.getLogger(__name__)

SWEEP_PARAM_KEYS = (
    "search_mode",
    "use_hybrid",
    "enable_memories_rethink",
    "score_threshold",
    "top_k",
)


def parse_sweep_values(raw_values: list[str]) -> dict[str, list[Any]]:
    """Parse CLI --values key=v1,v2 into a dict of param -> value list."""
    parsed: dict[str, list[Any]] = {}
    for item in raw_values:
        if "=" not in item:
            continue
        key, values_str = item.split("=", 1)
        key = key.strip()
        values: list[Any] = []
        for part in values_str.split(","):
            part = part.strip()
            if part.lower() in ("true", "false"):
                values.append(part.lower() == "true")
            else:
                try:
                    if "." in part:
                        values.append(float(part))
                    else:
                        values.append(int(part))
                except ValueError:
                    values.append(part)
        if values:
            parsed[key] = values
    return parsed


def build_param_grid(
    params: list[str],
    value_map: dict[str, list[Any]],
    *,
    top_k_default: int = 10,
) -> list[dict[str, Any]]:
    axes: list[tuple[str, list[Any]]] = []
    for param in params:
        if param == "top_k":
            axes.append(("top_k", value_map.get("top_k", [top_k_default])))
        elif param in SWEEP_PARAM_KEYS:
            axes.append((param, value_map.get(param, _default_values(param))))
    if not axes:
        axes = [
            ("search_mode", value_map.get("search_mode", ["semantic", "enhanced_semantic"])),
            ("use_hybrid", value_map.get("use_hybrid", [True, False])),
        ]
    keys = [k for k, _ in axes]
    value_lists = [vals for _, vals in axes]
    grid: list[dict[str, Any]] = []
    for combo in itertools.product(*value_lists):
        entry = dict(zip(keys, combo))
        if "top_k" not in entry:
            entry["top_k"] = top_k_default
        grid.append(entry)
    return grid


def _default_values(param: str) -> list[Any]:
    defaults = {
        "search_mode": ["semantic", "enhanced_semantic"],
        "use_hybrid": [True, False],
        "enable_memories_rethink": [False, True],
        "score_threshold": [0.0],
        "top_k": [10],
    }
    return defaults.get(param, [None])


async def run_timem_sweep(
    *,
    sweep_id: str | None = None,
    mode: Literal["T0", "T1"] = "T1",
    use_fixture: bool = False,
    persona_count: int | None = None,
    run_judge: bool = True,
    skip_backfill: bool = False,
    skip_ingest: bool = False,
    params: list[str] | None = None,
    value_map: dict[str, list[Any]] | None = None,
    preset: str | None = None,
    backfill_layers: list[str] | None = None,
) -> dict[str, Any]:
    from runners.retrieval_run import _retrieve_personas
    from evaluators.ark_judge import ARKJudge

    settings = get_settings()
    sweep_id = sweep_id or f"sweep_{new_run_id()}"
    persona_count = persona_count or (1 if use_fixture else settings.benchmark_persona_count)
    run_cfg = resolve_benchmark_config(preset=preset, mode=mode, backfill_layers=backfill_layers)
    ret_cfg = resolve_retrieval_config()
    params = params or ["search_mode", "use_hybrid"]
    grid = build_param_grid(params, value_map or {}, top_k_default=run_cfg.top_k)

    if not skip_ingest:
        await run_ingest(
            run_id=sweep_id,
            systems=["timem"],
            persona_count=persona_count,
            use_fixture=use_fixture,
        )

    personas = load_locomo_personas(persona_count=persona_count, use_fixture=use_fixture)
    adapters = get_adapters(["timem"])
    adapter = adapters["timem"]
    assert isinstance(adapter, TiMEMAdapter)

    backfill_report: list[dict[str, Any]] = []
    try:
        if mode == "T1" and not skip_backfill:
            backfill_report = await _timem_backfill_all(
                adapter,
                sweep_id,
                personas,
                settings,
                run_cfg.backfill_layers,
            )

        judge = ARKJudge() if run_judge else None
        matrix: list[dict[str, Any]] = []

        for combo in grid:
            top_k = int(combo.get("top_k", run_cfg.top_k))
            overrides = {
                k: combo[k]
                for k in ("search_mode", "use_hybrid", "enable_memories_rethink", "score_threshold")
                if k in combo
            }
            logger.info("Sweep combo: %s", overrides | {"top_k": top_k})
            report = await _retrieve_personas(
                adapter=adapter,
                system="timem",
                run_id=sweep_id,
                mode=mode,
                personas=personas,
                top_k=top_k,
                judge=judge,
                preset=run_cfg.preset,
                timem_overrides=overrides,
                backfill_summary={},
                ret_cfg=ret_cfg,
            )
            matrix.append(
                {
                    "params": combo,
                    "timem_overrides": overrides,
                    "top_k": top_k,
                    "metrics": {
                        "recall_at_5": report.recall_at_5,
                        "recall_at_10": report.recall_at_10,
                        "judge_accuracy": report.judge_accuracy,
                        "judge_avg_score": report.judge_avg_score,
                        "avg_recalled_tokens": report.avg_recalled_tokens,
                        "p50_recalled_tokens": report.p50_recalled_tokens,
                        "latency_p50": report.latency.get("p50"),
                        "latency_mean": report.latency.get("mean"),
                        "empty_count": report.empty_count,
                        "run_wall_ms": report.run_wall_ms,
                    },
                }
            )
    finally:
        await close_adapters(adapters)

    payload = {
        "sweep_id": sweep_id,
        "mode": mode,
        "preset": run_cfg.preset,
        "persona_count": persona_count,
        "use_fixture": use_fixture,
        "skip_backfill": skip_backfill,
        "backfill": backfill_report,
        "matrix": matrix,
    }
    out_dir = PROJECT_ROOT / "reports" / sweep_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "sweep_matrix.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    logger.info("Saved sweep matrix: %s", path)
    return payload
