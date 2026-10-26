"""C1 arm: the frozen A0 implementation, loaded from its immutable snapshot.

No behaviour changes here on purpose: this arm must reproduce the A0 evidence
records that were produced in the previous experiment.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

FROZEN_PATH = Path(r"F:\codex数据库memory\locomo_temporal_v2_20261023\patch\temporal_evidence.py")

import sys as _sys

_spec = importlib.util.spec_from_file_location("frozen_a0_impl", FROZEN_PATH)
a0 = importlib.util.module_from_spec(_spec)
_sys.modules["frozen_a0_impl"] = a0
_spec.loader.exec_module(a0)


def _turn(item: dict):
    return a0.SourceTurn(
        source_id=item["source_id"],
        user_id=item.get("user_id", ""),
        role=item.get("role", "user"),
        content=item.get("content", ""),
        observed_at=item.get("observed_at", ""),
    )


def annotate(rows: Sequence[dict], turns: Dict[str, dict], query: str, budget_chars: int = 8000) -> Tuple[List[dict], List[dict], bool]:
    plan = a0.plan_date_query(query)
    if plan is None:
        return list(rows), [], False
    turns_by_id = {sid: _turn(item) for sid, item in turns.items()}
    result = a0.render_date_evidence(plan, rows, turns_by_id, budget_chars=budget_chars)
    trace = []
    for mention in result.annotations:
        trace.append({
            "schema_version": "a0-calendar-v1",
            "source_id": mention.source_id,
            "span": list(mention.span),
            "raw_phrase": mention.raw_phrase,
            "observed_at": mention.observed_at,
            "anchor_date": mention.anchor_date,
            "precision": mention.precision,
            "value": mention.normalized_value,
            "rule": mention.rule_id,
            "status": "resolved",
        })
    return (list(result.rows) if result.changed else list(rows)), trace, result.changed
