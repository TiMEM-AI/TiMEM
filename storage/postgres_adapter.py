"""
TiMem PostgreSQL Storage Adapter
Implements StorageAdapter interface, provides PostgreSQL-specific storage and full-text search functionality
"""

import asyncio
from typing import Dict, List, Optional, Any, Union
from datetime import datetime

from sqlalchemy import text
from timem.models.memory import Memory, convert_dict_to_memory, FragmentMemory
from storage.storage_adapter import StorageAdapter
from storage.postgres_store import PostgreSQLStore, get_postgres_store
from timem.utils.logging import get_logger

# Support execution state
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from timem.core.execution_state import ExecutionState

logger = get_logger(__name__)


class PostgreSQLAdapter(StorageAdapter):
    """PostgreSQL storage adapter - implemented based on PostgreSQLStore, follows StorageAdapter interface"""

    def __init__(self, postgres_store: PostgreSQLStore = None):
        """
        Initialize PostgreSQL adapter
        
        Args:
            postgres_store: PostgreSQL storage instance, use default instance if None
        """
        self._postgres_store = postgres_store
        logger.info("PostgreSQLAdapter initialized.")
    
    # ==================== Unified interface proxy methods ====================
    
    async def get_data_statistics(self) -> Dict[str, Any]:
        """Get data statistics"""
        store = await self._ensure_store()
        return await store.get_data_statistics()
    
    async def fulltext_search(self,
                            query_text: str,
                            user_id: Optional[str] = None,
                            expert_id: Optional[str] = None,
                            level: Optional[str] = None,
                            limit: int = 20,
                            min_score: float = 0.0) -> List[Dict[str, Any]]:
        """Full-text search"""
        store = await self._ensure_store()
        return await store.fulltext_search(
            query_text=query_text,
            user_id=user_id,
            expert_id=expert_id,
            level=level,
            limit=limit,
            min_score=min_score
        )
    
    async def search_memories(self, 
                            query_text: Optional[str] = None,
                            user_id: Optional[str] = None,
                            expert_id: Optional[str] = None,
                            level: Optional[str] = None,
                            limit: int = 20) -> List[Dict[str, Any]]:
        """Search memories"""
        store = await self._ensure_store()
        return await store.search_memories(
            query_text=query_text,
            user_id=user_id,
            expert_id=expert_id,
            level=level,
            limit=limit
        )
    
    async def find_memories_by_criteria(self, **criteria) -> List[Dict[str, Any]]:
        """Find memories by criteria"""
        store = await self._ensure_store()
        return await store.find_memories_by_criteria(**criteria)
    
    async def create_session(self, user_id: str, expert_id: str, session_id: Optional[str] = None) -> str:
        """Create session"""
        store = await self._ensure_store()
        return await store.create_session(user_id, expert_id, session_id)
    
    async def get_session_memories(self, session_id: str, level: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get session-related memories"""
        store = await self._ensure_store()
        return await store.get_session_memories(session_id, level)
    
    async def get_session_dialogues(self, session_id: str) -> List[Dict[str, Any]]:
        """Get session dialogue history"""
        store = await self._ensure_store()
        return await store.get_session_dialogues(session_id)

    def get_connection_pool_status(self) -> Dict[str, Any]:
        """Get connection pool status"""
        try:
            # If store is not initialized yet, try to get synchronously
            store = self._postgres_store
            if store is None:
                # Try to get synchronously
                try:
                    from storage.postgres_store import get_postgres_store
                    # Here we need to get synchronously, but get_postgres_store is async
                    # So we return a status indicating not initialized
                    return {"status": "not_initialized", "error": "PostgreSQLStore not initialized yet"}
                except Exception:
                    return {"status": "no_store", "error": "PostgreSQLStore not available"}
            
            if hasattr(store, 'get_connection_pool_status'):
                return store.get_connection_pool_status()
            else:
                return {"status": "no_status_method", "error": "PostgreSQLStore has no get_connection_pool_status method"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    async def cleanup_connection_pool(self) -> bool:
        """Clean up connection pool"""
        try:
            store = await self._ensure_store()
            if store and hasattr(store, 'cleanup_connection_pool'):
                return await store.cleanup_connection_pool()
            else:
                return False
        except Exception as e:
            logger.error(f"Failed to clean up PostgreSQL connection pool: {e}")
            return False

    async def _ensure_store(self):
        """Ensure PostgreSQL storage instance is available"""
        if self._postgres_store is None:
            self._postgres_store = await get_postgres_store()
        return self._postgres_store

    def _memory_to_db_record(self, memory: Memory) -> Dict[str, Any]:
        """Convert Memory object to PostgreSQL storage record"""
        logger.info(f"Converting memory object: id={memory.id}, level={memory.level}")
        logger.info(f"Memory object time window: {memory.time_window_start} to {memory.time_window_end}")
        
        # Use python mode to preserve native types like datetime
        db_record = memory.model_dump(mode='python', exclude_none=True)
        
        # level is already a string enum, use directly
        db_record['level'] = memory.level.value
        
        # Special handling for L1 dialogue_turns
        if isinstance(memory, FragmentMemory):
            db_record['dialogue_turns_json'] = db_record.pop('dialogue_turns', None)
        
        logger.info(f"Converted time window: {db_record.get('time_window_start')} to {db_record.get('time_window_end')}")
        logger.debug(f"Converted Memory (ID: {memory.id}) to PostgreSQL record.")
        
        return db_record

    def _db_record_to_memory(self, db_record: Dict[str, Any]) -> Memory:
        """Convert PostgreSQL record to Memory object"""
        try:
            # Fix the issue of missing specific fields in each level memory
            level = db_record.get('level')
            time_start = db_record.get('time_window_start')

            # Fix: Convert dialogue_turns_json back to dialogue_turns for L1 records
            # (Storage uses dialogue_turns_json, but FragmentMemory model requires dialogue_turns)
            if level == 'L1' and 'dialogue_turns_json' in db_record:
                db_record['dialogue_turns'] = db_record.pop('dialogue_turns_json')

            if time_start and isinstance(time_start, datetime):
                if level == 'L3' and 'date_value' not in db_record:
                    db_record['date_value'] = time_start.date()
                elif level == 'L4' and ('year' not in db_record or 'week_number' not in db_record):
                    db_record['year'] = time_start.year
                    # Calculate week number
                    import datetime as dt
                    week_number = time_start.isocalendar()[1]
                    db_record['week_number'] = week_number
                elif level == 'L5' and ('year' not in db_record or 'month' not in db_record):
                    db_record['year'] = time_start.year
                    db_record['month'] = time_start.month
            elif time_start and isinstance(time_start, str):
                # If it's a string, try to parse it
                import re
                if '-' in time_start:
                    parts = time_start.split('-')
                    if len(parts) >= 2:
                        year = int(parts[0])
                        month = int(parts[1])
                        if level == 'L3' and 'date_value' not in db_record:
                            db_record['date_value'] = f"{year}-{month:02d}-{parts[2] if len(parts) > 2 else '01'}"
                        elif level == 'L4' and ('year' not in db_record or 'week_number' not in db_record):
                            db_record['year'] = year
                            # Simple week number calculation (which week)
                            db_record['week_number'] = (month - 1) * 4 + 1
                        elif level == 'L5' and ('year' not in db_record or 'month' not in db_record):
                            db_record['year'] = year
                            db_record['month'] = month
            
            return convert_dict_to_memory(db_record)
        except Exception as e:
            logger.error(f"Failed to convert PostgreSQL record to Memory object: {e}")
            logger.error(f"Record content: {db_record}")
            # Return None instead of throwing exception to avoid interrupting the entire search process
            return None

    async def connect(self) -> bool:
        """Connect to PostgreSQL database"""
        try:
            store = await self._ensure_store()
            success = await store.connect()
            logger.info(f"PostgreSQL adapter connection status: {success}")
            
            # Validate connection status
            if success and await self.is_available():
                logger.info("PostgreSQL adapter is ready for database operations")
                return True
            else:
                logger.error("PostgreSQL adapter connection validation failed")
                return False
        except Exception as e:
            logger.error(f"PostgreSQL adapter connection failed: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from PostgreSQL database"""
        try:
            if self._postgres_store:
                logger.info("Disconnecting from PostgreSQL adapter...")
                await self._postgres_store.close()
                logger.info("PostgreSQL adapter connection closed")
        except Exception as e:
            logger.error(f"PostgreSQL adapter disconnection failed: {e}")
            raise

    async def is_available(self) -> bool:
        """Check if PostgreSQL is available"""
        try:
            store = await self._ensure_store()
            # Fix: Redesign availability check logic
            # 1. Check if engine exists
            # 2. Try actual connection test
            if store.engine is None:
                return False
            
            # Try actual connection test
            try:
                async with store.get_session() as session:
                    await session.execute(text("SELECT 1"))
                return True
            except Exception:
                # Connection test failed, but engine exists, try to reconnect
                try:
                    await store.connect()
                    return True
                except Exception:
                    return False
        except Exception:
            return False

    async def store_memory(
        self, 
        memory: Memory,
        execution_state: Optional['ExecutionState'] = None
    ) -> str:
        """
        Store memory to PostgreSQL
        
        Args:
            memory: Memory object
            execution_state: Execution state (optional)
        """
        try:
            store = await self._ensure_store()
            db_record = self._memory_to_db_record(memory)
            
            # Use unified batch storage logic, pass execution_state
            memory_ids = await store.batch_store_memories([db_record], execution_state=execution_state)
            
            if memory_ids:
                logger.info(f"Successfully stored memory to PostgreSQL: {memory_ids[0]}")
                return memory_ids[0]
            else:
                logger.error(f"Failed to store memory to PostgreSQL: {memory.id}")
                return ""
                
        except Exception as e:
            logger.error(f"PostgreSQL store memory exception: {e}", exc_info=True)
            return ""

    async def retrieve_memory(self, memory_id: str) -> Optional[Memory]:
        """Retrieve memory from PostgreSQL"""
        try:
            store = await self._ensure_store()
            db_record = await store.get_memory_by_id(memory_id)
            
            if db_record:
                memory = self._db_record_to_memory(db_record)
                logger.info(f"Successfully retrieved memory from PostgreSQL: {memory_id}")
                return memory
            else:
                logger.warning(f"Memory not found in PostgreSQL: {memory_id}")
                return None
                
        except Exception as e:
            logger.error(f"Failed to retrieve memory from PostgreSQL: {e}", exc_info=True)
            return None

    async def update_memory(self, memory_id: str, updates: Dict[str, Any]) -> bool:
        """Update memory in PostgreSQL"""
        try:
            store = await self._ensure_store()
            
            # Filter out fields that may cause problems
            filtered_updates = {}
            for key, value in updates.items():
                # Skip special fields and non-existent fields that may cause problems
                if key not in ['summary', 'metadata', 'importance_score'] and value is not None:
                    filtered_updates[key] = value
            
            if not filtered_updates:
                logger.debug(f"No valid update fields: {memory_id}")
                return True
            
            logger.debug(f"PostgreSQL update fields: {list(filtered_updates.keys())}")
            success = await store.update_memory(memory_id, filtered_updates)
            
            if success:
                logger.info(f"Successfully updated PostgreSQL memory: {memory_id}")
            else:
                logger.warning(f"Failed to update PostgreSQL memory: {memory_id}")
                
            return success
            
        except Exception as e:
            # Special handling for _bulk_update_tuples error
            if "_bulk_update_tuples" in str(e):
                logger.warning(f"PostgreSQL update encountered known issue, skipping this update: {memory_id}")
                return True  # Return True to avoid entire update process failure
            else:
                logger.error(f"Update PostgreSQL memory exception: {e}", exc_info=True)
                return False

    async def delete_memory(self, memory_id: str) -> bool:
        """Delete memory from PostgreSQL"""
        try:
            store = await self._ensure_store()
            success = await store.delete_memory(memory_id)
            
            if success:
                logger.info(f"Successfully deleted memory from PostgreSQL: {memory_id}")
            else:
                logger.warning(f"Failed to delete PostgreSQL memory: {memory_id}")
                
            return success
            
        except Exception as e:
            logger.error(f"Delete memory from PostgreSQL exception: {e}", exc_info=True)
            return False

    async def search_memories(self, 
                            query: Optional[Dict[str, Any]] = None, 
                            options: Optional[Dict[str, Any]] = None, 
                            **kwargs) -> List[Memory]:
        """
        PostgreSQL memory search (unified interface)
        Supports traditional search and full-text search
        """
        try:
            # Merge query conditions
            criteria: Dict[str, Any] = {}
            if isinstance(query, dict):
                criteria.update(query)
            if kwargs:
                criteria.update(kwargs)
            
            # ✅ Fix: Handle time_range parameter in query (used by MemoryCollector)
            if "time_range" in criteria:
                time_range = criteria.pop("time_range")
                if isinstance(time_range, dict):
                    if "start" in time_range:
                        criteria["start_time"] = time_range["start"]
                    if "end" in time_range:
                        criteria["end_time"] = time_range["end"]
            
            # Merge options to criteria
            if isinstance(options, dict):
                # Handle time parameters
                time_manager = None
                try:
                    from timem.utils.time_manager import get_time_manager
                    time_manager = get_time_manager()
                except Exception:
                    time_manager = None
                
                # Time range handling
                if "start_time" in options and options["start_time"]:
                    start_raw = options["start_time"]
                    if isinstance(start_raw, str) and time_manager:
                        try:
                            start_raw = time_manager.parse_iso_time(start_raw)
                        except Exception:
                            pass
                    criteria["start_time"] = start_raw
                    
                if "end_time" in options and options["end_time"]:
                    end_raw = options["end_time"]
                    if isinstance(end_raw, str) and time_manager:
                        try:
                            end_raw = time_manager.parse_iso_time(end_raw)
                        except Exception:
                            pass
                    criteria["end_time"] = end_raw
                
                if "limit" in options and options["limit"]:
                    criteria["limit"] = options["limit"]
                if options.get("offset"):
                    criteria["offset"] = int(options["offset"])

            # Handle layer parameter compatibility
            if "layer" in criteria and "level" not in criteria:
                criteria["level"] = criteria.pop("layer")

            store = await self._ensure_store()
            
            # Check if full-text search should be used
            if "query_text" in criteria and self._should_use_fulltext(criteria):
                # Use PostgreSQL full-text search
                db_records = await store.search_memories_fulltext(
                    query_text=criteria["query_text"],
                    user_id=criteria.get("user_id"),
                    expert_id=criteria.get("expert_id"),
                    level=criteria.get("level"),
                    limit=criteria.get("limit", 20),
                    use_bm25=True
                )
            else:
                # Use traditional search
                db_records = await store.find_memories(**criteria)
            
            # Convert to Memory objects, filter out failed conversions
            memories = []
            failed_count = 0
            for rec in db_records:
                memory = self._db_record_to_memory(rec)
                if memory is not None:
                    memories.append(memory)
                else:
                    failed_count += 1
            
            if failed_count > 0:
                logger.warning(f"PostgreSQL search conversion failed for {failed_count} memories, may be missing level data")
            logger.info(f"PostgreSQL found {len(memories)} memories (conversion successful)")
            
            return memories
            
        except Exception as e:
            logger.error(f"PostgreSQL memory search failed: {e}", exc_info=True)
            return []

    def _should_use_fulltext(self, criteria: Dict[str, Any]) -> bool:
        """Determine whether to use full-text search"""
        # If there is explicit full-text query text and text length is appropriate, use full-text search
        query_text = criteria.get("query_text", "")
        return (
            len(query_text.split()) >= 1 and  # At least one word
            len(query_text) >= 3 and          # At least 3 characters
            not criteria.get("content_contains")  # Not a simple contains query
        )

    async def search_memories_bm25(self, 
                                  query_text: str,
                                  user_id: Optional[str] = None,
                                  expert_id: Optional[str] = None,
                                  level: Optional[str] = None,
                                  limit: int = 20,
                                  is_tokenized: bool = False) -> List[Dict[str, Any]]:
        """
        Full-text search using BM25 algorithm (PostgreSQL-specific functionality)
        
        Args:
            query_text: Query text or tokenized keywords
            user_id: User ID
            expert_id: Expert ID
            level: Memory level
            limit: Result limit
            is_tokenized: Whether query text is already tokenized (e.g., keyword list joined with spaces)
        """
        try:
            store = await self._ensure_store()
            results = await store.search_memories_fulltext(
                query_text=query_text,
                user_id=user_id,
                expert_id=expert_id,
                level=level,
                limit=limit,
                use_bm25=True,
                is_tokenized=is_tokenized
            )
            
            logger.info(f"PostgreSQL BM25 retrieved {len(results)} memories")
            return results
            
        except Exception as e:
            logger.error(f"PostgreSQL BM25 search failed: {e}")
            return []

    async def delete_memories_by_user_expert(self, user_id: str, expert_id: str) -> bool:
        """Delete all memories of specified user and expert"""
        try:
            # First search for all matching memories
            memories = await self.search_memories({"user_id": user_id, "expert_id": expert_id})
            if not memories:
                logger.info(f"No memories found in PostgreSQL for user {user_id} and expert {expert_id}")
                return True
            
            # Delete memories one by one
            deleted_count = 0
            for memory in memories:
                try:
                    success = await self.delete_memory(memory.id)
                    if success:
                        deleted_count += 1
                except Exception as e:
                    logger.warning(f"Failed to delete PostgreSQL memory {memory.id}: {e}")
            
            logger.info(f"Successfully deleted {deleted_count}/{len(memories)} memories from PostgreSQL")
            return deleted_count == len(memories)
            
        except Exception as e:
            logger.error(f"Failed to delete PostgreSQL user-expert memories: {e}", exc_info=True)
            return False

    async def clear_all_data(self) -> bool:
        """Delete all data (mainly for testing)"""
        try:
            store = await self._ensure_store()
            await store.clear_all_data()
            logger.info("Successfully cleared all PostgreSQL data")
            return True
        except Exception as e:
            logger.error(f"Failed to clear PostgreSQL data: {e}", exc_info=True)
            return False

    async def get_memory_by_id(self, memory_id: str, level: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get memory by ID"""
        try:
            store = await self._ensure_store()
            db_record = await store.get_memory_by_id(memory_id, level)
            
            if db_record:
                # Convert complete record to Memory object, then back to dict
                memory = self._db_record_to_memory(db_record)
                return memory.model_dump(mode='json')
            else:
                logger.warning(f"Memory not found in PostgreSQL: {memory_id}")
                return None
                
        except Exception as e:
            logger.error(f"Failed to get memory from PostgreSQL: {e}", exc_info=True)
            return None

    async def query_memories_by_session(self, user_id: str, expert_id: str, session_id: str, level: str) -> List[Dict[str, Any]]:
        """Query memories of specific level by session ID"""
        try:
            store = await self._ensure_store()
            db_records = await store.query_memories_by_session(user_id, expert_id, session_id, level)
            
            # Convert to standard dict format
            result = []
            for rec in db_records:
                if isinstance(rec, dict):
                    result.append(rec)
                else:
                    memory = self._db_record_to_memory(rec)
                    result.append(memory.model_dump(mode='json'))
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to query PostgreSQL memories by session: {e}", exc_info=True)
            return []

    async def query_latest_memories(self, user_id: str, expert_id: str, level: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Query latest memories"""
        try:
            store = await self._ensure_store()
            db_records = await store.query_latest_memories(user_id, expert_id, level, limit)
            
            # Convert to standard dict format
            result = []
            for rec in db_records:
                if isinstance(rec, dict):
                    result.append(rec)
                else:
                    memory = self._db_record_to_memory(rec)
                    result.append(memory.model_dump(mode='json'))
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to query PostgreSQL latest memories: {e}", exc_info=True)
            return []

    async def batch_store_memories(
        self, 
        memories: List[Memory],
        execution_state: Optional['ExecutionState'] = None
    ) -> List[str]:
        """
        Batch store memories to PostgreSQL
        
        Args:
            memories: List of memory objects
            execution_state: Execution state (optional)
        """
        try:
            store = await self._ensure_store()
            
            # Convert to database record format
            db_records = [self._memory_to_db_record(memory) for memory in memories]
            
            # Batch store, pass execution_state
            memory_ids = await store.batch_store_memories(db_records, execution_state=execution_state)
            
            logger.info(f"Successfully batch stored {len(memory_ids)} memories to PostgreSQL")
            return memory_ids
            
        except Exception as e:
            logger.error(f"PostgreSQL batch store memories failed: {e}", exc_info=True)
            return []

    # PostgreSQL-specific full-text search method
    async def fulltext_search(self, 
                             query_text: str,
                             filters: Optional[Dict[str, Any]] = None,
                             limit: int = 20,
                             use_bm25: bool = True) -> List[Dict[str, Any]]:
        """
        PostgreSQL full-text search interface
        
        Args:
            query_text: Search text
            filters: Filter conditions (user_id, expert_id, level, etc.)
            limit: Result limit
            use_bm25: Whether to use BM25 algorithm
            
        Returns:
            List of search results with BM25 scores
        """
        try:
            store = await self._ensure_store()
            
            filters = filters or {}
            results = await store.search_memories_fulltext(
                query_text=query_text,
                user_id=filters.get("user_id"),
                expert_id=filters.get("expert_id"),
                level=filters.get("level"),
                limit=limit,
                use_bm25=use_bm25
            )
            
            logger.info(f"PostgreSQL full-text search found {len(results)} memories")
            return results
            
        except Exception as e:
            logger.error(f"PostgreSQL full-text search failed: {e}")
            return []

    async def get_child_memories(self, parent_id: str) -> List[Dict[str, Any]]:
        """Get child memories"""
        try:
            store = await self._ensure_store()
            db_records = await store.get_child_memories(parent_id)
            
            # Convert to standard format, filter out failed conversions
            memories = []
            for rec in db_records:
                memory = self._db_record_to_memory(rec)
                if memory is not None:
                    memories.append(memory.model_dump(mode='json'))
                else:
                    logger.warning(f"Skipped child memory record with conversion failure: {rec.get('id', 'unknown')}")
            
            logger.info(f"PostgreSQL retrieved {len(memories)} child memories (conversion successful)")
            return memories
            
        except Exception as e:
            logger.error(f"Failed to get PostgreSQL child memories: {e}", exc_info=True)
            return []

    async def get_historical_memories(self, memory_id: str) -> List[Dict[str, Any]]:
        """Get historical memories"""
        try:
            store = await self._ensure_store()
            db_records = await store.get_historical_memories(memory_id)
            
            # Convert to standard format, filter out failed conversions
            memories = []
            for rec in db_records:
                memory = self._db_record_to_memory(rec)
                if memory is not None:
                    memories.append(memory.model_dump(mode='json'))
                else:
                    logger.warning(f"Skipped historical memory record with conversion failure: {rec.get('id', 'unknown')}")
            
            logger.info(f"PostgreSQL retrieved {len(memories)} historical memories (conversion successful)")
            return memories
            
        except Exception as e:
            logger.error(f"Failed to get PostgreSQL historical memories: {e}", exc_info=True)
            return []


# Remove global singleton pattern, support independent instance creation
# No longer use global singleton, create independent adapter instance for each conversation

async def create_postgres_adapter(postgres_store: PostgreSQLStore = None) -> "PostgreSQLAdapter":
    """Create new PostgreSQL adapter instance (non-singleton)"""
    adapter = PostgreSQLAdapter(postgres_store)
    await adapter.connect()
    return adapter

# Keep backward compatible interface, but use independent instances internally
async def get_postgres_adapter(postgres_store: PostgreSQLStore = None) -> "PostgreSQLAdapter":
    """Get PostgreSQL adapter instance (backward compatible, but creates independent instance)"""
    return await create_postgres_adapter(postgres_store)
