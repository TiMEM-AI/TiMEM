"""Samples used by the AML text-track simulator.

The harness plays the platform: it sends Add/Search and keeps gold answers
only for the local unofficial score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvalMessage:
    role: str  # user | assistant
    content: str
    timestamp_ms: int | None = None


@dataclass
class EvalQuestion:
    question_id: str
    question: str
    gold: str
    category: str | None = None
    options: list[str] | None = None
    preference: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalSample:
    dataset_id: str
    sample_id: str
    messages: list[EvalMessage]
    questions: list[EvalQuestion]

    @property
    def user_scope(self) -> str:
        return self.sample_id
