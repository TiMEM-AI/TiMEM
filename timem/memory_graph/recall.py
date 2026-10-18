"""Open memory-graph hits and place memories the vector search missed first."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

from timem.memory_graph.build import child_ids, memory_level, memory_text
from timem.memory_graph.schedule import wait_inflight
from timem.memory_graph.store import get_store
from timem.utils.logging import get_logger

logger = get_logger(__name__)

DRILL_L2_LIMIT = 3

DECIDE_PROMPT = """下面是和问题相关的记忆图节点。只决定要打开哪些节点的原文，不要回答问题。

问题:
{question}

节点:
{nodes}

如果需要打开原文，只返回 JSON: {{"open": ["节点名"], "verbatim": false}}
verbatim 为 true 表示还要带上这些会话记忆下面的原话。
最多打开 3 个节点。不需要打开时返回 {{"open": []}}。"""


def memory_row_id(row: Dict[str, Any]) -> str:
    return str(row.get("id") or row.get("memory_id") or "").strip()


def merge_graph_first(
    vector_rows: Iterable[Dict[str, Any]],
    graph_rows: Iterable[Dict[str, Any]],
    top_k: int,
) -> List[Dict[str, Any]]:
    """Keep graph-only memories first, then vector order, and cut to top_k."""
    if top_k <= 0:
        return []
    vector_list = list(vector_rows)
    vector_ids = {memory_row_id(row) for row in vector_list if memory_row_id(row)}
    graph_new: List[Dict[str, Any]] = []
    seen_new: set[str] = set()
    for row in graph_rows:
        memory_id = memory_row_id(row)
        content = str(row.get("content") or "").strip()
        if not memory_id or not content or memory_id in vector_ids or memory_id in seen_new:
            continue
        seen_new.add(memory_id)
        graph_new.append({"id": memory_id, "content": content})
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in graph_new + vector_list:
        memory_id = memory_row_id(row)
        if not memory_id or memory_id in seen:
            continue
        seen.add(memory_id)
        merged.append(row)
        if len(merged) >= top_k:
            break
    return merged


def parse_drill_request(text: str) -> Optional[Dict[str, Any]]:
    raw = (text or "").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or "open" not in payload:
        return None
    names: List[str] = []
    for item in payload.get("open") or []:
        name = str(item or "").strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= DRILL_L2_LIMIT:
            break
    return {"open": names, "verbatim": bool(payload.get("verbatim"))}


def collect_l2_ids(nodes: Iterable[Dict[str, Any]], names: Iterable[str]) -> List[str]:
    wanted = {name for name in names if name}
    found: List[str] = []
    for node in nodes:
        if node.get("name") not in wanted:
            continue
        for item in node.get("source_l2_ids") or []:
            text = str(item or "").strip()
            if text and text not in found:
                found.append(text)
            if len(found) >= DRILL_L2_LIMIT:
                return found
    return found


def format_nodes(nodes: List[Dict[str, Any]]) -> str:
    lines = []
    for node in nodes:
        sentence = str(node.get("sentence") or "").strip()
        lines.append(f"- {node.get('name')}: {sentence}" if sentence else f"- {node.get('name')}")
    return "\n".join(lines)


async def default_decide(question: str, nodes: List[Dict[str, Any]]) -> str:
    from timem.memory_graph.extract import default_complete

    if not nodes:
        return '{"open": []}'
    return await default_complete(DECIDE_PROMPT.format(question=question, nodes=format_nodes(nodes)))


async def _default_load(memory_id: str):
    from storage.memory_storage_manager import get_memory_storage_manager_async

    manager = await get_memory_storage_manager_async()
    return await manager.get_memory(memory_id)


async def rows_for_l2(
    l2_ids: List[str],
    load_memory,
    *,
    include_l1: bool,
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for memory_id in l2_ids:
        memory = await load_memory(memory_id)
        if memory is None:
            continue
        content = memory_text(memory, "content")
        if content:
            rows.append({"id": memory_id, "content": content})
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
                rows.append({"id": child_id, "content": child_content})
    return rows


async def merge_search_results(
    vector_rows: List[Dict[str, Any]],
    *,
    user_id: str,
    expert_id: str,
    question: str,
    top_k: int,
    store=None,
    decide=None,
    load_memory=None,
    wait=True,
) -> List[Dict[str, Any]]:
    """Vector order stays when the graph is off, empty, or fails."""
    rows = list(vector_rows)
    if top_k <= 0:
        return []
    try:
        if wait:
            await wait_inflight(user_id, expert_id)
        graph = store if store is not None else get_store()
        if not getattr(graph, "enabled", False):
            return rows[:top_k]
        nodes = await graph.context_nodes(user_id, expert_id, question)
        if not nodes:
            return rows[:top_k]
        text = await (decide or default_decide)(question, nodes)
        drill = parse_drill_request(text)
        if drill is None or not drill["open"]:
            return rows[:top_k]
        l2_ids = collect_l2_ids(nodes, drill["open"])
        if not l2_ids:
            return rows[:top_k]
        graph_rows = await rows_for_l2(
            l2_ids,
            load_memory or _default_load,
            include_l1=bool(drill["verbatim"]),
        )
        return merge_graph_first(rows, graph_rows, top_k)
    except Exception as exc:
        logger.warning("memory graph recall skipped user_id=%s: %s", user_id, exc)
        return rows[:top_k]
