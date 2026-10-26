"""One-hop entity expansion for AML search.

Strategy (deliberately bounded):
  1. run one normal search to get a small set of memories;
  2. pull person names / proper nouns out of those memories;
  3. run exactly ONE more search with those terms;
  4. merge, de-duplicate by id, prefer missing L1 fragments, cap the total.

Controlled by env vars so it can be switched off for A/B comparison:
  TIMEM_SEARCH_EXPAND   default "1"
  TIMEM_HOP_K           first-hop size            default 8
  TIMEM_ENTITY_MAX      entities reused            default 4
  TIMEM_SECOND_K        second-hop size            default 8
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Sequence

# Capitalised words that are almost never the entity we want.
STOPWORDS = {
    "The", "This", "That", "These", "Those", "There", "Then", "They", "Their",
    "You", "Your", "Yes", "Yeah", "Well", "What", "When", "Where", "Which",
    "Who", "Why", "How", "But", "And", "Not", "Now", "Just", "Okay", "Ok",
    "Hi", "Hello", "Thanks", "Thank", "Please", "Sorry", "Sure", "Maybe",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
}

_NAME_RE = re.compile(r"\b([A-Z][a-zA-Z'\u2019-]{1,24}(?:\s+[A-Z][a-zA-Z'\u2019-]{1,24}){0,2})\b")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def expansion_enabled() -> bool:
    return os.getenv("TIMEM_SEARCH_EXPAND", "1").strip().lower() not in {"0", "false", "no", "off"}


def extract_entities(rows: Sequence[Dict[str, Any]], limit: int) -> List[str]:
    """Most frequent capitalised sequences across the first-hop memories."""
    counts: Dict[str, int] = {}
    for row in rows:
        text = f"{row.get('title') or ''} {row.get('content') or ''}"
        for match in _NAME_RE.finditer(text):
            name = " ".join(match.group(1).split())
            if len(name) < 3 or name in STOPWORDS:
                continue
            if name.split()[0] in STOPWORDS and len(name.split()) == 1:
                continue
            counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
    return [name for name, _ in ranked][:limit]


def _rank_for_supplement(row: Dict[str, Any]) -> tuple:
    """Prefer L1 fragments, then higher score."""
    level = str(row.get("level") or "")
    try:
        score = float(row.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return (0 if level.upper() == "L1" else 1, -score)


async def search_with_expansion(
    service,
    user_id: str,
    query: str,
    top_k: int,
    *,
    memory_model: Optional[str] = None,
    enabled: Optional[bool] = None,
    hop_k: Optional[int] = None,
    entity_limit: Optional[int] = None,
    second_k: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """First hop, one entity hop, merge, de-dupe, cap."""
    enabled = expansion_enabled() if enabled is None else enabled
    hop_k = hop_k or _env_int("TIMEM_HOP_K", 8)
    entity_limit = entity_limit or _env_int("TIMEM_ENTITY_MAX", 4)
    # the second hop is the one that refills the result list, so it defaults to
    # the caller's top_k (100 in the official evaluation)
    second_k = second_k or _env_int("TIMEM_SECOND_K", top_k)

    first = list(await service.search(user_id, query, hop_k, memory_model=memory_model))
    if not enabled or not first:
        return first[:top_k]

    entities = extract_entities(first, entity_limit)
    if not entities:
        return first[:top_k]

    expansion_query = " ".join(entities)
    second = list(await service.search(user_id, expansion_query, second_k, memory_model=memory_model))

    merged: List[Dict[str, Any]] = []
    seen = set()
    for row in first:
        rid = row.get("id")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        merged.append(row)

    supplements = [r for r in second if r.get("id") and r.get("id") not in seen]
    supplements.sort(key=_rank_for_supplement)
    for row in supplements:
        rid = row.get("id")
        if rid in seen:
            continue
        seen.add(rid)
        merged.append(row)
        if len(merged) >= top_k:
            break

    return merged[:top_k]
