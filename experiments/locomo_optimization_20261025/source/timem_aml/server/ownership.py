"""Ownership guard for AML search results.

The AML contract forbids returning another user's memory. Several retrieval
paths (vector / BM25 / parent-chain) have historically been able to leak rows,
so the API layer verifies ownership against the authoritative table before
emitting anything.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Sequence


def _connect():
    import psycopg2

    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "timem_user"),
        password=os.getenv("POSTGRES_PASSWORD", "timem_password"),
        dbname=os.getenv("POSTGRES_DATABASE", "timem_db"),
        connect_timeout=5,
    )


def filter_owned(user_id: str, rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep only rows whose core_memories.user_id matches `user_id`."""
    if not user_id or not rows:
        return list(rows)

    ids = [str(r.get("id")) for r in rows if r.get("id")]
    if not ids:
        return list(rows)

    try:
        conn = _connect()
    except Exception:
        # If the guard itself fails, fail closed: drop everything rather than leak.
        return []

    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "select id from core_memories where user_id = %s and id = any(%s)",
                (user_id, ids),
            )
            owned = {str(row[0]) for row in cur.fetchall()}
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return [r for r in rows if str(r.get("id")) in owned]
