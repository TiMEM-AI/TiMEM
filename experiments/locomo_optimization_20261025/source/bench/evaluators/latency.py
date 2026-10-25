"""Latency statistics."""

from __future__ import annotations

import statistics
from typing import Iterable


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def summarize_latencies(latencies_ms: Iterable[float]) -> dict[str, float]:
    vals = list(latencies_ms)
    if not vals:
        return {"count": 0, "p50": 0.0, "p95": 0.0, "mean": 0.0, "min": 0.0, "max": 0.0}
    return {
        "count": len(vals),
        "p50": percentile(vals, 50),
        "p95": percentile(vals, 95),
        "mean": statistics.mean(vals),
        "min": min(vals),
        "max": max(vals),
    }
