"""Thin facade over the existing L1 writer and memory-only retrieval."""

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from llm.model_scope import memory_model_scope
from timem.utils.logging import get_logger

logger = get_logger(__name__)

EXPERT_ID = "assistant"


def render_messages(messages: List[Dict[str, Any]]) -> str:
    lines = []
    for message in messages:
        role = message.get("role") or "user"
        content = (message.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def message_timestamp(messages: List[Dict[str, Any]]) -> datetime:
    for message in messages:
        raw = message.get("timestamp")
        if isinstance(raw, (int, float)) and raw > 0:
            return datetime.fromtimestamp(raw / 1000.0, tz=timezone.utc)
    return datetime.now(tz=timezone.utc)


def memory_record(memory: Dict[str, Any]) -> Dict[str, Any]:
    created = memory.get("created_at") or memory.get("timestamp") or memory.get("time")
    if hasattr(created, "isoformat"):
        created = created.isoformat()
    score = memory.get("score")
    if score is None:
        score = memory.get("retrieval_score") or memory.get("similarity")
    return {
        "id": str(memory.get("id") or memory.get("memory_id") or ""),
        "content": str(memory.get("content") or memory.get("text") or ""),
        "score": score,
        "title": str(memory.get("title") or ""),
        "created_at": created,
        "level": _level_name(memory.get("level") or memory.get("layer")),
    }


def _level_name(level: Any) -> Optional[str]:
    if hasattr(level, "value"):
        level = level.value
    if level is None:
        return None
    text = str(level).replace("MemoryLevel.", "")
    return text or None


def _day_bound(value: str, end_of_day: bool) -> str:
    text = value.strip()
    if len(text) == 10:
        return text + ("T23:59:59" if end_of_day else "T00:00:00")
    return text


class EngineMemoryService:
    """Add waits for L1. Higher layers continue in the background."""

    def __init__(self, reply_model: str = "qwen3.7-flash"):
        self.reply_model = reply_model
        self.source_store = None
        # Default ON; only explicit 0/false/off disables SourceStore.
        _source_flag = (os.getenv("TIMEM_SOURCE_EVIDENCE") or "1").strip().lower()
        if _source_flag not in {"0", "false", "off"}:
            from server.source_evidence import SourceStore

            self.source_store = SourceStore(os.getenv("TIMEM_SOURCE_EVIDENCE_PATH", "data/source_evidence.sqlite"))

    async def add(
        self,
        user_id: str,
        session_id: str,
        messages: List[Dict[str, Any]],
        memory_model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        embed_model: Optional[str] = None,
        embed_base_url: Optional[str] = None,
        embed_api_key: Optional[str] = None,
        embed_dim: Optional[int] = None,
    ) -> None:
        from timem.workflows.memory_generation import MemoryGenerationWorkflow

        workflow = MemoryGenerationWorkflow()
        with memory_model_scope(
            memory_model,
            base_url=base_url,
            api_key=api_key,
            embed_model=embed_model,
            embed_base_url=embed_base_url,
            embed_api_key=embed_api_key,
            embed_dim=embed_dim,
        ):
            result = await workflow.run({
                "user_id": user_id,
                "expert_id": EXPERT_ID,
                "session_id": session_id,
                "content": render_messages(messages),
                "timestamp": message_timestamp(messages),
            })
            if not result.get("success"):
                raise RuntimeError(result.get("error") or "L1 generation failed")
            if self.source_store is not None:
                memories = result.get("generated_memories") or []
                if len(memories) != 1:
                    raise RuntimeError("Source evidence requires exactly one generated L1 memory")
                memory = memories[0]
                memory_id = memory.get("id") if isinstance(memory, dict) else getattr(memory, "id", None)
                if not memory_id:
                    raise RuntimeError("Generated L1 memory has no id")
                await asyncio.to_thread(self.source_store.put, user_id, str(memory_id), session_id, messages)
            asyncio.create_task(self._backfill(user_id))

    async def _backfill(self, user_id: str) -> None:
        try:
            from services.scheduled_backfill_service import get_scheduled_backfill_service
            service = get_scheduled_backfill_service()
            await service.backfill_for_user(user_id, expert_id=EXPERT_ID)
        except Exception:
            logger.exception("background L2-L5 backfill failed for %s", user_id)

    async def search(
        self,
        user_id: str,
        query: str,
        top_k: int,
        memory_model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        embed_model: Optional[str] = None,
        embed_base_url: Optional[str] = None,
        embed_api_key: Optional[str] = None,
        embed_dim: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        from timem.workflows.memory_retrieval import MemoryRetrievalWorkflow

        workflow = await MemoryRetrievalWorkflow.create(debug_mode=False)
        with memory_model_scope(
            memory_model,
            base_url=base_url,
            api_key=api_key,
            embed_model=embed_model,
            embed_base_url=embed_base_url,
            embed_api_key=embed_api_key,
            embed_dim=embed_dim,
        ):
            result = await workflow.run({
                "question": query,
                "user_id": user_id,
                "expert_id": EXPERT_ID,
                "return_memories_only": True,
            })
        records = []
        for memory in result.get("retrieved_memories") or []:
            if not isinstance(memory, dict):
                continue
            item = memory_record(memory)
            if item["id"] and item["content"]:
                records.append(item)
        from timem.memory_graph.recall import merge_search_results

        records = await merge_search_results(
            records,
            user_id=user_id,
            expert_id=EXPERT_ID,
            question=query,
            top_k=top_k,
        )
        if self.source_store is not None:
            from server.source_evidence import supplement

            records = await asyncio.to_thread(supplement, self.source_store, user_id, query, records, top_k)
        return records

    async def list_memories(
        self,
        user_id: str,
        limit: int = 20,
        page: int = 1,
        level: Optional[str] = None,
        keyword: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Dict[str, Any]:
        from storage.memory_storage_manager import get_memory_storage_manager_async

        size = max(1, min(int(limit or 20), 50))
        page_no = max(1, int(page or 1))
        query: Dict[str, Any] = {"user_id": user_id}
        if level:
            query["level"] = level
        if keyword and keyword.strip():
            query["content_contains"] = keyword.strip()
        options: Dict[str, Any] = {"limit": size + 1, "offset": (page_no - 1) * size}
        if start and end:
            options["start_time"] = _day_bound(start, end_of_day=False)
            options["end_time"] = _day_bound(end, end_of_day=True)
        manager = await get_memory_storage_manager_async()
        found = await manager.search_memories(query, options, storage_type="sql")
        records = []
        for memory in found or []:
            if isinstance(memory, dict):
                item = memory
            elif hasattr(memory, "model_dump"):
                item = memory.model_dump()
            else:
                item = getattr(memory, "__dict__", None)
            if isinstance(item, dict):
                records.append(memory_record(item))
        return {
            "memories": records[:size],
            "page": page_no,
            "page_size": size,
            "has_more": len(records) > size,
        }

    async def chat(
        self,
        user_id: str,
        session_id: str,
        message: str,
        reply_model: Optional[str] = None,
        memory_model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        memory_base_url: Optional[str] = None,
        memory_api_key: Optional[str] = None,
        embed_model: Optional[str] = None,
        embed_base_url: Optional[str] = None,
        embed_api_key: Optional[str] = None,
        embed_dim: Optional[int] = None,
    ) -> Dict[str, Any]:
        from llm.base_llm import Message, MessageRole
        from llm.llm_manager import get_llm

        memories = await self.search(
            user_id,
            message,
            top_k=8,
            memory_model=memory_model,
            base_url=memory_base_url or base_url,
            api_key=memory_api_key or api_key,
            embed_model=embed_model,
            embed_base_url=embed_base_url,
            embed_api_key=embed_api_key,
            embed_dim=embed_dim,
        )
        context = "\n".join(f"- {item['content']}" for item in memories) or "(no memories)"
        llm = get_llm("xingliu")
        response = await llm.chat(
            [
                Message(
                    role=MessageRole.SYSTEM,
                    content="Answer from the retrieved memories. If they do not contain the answer, say so.",
                ),
                Message(role=MessageRole.USER, content=f"Memories:\n{context}\n\nUser: {message}"),
            ],
            model=reply_model or self.reply_model,
            base_url=base_url,
            api_key=api_key,
        )
        reply = response.content
        await self.add(
            user_id,
            session_id,
            [
                {"role": "user", "content": message},
                {"role": "assistant", "content": reply},
            ],
            memory_model=memory_model,
            base_url=memory_base_url or base_url,
            api_key=memory_api_key or api_key,
            embed_model=embed_model,
            embed_base_url=embed_base_url,
            embed_api_key=embed_api_key,
            embed_dim=embed_dim,
        )
        return {"reply": reply, "memories": memories}
