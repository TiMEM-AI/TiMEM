"""Platform-side text chunking for AML Add.

Official rule: split when either 20 messages or 2,000 words is reached,
whichever comes first. The frozen word-count adapter is not public.
This counter is an approximation: each CJK character is one word, and each
Latin/number token split by whitespace or punctuation is one word.
"""

from __future__ import annotations

import re

from benchmark_data.aml_models import EvalMessage

MAX_MESSAGES = 20
MAX_WORDS = 2000

_WORD_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?")


def count_words(text: str) -> int:
    """Approximate word count. Punctuation and whitespace are separators."""
    if not text:
        return 0
    return len(_WORD_RE.findall(text))


def chunk_messages(
    messages: list[EvalMessage],
    *,
    max_messages: int = MAX_MESSAGES,
    max_words: int = MAX_WORDS,
) -> list[list[EvalMessage]]:
    """Split in source order. A single over-long message stays in its own chunk."""
    chunks: list[list[EvalMessage]] = []
    current: list[EvalMessage] = []
    words = 0
    for message in messages:
        message_words = count_words(message.content)
        over_messages = len(current) >= max_messages
        over_words = current and words + message_words > max_words
        if over_messages or over_words:
            chunks.append(current)
            current = []
            words = 0
        current.append(message)
        words += message_words
    if current:
        chunks.append(current)
    return chunks
