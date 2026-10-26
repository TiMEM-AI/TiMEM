"""Abstract adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from models.records import BackfillResult, IngestResult, LoCoMoMessage, SearchResult


class MemoryAdapter(ABC):
    name: str

    @abstractmethod
    async def ingest(
        self,
        user_id: str,
        session_id: str,
        messages: list[LoCoMoMessage],
        *,
        conversation_id: Optional[str] = None,
    ) -> IngestResult:
        ...

    @abstractmethod
    async def search(
        self,
        user_id: str,
        query: str,
        *,
        top_k: int = 10,
        conversation_id: Optional[str] = None,
        overrides: Optional[dict[str, Any]] = None,
    ) -> SearchResult:
        ...

    async def backfill(self, user_id: str, layers: list[str]) -> BackfillResult:
        """Optional: TiMEM L2-L5 backfill. MemOS default no-op."""
        return BackfillResult(success=True, latency_ms=0.0, layers=[])

    async def close(self) -> None:
        pass
