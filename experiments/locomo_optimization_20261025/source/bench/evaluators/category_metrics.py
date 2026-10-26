"""LoCoMo category-level metric aggregation."""

from __future__ import annotations

from typing import Any

from evaluators.latency import summarize_latencies

CATEGORY_MAP: dict[Any, str] = {
    1: "single_hop",
    2: "temporal",
    3: "multi_hop",
    4: "open_domain",
    "1": "single_hop",
    "2": "temporal",
    "3": "multi_hop",
    "4": "open_domain",
    "single-hop": "single_hop",
    "single_hop": "single_hop",
    "multi-hop": "multi_hop",
    "multi_hop": "multi_hop",
    "temporal": "temporal",
    "open-domain": "open_domain",
    "open_domain": "open_domain",
}


def normalize_category(raw: Any) -> str:
    if raw is None:
        return "unknown"
    if raw in CATEGORY_MAP:
        return CATEGORY_MAP[raw]
    key = str(raw).strip().lower().replace(" ", "_")
    return CATEGORY_MAP.get(key, key or "unknown")


def aggregate_by_category(details: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate per-query detail rows by LoCoMo category."""
    buckets: dict[str, dict[str, list[float]]] = {}

    for row in details:
        cat = normalize_category(row.get("category"))
        if cat not in buckets:
            buckets[cat] = {
                "latencies": [],
                "recalled_tokens": [],
                "recall10": [],
                "judge_hits": [],
                "judge_scores": [],
            }
        b = buckets[cat]
        if row.get("latency_ms") is not None:
            b["latencies"].append(float(row["latency_ms"]))
        if row.get("recalled_tokens") is not None:
            b["recalled_tokens"].append(float(row["recalled_tokens"]))
        if row.get("recall@10") is not None:
            b["recall10"].append(float(row["recall@10"]))
        judge = row.get("judge") or {}
        if judge.get("can_answer") is not None:
            b["judge_hits"].append(1.0 if judge.get("can_answer") else 0.0)
        if judge.get("score") is not None:
            b["judge_scores"].append(float(judge.get("score", 0.0)))

    def _avg(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    out: dict[str, dict[str, Any]] = {}
    for cat, b in buckets.items():
        out[cat] = {
            "query_count": max(
                len(b["latencies"]),
                len(b["recalled_tokens"]),
                len(b["recall10"]),
                len(b["judge_hits"]),
            ),
            "latency": summarize_latencies(b["latencies"]),
            "avg_recalled_tokens": _avg(b["recalled_tokens"]),
            "recall_at_10": _avg(b["recall10"]),
            "judge_accuracy": _avg(b["judge_hits"]),
            "judge_avg_score": _avg(b["judge_scores"]),
        }
    return out


def build_category_summary(
    retrieval_reports: dict[str, Any],
    *,
    mode: str = "T0",
) -> dict[str, Any]:
    """Build summary_by_category.json payload for all systems."""
    systems: dict[str, Any] = {}
    for system, report in retrieval_reports.items():
        if isinstance(report, dict):
            details = report.get("details") or []
        else:
            details = getattr(report, "details", []) or []
        systems[system] = aggregate_by_category(details)
    return {"mode": mode, "systems": systems}
