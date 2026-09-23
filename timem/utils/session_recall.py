"""Keep complex-query evidence from more than one session."""

from typing import Any, Dict, List


def limit_l1_to_top_sessions(
    l1_results: List[Dict[str, Any]],
    session_scores: List[tuple],
    top_n: int = 3,
) -> List[Dict[str, Any]]:
    """Keep L1 hits from the highest-scoring sessions so one session cannot crowd out the rest."""
    top_ids = [session_id for session_id, _score in (session_scores or [])[:top_n] if session_id]
    if len(top_ids) <= 1:
        return l1_results
    allowed = set(top_ids)
    kept = [memory for memory in l1_results if memory.get("session_id") in allowed]
    return kept or l1_results
