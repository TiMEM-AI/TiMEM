"""
TiMem Vector Storage Adapter
Implements vector storage and retrieval functionality based on Qdrant
"""

import asyncio
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
import uuid
from dataclasses import dataclass, asdict
from qdrant_client import QdrantClient, models
from qdrant_client.http import models as http_models
from qdrant_client.http.exceptions import ResponseHandlingException
from qdrant_client.http.models import UpdateStatus

from timem.utils.logging import get_logger
from timem.utils.config_manager import get_storage_config
from timem.utils.qdrant_utils import ensure_collection_with_correct_dim, _create_new_collection
from timem.utils.time_utils import ensure_iso_format


@dataclass
class VectorPoint:
    """Vector point data structure"""
    id: str
    vector: List[float]
    payload: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)


@dataclass
class VectorSearchResult:
    """Vector search result"""
    id: str
    score: float
    payload: Dict[str, Any]
    vector: Optional[List[float]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)


class VectorStore:
    """Vector storage adapter - based on Qdrant"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        if config is None:
            # Get latest config from ConfigManager each time (supports dataset config)
            try:
                from timem.utils.config_manager import get_config_manager
                config_manager = get_config_manager()
                storage_config = config_manager.get_storage_config()
                self.config = storage_config.get('vector', {})
                self.logger = get_logger(__name__)
                self.logger.info(f"Loaded config from ConfigManager: url={self.config.get('url')}, collection={self.config.get('collection_name')}")
            except Exception as e:
                # If config loading fails, use environment variables (no hardcoded defaults)
                import os
                self.logger = get_logger(__name__)
                self.logger.warning(f"Failed to load config from ConfigManager, using environment variables: {e}")
                self.config = {
                    'url': os.getenv('QDRANT_URL'),
                    'api_key': os.getenv('QDRANT_API_KEY'),
                    'collection_name': os.getenv('QDRANT_COLLECTION', 'timem_memories'),
                    'vector_size': int(os.getenv('QDRANT_VECTOR_SIZE', '2048')),
                    'distance': os.getenv('QDRANT_DISTANCE', 'Cosine'),
                    'timeout': int(os.getenv('QDRANT_TIMEOUT', '30')),
                    'retry_count': int(os.getenv('QDRANT_RETRY_COUNT', '3'))
                }
        else:
            self.config = config
            self.logger = get_logger(__name__)

        self.client = None
        # Completely rely on config, no hardcoded defaults
        import os
        if os.getenv('QDRANT_URL'):
            self.config['url'] = os.getenv('QDRANT_URL')
        if os.getenv('QDRANT_VECTOR_SIZE'):
            self.config['vector_size'] = int(os.getenv('QDRANT_VECTOR_SIZE'))
        self.collection_name = self.config.get('collection_name')
        self.vector_size = self.config.get('vector_size', 2048)
        self.distance = self.config.get('distance', 'Cosine')
        self._initialized = False
        
        # Intelligent dimension management
        self._dimension_cache = {}
        self._last_check_time = {}
        self._check_interval = 300  # Check every 5 minutes
        self._current_collection_dimension = None
        
    async def _ensure_initialized(self):
        """Ensure client is initialized"""
        if not self._initialized:
            await self._initialize_client()
    
    async def _initialize_client(self):
        """Initialize Qdrant client"""
        try:
            # Get URL from config, no hardcoded defaults
            import os
            url = self.config.get('url') or os.getenv('QDRANT_URL')
            api_key = self.config.get('api_key') or os.getenv('QDRANT_API_KEY')
            
            if not url:
                raise ValueError("Qdrant URL not configured, please set QDRANT_URL in dataset_profiles.yaml or environment variables")
            
            self.logger.info(f"Connecting to Qdrant: {url}")
            
            # Add connection timeout and retry mechanism
            if api_key and api_key.strip():
                self.client = QdrantClient(
                    url=url, 
                    api_key=api_key,
                    timeout=self.config.get('timeout', 30)
                )
            else:
                self.client = QdrantClient(
                    url=url,
                    timeout=self.config.get('timeout', 30)
                )
            
            # Test connection
            await self._test_connection()
            self._initialized = True
            self.logger.info(f"Qdrant client initialized successfully: {url}")
            
        except Exception as e:
            self.logger.error(f"Qdrant client initialization failed: {e}")
            raise
    
    async def _test_connection(self):
        """Test connection"""
        max_retries = self.config.get('retry_count', 3)
        
        for attempt in range(max_retries):
            try:
                # Get collection list to test connection
                await asyncio.to_thread(self.client.get_collections)
                self.logger.info(f"Qdrant connection test successful (attempt {attempt + 1}/{max_retries})")
                return
            except Exception as e:
                error_msg = str(e).lower()
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff
                    self.logger.warning(f"Qdrant connection test failed (attempt {attempt + 1}/{max_retries}), retry after {wait_time}s: {e}")
                    await asyncio.sleep(wait_time)
                    continue
                
                # Last attempt failed, throw detailed error
                self.logger.error(f"Qdrant connection test failed (all retries failed): {e}")
                if "getaddrinfo failed" in error_msg:
                    raise Exception(f"Qdrant service unreachable, please check if service is started: {e}")
                elif "connection refused" in error_msg:
                    raise Exception(f"Qdrant connection refused, please check if port is correct: {e}")
                elif "timeout" in error_msg:
                    raise Exception(f"Qdrant connection timeout, please check network connection: {e}")
                else:
                    raise Exception(f"Qdrant connection test failed: {e}")
    
    async def create_collection(self, collection_name: Optional[str] = None) -> bool:
        """
        Use utility function to ensure collection exists and is configured correctly.
        """
        await self._ensure_initialized()
        collection_name = collection_name or self.collection_name
        try:
            await asyncio.to_thread(
                ensure_collection_with_correct_dim,
                client=self.client,
                collection_name=collection_name,
                expected_vector_size=self.vector_size,
                distance_metric=self.distance
            )
            return True
        except Exception as e:
            self.logger.error(f"Failed to call ensure_collection_with_correct_dim in VectorStore: {e}", exc_info=True)
            return False
    
    async def recreate_collection(self) -> bool:
        """Delete and recreate collection, ensure indexes for key fields exist."""
        try:
            await asyncio.to_thread(self.client.delete_collection, collection_name=self.collection_name)
            self.logger.info(f"🗑️ Collection '{self.collection_name}' deleted successfully.")
        except Exception as e:
            error_str = str(e).lower()
            if "not found" in error_str or "404" in error_str:
                self.logger.info(f"ℹ️ Collection '{self.collection_name}' does not exist, no need to delete.")
            else:
                self.logger.error(f"Failed to delete collection '{self.collection_name}': {e}", exc_info=True)
                return False
        
        try:
            await asyncio.to_thread(_create_new_collection, self.client, self.collection_name, self.vector_size, self.distance)
            self.logger.info(f"✅ Collection '{self.collection_name}' created successfully via recreate.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to create collection '{self.collection_name}' in recreate process: {e}", exc_info=True)
            return False

    async def ensure_dimension_compatibility(self, expected_dimension: int) -> bool:
        """Ensure vector dimension is compatible with collection"""
        try:
            # Get current collection dimension
            current_dimension = await self._get_collection_dimension()
            
            if current_dimension is None:
                # Collection does not exist, create new collection
                return await self._create_collection_with_dimension(expected_dimension)
            
            if current_dimension == expected_dimension:
                # Dimension matches
                return True
            else:
                # Dimension mismatch, need to handle
                return await self._handle_dimension_mismatch(current_dimension, expected_dimension)
                
        except Exception as e:
            self.logger.error(f"Dimension compatibility check failed: {e}")
            return False
    
    async def _get_collection_dimension(self) -> Optional[int]:
        """Get vector dimension of collection"""
        try:
            collection_info = await asyncio.to_thread(self.client.get_collection, collection_name=self.collection_name)
            
            # For qdrant-client > 1.7.0
            if isinstance(collection_info.vectors_config, http_models.VectorsConfig):
                if 'default' in collection_info.vectors_config.params_map:
                    return collection_info.vectors_config.params_map['default'].size
            # For qdrant-client <= 1.7.0
            elif isinstance(collection_info.vectors_config, http_models.VectorParams):
                 return collection_info.vectors_config.params.size

            return None
            
        except Exception as e:
            error_str = str(e).lower()
            if "404" in error_str or "not found" in error_str:
                return None  # Collection does not exist
            else:
                self.logger.warning(f"Failed to get collection dimension: {e}, will return default dimension {self.vector_size}")
                return self.vector_size
    
    async def _create_collection_with_dimension(self, dimension: int) -> bool:
        """Create collection with specified dimension"""
        try:
            self.logger.info(f"🚀 Creating collection '{self.collection_name}' (dimension: {dimension})...")
            
            distance = http_models.Distance.COSINE if self.distance.upper() == 'COSINE' else http_models.Distance.DOT
            
            await asyncio.to_thread(
                self.client.create_collection,
                collection_name=self.collection_name,
                vectors_config=http_models.VectorParams(
                    size=dimension,
                    distance=distance
                )
            )
            
            self.logger.info(f"✅ Collection '{self.collection_name}' created successfully")
            return True
            
        except Exception as e:
            error_str = str(e).lower()
            if "already exists" in error_str or "409" in error_str:
                # Collection already exists, not an error
                self.logger.info(f"ℹ️ Collection '{self.collection_name}' already exists, skip creation")
                return True
            else:
                self.logger.error(f"❌ Failed to create collection: {e}")
                return False
    
    async def _handle_dimension_mismatch(self, current_dim: int, expected_dim: int) -> bool:
        """Handle dimension mismatch"""
        try:
            self.logger.warning(
                f"Collection '{self.collection_name}' dimension mismatch "
                f"(current: {current_dim}, required: {expected_dim})"
            )
            
            # For test collections, allow rebuild
            if self.collection_name.startswith('test_'):
                self.logger.info(f"Rebuild test collection '{self.collection_name}' to match dimension {expected_dim}")
                await self.recreate_collection()
                return True
            else:
                # For production collections, reject mismatched dimensions
                self.logger.error(
                    f"Production collection '{self.collection_name}' dimension mismatch, reject operation"
                )
                return False
                
        except Exception as e:
            self.logger.error(f"Failed to handle dimension mismatch: {e}")
            return False
    
    async def store_vector(self, vector: List[float], payload: Dict[str, Any], 
                          point_id: Optional[str] = None, 
                          collection_name: Optional[str] = None,
                          wait: bool = False) -> str:
        """Store vector"""
        await self._ensure_initialized()
        collection_name = collection_name or self.collection_name
        
        # Intelligent dimension compatibility check
        vector_dimension = len(vector)
        if not await self.ensure_dimension_compatibility(vector_dimension):
            raise ValueError(f"Vector dimension {vector_dimension} is incompatible with collection")
        
        # Handle point ID format - ensure valid UUID
        if point_id is None:
            point_id = str(uuid.uuid4())
        elif isinstance(point_id, str):
            # Check if already valid UUID
            try:
                uuid.UUID(point_id)
            except ValueError:
                # If not a valid UUID, generate a UUID based on the string
                point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, point_id))
        
        try:
            # Create point
            point = http_models.PointStruct(
                id=point_id,
                vector=vector,
                payload=payload
            )
            
            # Store point
            await asyncio.to_thread(
                self.client.upsert,
                collection_name=collection_name,
                points=[point],
                wait=wait
            )
            
            self.logger.debug(f"Vector stored successfully: {point_id}")
            return point_id
            
        except Exception as e:
            self.logger.error(f"Vector storage failed: {e}")
            raise
    
    async def store_vectors(self, vectors: List[VectorPoint], 
                           collection_name: Optional[str] = None,
                           wait: bool = False) -> List[str]:
        """Batch store vectors"""
        await self._ensure_initialized()
        collection_name = collection_name or self.collection_name
        
        try:
            # Ensure collection exists
            await self.create_collection(collection_name)
            
            # Create point list
            points = []
            for vector_point in vectors:
                point = http_models.PointStruct(
                    id=vector_point.id,
                    vector=vector_point.vector,
                    payload=vector_point.payload
                )
                points.append(point)
            
            # Batch store
            await asyncio.to_thread(
                self.client.upsert,
                collection_name=collection_name,
                points=points,
                wait=wait
            )
            
            point_ids = [vp.id for vp in vectors]
            self.logger.info(f"Batch store vectors successfully: {len(point_ids)} items")
            return point_ids
            
        except Exception as e:
            self.logger.error(f"Batch store vectors failed: {e}")
            raise
    
    async def search_vectors(self, query_vector: List[float], 
                           limit: int = 10, 
                           score_threshold: Optional[float] = None,
                           filter_conditions: Optional[Dict[str, Any]] = None,
                           collection_name: Optional[str] = None) -> List[VectorSearchResult]:
        """Search vectors"""
        await self._ensure_initialized()
        collection_name = collection_name or self.collection_name
        
        try:
            # Build filter conditions
            query_filter = None
            if filter_conditions:
                self.logger.info(f"Building filter conditions: {filter_conditions}")
                query_filter = self._build_filter(filter_conditions)
                self.logger.info(f"Built query filter: {query_filter}")
            
            # Execute search
            search_results = await asyncio.to_thread(
                self.client.search,
                collection_name=collection_name,
                query_vector=query_vector,
                limit=limit,
                score_threshold=score_threshold,
                query_filter=query_filter,
                with_payload=True,
                with_vectors=True  # Add this parameter to get vector data
            )
            
            # Convert results
            results = []
            for result in search_results:
                results.append(VectorSearchResult(
                    id=result.id,
                    score=result.score,
                    payload=result.payload or {},
                    vector=result.vector  # Include vector data
                ))
            
            return results
            
        except Exception as e:
            self.logger.error(f"Vector search failed: {e}")
            raise
    
    def _build_filter(self, filter_conditions: Dict[str, Any]) -> Optional[http_models.Filter]:
        """Build filter conditions, enforce user group isolation"""
        if not filter_conditions:
            return None
        
        must_conditions = []
        user_group_filter = None  # Save user group filter separately
        
        for key, value in filter_conditions.items():
            # Correct user_group_ids filter logic: bidirectional matching based on user_id and expert_id fields
            if key == "user_group_ids" and isinstance(value, list) and len(value) >= 2:
                id_a, id_b = value[0], value[1]
                
                # Build bidirectional matching should filter
                # Case 1: user_id=id_a AND expert_id=id_b
                # Case 2: user_id=id_b AND expert_id=id_a
                user_group_filter = http_models.Filter(
                    should=[
                        # Case 1
                        http_models.Filter(
                            must=[
                                http_models.FieldCondition(key="user_id", match=http_models.MatchValue(value=id_a)),
                                http_models.FieldCondition(key="expert_id", match=http_models.MatchValue(value=id_b))
                            ]
                        ),
                        # Case 2
                        http_models.Filter(
                            must=[
                                http_models.FieldCondition(key="user_id", match=http_models.MatchValue(value=id_b)),
                                http_models.FieldCondition(key="expert_id", match=http_models.MatchValue(value=id_a))
                            ]
                        )
                    ]
                )
                
                self.logger.info(f"Building user group filter: (user_id={id_a[:8]}... AND expert_id={id_b[:8]}...) OR (user_id={id_b[:8]}... AND expert_id={id_a[:8]}...)")
                continue
            
            # Special handling for character_ids - create OR condition query (only effective without user_group_ids)
            elif key == "character_ids" and isinstance(value, list):
                # Create OR condition: user_id or expert_id in character_ids list
                char_conditions = []
                for char_id in value:
                    char_conditions.append(
                        http_models.Filter(
                            must=[http_models.FieldCondition(
                                key="user_id",
                                match=http_models.MatchValue(value=char_id)
                            )]
                        )
                    )
                    char_conditions.append(
                        http_models.Filter(
                            must=[http_models.FieldCondition(
                                key="expert_id", 
                                match=http_models.MatchValue(value=char_id)
                            )]
                        )
                    )
                
                # Create OR filter
                char_filter = http_models.Filter(should=char_conditions)
                must_conditions.append(char_filter)
                continue
            
            # Modified: handle time range query using timestamp
            elif key in ["created_at", "timestamp"] and isinstance(value, dict):
                gte_value = value.get("gte")
                lte_value = value.get("lte")
                
                # Convert to integer timestamp
                if gte_value and isinstance(gte_value, datetime):
                    gte_value = int(gte_value.timestamp())
                if lte_value and isinstance(lte_value, datetime):
                    lte_value = int(lte_value.timestamp())

                must_conditions.append(http_models.FieldCondition(
                    key="created_at_ts",  # Use new timestamp field
                    range=http_models.Range(
                        gte=gte_value,
                        lte=lte_value
                    )
                ))
            elif isinstance(value, (str, int, float, bool)):
                must_conditions.append(http_models.FieldCondition(
                    key=key,
                    match=http_models.MatchValue(value=value)
                ))
            elif isinstance(value, list):
                must_conditions.append(http_models.FieldCondition(
                    key=key,
                    match=http_models.MatchAny(any=value)
                ))
            elif isinstance(value, dict) and 'range' in value:
                range_filter = value['range']
                must_conditions.append(http_models.FieldCondition(
                    key=key,
                    range=http_models.DatetimeRange(
                        gte=range_filter.get('gte'),
                        lte=range_filter.get('lte')
                    )
                ))
        
        # Correctly combine user group filter and other must conditions
        final_must_conditions = []
        
        # If there is a user group filter, add it as one of the must conditions
        if user_group_filter:
            final_must_conditions.append(user_group_filter)
        
        # Add other must conditions (such as level, etc.)
        final_must_conditions.extend(must_conditions)
        
        # Build final Filter object
        if final_must_conditions:
            filter_obj = http_models.Filter(must=final_must_conditions)
            self.logger.info(f"Final filter: {len(final_must_conditions)} must conditions (includes user group filter: {user_group_filter is not None})")
            # Debug: print complete filter structure
            if user_group_filter:
                self.logger.info(f"User group filter detailed structure: {user_group_filter}")
            return filter_obj
        else:
            return None
    
    async def get_vector(self, vector_id: str, collection_name: Optional[str] = None) -> Optional[VectorSearchResult]:
        """Get single vector by ID"""
        await self._ensure_initialized()
        collection_name = collection_name or self.collection_name
        
        try:
            # Ensure ID is valid UUID format
            try:
                uuid.UUID(vector_id)
            except ValueError:
                # If not a valid UUID, generate a UUID based on the string
                vector_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, vector_id))
            
            # Qdrant uses retrieve API to get points
            points = await asyncio.to_thread(
                self.client.retrieve,
                collection_name=collection_name,
                ids=[vector_id],
                with_payload=True,
                with_vectors=True
            )
            if not points:
                return None
            
            point = points[0]
            # Depending on qdrant_client version, vector may be in different locations
            vector_data = []
            if isinstance(point.vector, dict):
                vector_data = point.vector.get('default', [])
            elif isinstance(point.vector, list):
                vector_data = point.vector
            
            return VectorSearchResult(
                id=point.id,
                score=1.0,  # Score is usually meaningless when getting single item
                payload=point.payload or {},
                vector=vector_data
            )
        except Exception as e:
            self.logger.error(f"Failed to get vector: {e}")
            return None
    
    async def delete_vector(self, point_id: str, 
                           collection_name: Optional[str] = None,
                           wait: bool = False) -> bool:
        """
        Delete vector point
        
        Args:
            point_id: Vector point ID
            collection_name: Collection name (optional)
            wait: Whether to wait for operation to complete
            
        Returns:
            bool: Whether deletion was successful
        """
        try:
            await self._ensure_initialized()
            
            if collection_name is None:
                collection_name = self.collection_name
            
            # Check if collection exists
            try:
                collection_info = self.client.get_collection(collection_name)
                if not collection_info:
                    self.logger.warning(f"Collection {collection_name} does not exist")
                    return False
            except Exception as e:
                self.logger.warning(f"Error checking collection {collection_name}: {e}")
                return False
            
            # Delete vector point - using filter format
            result = await asyncio.to_thread(
                self.client.delete,
                collection_name=collection_name,
                points_selector=http_models.Filter(
                    must=[
                        http_models.FieldCondition(
                            key="id",
                            match=http_models.MatchValue(value=point_id)
                        )
                    ]
                ),
                wait=wait
            )
            
            # Check deletion result status
            if result.status in [http_models.UpdateStatus.COMPLETED, http_models.UpdateStatus.ACKNOWLEDGED]:
                self.logger.info(f"Successfully deleted vector point: {point_id}")
                return True
            else:
                self.logger.warning(f"Failed to delete vector point: {point_id}, status: {result.status}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error occurred while deleting vector point: {e}")
            return False

    async def delete_memory(self, memory_id: str, 
                           collection_name: Optional[str] = None,
                           wait: bool = False) -> bool:
        """
        Delete memory (alias for delete_vector)
        
        Args:
            memory_id: Memory ID
            collection_name: Collection name (optional)
            wait: Whether to wait for operation to complete
            
        Returns:
            bool: Whether deletion was successful
        """
        return await self.delete_vector(memory_id, collection_name, wait)

    async def delete_vectors(self, point_ids: List[str], 
                            collection_name: Optional[str] = None,
                            wait: bool = False) -> bool:
        """Batch delete vectors"""
        await self._ensure_initialized()
        collection_name = collection_name or self.collection_name
        
        try:
            # Ensure all IDs are valid UUID format
            valid_ids = []
            for point_id in point_ids:
                try:
                    uuid.UUID(point_id)
                    valid_ids.append(point_id)
                except ValueError:
                    # If not a valid UUID, generate a UUID based on the string
                    valid_ids.append(str(uuid.uuid5(uuid.NAMESPACE_DNS, point_id)))
            
            # Use direct ID list deletion method, more reliable
            result = await asyncio.to_thread(
                self.client.delete,
                collection_name=collection_name,
                points_selector=valid_ids,
                wait=wait
            )
            
            # Check deletion result status
            if result.status in [http_models.UpdateStatus.COMPLETED, http_models.UpdateStatus.ACKNOWLEDGED]:
                self.logger.info(f"Batch delete vectors successfully: {len(valid_ids)} items")
                return True
            else:
                self.logger.warning(f"Batch delete vectors failed: {len(valid_ids)} items, status: {result.status}")
                return False
            
        except Exception as e:
            self.logger.error(f"Batch delete vectors failed: {e}")
            return False
    
    async def update_vector(self, point_id: str, vector: List[float], 
                           payload: Dict[str, Any], 
                           collection_name: Optional[str] = None) -> bool:
        """Update vector"""
        await self._ensure_initialized()
        collection_name = collection_name or self.collection_name
        
        try:
            point = http_models.PointStruct(
                id=point_id,
                vector=vector,
                payload=payload
            )
            
            await asyncio.to_thread(
                self.client.upsert,
                collection_name=collection_name,
                points=[point]
            )
            
            self.logger.debug(f"Vector updated successfully: {point_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Vector update failed: {e}")
            return False
    
    async def count_vectors(self, filter_conditions: Optional[Dict[str, Any]] = None,
                           collection_name: Optional[str] = None) -> int:
        """Count vectors"""
        await self._ensure_initialized()
        collection_name = collection_name or self.collection_name
        
        try:
            count_result = await asyncio.to_thread(
                self.client.count,
                collection_name=collection_name,
                count_filter=self._build_filter(filter_conditions) if filter_conditions else None
            )
            
            return count_result.count
            
        except Exception as e:
            self.logger.error(f"Failed to count vectors: {e}")
            return 0
    
    async def get_collection_info(self, collection_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get collection information"""
        await self._ensure_initialized()
        collection_name = collection_name or self.collection_name
        
        try:
            collection_info = await asyncio.to_thread(self.client.get_collection, collection_name)
            return {
                'name': collection_name,
                'vectors_count': getattr(collection_info, 'vectors_count', 0),
                'points_count': getattr(collection_info, 'points_count', 0),
                'segments_count': getattr(collection_info, 'segments_count', 0),
                'config': collection_info.config.dict() if hasattr(collection_info, 'config') and collection_info.config else {}
            }
            
        except Exception as e:
            self.logger.error(f"Failed to get collection information: {e}")
            return None
    
    async def similarity_search(self, query_vector: List[float], 
                               top_k: int = 10,
                               layer_filter: Optional[str] = None,
                               user_filter: Optional[str] = None,
                               time_filter: Optional[Dict[str, Any]] = None,
                               collection_name: Optional[str] = None) -> List[VectorSearchResult]:
        """Similarity search"""
        await self._ensure_initialized()
        
        # Build filter conditions
        filter_conditions = {}
        if layer_filter:
            filter_conditions['layer'] = layer_filter
        if user_filter:
            filter_conditions['user_id'] = user_filter
        if time_filter:
            filter_conditions['created_at'] = time_filter
        
        return await self.search_vectors(
            query_vector=query_vector,
            limit=top_k,
            filter_conditions=filter_conditions,
            collection_name=collection_name
        )
    
    async def connect(self):
        """Connect to vector store"""
        await self._ensure_initialized()
        self.logger.info("Vector store connected successfully")
    
    async def disconnect(self):
        """Disconnect from vector store"""
        await self.close()
    
    async def close(self):
        """Close connection"""
        if self.client:
            await asyncio.to_thread(self.client.close)
            self._initialized = False
            self.logger.info("Qdrant client connection closed")

# Factory method
def get_vector_store() -> VectorStore:
    return VectorStore()
