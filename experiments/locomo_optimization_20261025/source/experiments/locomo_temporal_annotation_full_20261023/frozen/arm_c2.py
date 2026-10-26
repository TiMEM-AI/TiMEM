"""C2 arm: frozen A0 parser/routing/mentions, rendered with the spec field format.

Differences from C1 are limited to the annotation body: `anchor_date`, `span`
and `status` are added. Parser, routing, mention order, per-row dedupe and the
budget rule stay exactly as in the frozen A0 implementation.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from arm_c1 import a0  # noqa: E402

HEADER = "Calendar annotations derived from the original turns:"
DEFAULT_BUDGET_CHARS = 8000


def _line(mention) -> str:
    span = f"[{mention.span[0]},{mention.span[1]})"
    return (
        f"- source_id={mention.source_id}; span={span}; "
        f'raw_phrase="{mention.raw_phrase}"; observed_at={mention.observed_at}; '
        f"anchor_date={mention.anchor_date}; precision={mention.precision}; "
        f"value={mention.normalized_value}; rule={mention.rule_id}; status=resolved"
    )


def annotate(rows: Sequence[dict], turns: Dict[str, dict], query: str, budget_chars: int = DEFAULT_BUDGET_CHARS) -> Tuple[List[dict], List[dict], bool]:
    plan = a0.plan_date_query(query)
    out = [dict(row) for row in rows]
    if plan is None:
        return out, [], False

    turns_by_id = {
        sid: a0.SourceTurn(
            source_id=item["source_id"], user_id=item.get("user_id", ""),
            role=item.get("role", "user"), content=item.get("content", ""),
            observed_at=item.get("observed_at", ""),
        )
        for sid, item in turns.items()
    }

    per_row: List[Tuple[int, List[str], list]] = []
    trace: List[dict] = []
    for index, row in enumerate(out):
        content = str(row.get("content") or "")
        if a0.ANNOTATION_HEADER in content:
            continue
        if not content.lstrip().startswith("Original conversation evidence:"):
            continue
        source_ids = row.get("source_ids") or []
        if not isinstance(source_ids, (list, tuple)):
            continue
        lines, used, seen = [], [], set()
        for sid in source_ids:
            turn = turns_by_id.get(str(sid))
            if turn is None:
                continue
            for mention in a0.parse_time_mentions(turn):
                if mention.status != "resolved" or not mention.normalized_value:
                    continue
                key = (mention.source_id, mention.span, mention.rule_id)
                if key in seen:
                    continue
                seen.add(key)
                lines.append(_line(mention))
                used.append(mention)
        if lines:
            per_row.append((index, lines, used))

    if not per_row:
        return out, trace, False

    total = sum(len(line) + 1 for _, lines, _ in per_row for line in lines) + len(HEADER) + 2
    if total > budget_chars:
        return out, trace, False

    for index, lines, used in per_row:
        block = "\n\n" + HEADER + "\n" + "\n".join(lines)
        out[index]["content"] = str(out[index].get("content") or "") + block
        for mention in used:
            trace.append({
                "schema_version": "temporal-annotation-v2-format-only",
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
    return out, trace, True
