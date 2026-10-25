"""W0 arm: frozen C1 plus `last week` / `last weekend` anchoring (no range)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import arm_b  # noqa: E402
import week_ext  # noqa: E402


def annotate(rows: Sequence[dict], turns: Dict[str, dict], query: str, budget_chars: int = 8000) -> Tuple[List[dict], List[dict], bool]:
    if arm_b.a0.plan_date_query(query) is None:
        return list(rows), [], False
    c1_rows, trace_b, _ = arm_b.annotate(rows, turns, query, budget_chars=budget_chars)
    week_rows, trace_w, changed = week_ext.add_week_anchors(
        c1_rows, rows, turns, with_range=False, budget_chars=budget_chars)
    return week_rows, trace_b + trace_w, changed
