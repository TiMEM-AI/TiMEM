"""Run / persona id helpers."""

from __future__ import annotations

import re
from datetime import datetime

_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_RESERVED_RUN_IDS = frozenset({"new", "runs", "delete"})


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def validate_run_id(run_id: str) -> str:
    """Normalize and validate a user-supplied run_id."""
    rid = (run_id or "").strip()
    if not rid:
        raise ValueError("run_id cannot be empty")
    if "/" in rid or "\\" in rid or ".." in rid:
        raise ValueError("run_id must not contain path separators")
    if rid.lower() in _RESERVED_RUN_IDS:
        raise ValueError(f"run_id {rid!r} is reserved")
    if not _RUN_ID_PATTERN.match(rid):
        raise ValueError(
            "run_id must be 1-64 chars: letters, digits, underscore, hyphen; "
            "must start with a letter or digit"
        )
    return rid


def session_id_for(persona_id: str, index: int) -> str:
    return f"{persona_id}_session_{index:02d}"


_TIMEM_SESSION_ID_MAX_LEN = 128


def scoped_session_id(run_id: str, session_id: str) -> str:
    """Namespace LoCoMo session_id per benchmark run for TiMEM memory_sessions isolation."""
    rid = validate_run_id(run_id)
    sid = (session_id or "").strip()
    if not sid:
        raise ValueError("session_id cannot be empty")
    scoped = f"{rid}_{sid}"
    if len(scoped) > _TIMEM_SESSION_ID_MAX_LEN:
        raise ValueError(
            f"scoped session_id length {len(scoped)} exceeds {_TIMEM_SESSION_ID_MAX_LEN}; "
            "use a shorter run_id"
        )
    return scoped
