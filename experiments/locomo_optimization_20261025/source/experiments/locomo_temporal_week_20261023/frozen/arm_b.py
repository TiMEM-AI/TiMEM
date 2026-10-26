"""B arm: frozen C1 (A0) from this run's own frozen copy."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

FROZEN_A0 = Path(__file__).resolve().parents[1] / "frozen" / "a0_temporal_evidence.py"

_spec = importlib.util.spec_from_file_location("week_run_a0", FROZEN_A0)
a0 = importlib.util.module_from_spec(_spec)
sys.modules["week_run_a0"] = a0
_spec.loader.exec_module(a0)


def annotate(rows: Sequence[dict], turns: Dict[str, dict], query: str, budget_chars: int = 8000) -> Tuple[List[dict], List[dict], bool]:
    plan = a0.plan_date_query(query)
    if plan is None:
        return list(rows), [], False
    turns_by_id = {
        sid: a0.SourceTurn(
            source_id=item.get("source_id", sid), user_id=item.get("user_id", ""),
            role=item.get("role", "user"), content=item.get("content", ""),
            observed_at=item.get("observed_at", ""))
        for sid, item in turns.items()
    }
    result = a0.render_date_evidence(plan, rows, turns_by_id, budget_chars=budget_chars)
    trace = [{
        "schema_version": "a0-calendar-v1",
        "source_id": m.source_id,
        "span": list(m.span),
        "raw_phrase": m.raw_phrase,
        "observed_at": m.observed_at,
        "anchor_date": m.anchor_date,
        "precision": m.precision,
        "value": m.normalized_value,
        "rule": m.rule_id,
        "status": "resolved",
    } for m in result.annotations]
    return (list(result.rows) if result.changed else list(rows)), trace, result.changed
