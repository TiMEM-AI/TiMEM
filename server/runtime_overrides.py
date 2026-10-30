"""Runtime overrides for AML memory model (hot-switch, no restart)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


FORBIDDEN_OVERRIDE_KEYS = frozenset({
    "embed_model",
    "embed",
    "dimensions",
    "embed_dim",
    "XINGLIU_EMBED_MODEL",
    "XINGLIU_EMBED_DIM",
})


class RuntimeOverridesStore:
    """Read/write data/runtime_overrides.json with mtime-backed memory cache."""

    def __init__(self, path: str, audit_path: Optional[str] = None):
        self.path = Path(path)
        self.audit_path = Path(audit_path) if audit_path else self.path.parent / "model_switch_audit.jsonl"
        self._cache: dict[str, Any] = {}
        self._mtime: Optional[float] = None

    def _refresh(self) -> dict[str, Any]:
        if not self.path.is_file():
            self._cache = {}
            self._mtime = None
            return self._cache
        mtime = self.path.stat().st_mtime
        if self._mtime is not None and mtime == self._mtime:
            return self._cache
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        self._cache = data
        self._mtime = mtime
        return self._cache

    def snapshot(self) -> dict[str, Any]:
        return dict(self._refresh())

    def memory_model(self) -> Optional[str]:
        value = self._refresh().get("memory_model")
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def resolve_memory_model(self, env_default: Optional[str] = None) -> tuple[str, str]:
        """Return (model_name, source) where source is override|env."""
        override = self.memory_model()
        if override:
            return override, "override"
        fallback = (
            env_default
            if env_default is not None
            else os.getenv("XINGLIU_CHAT_MODEL", "glm-5.3-flash")
        ).strip()
        return fallback or "glm-5.3-flash", "env"

    def _atomic_write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(data, ensure_ascii=False, indent=2)
        fd, tmp_name = tempfile.mkstemp(
            prefix="runtime_overrides_", suffix=".json", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        self._cache = data
        self._mtime = self.path.stat().st_mtime

    def _append_audit(self, event: dict[str, Any]) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def set_memory_model(self, memory_model: str, updated_by: str = "admin") -> dict[str, Any]:
        model = (memory_model or "").strip()
        if not model:
            raise ValueError("memory_model is required")
        current = self._refresh()
        old = current.get("memory_model")
        payload = {
            "memory_model": model,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": (updated_by or "admin").strip() or "admin",
        }
        self._atomic_write(payload)
        self._append_audit(
            {
                "ts": payload["updated_at"],
                "action": "set_memory_model",
                "old": old,
                "new": model,
                "updated_by": payload["updated_by"],
                "source": "admin",
            }
        )
        return payload

    def clear_memory_model(self, updated_by: str = "admin") -> dict[str, Any]:
        current = self._refresh()
        old = current.get("memory_model")
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": (updated_by or "admin").strip() or "admin",
        }
        self._atomic_write(payload)
        self._append_audit(
            {
                "ts": payload["updated_at"],
                "action": "clear_memory_model",
                "old": old,
                "new": None,
                "updated_by": payload["updated_by"],
                "source": "admin",
            }
        )
        return {"cleared": True, "old": old}


def reject_forbidden_body(body: dict[str, Any]) -> None:
    for key in FORBIDDEN_OVERRIDE_KEYS:
        if key in body and body.get(key) not in (None, "", 0):
            raise ValueError(
                f"hot-switch of {key} is not allowed; rebuild the vector collection instead"
            )