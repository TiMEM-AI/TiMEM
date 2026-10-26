"""Standalone TiMEM backfill job (dashboard manual layer selection)."""

from __future__ import annotations

import time
from typing import Any

from adapters.registry import close_adapters, get_adapters
from adapters.timem_adapter import TiMEMAdapter
from benchmark_data.locomo_loader import load_locomo_personas
from runners.retrieval_run import _save_backfill_report, _timem_backfill_all
from utils.config import get_settings, resolve_retrieval_config

VALID_BACKFILL_LAYERS = ("L2", "L3", "L4", "L5")


def normalize_backfill_layers(layers: list[str] | str | None) -> list[str]:
    """Normalize and validate manual backfill layer list."""
    if layers is None:
        raise ValueError("backfill_layers is required")
    if isinstance(layers, str):
        raw = [part.strip() for part in layers.split(",") if part.strip()]
    else:
        raw = list(layers)
    if not raw:
        raise ValueError("backfill_layers cannot be empty")
    valid = {layer.upper() for layer in VALID_BACKFILL_LAYERS}
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        layer = str(item).strip().upper()
        if layer not in valid:
            raise ValueError(f"invalid backfill layer {item!r}; allowed: {list(VALID_BACKFILL_LAYERS)}")
        if layer not in seen:
            seen.add(layer)
            out.append(layer)
    return out


async def run_timem_backfill(
    *,
    run_id: str,
    layers: list[str] | str,
    use_fixture: bool = False,
    persona_count: int | None = None,
    backfill_concurrency: int | None = None,
    retrieval_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run TiMEM backfill for all personas; write reports/{run_id}/backfill.json."""
    settings = get_settings()
    normalized = normalize_backfill_layers(layers)
    persona_count = persona_count or settings.benchmark_persona_count
    ret_cfg = resolve_retrieval_config(retrieval_overrides)
    concurrency = backfill_concurrency if backfill_concurrency is not None else ret_cfg.backfill_concurrency

    personas = load_locomo_personas(persona_count=persona_count, use_fixture=use_fixture)
    adapters = get_adapters(["timem"])
    adapter = adapters.get("timem")
    if not isinstance(adapter, TiMEMAdapter):
        raise RuntimeError("TiMEM adapter unavailable")

    started = time.perf_counter()
    try:
        rows = await _timem_backfill_all(
            adapter,
            run_id,
            personas,
            settings,
            normalized,
            backfill_concurrency=concurrency,
        )
    finally:
        await close_adapters(adapters)

    wall_ms = (time.perf_counter() - started) * 1000
    _save_backfill_report(run_id, "manual", rows, backfill_wall_ms=wall_ms)
    success_count = sum(1 for row in rows if row.get("success"))
    return {
        "run_id": run_id,
        "mode": "manual",
        "backfill_layers": normalized,
        "persona_count": len(personas),
        "success_count": success_count,
        "failure_count": len(rows) - success_count,
        "backfill_wall_ms": wall_ms,
        "details": rows,
    }
