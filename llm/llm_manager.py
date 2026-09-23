"""
TiMem LLM Manager
Responsible for dynamically loading and managing different LLM adapters based on configuration.
"""
from typing import Optional
from functools import lru_cache

from .base_llm import BaseLLM
from .openai_adapter import OpenAIAdapter
from .claude_adapter import ClaudeAdapter
from .zhipuai_adapter import ZhipuAIAdapter
from .mock_adapter import MockLLMAdapter
from .qwen_adapter import QwenAdapter
from .qwen_local_adapter import QwenLocalAdapter
from .xingliu_adapter import XingliuAdapter
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
        return OpenAIAdapter()
    elif provider == "claude":
        return ClaudeAdapter()
    elif provider == "zhipuai":
        return ZhipuAIAdapter()
    elif provider == "qwen":
        return QwenAdapter()
    elif provider == "qwen_local":
        return QwenLocalAdapter()
    elif provider == "xingliu":
        return XingliuAdapter()
    elif provider == "mock":
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

def get_mock_llm_for_testing() -> MockLLMAdapter:
    """
    Get mock LLM instance, specifically for testing.
    
    Returns:
        MockLLMAdapter: Mock LLM adapter instance
    """
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