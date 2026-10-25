"""MemOS Cloud REST adapter."""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from adapters.base import MemoryAdapter
from evaluators.tokens import attach_ingest_metrics, attach_search_metrics
from models.records import IngestResult, LoCoMoMessage, MemoryRecord, SearchResult, SourceSystem
from utils.config import get_settings
from utils.message_pairs import pair_count


def _memos_api_role(role: str) -> str:
    """Map LoCoMo user1/user2 to MemOS API roles (user/assistant only)."""
    r = role.lower().strip()
    if r == "user1":
        return "user"
    if r == "user2":
        return "assistant"
    return role


class MemOSCloudAdapter(MemoryAdapter):
    name = "memos"

    def __init__(self, client: Optional[httpx.AsyncClient] = None) -> None:
        self._settings = get_settings()
        self._client = client or httpx.AsyncClient(timeout=120.0)
        self._owns_client = client is None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token {self._settings.memos_api_key}",
            "Content-Type": "application/json",
        }

    async def ingest(
        self,
        user_id: str,
        session_id: str,
        messages: list[LoCoMoMessage],
        *,
        conversation_id: Optional[str] = None,
    ) -> IngestResult:
        conv_id = conversation_id or session_id
        url = f"{self._settings.memos_base_url.rstrip('/')}/add/message"
        payload = {
            "user_id": user_id,
            "conversation_id": conv_id,
            "messages": [
                {"role": _memos_api_role(m.role), "content": m.content} for m in messages
            ],
        }
        started = time.perf_counter()
        try:
            resp = await self._client.post(url, headers=self._headers(), json=payload)
            latency_ms = (time.perf_counter() - started) * 1000
            resp.raise_for_status()
            data = resp.json()
            raw: dict[str, Any] = data if isinstance(data, dict) else {"body": data}
            raw["ingest_mode"] = "session"
            raw["api_calls"] = 1
            raw["pair_count"] = pair_count(messages)
            result = IngestResult(
                success=True,
                latency_ms=latency_ms,
                session_id=session_id,
                raw=raw,
                memory_count=1,
            )
            attach_ingest_metrics(result, messages)
            return result
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            result = IngestResult(
                success=False,
                latency_ms=latency_ms,
                session_id=session_id,
                error=str(exc),
            )
            attach_ingest_metrics(result, messages)
            return result

    async def search(
        self,
        user_id: str,
        query: str,
        *,
        top_k: int = 10,
        conversation_id: Optional[str] = None,
        overrides: Optional[dict[str, Any]] = None,
    ) -> SearchResult:
        url = f"{self._settings.memos_base_url.rstrip('/')}/search/memory"
        payload: dict[str, Any] = {"user_id": user_id, "query": query}
        if conversation_id:
            payload["conversation_id"] = conversation_id

        started = time.perf_counter()
        try:
            resp = await self._client.post(url, headers=self._headers(), json=payload)
            latency_ms = (time.perf_counter() - started) * 1000
            resp.raise_for_status()
            data = resp.json()
            records = _normalize_memos_search(data, top_k=top_k)
            result = SearchResult(
                success=True,
                latency_ms=latency_ms,
                query=query,
                records=records[:top_k],
                raw=data if isinstance(data, dict) else {"body": data},
            )
            attach_search_metrics(result, top_k=top_k)
            return result
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            return SearchResult(
                success=False,
                latency_ms=latency_ms,
                query=query,
                error=str(exc),
            )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _normalize_memos_search(data: Any, *, top_k: int) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []

    if not isinstance(data, dict):
        return records

    # MemOS Cloud wraps payload: {"code": 0, "data": {...}}
    payload = data.get("data") if isinstance(data.get("data"), dict) else data

    # Factual memories
    for item in payload.get("memory_detail_list") or payload.get("memories") or []:
        if not isinstance(item, dict):
            continue
        key = item.get("memory_key") or ""
        value = item.get("memory_value") or item.get("content") or ""
        content = f"{key}: {value}".strip(": ").strip() if key else str(value)
        records.append(
            MemoryRecord(
                id=str(item.get("id") or item.get("memory_id") or key or len(records)),
                content=content,
                memory_type="factual",
                source_system=SourceSystem.MEMOS,
                layer=None,
                score=_float_or_none(item.get("score")),
                metadata={"raw": item},
            )
        )

    # Preference memories
    pref_root = payload.get("preference_detail_list") or []
    for item in pref_root:
        if not isinstance(item, dict):
            continue
        pref = item.get("preference") or item.get("content") or ""
        pref_type = item.get("preference_type") or "preference"
        records.append(
            MemoryRecord(
                id=str(item.get("id") or f"pref_{len(records)}"),
                content=str(pref),
                memory_type=str(pref_type),
                source_system=SourceSystem.MEMOS,
                layer=None,
                score=None,
                metadata={"raw": item},
            )
        )

    return records[:top_k]


def _float_or_none(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
