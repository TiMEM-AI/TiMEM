"""TiMem LLM adapters."""

def get_llm(provider: str = None, **kwargs):
    from .llm_manager import get_llm as _get_llm
    return _get_llm(provider)

__all__ = ["get_llm"]
