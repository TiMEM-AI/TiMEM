"""Graph search: lexical, vector, and fulltext seeds fused by RRF, then one hop."""

from __future__ import annotations

from typing import Dict, List, Sequence

RRF_K = 60


def rrf_fuse_seed_ids(*ranked_lists: Sequence[str], limit: int) -> List[str]:
    """Same rank fusion as the platform graph search. k = 60."""
    scores: Dict[str, float] = {}
    for ranked in ranked_lists:
        seen = set()
        for rank, raw in enumerate(ranked, start=1):
            seed = str(raw or "").strip()
            if not seed or seed in seen:
                continue
            seen.add(seed)
            scores[seed] = scores.get(seed, 0.0) + 1.0 / (RRF_K + rank)
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [node_id for node_id, _ in ordered[: max(limit, 0)]]


def empty_view(query: str) -> dict:
    return {"query": query, "nodes": [], "edges": [], "answer": None}


async def search_graph(
    store,
    *,
    user_id: str,
    expert_id: str,
    query: str,
    limit: int = 20,
    query_vector: List[float] | None = None,
) -> dict:
    """Return the 1-hop subgraph. answer stays empty."""
    text = (query or "").strip()
    if limit <= 0 or not text or not getattr(store, "enabled", False):
        return empty_view(text)
    seed_limit = max(limit * 2, limit)
    lexical = await _safe(store.lexical_seed_ids, user_id, expert_id, text, seed_limit)
    fulltext = await _safe(store.fulltext_seed_ids, user_id, expert_id, text, seed_limit)
    vector: List[str] = []
    if query_vector:
        vector = await _safe(store.vector_seed_ids, user_id, expert_id, query_vector, seed_limit)
    seeds = rrf_fuse_seed_ids(lexical, vector, fulltext, limit=limit) or lexical[:limit]
    if not seeds:
        return empty_view(text)
    expanded = await store.expand(user_id, expert_id, seeds, 1, limit)
    return {
        "query": text,
        "nodes": expanded.get("nodes") or [],
        "edges": expanded.get("edges") or [],
        "answer": None,
    }


async def run_search(user_id: str, query: str, *, expert_id: str = "assistant", limit: int = 20) -> dict:
    from timem.memory_graph.build import safe_embed
    from timem.memory_graph.store import get_store

    vector = None
    try:
        vector = await safe_embed(query)
    except Exception:
        vector = None
    return await search_graph(
        get_store(),
        user_id=user_id,
        expert_id=expert_id,
        query=query,
        limit=limit,
        query_vector=vector,
    )


async def _safe(method, *args):
    try:
        return list(await method(*args))
    except Exception:
        return []
