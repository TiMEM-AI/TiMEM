"""Unified token counting for benchmark metrics."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Iterable

from models.records import MemoryRecord

ENCODING_NAME = "cl100k_base"


@lru_cache(maxsize=1)
def _get_encoding():
    try:
        import tiktoken

        return tiktoken.get_encoding(ENCODING_NAME)
    except Exception:
        return None


def count_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _get_encoding()
    if enc is not None:
        return len(enc.encode(text))
    return max(1, len(text) // 4)


def count_messages_tokens(messages: Iterable[Any]) -> int:
    parts: list[str] = []
    for m in messages:
        if hasattr(m, "content"):
            parts.append(str(m.content or ""))
        elif isinstance(m, dict):
            parts.append(str(m.get("content") or ""))
    return count_tokens("\n".join(parts))


def recalled_tokens_from_records(
    records: list[MemoryRecord],
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    items = records[:limit] if limit is not None else records
    texts = [r.content for r in items if r.content]
    joined = "\n".join(texts)
    layer_breakdown: dict[str, int] = {}
    for r in items:
        layer = str(r.layer or "unknown")
        layer_breakdown[layer] = layer_breakdown.get(layer, 0) + count_tokens(r.content)
    return {
        "recalled_tokens": count_tokens(joined),
        "recalled_chars": len(joined),
        "layer_breakdown": layer_breakdown,
        "record_count": len(items),
        "per_record_tokens": [count_tokens(r.content) for r in items if r.content],
    }


def attach_search_metrics(result: Any, *, top_k: int | None = None) -> None:
    """Populate SearchResult.metrics from retrieved records."""
    metrics = recalled_tokens_from_records(result.records, limit=top_k)
    result.metrics = metrics


def attach_ingest_metrics(result: Any, messages: Iterable[Any]) -> None:
    """Populate IngestResult.metrics with input dialogue token estimate."""
    input_tokens = count_messages_tokens(messages)
    result.metrics = {
        "input_tokens": input_tokens,
        "input_chars": sum(
            len(str(getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else "")))
            for m in messages
        ),
    }
