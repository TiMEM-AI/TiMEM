"""Mem0 Platform REST adapter (V3 API)."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import httpx

from adapters.base import MemoryAdapter
from evaluators.tokens import attach_ingest_metrics, attach_search_metrics
from models.records import IngestResult, LoCoMoMessage, MemoryRecord, SearchResult, SourceSystem
from utils.config import get_settings, load_yaml_config, resolve_ingest_config
from utils.message_pairs import iter_message_pairs, pair_count


class Mem0PlatformAdapter(MemoryAdapter):
    name = "mem0"

    def __init__(self, client: Optional[httpx.AsyncClient] = None) -> None:
        self._settings = get_settings()
        self._client = client or httpx.AsyncClient(timeout=120.0)
        self._owns_client = client is None
        self._pending_event_ids: list[str] = []
        self._last_flush_ms: float = 0.0
        self._last_flush_count: int = 0

    def _base_url(self) -> str:
        return self._settings.mem0_base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token {self._settings.mem0_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _poll_timeout_sec(self) -> int:
        return self._settings.mem0_ingest_poll_timeout_sec

    def _poll_mode(self) -> str:
        return resolve_ingest_config().mem0_poll_mode

    def _poll_concurrency(self) -> int:
        return resolve_ingest_config().mem0_poll_concurrency

    def _search_threshold(self) -> float:
        cfg = load_yaml_config().get("retrieval", {})
        return float(cfg.get("mem0_search_threshold", self._settings.mem0_search_threshold))

    async def ingest(
        self,
        user_id: str,
        session_id: str,
        messages: list[LoCoMoMessage],
        *,
        conversation_id: Optional[str] = None,
    ) -> IngestResult:
        """Contextual Add: one user+assistant pair per API call, scoped by run_id=session."""
        conv_id = conversation_id or session_id
        chunks = list(iter_message_pairs(messages))
        n_pairs = pair_count(messages)
        poll_mode = self._poll_mode()
        if not chunks:
            result = IngestResult(
                success=True,
                latency_ms=0.0,
                session_id=session_id,
                memory_count=0,
                raw={
                    "pair_count": 0,
                    "api_calls": 0,
                    "ingest_mode": "pair",
                    "poll_mode": poll_mode,
                },
            )
            attach_ingest_metrics(result, messages)
            return result

        total_latency_ms = 0.0
        total_memory_count = 0
        pair_responses: list[Any] = []
        pair_details: list[dict[str, Any]] = []
        errors: list[str] = []
        session_event_ids: list[str] = []
        session_flush_ms = 0.0
        prev_event_id: str | None = None

        for pair_index, pair in enumerate(chunks):
            if poll_mode == "pipeline" and prev_event_id and pair_details:
                try:
                    wait_started = time.perf_counter()
                    await self._wait_event_with_retry(prev_event_id)
                    poll_ms = (time.perf_counter() - wait_started) * 1000
                    total_latency_ms += poll_ms
                    prev_entry = pair_details[-1]
                    prev_entry["poll_ms"] = float(prev_entry.get("poll_ms") or 0) + poll_ms
                    prev_entry["latency_ms"] = float(prev_entry.get("post_ms") or 0) + prev_entry["poll_ms"]
                except Exception as exc:
                    errors.append(str(exc))

            one = await self._ingest_message_chunk(user_id, conv_id, pair, wait_event=False)
            post_ms = one.latency_ms
            total_latency_ms += post_ms
            event_id = (one.raw or {}).get("event_id") if isinstance(one.raw, dict) else None
            entry: dict[str, Any] = {
                "pair_index": pair_index,
                "post_ms": post_ms,
                "poll_ms": 0.0,
                "latency_ms": post_ms,
                "success": one.success,
            }
            pair_details.append(entry)

            if one.success:
                total_memory_count += one.memory_count
                pair_responses.append(one.raw)
            elif one.error:
                errors.append(one.error)
                prev_event_id = None
                continue

            if not event_id:
                prev_event_id = None
                continue

            if poll_mode == "sync":
                try:
                    wait_started = time.perf_counter()
                    await self._wait_event_with_retry(str(event_id))
                    poll_ms = (time.perf_counter() - wait_started) * 1000
                    total_latency_ms += poll_ms
                    entry["poll_ms"] = poll_ms
                    entry["latency_ms"] = post_ms + poll_ms
                except Exception as exc:
                    errors.append(str(exc))
            elif poll_mode == "pipeline":
                prev_event_id = str(event_id)
            else:
                session_event_ids.append(str(event_id))
                self._pending_event_ids.append(str(event_id))
                prev_event_id = None

        if poll_mode == "pipeline" and prev_event_id and pair_details and not errors:
            try:
                wait_started = time.perf_counter()
                await self._wait_event_with_retry(prev_event_id)
                poll_ms = (time.perf_counter() - wait_started) * 1000
                total_latency_ms += poll_ms
                last_entry = pair_details[-1]
                last_entry["poll_ms"] = float(last_entry.get("poll_ms") or 0) + poll_ms
                last_entry["latency_ms"] = float(last_entry.get("post_ms") or 0) + last_entry["poll_ms"]
            except Exception as exc:
                errors.append(str(exc))

        if poll_mode == "deferred" and session_event_ids and not errors:
            try:
                session_flush_ms = await self._flush_events(session_event_ids)
                total_latency_ms += session_flush_ms
                successful = [e for e in pair_details if e.get("success")]
                if successful:
                    share = session_flush_ms / len(successful)
                    for entry in successful:
                        entry["poll_ms"] = float(entry.get("poll_ms") or 0) + share
                        entry["latency_ms"] = float(entry.get("post_ms") or 0) + entry["poll_ms"]
                for eid in session_event_ids:
                    if eid in self._pending_event_ids:
                        self._pending_event_ids.remove(eid)
            except Exception as exc:
                errors.append(str(exc))

        result = IngestResult(
            success=not errors,
            latency_ms=total_latency_ms,
            session_id=session_id,
            memory_count=total_memory_count,
            error="; ".join(errors) if errors else None,
            raw={
                "pair_count": n_pairs,
                "api_calls": len(chunks),
                "ingest_mode": "pair",
                "run_id": conv_id,
                "poll_mode": poll_mode,
                "session_flush_ms": session_flush_ms,
                "pair_responses": pair_responses,
                "pair_details": pair_details,
            },
        )
        attach_ingest_metrics(result, messages)
        return result

    async def _ingest_message_chunk(
        self,
        user_id: str,
        run_id: str,
        messages: list[LoCoMoMessage],
        *,
        wait_event: bool = True,
    ) -> IngestResult:
        url = f"{self._base_url()}/v3/memories/add/"
        payload = {
            "user_id": user_id,
            "run_id": run_id,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "metadata": {"session_id": run_id},
        }
        started = time.perf_counter()
        try:
            data = await self._post_with_retry(url, payload)
            event_id = data.get("event_id") if isinstance(data, dict) else None
            if event_id and wait_event and self._poll_mode() == "sync":
                await self._wait_event_with_retry(str(event_id))
            latency_ms = (time.perf_counter() - started) * 1000
            memory_count = 0
            if isinstance(data, dict):
                raw = dict(data)
                if event_id:
                    raw["event_id"] = str(event_id)
                memories = data.get("memories") or data.get("results")
                if isinstance(memories, list):
                    memory_count = len(memories)
                elif event_id:
                    memory_count = 1
            else:
                raw = {"body": data}
            return IngestResult(
                success=True,
                latency_ms=latency_ms,
                session_id=run_id,
                raw=raw,
                memory_count=memory_count or 1,
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            return IngestResult(
                success=False,
                latency_ms=latency_ms,
                session_id=run_id,
                error=str(exc),
            )

    async def _post_with_retry(self, url: str, payload: dict[str, Any]) -> Any:
        max_attempts = 4
        delay = 2.0
        last_exc: Exception | None = None
        for attempt in range(max_attempts):
            try:
                resp = await self._client.post(url, headers=self._headers(), json=payload)
                if resp.status_code == 429:
                    raise httpx.HTTPStatusError(
                        "rate limited",
                        request=resp.request,
                        response=resp,
                    )
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code == 429 and attempt < max_attempts - 1:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise
            except Exception as exc:
                last_exc = exc
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("Mem0 POST failed")

    async def _wait_event_with_retry(self, event_id: str) -> None:
        max_attempts = 3
        delay = 2.0
        for attempt in range(max_attempts):
            try:
                await self._wait_event(event_id)
                return
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and attempt < max_attempts - 1:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise

    async def _wait_event(self, event_id: str) -> None:
        url = f"{self._base_url()}/v1/event/{event_id}/"
        timeout = self._poll_timeout_sec()
        elapsed = 0
        interval = 2
        while elapsed < timeout:
            resp = await self._client.get(url, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            status = str(data.get("status") or "").upper()
            if status in ("SUCCEEDED", "SUCCESS", "COMPLETED"):
                return
            if status in ("FAILED", "ERROR"):
                detail = data.get("message") or data.get("error") or data
                raise RuntimeError(f"Mem0 event {event_id} failed: {detail}")
            await asyncio.sleep(interval)
            elapsed += interval
        raise TimeoutError(f"Mem0 event {event_id} not ready after {timeout}s")

    async def _flush_events(self, event_ids: list[str]) -> float:
        unique = list(dict.fromkeys(e for e in event_ids if e))
        if not unique:
            return 0.0
        started = time.perf_counter()
        sem = asyncio.Semaphore(self._poll_concurrency())

        async def _one(eid: str) -> None:
            async with sem:
                await self._wait_event_with_retry(eid)

        await asyncio.gather(*[_one(eid) for eid in unique])
        elapsed_ms = (time.perf_counter() - started) * 1000
        self._last_flush_ms = elapsed_ms
        self._last_flush_count = len(unique)
        return elapsed_ms

    async def flush_pending_events(self) -> dict[str, Any]:
        """Flush any event IDs still pending (safety net before retrieval)."""
        pending = list(dict.fromkeys(self._pending_event_ids))
        if not pending:
            return {"pending_events_flushed": 0, "flush_ms": 0.0}
        flush_ms = await self._flush_events(pending)
        self._pending_event_ids.clear()
        return {"pending_events_flushed": len(pending), "flush_ms": flush_ms}

    async def search(
        self,
        user_id: str,
        query: str,
        *,
        top_k: int = 10,
        conversation_id: Optional[str] = None,
        overrides: Optional[dict[str, Any]] = None,
    ) -> SearchResult:
        url = f"{self._base_url()}/v3/memories/search/"
        payload: dict[str, Any] = {
            "query": query,
            "filters": {"user_id": user_id},
            "top_k": top_k,
            "threshold": self._search_threshold(),
        }
        if conversation_id:
            payload["filters"] = {
                "AND": [
                    {"user_id": user_id},
                    {"metadata": {"session_id": conversation_id}},
                ]
            }

        started = time.perf_counter()
        try:
            resp = await self._client.post(url, headers=self._headers(), json=payload)
            latency_ms = (time.perf_counter() - started) * 1000
            resp.raise_for_status()
            data = resp.json()
            records = _normalize_mem0_search(data, top_k=top_k)
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


def _normalize_mem0_search(data: Any, *, top_k: int) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    if not isinstance(data, dict):
        return records

    items = data.get("results") or data.get("memories") or []
    if not isinstance(items, list):
        return records

    for item in items:
        if not isinstance(item, dict):
            continue
        content = str(item.get("memory") or item.get("content") or "")
        categories = item.get("categories") or []
        memory_type = "factual"
        if isinstance(categories, list) and categories:
            memory_type = str(categories[0])
        records.append(
            MemoryRecord(
                id=str(item.get("id") or len(records)),
                content=content,
                memory_type=memory_type,
                source_system=SourceSystem.MEM0,
                layer=None,
                score=_float_or_none(item.get("score")),
                metadata={"raw": item},
            )
        )

    return records[:top_k]


def _float_or_none(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
