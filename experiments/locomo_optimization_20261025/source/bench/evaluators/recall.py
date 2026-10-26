"""Recall@K and keyword overlap metrics."""

from __future__ import annotations

import re
from typing import Iterable

from models.records import MemoryRecord


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]+", text) if len(t) > 1}


def recall_at_k(records: list[MemoryRecord], gold_answer: str, k: int) -> float:
    """1.0 if any gold token appears in top-k concatenated content, else 0.0."""
    gold = _tokens(gold_answer)
    if not gold:
        return 0.0
    top = records[:k]
    blob = " ".join(r.content for r in top).lower()
    hits = sum(1 for g in gold if g in blob)
    return 1.0 if hits >= max(1, len(gold) // 2) else 0.0


def batch_recall(results: Iterable[tuple[list[MemoryRecord], str]], k: int) -> dict[str, float]:
    scores = [recall_at_k(records, gold, k) for records, gold in results]
    if not scores:
        return {"count": 0, f"recall@{k}": 0.0}
    return {"count": len(scores), f"recall@{k}": sum(scores) / len(scores)}
