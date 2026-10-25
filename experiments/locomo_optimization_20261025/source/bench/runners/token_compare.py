"""Cross-system per-question token comparison report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from utils.config import PROJECT_ROOT


def build_token_compare(
    reports_by_system: dict[str, Any],
    *,
    mode: str,
    run_id: str,
) -> dict[str, Any]:
    """Align retrieval details by question and compute token deltas."""
    systems = list(reports_by_system.keys())
    if not systems:
        return {"run_id": run_id, "mode": mode, "questions": [], "summary": {}}

    question_map: dict[str, dict[str, Any]] = {}

    for system in systems:
        block = reports_by_system[system]
        details = block.get("details") if isinstance(block, dict) else getattr(block, "details", [])
        for row in details or []:
            q = row.get("question") or ""
            if not q:
                continue
            if q not in question_map:
                question_map[q] = {
                    "question": q,
                    "gold": row.get("gold"),
                    "category": row.get("category"),
                    "persona_id": row.get("persona_id"),
                    "systems": {},
                }
            entry = question_map[q]
            entry["gold"] = row.get("gold") or entry.get("gold")
            entry["category"] = row.get("category") or entry.get("category")
            entry["systems"][system] = {
                "recalled_tokens": row.get("recalled_tokens"),
                "recall@5": row.get("recall@5"),
                "recall@10": row.get("recall@10"),
                "latency_ms": row.get("latency_ms"),
                "result_count": row.get("result_count"),
                "record_count": row.get("record_count"),
                "judge": row.get("judge") or {},
            }

    questions: list[dict[str, Any]] = []
    for q, entry in sorted(question_map.items(), key=lambda x: x[0]):
        token_delta: dict[str, float | None] = {}
        tokens_by_sys = {
            s: (entry["systems"].get(s) or {}).get("recalled_tokens") for s in systems
        }
        for i, s1 in enumerate(systems):
            for s2 in systems[i + 1 :]:
                t1 = tokens_by_sys.get(s1)
                t2 = tokens_by_sys.get(s2)
                key = f"{s1}_vs_{s2}"
                if t1 is not None and t2 is not None:
                    token_delta[key] = float(t1) - float(t2)
                else:
                    token_delta[key] = None
        entry["token_delta"] = token_delta
        questions.append(entry)

    summary = _build_summary(reports_by_system, systems, questions)
    return {
        "run_id": run_id,
        "mode": mode,
        "systems": systems,
        "question_count": len(questions),
        "questions": questions,
        "summary": summary,
    }


def _build_summary(
    reports_by_system: dict[str, Any],
    systems: list[str],
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    avg_tokens_by_system: dict[str, float] = {}
    efficiency_score: dict[str, float] = {}

    for system in systems:
        block = reports_by_system[system]
        if isinstance(block, dict):
            avg_tokens_by_system[system] = float(block.get("avg_recalled_tokens") or 0)
            judge_acc = float(block.get("judge_accuracy") or 0)
        else:
            avg_tokens_by_system[system] = float(getattr(block, "avg_recalled_tokens", 0) or 0)
            judge_acc = float(getattr(block, "judge_accuracy", 0) or 0)
        avg_tok = avg_tokens_by_system[system]
        efficiency_score[system] = (judge_acc / avg_tok * 1000) if avg_tok > 0 else 0.0

    cat_acc: dict[str, dict[str, list[float]]] = {}
    for row in questions:
        cat = str(row.get("category") or "unknown")
        cat_acc.setdefault(cat, {})
        for system, sdata in row.get("systems", {}).items():
            tok = sdata.get("recalled_tokens")
            if tok is None:
                continue
            cat_acc[cat].setdefault(system, []).append(float(tok))
    avg_tokens_by_category = {
        cat: {sys: sum(v) / len(v) for sys, v in smap.items()}
        for cat, smap in cat_acc.items()
    }

    return {
        "avg_tokens_by_system": avg_tokens_by_system,
        "efficiency_score": efficiency_score,
        "avg_tokens_by_category": avg_tokens_by_category,
    }


def save_token_compare(
    run_id: str,
    mode: str,
    reports_by_system: dict[str, Any],
) -> Path:
    payload = build_token_compare(reports_by_system, mode=mode, run_id=run_id)
    out_dir = PROJECT_ROOT / "reports" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"token_compare_{mode}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path
