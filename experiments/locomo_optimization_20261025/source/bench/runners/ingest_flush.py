"""Mem0 deferred poll flush helpers."""

from __future__ import annotations

from typing import Any

from adapters.base import MemoryAdapter
from adapters.mem0_platform_adapter import Mem0PlatformAdapter


async def flush_mem0_pending(adapters: dict[str, MemoryAdapter]) -> dict[str, Any]:
    """Wait for any Mem0 events still pending after ingest (no-op if none)."""
    adapter = adapters.get("mem0")
    if not isinstance(adapter, Mem0PlatformAdapter):
        return {}
    return await adapter.flush_pending_events()
