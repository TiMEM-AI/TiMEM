"""
TiMem LLM Manager
Responsible for dynamically loading and managing different LLM adapters based on configuration.
"""
from typing import Optional
from functools import lru_cache

from .base_llm import BaseLLM
from timem.utils.config_manager import get_llm_config
from timem.utils.logging import get_logger

logger = get_logger(__name__)

@lru_cache(maxsize=8)
def _get_llm_cached(provider: str) -> BaseLLM:
    """
    Get LLM adapter instance for specified provider (cached version).
    """
    logger.info(f"Getting LLM adapter, provider: {provider}")

    if provider == "openai":
        from .openai_adapter import OpenAIAdapter
        return OpenAIAdapter()
    elif provider == "claude":
        from .claude_adapter import ClaudeAdapter
        return ClaudeAdapter()
    elif provider == "zhipuai":
        from .zhipuai_adapter import ZhipuAIAdapter
        return ZhipuAIAdapter()
    elif provider == "qwen":
        from .qwen_adapter import QwenAdapter
        return QwenAdapter()
    elif provider == "qwen_local":
        from .qwen_local_adapter import QwenLocalAdapter
        return QwenLocalAdapter()
    elif provider == "xingliu":
        from .xingliu_adapter import XingliuAdapter
        return XingliuAdapter()
    elif provider == "mock":
        from .mock_adapter import MockLLMAdapter
        return MockLLMAdapter()
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")


def get_llm(provider: Optional[str] = None) -> BaseLLM:
    """
    Get LLM adapter instance for specified provider.

    Args:
        provider (str, optional): LLM provider name (e.g., "openai", "zhipuai", "mock").
                                  If None, uses default provider from configuration.

    Returns:
        BaseLLM: LLM adapter instance.
    """
    llm_config = get_llm_config()
    
    # If provider not specified, use default provider from configuration
    if provider is None:
        provider = llm_config.get("default_provider", "openai")
    
    # Use cached version to get instance, ensuring same provider returns same instance
    return _get_llm_cached(provider)

def get_mock_llm_for_testing():
    """
    Get mock LLM instance, specifically for testing.
    
    Returns:
        MockLLMAdapter: Mock LLM adapter instance
    """
    from .mock_adapter import MockLLMAdapter
    return MockLLMAdapter()

def set_mock_mode(enabled: bool = True):
    """
    Set mock mode for quick testing of each level logic.
    
    Args:
        enabled (bool): Whether to enable mock mode
    """
    if enabled:
        logger.info("Enabling mock LLM mode for quick testing")
        # Can set global configuration here to make all LLM calls use mock mode
        # Or modify default provider to mock
    else:
        logger.info("Disabling mock LLM mode, using real LLM")