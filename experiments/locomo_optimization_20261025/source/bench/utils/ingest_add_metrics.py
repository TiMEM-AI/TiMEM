"""Per-add (pair) ingest latency aggregation."""

from __future__ import annotations

from typing import Any

from evaluators.latency import summarize_latencies


def add_latencies_from_session_detail(detail: dict[str, Any]) -> list[float]:
    """Expand one session detail row into per-add latency values."""
    pair_details = detail.get("pair_details")
    if isinstance(pair_details, list) and pair_details:
        return [
            float(p.get("latency_ms") or 0)
            for p in pair_details
            if p.get("success", True)
        ]

    if not detail.get("success", False):
        return []

    api_calls = int(detail.get("api_calls") or detail.get("pair_count") or 1)
    if api_calls <= 0:
        api_calls = 1
    session_ms = float(detail.get("latency_ms") or 0)
    per_add = session_ms / api_calls
    return [per_add] * api_calls


def enrich_session_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Add avg_add_latency_ms; ensure pair_details estimated rows if missing."""
    out = dict(detail)
    api_calls = int(out.get("api_calls") or out.get("pair_count") or 0)
    session_ms = float(out.get("latency_ms") or 0)
    if api_calls > 0 and out.get("success"):
        out["avg_add_latency_ms"] = session_ms / api_calls
    else:
        out["avg_add_latency_ms"] = 0.0

    if out.get("ingest_mode") == "pair" and api_calls > 0 and not out.get("pair_details"):
        per_add = session_ms / api_calls if out.get("success") else 0.0
        out["pair_details"] = [
            {
                "pair_index": i,
                "post_ms": per_add,
                "poll_ms": 0.0,
                "latency_ms": per_add,
                "success": bool(out.get("success")),
                "estimated": True,
            }
            for i in range(api_calls)
        ]
    elif out.get("ingest_mode") == "session" and out.get("success"):
        out.setdefault(
            "pair_details",
            [
                {
                    "pair_index": 0,
                    "post_ms": session_ms,
                    "poll_ms": 0.0,
                    "latency_ms": session_ms,
                    "success": True,
                }
            ],
        )
    return out


def aggregate_add_metrics(
    details: list[dict[str, Any]],
    *,
    estimated: bool = False,
) -> dict[str, Any]:
    """Build add-level summary from session detail rows."""
    enriched = [enrich_session_detail(d) for d in details]
    session_latencies = [float(d["latency_ms"]) for d in enriched if d.get("latency_ms") is not None]

    add_latencies: list[float] = []
    add_success = 0
    for row in enriched:
        adds = add_latencies_from_session_detail(row)
        add_latencies.extend(adds)
        add_success += len(adds)

    session_success = sum(1 for d in enriched if d.get("success"))
    add_latency = summarize_latencies(add_latencies)
    session_latency = summarize_latencies(session_latencies)

    return {
        "details": enriched,
        "add_count": len(add_latencies),
        "add_success_count": add_success,
        "add_latency": add_latency,
        "session_latency": session_latency,
        "latency": add_latency,
        "latency_granularity": "add",
        "add_latency_estimated": estimated,
        "sum_latency_ms": sum(add_latencies),
        "session_count": len(enriched),
        "success_count": session_success,
        "failure_count": len(enriched) - session_success,
    }
