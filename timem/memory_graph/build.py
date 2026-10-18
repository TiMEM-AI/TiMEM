"""Turn an L3 memory's child L2 rows into episodes. One failure does not stop the rest."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional

from timem.memory_graph.episode import (
    build_facts,
    content_hash,
    entity_id,
    episode_id,
    episode_key,
    episode_name,
)
from timem.memory_graph.extract import extract_event
from timem.utils.logging import get_logger

logger = get_logger(__name__)

LoadMemory = Callable[[str], Awaitable[Optional[Dict[str, Any]]]]
Complete = Callable[[str], Awaitable[str]]


def child_ids(memory: Any) -> List[str]:
    if memory is None:
        return []
    raw = memory.get("child_memory_ids") if isinstance(memory, dict) else getattr(memory, "child_memory_ids", None)
    if not isinstance(raw, list):
        return []
    ids = []
    for item in raw:
        text = str(item or "").strip()
        if text and text not in ids:
            ids.append(text)
    return ids


def memory_text(memory: Any, field: str) -> str:
    if isinstance(memory, dict):
        value = memory.get(field)
    else:
        value = getattr(memory, field, None)
    return str(value or "").strip()


def memory_level(memory: Any) -> str:
    level = memory.get("level") if isinstance(memory, dict) else getattr(memory, "level", None)
    if hasattr(level, "value"):
        level = level.value
    return str(level or "").replace("MemoryLevel.", "").strip()


async def l2_children(memory: Any, load_memory: LoadMemory) -> List[Dict[str, str]]:
    rows = []
    for child_id in child_ids(memory):
        loaded = await load_memory(child_id)
        if loaded is None:
            continue
        level = memory_level(loaded)
        if level and level != "L2":
            continue
        content = memory_text(loaded, "content")
        if not content:
            continue
        rows.append({"id": child_id, "content": content})
    return rows


async def ingest_l2_rows(
    *,
    user_id: str,
    expert_id: str,
    report_date: str,
    rows: List[Dict[str, str]],
    store,
    complete: Complete,
    embed: Optional[Callable[[str], Awaitable[Optional[List[float]]]]] = None,
) -> Dict[str, int]:
    stats = {"done": 0, "skipped": 0, "failed": 0, "empty": 0}
    if not rows:
        stats["empty"] = 1
        return stats
    for row in rows:
        l2_id = str(row.get("id") or "").strip()
        content = str(row.get("content") or "").strip()
        if not l2_id or not content:
            continue
        try:
            digest = content_hash(l2_id, content)
            existing = await store.find_l2(user_id, expert_id, l2_id)
            if existing and existing.get("content_hash") == digest:
                stats["skipped"] += 1
                continue
            extracted = await extract_event(content, complete)
            name = episode_name(extracted.get("name", ""), content)
            facts = build_facts(name, extracted)
            for fact in facts:
                fact["entity_id"] = entity_id(
                    user_id=user_id,
                    expert_id=expert_id,
                    slot=fact["slot"],
                    name=fact["name"],
                )
            key = episode_key(
                user_id=user_id,
                expert_id=expert_id,
                report_date=report_date or "unknown",
                seg_hash=digest,
            )
            sentence = "；".join(fact["sentence"] for fact in facts) or content[:200]
            episode_vector = await _embed_text(embed, sentence)
            for fact in facts:
                fact["embedding"] = await _embed_text(embed, fact["sentence"])
            await store.write_episode(
                {
                    "episode_id": episode_id(key),
                    "episode_key": key,
                    "user_id": user_id,
                    "expert_id": expert_id,
                    "l2_id": l2_id,
                    "content_hash": digest,
                    "name": name,
                    "sentence": sentence,
                    "report_date": report_date or "",
                    "embedding": episode_vector,
                },
                facts,
            )
            stats["done"] += 1
        except Exception as exc:
            stats["failed"] += 1
            logger.warning("memory graph L2 ingest failed l2_id=%s: %s", l2_id, exc)
    return stats


async def _embed_text(embed, text: str):
    if embed is None:
        return None
    try:
        vector = await embed(text)
    except Exception as exc:
        logger.warning("memory graph embed skipped: %s", exc)
        return None
    if not isinstance(vector, list) or not vector:
        return None
    return [float(item) for item in vector]


async def safe_embed(text: str):
    """Production embedding. A failure leaves the node without a vector."""
    from llm.llm_manager import get_llm

    response = await get_llm().embed(text)
    return list(getattr(response, "embedding", []) or [])
