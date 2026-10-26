"""Shared data models for benchmark runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class SourceSystem(str, Enum):
    TIMEM = "timem"
    MEMOS = "memos"
    MEM0 = "mem0"


class RetrievalMode(str, Enum):
    T0 = "T0"  # immediately after L1 ingest
    T1 = "T1"  # after TiMEM L2-L5 backfill


@dataclass
class MemoryRecord:
    id: str
    content: str
    memory_type: str  # factual | preference | fragment | summary | unknown
    source_system: SourceSystem
    layer: Optional[str] = None
    score: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestResult:
    success: bool
    latency_ms: float
    session_id: str
    raw: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    memory_count: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    success: bool
    latency_ms: float
    query: str
    records: list[MemoryRecord] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def result_count(self) -> int:
        return len(self.records)


@dataclass
class BackfillResult:
    success: bool
    latency_ms: float
    layers: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class LoCoMoMessage:
    role: str  # vendor-stable: user1|user2 (MemOS/Mem0 map to user/assistant)
    content: str
    timestamp: Optional[str] = None  # ISO 8601
    speaker: Optional[str] = None  # LoCoMo real name; TiMEM posts this as messages[].role


@dataclass
class LoCoMoSession:
    session_id: str
    persona_id: str
    messages: list[LoCoMoMessage]
    started_at: Optional[str] = None  # session anchor from session_N_date_time


@dataclass
class LoCoMoQA:
    persona_id: str
    question: str
    answer: str
    session_id: Optional[str] = None
    category: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LoCoMoPersona:
    persona_id: str
    sessions: list[LoCoMoSession]
    qa_pairs: list[LoCoMoQA]
