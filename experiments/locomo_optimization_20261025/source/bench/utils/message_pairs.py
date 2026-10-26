"""Split dialogue messages into pairs (2 turns per chunk, TiMEM fragment_size=2)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TypeVar

T = TypeVar("T")


def iter_message_pairs(messages: list[T]) -> Iterator[list[T]]:
    """Yield consecutive pairs: [0,1], [2,3], …; trailing singleton if odd count."""
    for start in range(0, len(messages), 2):
        yield messages[start : start + 2]


def pair_count(messages: list[T]) -> int:
    """Number of pair chunks for a message list."""
    if not messages:
        return 0
    return (len(messages) + 1) // 2
