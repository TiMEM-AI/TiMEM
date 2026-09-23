"""
TiMem Embedding Vector Service Module
Provides unified text embedding vector service interface
"""

import asyncio
from typing import List, Dict, Any, Optional
from abc import ABC, abstractmethod
import numpy as np
import hashlib
import json

from typing import TYPE_CHECKING
from llm.base_llm import BaseEmbeddingService, ModelConfig
from llm.qwen_embedding_adapter import Qwen3EmbeddingService
from timem.utils.logging import get_logger
from timem.utils.config_manager import get_config

if TYPE_CHECKING:
    from llm.openai_adapter import OpenAIAdapter
    from llm.zhipuai_adapter import ZhipuAIAdapter

# Initialize module-level logger
logger = get_logger(__name__)


class EmbeddingService(ABC):
    """Embedding vector service abstract base class"""
    
    @abstractmethod
    async def embed_text(self, text: str) -> List[float]:
        """Text embedding"""
        pass
    
    @abstractmethod
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Batch text embedding"""
        pass
    
    @abstractmethod
    def get_dimension(self) -> int:
        """Get vector dimension"""
        pass


class OpenAIEmbeddingService(EmbeddingService):
    """OpenAI embedding service"""
    
    def __init__(self, openai_adapter: 'OpenAIAdapter'):
        self.adapter = openai_adapter
        self.logger = get_logger(__name__)
        self.model_name = "text-embedding-ada-002"
        
    async def embed_text(self, text: str) -> List[float]:
        """Text embedding"""
        try:
            response = await self.adapter.embed(text)
            return response.embedding
        except Exception as e:
            self.logger.error(f"OpenAI text embedding failed: {e}")
            raise
            
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Batch text embedding"""
        try:
            embeddings = []
            for text in texts:
                embedding = await self.embed_text(text)
                embeddings.append(embedding)
            return embeddings
        except Exception as e:
            self.logger.error(f"OpenAI batch embedding failed: {e}")
            raise
    
    def get_dimension(self) -> int:
        """Get vector dimension"""
        # Return dimension based on actual model used
        if hasattr(self, 'model_name') and self.model_name in ["text-embedding-3-small", "text-embedding-ada-002"]:
            return 1536  # OpenAI text-embedding-ada-002 and text-embedding-3-small dimension
        elif hasattr(self, 'model_name') and self.model_name == "text-embedding-3-large":
            return 3072  # OpenAI text-embedding-3-large dimension
        else:
            # If current project mainly uses SentenceBERT, return 384 for compatibility
            return 384


class ZhipuAIEmbeddingService(EmbeddingService):
    """ZhipuAI embedding service"""
    
    def __init__(self, zhipuai_adapter: 'ZhipuAIAdapter'):
        self.adapter = zhipuai_adapter
        self.logger = get_logger(__name__)
        self.model_name = "embedding-3"
        
    async def embed_text(self, text: str) -> List[float]:
        """Text embedding"""
        try:
            response = await self.adapter.embed(text)
            return response.embedding
        except Exception as e:
            self.logger.error(f"ZhipuAI text embedding failed: {e}")
            raise
            
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Batch text embedding"""
        try:
            responses = await self.adapter.embed_batch(texts)
            return [response.embedding for response in responses]
        except Exception as e:
            self.logger.error(f"ZhipuAI batch embedding failed: {e}")
            raise
    
    def get_dimension(self) -> int:
        """Get vector dimension"""
        # Return dimension based on model name
        if hasattr(self.adapter, 'embedding_model_name'):
            model_name = self.adapter.embedding_model_name
            if model_name == "embedding-3":
                return 1536  # embedding-3 dimension
            elif model_name == "embedding-2":
                return 1024  # embedding-2 dimension
            else:
                # If current project mainly uses SentenceBERT, return 384 for compatibility
                return 384
        # If current project mainly uses SentenceBERT, return 384 for compatibility
        return 384


class XingliuEmbeddingService(EmbeddingService):
    """Xingliu embedding. Rejects vectors whose length is not the requested dimension."""

    def __init__(self, adapter):
        self.adapter = adapter
        self.logger = get_logger(__name__)
        self.model_name = adapter.embed_model
        self._dimension = adapter.embed_dim

    async def embed_text(self, text: str) -> List[float]:
        response = await self.adapter.embed(text, dimensions=self._dimension)
        return response.embedding

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        responses = await self.adapter.embed_batch(texts, dimensions=self._dimension)
        return [response.embedding for response in responses]

    def get_dimension(self) -> int:
        return self._dimension


class SentenceBERTEmbeddingService(BaseEmbeddingService):
    """SentenceBERT local embedding service"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.model = None
        self.model_name = self.config.get('model', 'all-MiniLM-L6-v2')
        self.device = self.config.get('device', 'cpu')
        self.max_length = self.config.get('max_length', 512)
        self.normalize_embeddings = self.config.get('normalize_embeddings', True)
        self.batch_size = self.config.get('batch_size', 64)

        try:
            # Lazy import: only import sentence_transformers when actually using this class
            from sentence_transformers import SentenceTransformer
            # Attempt to load model
            self.model = SentenceTransformer(self.model_name, device=self.device)
            self.logger.info(f"SentenceBERT model loaded successfully: {self.model_name}")
        except ImportError:
            self.logger.error("sentence_transformers package not installed. Install it with: pip install sentence-transformers")
            raise ImportError("sentence_transformers is required for SentenceBERT embedding service")
        except Exception as e:
            self.logger.error(f"SentenceBERT model loading failed: {e}")
            raise

    async def embed_text(self, text: str) -> List[float]:
        """Text embedding"""
        if not self.model:
            raise RuntimeError("SentenceBERT model not loaded")
        
        # Asynchronously execute encoding
        loop = asyncio.get_running_loop()
        embedding = await loop.run_in_executor(
            None, 
            lambda: self.model.encode(text, normalize_embeddings=self.normalize_embeddings)
        )
        return embedding.tolist()

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Batch text embedding"""
        if not self.model:
            raise RuntimeError("SentenceBERT model not loaded")
        
        # Asynchronously execute encoding
        loop = asyncio.get_running_loop()
        embeddings = await loop.run_in_executor(
            None, 
            lambda: self.model.encode(
                texts, 
                batch_size=self.batch_size,
                normalize_embeddings=self.normalize_embeddings
            )
        )
        return [emb.tolist() for emb in embeddings]
    
    def get_dimension(self) -> int:
        """Get vector dimension"""
        if not self.model:
            # Return default dimension or dimension from config file
            return 384
        return self.model.get_sentence_embedding_dimension()


class CachedEmbeddingService(EmbeddingService):
    """Cached embedding service"""
    
    def __init__(self, base_service: EmbeddingService, cache_size: int = 10000):
        self.base_service = base_service
        self.cache = {}
        self.cache_size = cache_size
        self.logger = get_logger(__name__)
        
    def _get_cache_key(self, text: str) -> str:
        """Generate cache key"""
        return hashlib.md5(text.encode()).hexdigest()
        
    async def embed_text(self, text: str) -> List[float]:
        """Text embedding with cache"""
        cache_key = self._get_cache_key(text)
        
        # Check cache
        if cache_key in self.cache:
            self.logger.debug(f"Retrieved embedding from cache: {cache_key}")
            return self.cache[cache_key]
        
        # Compute embedding
        embedding = await self.base_service.embed_text(text)
        
        # Cache management
        if len(self.cache) >= self.cache_size:
            # Remove oldest cache item
            oldest_key = next(iter(self.cache))
            del self.cache[oldest_key]
        
        self.cache[cache_key] = embedding
        return embedding
        
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Batch text embedding with cache"""
        embeddings = []
        uncached_texts = []
        uncached_indices = []
        
        # Check which texts need to be computed
        for i, text in enumerate(texts):
            cache_key = self._get_cache_key(text)
            if cache_key in self.cache:
                embeddings.append(self.cache[cache_key])
            else:
                embeddings.append(None)
                uncached_texts.append(text)
                uncached_indices.append(i)
        
        # Compute uncached embeddings in batch
        if uncached_texts:
            new_embeddings = await self.base_service.embed_batch(uncached_texts)
            
            # Update cache and results
            for i, embedding in enumerate(new_embeddings):
                index = uncached_indices[i]
                text = uncached_texts[i]
                cache_key = self._get_cache_key(text)
                
                # Cache management
                if len(self.cache) >= self.cache_size:
                    oldest_key = next(iter(self.cache))
                    del self.cache[oldest_key]
                
                self.cache[cache_key] = embedding
                embeddings[index] = embedding
        
        return embeddings
    
    def get_dimension(self) -> int:
        """Get vector dimension"""
        return self.base_service.get_dimension()


class UnifiedEmbeddingService:
    """Unified embedding service - manages multiple embedding services and provides a unified interface"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or get_config("llm")
        self.default_service: Optional[EmbeddingService] = None
        self.provider_name: str = "mock"
        self.logger = get_logger(__name__)
        self._initialize_services()

    def _initialize_services(self):
        """Initialize embedding services"""
        embedding_config = self.config.get('embedding', {})
        self.provider_name = embedding_config.get('provider', 'sentence_bert')
        
        try:
            if self.provider_name == 'openai':
                # OpenAI embedding service - uses global LLM manager
                from llm.llm_manager import get_llm
                adapter = get_llm('openai')
                self.default_service = OpenAIEmbeddingService(adapter)
                self.logger.info("Using OpenAI embedding service")
            
            elif self.provider_name == 'zhipuai':
                # ZhipuAI embedding service - uses global LLM manager
                from llm.llm_manager import get_llm
                adapter = get_llm('zhipuai')
                self.default_service = ZhipuAIEmbeddingService(adapter)
                self.logger.info("Using ZhipuAI embedding service")

            elif self.provider_name == 'sentence_bert':
                # SentenceBERT local embedding service
                self.default_service = SentenceBERTEmbeddingService(embedding_config)
                self.logger.info("Using SentenceBERT embedding service")
                
            elif self.provider_name == 'qwen_local':
                # Qwen3 local embedding service
                self.default_service = Qwen3EmbeddingService(embedding_config)
                self.logger.info(f"Using local Qwen3 Embedding service: {embedding_config.get('model', 'not specified')}")

            elif self.provider_name == 'xingliu':
                from llm.xingliu_adapter import XingliuAdapter
                self.default_service = XingliuEmbeddingService(XingliuAdapter())
                self.logger.info("Using Xingliu embedding service")
            
            else:
                self.logger.warning(f"Unknown embedding service provider: {self.provider_name}, using fallback")
                raise ValueError(f"Unknown embedding service provider: {self.provider_name}")

        except Exception as e:
            self.logger.error(f"Embedding service initialization failed: {e}")
            
            # Attempt to use fallback service
            fallback_config = embedding_config.get('fallback', {})
            fallback_provider = fallback_config.get('provider')
            
            self.logger.warning(f"Attempting to use fallback service: {fallback_provider or 'SentenceBERT'}")
            
            try:
                if fallback_provider == 'zhipuai':
                    # Fallback ZhipuAI embedding - uses global LLM manager
                    from llm.llm_manager import get_llm
                    adapter = get_llm('zhipuai')
                    self.default_service = ZhipuAIEmbeddingService(adapter)
                    self.provider_name = "zhipuai"
                    self.logger.info("Using fallback ZhipuAI embedding service")
                else:
                    # Default fallback to SentenceBERT
                    self.default_service = SentenceBERTEmbeddingService(embedding_config)
                    self.provider_name = "sentence_bert"
                    self.logger.info("Using fallback SentenceBERT embedding service")
            except Exception as e_fallback:
                self.logger.error(f"Fallback embedding service initialization failed: {e_fallback}")
                self.logger.warning("All embedding services initialization failed, using mock service")
                self.default_service = MockEmbeddingService()
                self.provider_name = "mock"

    async def embed_text(self, text: str) -> List[float]:
        """Text embedding"""
        if not self.default_service:
            raise RuntimeError("No available embedding service")
        return await self.default_service.embed_text(text)
    
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Batch text embedding"""
        if not self.default_service:
            raise RuntimeError("No available embedding service")
        return await self.default_service.embed_batch(texts)
    
    def get_dimension(self) -> int:
        """Get vector dimension"""
        if not self.default_service:
            return 384  # Default dimension
        return self.default_service.get_dimension()
    
    def get_provider_name(self) -> str:
        """Get current provider name"""
        return self.provider_name

    def get_model_info(self) -> Dict[str, Any]:
        """Get model information"""
        if isinstance(self.default_service, SentenceBERTEmbeddingService):
            return {
                "model_name": self.default_service.model_name,
                "dimension": self.default_service.get_dimension(),
                "device": self.default_service.device,
                "max_length": self.default_service.max_length,
                "provider": "sentence_bert"
            }
        elif isinstance(self.default_service, Qwen3EmbeddingService):
            return {
                "model_name": self.default_service.model_name,
                "dimension": self.default_service.get_dimension(),
                "device": self.default_service.device,
                "max_length": self.default_service.max_length,
                "provider": "qwen_local"
            }
        elif isinstance(self.default_service, ZhipuAIEmbeddingService):
            return {
                "model_name": self.default_service.model_name,
                "dimension": self.default_service.get_dimension(),
                "provider": "zhipuai"
            }
        elif isinstance(self.default_service, OpenAIEmbeddingService):
             return {
                "model_name": self.default_service.model_name,
                "dimension": self.default_service.get_dimension(),
                "provider": "openai"
            }
        return {"provider": "mock"}


class MockEmbeddingService(BaseEmbeddingService):
    """Mock embedding service - for testing and fallback"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._embedding_dim = 384  # Keep 384 dimension
        self.logger = get_logger(__name__)
        
    async def embed_text(self, text: str) -> List[float]:
        """Generate mock vector"""
        import hashlib
        import random
        
        # Use text hash as random seed to ensure same text generates same vector
        seed = int(hashlib.md5(text.encode()).hexdigest(), 16)
        random.seed(seed)
        
        # Generate 384-dimensional mock vector
        vector = [random.uniform(-1, 1) for _ in range(self._embedding_dim)]
        
        # Normalize vector
        magnitude = sum(x * x for x in vector) ** 0.5
        if magnitude > 0:
            vector = [x / magnitude for x in vector]
        
        self.logger.warning(f"Using mock embedding service: {text[:50]}...")
        return vector
    
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Batch generate mock vectors"""
        return [await self.embed_text(text) for text in texts]
    
    def get_dimension(self) -> int:
        """Get mock vector dimension"""
        return self._embedding_dim


# Global embedding service instance
_embedding_service = None
_embedding_lock = asyncio.Lock()


async def init_embedding_service(config: Optional[Dict[str, Any]] = None) -> UnifiedEmbeddingService:
    """
    Explicitly initialize embedding service (hot loading)
    
    Call this function at application startup to pre-load models into memory, avoiding delay on first request
    
    Args:
        config: Configuration dictionary, optional
        
    Returns:
        UnifiedEmbeddingService instance
    """
    global _embedding_service
    
    async with _embedding_lock:
        if _embedding_service is None:
            _embedding_service = UnifiedEmbeddingService(config)
            logger.info("Embedding service hot loaded into memory")
        else:
            logger.info("Embedding service already initialized, skipping re-initialization")
    
    return _embedding_service


def get_embedding_service() -> UnifiedEmbeddingService:
    """
    Get global unified embedding service instance (lazy loading mode, compatible with legacy code)
    
    Recommended to use init_embedding_service() for hot loading at application startup
    """
    global _embedding_service
    if _embedding_service is None:
        # Lazy loading mode (compatibility)
        logger.warning(" Embedding service using lazy loading mode, recommended to use init_embedding_service() for hot loading at startup")
        _embedding_service = UnifiedEmbeddingService()
    return _embedding_service