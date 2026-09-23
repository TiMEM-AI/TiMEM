"""Request-scoped model and OpenAI-compatible credentials."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass(frozen=True)
class LLMRuntime:
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    embed_model: Optional[str] = None
    embed_base_url: Optional[str] = None
    embed_api_key: Optional[str] = None
    embed_dim: Optional[int] = None


_runtime: ContextVar[Optional[LLMRuntime]] = ContextVar("timem_llm_runtime", default=None)


def current_runtime() -> Optional[LLMRuntime]:
    return _runtime.get()


def current_memory_model() -> Optional[str]:
    runtime = _runtime.get()
    return runtime.model if runtime else None


@contextmanager
def memory_model_scope(
    name: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    embed_model: Optional[str] = None,
    embed_base_url: Optional[str] = None,
    embed_api_key: Optional[str] = None,
    embed_dim: Optional[int] = None,
) -> Iterator[None]:
    if not any([name, base_url, api_key, embed_model, embed_base_url, embed_api_key, embed_dim]):
        yield
        return
    token = _runtime.set(LLMRuntime(
        model=name,
        base_url=base_url,
        api_key=api_key,
        embed_model=embed_model,
        embed_base_url=embed_base_url,
        embed_api_key=embed_api_key,
        embed_dim=embed_dim,
    ))
    try:
        yield
    finally:
        _runtime.reset(token)
