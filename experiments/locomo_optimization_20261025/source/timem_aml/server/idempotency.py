"""Persist AML Add request ids so retries do not write twice."""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional


class IdempotencyStore:
    def __init__(self, path: str):
        self._path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS adds (request_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def get(self, request_id: str) -> Optional[dict]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM adds WHERE request_id = ?", (request_id,)
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def put(self, request_id: str, payload: dict) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO adds (request_id, payload) VALUES (?, ?)",
                (request_id, json.dumps(payload)),
            )
