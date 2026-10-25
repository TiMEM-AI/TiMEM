"""Adapter factory for benchmark systems."""

from __future__ import annotations

from adapters.base import MemoryAdapter
from adapters.mem0_platform_adapter import Mem0PlatformAdapter
from adapters.memos_cloud_adapter import MemOSCloudAdapter
from adapters.timem_adapter import TiMEMAdapter

SUPPORTED_SYSTEMS = ("timem", "memos", "mem0")

_ADAPTER_TYPES: dict[str, type[MemoryAdapter]] = {
    "timem": TiMEMAdapter,
    "memos": MemOSCloudAdapter,
    "mem0": Mem0PlatformAdapter,
}


def get_adapters(systems: list[str] | None = None) -> dict[str, MemoryAdapter]:
    """Instantiate adapters for the requested system names."""
    names = systems or list(SUPPORTED_SYSTEMS)
    adapters: dict[str, MemoryAdapter] = {}
    for name in names:
        key = name.strip().lower()
        if key not in _ADAPTER_TYPES:
            raise ValueError(f"Unknown system: {name!r}. Supported: {', '.join(SUPPORTED_SYSTEMS)}")
        adapters[key] = _ADAPTER_TYPES[key]()
    return adapters


async def close_adapters(adapters: dict[str, MemoryAdapter]) -> None:
    for adapter in adapters.values():
        await adapter.close()
