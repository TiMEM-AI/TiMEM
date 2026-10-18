"""Graph QA. Relations that suffice become the answer. Otherwise open at most three L2 texts, then answer."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional

from timem.memory_graph.build import child_ids, memory_level, memory_text
from timem.memory_graph.recall import collect_l2_ids, parse_drill_request
from timem.memory_graph.search import search_graph
from timem.utils.logging import get_logger

logger = get_logger(__name__)

ALLOWED_HOPS = (1, 2, 3)

DECIDE_PROMPT = """你只根据提供的图谱关系回答问题。
关系足够时，直接输出答案正文，不要 JSON。
只有必须查看节点的原始记忆才能回答时，才只输出 JSON：{{"open":["节点名"],"verbatim":false}}。
open 最多 3 个，名字必须来自上下文。verbatim 为 true 表示需要原始对话。
不要编造未出现的实体或关系。用与问题相同的语言作答。

问题：
{question}

图谱上下文：
{context}
"""

ANSWER_PROMPT = """你只根据提供的材料回答问题。材料不足就说明没有足够证据。
用与问题相同的语言作答。只输出答案正文，不要 JSON。

问题：
{question}

材料：
{context}
"""

Complete = Callable[[str], Awaitable[str]]


class GraphQueryError(ValueError):
    """Caller maps this to HTTP 400."""


def pack_relations(view: dict) -> str:
    lines = []
    for edge in view.get("edges") or []:
        fact = str(edge.get("fact") or "").strip()
        if fact:
            lines.append(fact)
            continue
        lines.append(f"{edge.get('from')} {edge.get('type')} {edge.get('to')}".strip())
    if lines:
        return "\n".join(lines)
    return "\n".join(f"- {node.get('name')}" for node in view.get("nodes") or [] if node.get("name"))


async def answer_graph(
    store,
    *,
    user_id: str,
    expert_id: str,
    question: str,
    hops: int = 1,
    limit: int = 20,
    complete: Complete,
    load_memory=None,
    query_vector: Optional[List[float]] = None,
) -> dict:
    if hops not in ALLOWED_HOPS:
        raise GraphQueryError("hops 只允许 1、2 或 3")
    text = (question or "").strip()
    view = await search_graph(
        store,
        user_id=user_id,
        expert_id=expert_id,
        query=text,
        limit=limit,
        query_vector=query_vector,
    )
    if hops > 1 and view["nodes"]:
        seed_ids = [node["id"] for node in view["nodes"]]
        expanded = await store.expand(user_id, expert_id, seed_ids, hops, limit)
        view["nodes"] = expanded.get("nodes") or view["nodes"]
        view["edges"] = expanded.get("edges") or view["edges"]
    if not view["nodes"]:
        view["answer"] = "没有足够证据。"
        return view
    context = pack_relations(view)
    decided = await complete(DECIDE_PROMPT.format(question=text, context=context))
    drill = parse_drill_request(decided) if (decided or "").strip().startswith("{") else None
    if drill is None:
        view["answer"] = (decided or "").strip() or "没有足够证据。"
        return view
    opened = ""
    l2_ids = collect_l2_ids(view["nodes"], drill["open"])
    if l2_ids and load_memory is not None:
        opened = await _lineage_text(l2_ids, load_memory, include_l1=bool(drill["verbatim"]))
    material = f"{context}\n\n{opened}".strip() if opened else context
    view["answer"] = (await complete(ANSWER_PROMPT.format(question=text, context=material))).strip()
    return view


async def _lineage_text(l2_ids: List[str], load_memory, *, include_l1: bool) -> str:
    lines = []
    for memory_id in l2_ids:
        try:
            memory = await load_memory(memory_id)
        except Exception as exc:
            logger.warning("graph qa load failed memory_id=%s: %s", memory_id, exc)
            continue
        if memory is None:
            continue
        content = memory_text(memory, "content")
        if content:
            lines.append(f"L2: {content}")
        if not include_l1:
            continue
        for child_id in child_ids(memory):
            child = await load_memory(child_id)
            if child is None:
                continue
            level = memory_level(child)
            if level and level != "L1":
                continue
            child_content = memory_text(child, "content")
            if child_content:
                lines.append(f"L1: {child_content}")
    return "\n".join(lines)


async def run_qa(
    user_id: str,
    question: str,
    *,
    expert_id: str = "assistant",
    hops: int = 1,
    limit: int = 20,
) -> dict:
    from timem.memory_graph.build import safe_embed
    from timem.memory_graph.extract import default_complete
    from timem.memory_graph.store import get_store

    vector = None
    try:
        vector = await safe_embed(question)
    except Exception:
        vector = None

    async def load(memory_id: str):
        from storage.memory_storage_manager import get_memory_storage_manager_async

        manager = await get_memory_storage_manager_async()
        return await manager.get_memory(memory_id)

    return await answer_graph(
        get_store(),
        user_id=user_id,
        expert_id=expert_id,
        question=question,
        hops=hops,
        limit=limit,
        complete=default_complete,
        load_memory=load,
        query_vector=vector,
    )


def parse_answer_or_drill(text: str) -> Optional[Dict[str, Any]]:
    """Kept for tests. Prose is an answer. JSON with open is a drill request."""
    raw = (text or "").strip()
    if not raw.startswith("{"):
        return None
    return parse_drill_request(raw)
