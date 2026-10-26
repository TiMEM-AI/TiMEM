"""TiMEM REST adapter."""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from adapters.base import MemoryAdapter
from evaluators.tokens import attach_ingest_metrics, attach_search_metrics
from models.records import (
    BackfillResult,
    IngestResult,
    LoCoMoMessage,
    MemoryRecord,
    SearchResult,
    SourceSystem,
)
from utils.config import get_settings, load_yaml_config
from utils.message_pairs import iter_message_pairs, pair_count


def _message_payload(m: LoCoMoMessage) -> dict[str, str]:
    # Prefer LoCoMo real speaker for Platform A1 dialogue_turns; fall back to user1/user2.
    item: dict[str, str] = {"role": (m.speaker or m.role), "content": m.content}
    if m.timestamp:
        item["timestamp"] = m.timestamp
    return item


class TiMEMAdapter(MemoryAdapter):
    name = "timem"

    def __init__(self, client: Optional[httpx.AsyncClient] = None) -> None:
        self._settings = get_settings()
        self._client = client or httpx.AsyncClient(timeout=120.0)
        self._owns_client = client is None

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self._settings.timem_api_key,
            "Content-Type": "application/json",
        }

    def _search_config(self, overrides: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        cfg = load_yaml_config().get("retrieval", {})
        ovr = overrides or {}
        return {
            "search_mode": ovr.get(
                "search_mode", cfg.get("timem_search_mode", "enhanced_semantic")
            ),
            "use_hybrid": ovr.get("use_hybrid", cfg.get("timem_use_hybrid", True)),
            "enable_memories_rethink": ovr.get(
                "enable_memories_rethink",
                cfg.get("timem_enable_memories_rethink", False),
            ),
            "score_threshold": ovr.get("score_threshold", cfg.get("timem_score_threshold", 0.0)),
        }

    async def ingest(
        self,
        user_id: str,
        session_id: str,
        messages: list[LoCoMoMessage],
        *,
        conversation_id: Optional[str] = None,
    ) -> IngestResult:
        """Ingest one LoCoMo session as consecutive user/assistant pairs (fragment_size=2)."""
        chunks = list(iter_message_pairs(messages))
        n_pairs = pair_count(messages)
        if not chunks:
            result = IngestResult(
                success=True,
                latency_ms=0.0,
                session_id=session_id,
                memory_count=0,
                raw={"pair_count": 0, "api_calls": 0, "ingest_mode": "pair"},
            )
            attach_ingest_metrics(result, messages)
            return result

        total_latency_ms = 0.0
        total_memory_count = 0
        pair_bodies: list[Any] = []
        pair_details: list[dict[str, Any]] = []
        errors: list[str] = []
        for pair_index, pair in enumerate(chunks):
            one = await self._ingest_message_chunk(user_id, session_id, pair)
            total_latency_ms += one.latency_ms
            pair_details.append(
                {
                    "pair_index": pair_index,
                    "post_ms": one.latency_ms,
                    "poll_ms": 0.0,
                    "latency_ms": one.latency_ms,
                    "success": one.success,
                }
            )
            if one.success:
                total_memory_count += one.memory_count
                body = one.raw.get("body")
                if body is not None:
                    pair_bodies.append(body)
            elif one.error:
                errors.append(one.error)

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
                "pair_bodies": pair_bodies,
                "pair_details": pair_details,
            },
        )
        attach_ingest_metrics(result, messages)
        return result

    async def _ingest_message_chunk(
        self,
        user_id: str,
        session_id: str,
        messages: list[LoCoMoMessage],
    ) -> IngestResult:
        url = f"{self._settings.timem_base_url.rstrip('/')}/api/v1/memory/"
        payload = {
            "user_id": user_id,
            "expert_id": self._settings.benchmark_expert_id,
            "session_id": session_id,
            "messages": [_message_payload(m) for m in messages],
            "memory_levels": ["L1"],
            "format": "compact",
        }
        started = time.perf_counter()
        try:
            resp = await self._client.post(
                url,
                headers=self._headers(),
                json=payload,
            )
            latency_ms = (time.perf_counter() - started) * 1000
            resp.raise_for_status()
            data = resp.json()
            # Platform returns 202 + task_id (async generate). Do not poll — fire-and-forget.
            if isinstance(data, dict) and data.get("task_id"):
                return IngestResult(
                    success=True,
                    latency_ms=latency_ms,
                    session_id=session_id,
                    raw={
                        "body": data,
                        "accepted": True,
                        "task_id": data.get("task_id"),
                        "poll_url": data.get("poll_url"),
                    },
                    memory_count=0,
                )
            count = len(data) if isinstance(data, list) else 1
            return IngestResult(
                success=True,
                latency_ms=latency_ms,
                session_id=session_id,
                raw={"body": data, "accepted": False},
                memory_count=count,
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            return IngestResult(
                success=False,
                latency_ms=latency_ms,
                session_id=session_id,
                error=str(exc),
            )

    async def search(
        self,
        user_id: str,
        query: str,
        *,
        top_k: int = 10,
        conversation_id: Optional[str] = None,
        overrides: Optional[dict[str, Any]] = None,
    ) -> SearchResult:
        url = f"{self._settings.timem_base_url.rstrip('/')}/api/v1/memory/search"
        search_cfg = self._search_config(overrides)
        payload: dict[str, Any] = {
            "user_id": user_id,
            "query_text": query,
            "character_id": self._settings.benchmark_expert_id,
            "limit": top_k,
            "format": "full",
            "config": {
                "search_mode": search_cfg["search_mode"],
                "use_hybrid": search_cfg["use_hybrid"],
                "enable_memories_rethink": search_cfg["enable_memories_rethink"],
                "score_threshold": search_cfg["score_threshold"],
            },
        }
        if conversation_id:
            payload["session_id"] = conversation_id

        started = time.perf_counter()
        try:
            resp = await self._client.post(url, headers=self._headers(), json=payload)
            latency_ms = (time.perf_counter() - started) * 1000
            resp.raise_for_status()
            data = resp.json()
            records = _normalize_timem_search(data)
            raw: dict[str, Any] = {"body": data, "search_config": search_cfg}
            if isinstance(data, dict):
                for key in ("workflow_metadata", "debug", "metadata", "filters_applied"):
                    if key in data:
                        raw[key] = data[key]
            result = SearchResult(
                success=True,
                latency_ms=latency_ms,
                query=query,
                records=records,
                raw=raw,
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
                raw={"search_config": search_cfg},
            )

    async def ensure_memory_language(
        self,
        user_id: str,
        memory_language: str = "en",
    ) -> dict[str, Any]:
        """Set Policy memory_language for (user_id, benchmark expert) before ingest."""
        lang = str(memory_language or "en").strip().lower()
        if lang not in ("zh", "en"):
            raise ValueError(f"memory_language must be zh or en, got {memory_language!r}")
        expert_id = self._settings.benchmark_expert_id
        base = self._settings.timem_base_url.rstrip("/")
        headers = self._headers()
        get_url = f"{base}/api/v1/memory/policy"
        put_url = get_url
        params = {"user_id": user_id, "expert_id": expert_id}
        get_resp = await self._client.get(get_url, headers=headers, params=params)
        get_resp.raise_for_status()
        data = (get_resp.json() or {}).get("data") or {}
        if data.get("memory_language") == lang and data.get("effective_memory_language") == lang:
            return {"skipped": True, "user_id": user_id, "memory_language": lang, "policy": data}
        payload = {
            "user_id": user_id,
            "expert_id": expert_id,
            "max_memory_layer": data.get("max_memory_layer")
            or data.get("effective_max_memory_layer")
            or "L5",
            "profile_auto_recompute_enabled": bool(
                data.get("profile_auto_recompute_enabled", False)
            ),
            "memory_language": lang,
        }
        put_resp = await self._client.put(put_url, headers=headers, json=payload)
        put_resp.raise_for_status()
        body = put_resp.json() or {}
        return {
            "skipped": False,
            "user_id": user_id,
            "memory_language": lang,
            "policy": body.get("data") or body,
        }

    async def backfill(self, user_id: str, layers: list[str]) -> BackfillResult:
        url = f"{self._settings.timem_base_url.rstrip('/')}/api/v1/backfill/manual"
        payload = {
            "account_id": self._settings.timem_account_id,
            "user_id": user_id,
            "expert_id": self._settings.benchmark_expert_id,
            "layers": layers,
            "force_update": True,
        }
        started = time.perf_counter()
        try:
            resp = await self._client.post(url, headers=self._headers(), json=payload)
            latency_ms = (time.perf_counter() - started) * 1000
            resp.raise_for_status()
            data = resp.json()
            raw = data if isinstance(data, dict) else {"body": data}
            success, error = _evaluate_backfill_response(raw)
            return BackfillResult(
                success=success,
                latency_ms=latency_ms,
                layers=layers,
                raw=raw,
                error=error,
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            return BackfillResult(
                success=False,
                latency_ms=latency_ms,
                layers=layers,
                error=str(exc),
            )

    async def count_layers(self, user_id: str) -> dict[str, int]:
        """Count memories per layer via list API (for backfill polling)."""
        counts: dict[str, int] = {}
        base = f"{self._settings.timem_base_url.rstrip('/')}/api/v1/memories"
        for layer in ("L1", "L2", "L3", "L4", "L5"):
            params = {
                "user_id": user_id,
                "account_id": self._settings.timem_account_id,
                "expert_id": self._settings.benchmark_expert_id,
                "layer": layer,
                "page": 1,
                "size": 1,
            }
            try:
                resp = await self._client.get(url=base, headers=self._headers(), params=params)
                resp.raise_for_status()
                data = resp.json()
                counts[layer] = _extract_total(data, layer=layer)
            except Exception:
                counts[layer] = 0
        return counts

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _evaluate_backfill_response(data: Any) -> tuple[bool, str | None]:
    if not isinstance(data, dict):
        return False, "backfill response is not a JSON object"
    stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
    total = int(stats.get("total_tasks") or 0)
    failed = int(stats.get("failed_tasks") or 0)
    generated = int(stats.get("generated_memories") or 0)
    api_success = bool(data.get("success", True))
    if api_success and failed == 0 and total > 0:
        return True, None
    if api_success and failed == 0 and generated > 0:
        return True, None
    return (
        False,
        f"backfill noop or failed: total_tasks={total}, failed_tasks={failed}, generated_memories={generated}",
    )


def _extract_total(data: Any, *, layer: str | None = None) -> int:
    if isinstance(data, dict):
        total = data.get("total")
        if total is not None and int(total) > 0:
            return int(total)
        if layer:
            counts = data.get("counts_by_level")
            if isinstance(counts, dict) and layer in counts:
                return int(counts[layer] or 0)
        if "data" in data and isinstance(data["data"], dict) and "total" in data["data"]:
            return int(data["data"]["total"])
        memories = data.get("memories") or data.get("data") or data.get("items")
        if isinstance(memories, list):
            return len(memories)
    if isinstance(data, list):
        return len(data)
    return 0


def _normalize_timem_search(data: Any) -> list[MemoryRecord]:
    items: list[Any] = []
    if isinstance(data, dict):
        items = (
            data.get("memories")
            or data.get("data")
            or (data.get("data") or {}).get("memories")
            or []
        )
        if isinstance(items, dict):
            items = items.get("memories") or items.get("items") or []
    elif isinstance(data, list):
        items = data

    records: list[MemoryRecord] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = item.get("content") or item.get("title") or ""
        layer = item.get("layer") or item.get("level")
        records.append(
            MemoryRecord(
                id=str(item.get("id") or item.get("memory_id") or ""),
                content=str(content),
                memory_type="fragment" if str(layer).upper() == "L1" else "summary",
                source_system=SourceSystem.TIMEM,
                layer=str(layer) if layer else None,
                score=_float_or_none(item.get("retrieval_score") or item.get("score")),
                metadata={"raw": item},
            )
        )
    return records


def _float_or_none(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
