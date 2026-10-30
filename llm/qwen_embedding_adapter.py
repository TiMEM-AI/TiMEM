"""
Qwen3 Embedding Local Model Adapter
Supports using local Qwen3 Embedding model for text vectorization
"""
import os
import time
import asyncio
from typing import List, Dict, Any, Optional
import numpy as np

from .base_llm import BaseEmbeddingService, EmbeddingResponse, ModelConfig
from timem.utils.config_manager import get_llm_config
from timem.utils.logging import get_logger

logger = get_logger(__name__)

class Qwen3EmbeddingService(BaseEmbeddingService):
    """Qwen3 Embedding local model embedding service"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.model = None
        self.tokenizer = None
        self.model_name = self.config.get('model', 'D:\\LLM\\Qwen3-Embedding-0.6B')
        self.device = self.config.get('device', 'cpu')
        self.max_length = self.config.get('max_length', 512)
        self.normalize_embeddings = self.config.get('normalize_embeddings', True)
        self.batch_size = self.config.get('batch_size', 32)
        
        try:
            # Check if model path exists
            if not os.path.exists(self.model_name):
                raise FileNotFoundError(f"Qwen3 Embedding model path does not exist: {self.model_name}")
                
            # Lazy import: torch/transformers only needed for local Qwen embedding
            try:
                import torch  # noqa: F401
                from transformers import AutoTokenizer, AutoModel
            except ImportError as e:
                raise ImportError(
                    "torch and transformers are required for qwen_local embedding; "
                    "install optional local-model deps or use an API embedding provider"
                ) from e

            # Load model and tokenizer
            logger.info(f"Loading Qwen3 Embedding model: {self.model_name}")
            self.tokenizer = AutoTokenizer.from_pretrained(self.model_name, trust_remote_code=True)
            self.model = AutoModel.from_pretrained(self.model_name, trust_remote_code=True)
            
            # Set device
            self.model = self.model.to(self.device)
            self.model.eval()
            
            # Get model dimension
            self._embedding_dim = self.model.config.hidden_size
            
            logger.info(f"Qwen3 Embedding model loaded successfully: {self.model_name}, dimension: {self._embedding_dim}")
        except Exception as e:
            logger.error(f"Qwen3 Embedding model loading failed: {e}")
            raise
    
    def _get_embeddings(self, texts, batch_size=32):
        """Get text embedding vectors"""
        all_embeddings = []
        
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i+batch_size]
            inputs = self.tokenizer(batch_texts, padding=True, truncation=True, 
                                   max_length=self.max_length, return_tensors="pt")
            inputs = {k: v.to(self.device) for k, v in inputs.items()}
            
            import torch
            with torch.no_grad():
                outputs = self.model(**inputs)
                
                # Fix: Use average pooling instead of just taking the first token
                # Get attention_mask for correct average calculation
                attention_mask = inputs['attention_mask']
                
                # Expand attention_mask dimension to match hidden_states
                attention_mask_expanded = attention_mask.unsqueeze(-1).expand(outputs.last_hidden_state.size()).float()
                
                # Calculate weighted average (ignore padding tokens)
                sum_embeddings = torch.sum(outputs.last_hidden_state * attention_mask_expanded, 1)
                sum_mask = torch.clamp(attention_mask_expanded.sum(1), min=1e-9)
                embeddings = (sum_embeddings / sum_mask).cpu().numpy()
                
                if self.normalize_embeddings:
                    # Normalize embedding vectors
                    embeddings = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)
                
                all_embeddings.extend(embeddings)
        
        return all_embeddings

    async def embed_text(self, text: str) -> List[float]:
        """Text embedding"""
        if not self.model or not self.tokenizer:
            raise RuntimeError("Qwen3 Embedding model not loaded")
        
        # Execute encoding asynchronously
        loop = asyncio.get_running_loop()
        start_time = time.time()
        
        try:
            embedding = await loop.run_in_executor(
                None, 
                lambda: self._get_embeddings([text], batch_size=1)[0]
            )
            
            end_time = time.time()
            logger.debug(f"Qwen3 Embedding single text embedding elapsed: {end_time - start_time:.3f}s")
            
            return embedding.tolist()
        except Exception as e:
            logger.error(f"Qwen3 Embedding text embedding failed: {e}")
            raise

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Batch text embedding"""
        if not self.model or not self.tokenizer:
            raise RuntimeError("Qwen3 Embedding model not loaded")
        
        # Execute encoding asynchronously
        loop = asyncio.get_running_loop()
        start_time = time.time()
        
        try:
            embeddings = await loop.run_in_executor(
                None, 
                lambda: self._get_embeddings(texts, batch_size=self.batch_size)
            )
            
            end_time = time.time()
            logger.debug(f"Qwen3 Embedding batch embedding ({len(texts)} items) elapsed: {end_time - start_time:.3f}s")
            
            return [emb.tolist() for emb in embeddings]
        except Exception as e:
            logger.error(f"Qwen3 Embedding batch embedding failed: {e}")
            raise
    
    def get_dimension(self) -> int:
        """Get vector dimension"""
        if hasattr(self, '_embedding_dim'):
            return self._embedding_dim
        
        # Default value - Qwen3-Embedding-0.6B dimension is 1536
        return 1536
