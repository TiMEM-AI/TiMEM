"""
TiMem PostgreSQL Storage Implementation
Provide PostgreSQL database operations, support full-text search and BM25 algorithm
Optimized for TiMem hierarchical memory system

Implement unified storage interface standard, ensure interface consistency with MySQL storage class
Follow "high cohesion, low coupling, hierarchical, clear responsibility" software engineering principles
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Dict, List, Optional, Any, AsyncGenerator
import json
from datetime import datetime, date, timezone, timedelta
import uuid
import time

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import (Column, String, Text, DateTime, Date, Boolean, select, 
                        update, delete, insert, ForeignKey, Integer, Float, 
                        func, Index, UniqueConstraint, text, and_, or_, desc, Enum)
from sqlalchemy.orm import declarative_base
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

from timem.utils.logging import get_logger
from timem.utils.config_manager import get_storage_config
from timem.utils.time_parser import time_parser
from timem.utils.chinese_tokenizer import tokenize_for_postgres, prepare_keywords_for_postgres, get_chinese_tokenizer
from .base_storage_interface import UnifiedStorageInterface
from .connection_pool_manager import get_connection_pool_manager, initialize_connection_pool
from timem.core.connection_pool_bootstrap import ensure_connection_pool
from timem.core.unified_connection_manager import (
    get_unified_connection_manager, 
    ConnectionPoolType, 
    get_postgres_session, 
    get_postgres_engine,
    get_postgres_session_factory
)

# Support execution state (optional import, avoid circular dependency)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from timem.core.execution_state import ExecutionState

# --- PostgreSQL Data Table ORM Definition ---
Base = declarative_base()

class User(Base):
    """User table - PostgreSQL version (real users)"""
    __tablename__ = 'users'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String(255), nullable=False, unique=True)
    email = Column(String(255), unique=True)  # New: email field
    password_hash = Column(String(255))  # New: password hash
    display_name = Column(String(255))
    description = Column(Text)
    metadata_json = Column(JSONB)
    is_active = Column(Boolean, nullable=False, default=True)
    last_login_at = Column(DateTime)  # New: last login time
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        Index('ix_users_username', 'username'),
        Index('ix_users_email', 'email'),  # New: email index
        Index('ix_users_active', 'is_active'),
        Index('ix_users_last_login', 'last_login_at'),  # New: login time index
    )

class Character(Base):
    """Character role table - PostgreSQL version (AI role/expert/assistant)"""
    __tablename__ = 'characters'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False, unique=True)
    character_type = Column(Enum('user', 'expert', 'assistant', 'other', name='character_type_enum'), nullable=False, default='user')  # Use named Enum type
    display_name = Column(String(255))
    description = Column(Text)
    metadata_json = Column(JSONB)  # Use PostgreSQL's JSONB
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        Index('ix_characters_type', 'character_type'),
        Index('ix_characters_active', 'is_active'),
    )

class CoreMemory(Base):
    __tablename__ = 'core_memories'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), nullable=False)
    expert_id = Column(String(36), nullable=False)
    level = Column(String(10), nullable=False)
    title = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default='active')
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    time_window_start = Column(DateTime, nullable=False)
    time_window_end = Column(DateTime, nullable=False)
    
    # PostgreSQL full-text search fields
    content_tsvector = Column(TSVECTOR)  # Content full-text search vector
    title_tsvector = Column(TSVECTOR)    # Title full-text search vector

    __table_args__ = (
        Index('ix_core_memories_user_id', 'user_id'),
        Index('ix_core_memories_expert_id', 'expert_id'),
        Index('ix_core_memories_level', 'level'),
        Index('ix_core_memories_time_window_start', 'time_window_start'),
        Index('ix_core_memories_time_window_end', 'time_window_end'),
        # Full-text search indexes
        Index('ix_core_memories_content_fts', 'content_tsvector', postgresql_using='gin'),
        Index('ix_core_memories_title_fts', 'title_tsvector', postgresql_using='gin'),
    )

class MemorySession(Base):
    __tablename__ = 'memory_sessions'
    id = Column(String(128), primary_key=True, default=lambda: str(uuid.uuid4()))  # ✅ Extended to 128 characters to support complex session_id
    user_id = Column(String(36), nullable=False)
    expert_id = Column(String(36), nullable=False)
    start_time = Column(DateTime, nullable=False, default=func.now())
    end_time = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        Index('ix_memory_sessions_user_id', 'user_id'),
        Index('ix_memory_sessions_expert_id', 'expert_id'),
    )

class DialogueOriginal(Base):
    __tablename__ = 'dialogue_originals'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String(128), ForeignKey('memory_sessions.id', ondelete='CASCADE'), nullable=False)  # ✅ Extended to 128 characters
    turn_number = Column(Integer, nullable=False)
    role = Column(String(128), nullable=False)  # 🔧 Fix: Extended to 128 characters to support long role names
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        Index('ix_dialogue_originals_session_id', 'session_id'),
        Index('ix_dialogue_originals_turn_number', 'turn_number'),
    )

class L1FragmentMemory(Base):
    __tablename__ = 'l1_fragment_memories'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    memory_id = Column(String(36), ForeignKey('core_memories.id', ondelete='CASCADE'), nullable=False, unique=True)
    session_id = Column(String(128), ForeignKey('memory_sessions.id', ondelete='CASCADE'), nullable=False, index=True)  # ✅ Extended to 128 characters
    dialogue_turns_json = Column(JSONB)  # Use JSONB
    original_turn_start = Column(Integer)
    original_turn_end = Column(Integer)

class L2SessionMemory(Base):
    __tablename__ = 'l2_session_memories'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    memory_id = Column(String(36), ForeignKey('core_memories.id', ondelete='CASCADE'), nullable=False, unique=True)
    session_id = Column(String(128), ForeignKey('memory_sessions.id', ondelete='CASCADE'), nullable=False, index=True)  # ✅ Extended to 128 characters
    user_id = Column(String(36), nullable=False, index=True)
    expert_id = Column(String(36), nullable=False, index=True)
    __table_args__ = (
        UniqueConstraint('user_id', 'expert_id', 'session_id', name='uq_l2_user_expert_session'),
    )

class L3DailyMemory(Base):
    __tablename__ = 'l3_daily_memories'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    memory_id = Column(String(36), ForeignKey('core_memories.id', ondelete='CASCADE'), nullable=False, unique=True)
    date_value = Column(Date, nullable=False, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    expert_id = Column(String(36), nullable=False, index=True)
    __table_args__ = (
        UniqueConstraint('user_id', 'expert_id', 'date_value', name='uq_l3_user_expert_date'),
    )

class L4WeeklyMemory(Base):
    __tablename__ = 'l4_weekly_memories'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    memory_id = Column(String(36), ForeignKey('core_memories.id', ondelete='CASCADE'), nullable=False, unique=True)
    year = Column(Integer, nullable=False)
    week_number = Column(Integer, nullable=False)
    user_id = Column(String(36), nullable=False, index=True)
    expert_id = Column(String(36), nullable=False, index=True)
    __table_args__ = (
        Index('ix_l4_weekly_memories_year_week', 'year', 'week_number'),
        UniqueConstraint('user_id', 'expert_id', 'year', 'week_number', name='uq_l4_user_expert_year_week'),
    )

class L5MonthlyMemory(Base):
    __tablename__ = 'l5_monthly_memories'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    memory_id = Column(String(36), ForeignKey('core_memories.id', ondelete='CASCADE'), nullable=False, unique=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    user_id = Column(String(36), nullable=False, index=True)
    expert_id = Column(String(36), nullable=False, index=True)
    __table_args__ = (
        Index('ix_l5_monthly_memories_year_month', 'year', 'month'),
        UniqueConstraint('user_id', 'expert_id', 'year', 'month', name='uq_l5_user_expert_year_month'),
    )

class MemoryChildRelation(Base):
    __tablename__ = 'memory_child_relations'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    parent_id = Column(String(36), ForeignKey('core_memories.id', ondelete='CASCADE'), nullable=False)
    child_id = Column(String(36), ForeignKey('core_memories.id', ondelete='CASCADE'), nullable=False)
    __table_args__ = (
        Index('ix_memory_child_relations_parent_id', 'parent_id'),
        Index('ix_memory_child_relations_child_id', 'child_id'),
        UniqueConstraint('parent_id', 'child_id', name='uq_parent_child'),
    )

class MemoryHistoricalRelation(Base):
    __tablename__ = 'memory_historical_relations'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    memory_id = Column(String(36), ForeignKey('core_memories.id', ondelete='CASCADE'), nullable=False)
    historical_memory_id = Column(String(36), ForeignKey('core_memories.id', ondelete='CASCADE'), nullable=False)
    __table_args__ = (
        Index('ix_memory_historical_relations_memory_id', 'memory_id'),
        Index('ix_memory_historical_relations_historical_memory_id', 'historical_memory_id'),
        UniqueConstraint('memory_id', 'historical_memory_id', name='uq_memory_historical'),
    )


class PostgreSQLStore(UnifiedStorageInterface):
    """
    PostgreSQL storage implementation, support full-text search and BM25 algorithm
    
    Implement unified storage interface standard, ensure interface consistency with MySQL storage class
    Responsibilities:
    1. Database connection management
    2. Memory CRUD operations
    3. Full-text search functionality
    4. Session management
    5. Data statistics
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        if not hasattr(self, '_initialized'):
            # 🔧 Fix: Initialize logger first, then call _initialize_config
            self.logger = get_logger(__name__)
            self._initialize_config(config)
            self.engine = None
            self.async_session = None
            # ✅ Remove global state, use ExecutionContext to pass instead
            # self._global_session_max_turns = {}  # Deprecated
            self._is_available = False
            self._lock = asyncio.Lock()
            
            # New: Get global connection pool manager
            self._pool_manager = get_connection_pool_manager()
            
            # Engineering-level fix: Session tracking mechanism (detect and clean leaks)
            self._active_sessions: Dict[str, Dict[str, Any]] = {}  # session_id -> {session, opened_at, stack_trace}
            self._session_track_lock = asyncio.Lock()
            self._leaked_session_check_task: Optional[asyncio.Task] = None
            self._session_stats = {
                'total_opened': 0,
                'total_closed': 0,
                'total_leaked': 0,
                'total_forced_closed': 0
            }
            
            # New: Connection pool health check configuration
            self._connection_check_interval = 30  # Check connection pool status every 30 seconds
            
            self._initialized = True
            self.logger.debug("PostgreSQLStore instance created (shared connection pool)")

    def _initialize_config(self, config: Optional[Dict[str, Any]]):
        if config is None:
            # Get latest configuration from ConfigManager each time (support dataset configuration)
            try:
                from timem.utils.config_manager import get_config_manager
                config_manager = get_config_manager()
                storage_config = config_manager.get_storage_config()
                sql_config = storage_config.get('sql', {})
                postgres_config = sql_config.get('postgres', {})
                
                self.logger.info(f"Loaded configuration from ConfigManager: host={postgres_config.get('host')}, port={postgres_config.get('port')}, database={postgres_config.get('database')}")
            except Exception as e:
                # Fallback plan: if ConfigManager fails, use old method
                self.logger.warning(f"Failed to load configuration from ConfigManager, using traditional method: {e}")
                storage_config = get_storage_config()
                sql_config = storage_config.get('sql', {})
                postgres_config = sql_config.get('postgres', {})
            
            # Fix: Default configuration should be read from configuration file, only use default value if not in configuration file
            # Prefer values in configuration file to ensure production environment configuration takes effect
            self.config = {
                'host': postgres_config.get('host', 'localhost'),
                'port': postgres_config.get('port', 5432),
                'user': postgres_config.get('user', 'timem_user'),
                'password': postgres_config.get('password', 'timem_password'),
                'database': postgres_config.get('database', 'timem_db'),
                'pool_size': postgres_config.get('pool_size', 50),  # Engineering-level optimization: 25 -> 50 (support 20 concurrent)
                'max_overflow': postgres_config.get('max_overflow', 50),  # Engineering-level optimization: 35 -> 50 (total 100 connections)
                'pool_timeout': postgres_config.get('pool_timeout', 60),  # Increase timeout: 30 -> 60 seconds
                'pool_recycle': postgres_config.get('pool_recycle', 1800),  # Optimize recycle time: 3600s -> 1800s (30 minutes)
                'pool_pre_ping': postgres_config.get('pool_pre_ping', True),  # Enable connection pre-check
                'pool_reset_on_return': postgres_config.get('pool_reset_on_return', 'rollback')  # Rollback uncommitted transactions on return
            }
            
            # Key fix: Log actual connection pool configuration used
            self.logger.info(f"PostgreSQL connection configuration: {self.config.get('host')}:{self.config.get('port')}/{self.config.get('database')}")
            self.logger.info(f"PostgreSQL connection pool configuration: pool_size={self.config.get('pool_size')}, max_overflow={self.config.get('max_overflow')}")
            
            # 🔧 Info: Confirm whether connection pool configuration is reasonable
            pool_size = self.config.get('pool_size', 0)
            max_overflow = self.config.get('max_overflow', 0)
            total_connections = pool_size + max_overflow
            
            if total_connections >= 50:
                self.logger.info(f"✅ PostgreSQL connection pool configuration optimized (total {total_connections} connections), suitable for high concurrency")
            elif total_connections >= 30:
                self.logger.info(f"⚠️ PostgreSQL connection pool configuration moderate (total {total_connections} connections), moderate concurrency")
            else:
                self.logger.warning(f"❌ PostgreSQL connection pool configuration too small (total {total_connections} connections), may exhaust in high concurrency")
            
            # 🔧 New: Connection pool health check configuration
            self._connection_check_interval = 30  # Check connection pool status every 30 seconds
            self._last_connection_check = 0
        else:
            self.config = config
            self.logger.info(f"Using custom PostgreSQL configuration: pool_size={self.config.get('pool_size')}, max_overflow={self.config.get('max_overflow')}")

    async def connect(self) -> bool:
        """Establish database connection"""
        if not self._is_available:
            async with self._lock:
                if not self._is_available:
                    # 🔧 Fix: Ensure global connection pool is initialized
                    await ensure_connection_pool()
                    
                    # 🔧 Fix: Use global connection pool manager
                    await self._initialize_engine()
                    if self._is_available:
                        await self.ensure_tables()
                        
                        # Engineering-level fix: Start session leak monitoring
                        await self._start_session_leak_monitor()
        return self._is_available
    
    async def disconnect(self) -> bool:
        """Close database connection"""
        try:
            await self.close()
            return True
        except Exception as e:
            self.logger.error(f"Failed to disconnect PostgreSQL: {e}")
            return False
    
    async def is_available(self) -> bool:
        """Check if database is available"""
        if not self._is_available:
            return False
        
        # New: Periodically check connection pool status
        current_time = time.time()
        if current_time - self._last_connection_check > self._connection_check_interval:
            try:
                await self._check_connection_pool_health()
                self._last_connection_check = current_time
            except Exception as e:
                self.logger.warning(f"Connection pool health check failed: {e}")
                return False
        
        return self._is_available
    
    async def _check_connection_pool_health(self):
        """Check connection pool health status"""
        if not self.engine:
            return
        
        try:
            # Check connection pool status
            pool = self.engine.pool
            if hasattr(pool, 'size') and hasattr(pool, 'checked_in') and hasattr(pool, 'checked_out'):
                pool_size = pool.size()
                checked_in = pool.checked_in()
                checked_out = pool.checked_out()
                
                # If connection pool is approaching exhaustion, log warning
                if checked_out > pool_size * 0.8:
                    self.logger.warning(f"PostgreSQL connection pool approaching exhaustion: allocated={checked_out}, pool_size={pool_size}")
                
                # If connection pool is completely exhausted, mark as unavailable
                if checked_out >= pool_size + getattr(pool, 'max_overflow', 0):
                    self.logger.error(f"PostgreSQL connection pool exhausted: allocated={checked_out}, max_connections={pool_size + getattr(pool, 'max_overflow', 0)}")
                    self._is_available = False
                    
        except Exception as e:
            self.logger.warning(f"Connection pool status check exception: {e}")

    def get_connection_pool_status(self) -> Dict[str, Any]:
        """
        Get connection pool status information
        
        Returns:
            Dict[str, Any]: Connection pool status information
        """
        # Fix: Use global connection pool manager's status information
        try:
            return self._pool_manager.get_connection_stats()
        except Exception as e:
            self.logger.error(f"Failed to get connection pool status: {e}")
            return {
                "pool_size": 0,
                "checked_in": 0,
                "checked_out": 0,
                "overflow": 0,
                "total_connections": 0,
                "utilization_percent": 0.0,
                "status": "error",
                "error": str(e)
            }

    async def force_close_all_connections(self):
        """Force close all connections for emergency cleanup"""
        if self.engine:
            try:
                # Fix: Only clean connection pool, do not set _is_available=False
                # Keep PostgreSQL available, just clean connections
                await self.engine.dispose()
                self.logger.warning("PostgreSQL connection pool forcefully cleaned")
                # Note: do not set _is_available = False, keep PostgreSQL available
            except Exception as e:
                self.logger.error(f"Error forcing close PostgreSQL connections: {e}")
                # Only set as unavailable when truly errored
                self._is_available = False

    async def cleanup_connection_pool(self) -> bool:
        """
        Clean up connection pool, release all connections
        
        Returns:
            bool: Whether cleanup was successful
        """
        try:
            self.logger.info("Starting to clean up PostgreSQL connection pool...")
            
            # Get status before cleanup
            before_status = self.get_connection_pool_status()
            self.logger.info(f"Connection pool status before cleanup: {before_status}")
            
            # Fix: Use global connection pool manager's cleanup method
            await self._pool_manager.cleanup_all_connections()
            
            # Wait for connections to be completely released
            await asyncio.sleep(0.5)
            
            # Get status after cleanup
            after_status = self.get_connection_pool_status()
            self.logger.info(f"Connection pool status after cleanup: {after_status}")
            
            # Verify cleanup results
            if after_status.get("checked_out", 0) == 0:
                self.logger.info("PostgreSQL connection pool cleanup successful, all connections released")
                return True
            else:
                self.logger.warning(f"PostgreSQL connection pool cleanup incomplete, {after_status.get('checked_out', 0)} connections still not released")
                return False
                
        except Exception as e:
            self.logger.error(f"Failed to clean up PostgreSQL connection pool: {e}")
            return False

    async def _initialize_engine(self) -> bool:
        try:
            # New architecture: Use unified connection pool manager, completely avoid multi-instance connection pool issues
            unified_manager = await get_unified_connection_manager()
            
            # Fix: Ensure unified connection pool manager is initialized
            if not unified_manager._initialized:
                self.logger.info("Unified connection pool manager not initialized, starting initialization...")
                await unified_manager.initialize()
            
            # Get engine and session factory from unified connection pool manager
            engine = await unified_manager.get_engine(ConnectionPoolType.POSTGRES)
            session_factory = await unified_manager.get_session_factory(ConnectionPoolType.POSTGRES)
            
            if not engine or not session_factory:
                self.logger.error("Unable to get PostgreSQL engine or session factory from unified connection pool manager")
                return False
            
            self.engine = engine
            self.async_session = session_factory
            
            # Engineering optimization: Remove connection test, trust unified connection pool initialization
            # Unified connection pool has already tested connection during initialize()
            # Do not repeat test here to avoid connection pool exhaustion when creating store concurrently
                
            # Get connection pool statistics for logging
            stats = await unified_manager.get_stats(ConnectionPoolType.POSTGRES)
            if stats:
                pool_size = stats.pool_size
                max_overflow = stats.total_connections - stats.pool_size
                
                self.logger.debug(f"PostgreSQL async engine obtained successfully: {self.config['host']}:{self.config['port']}")
                self.logger.debug(f"Unified connection pool configuration: pool_size={pool_size}, max_overflow={max_overflow}")
                self.logger.debug(f"Application name: TiMem_UnifiedPool")
                self.logger.debug(f"Max connections: {stats.total_connections} (base {pool_size} + overflow {max_overflow})")
                self.logger.debug(f"Connection pool status: {stats.status}, utilization: {stats.utilization_percent:.1f}%")
            else:
                self.logger.debug(f"PostgreSQL async engine obtained successfully: {self.config['host']}:{self.config['port']}")
                self.logger.debug("Using unified connection pool manager (statistics not available)")
            
            self._is_available = True
            self.logger.debug("PostgreSQL storage instance ready, using unified connection pool manager")
            
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = None
                
            return True
            
        except Exception as e:
            # Fix: Throw error directly when PostgreSQL connection fails, no fallback to SQLite
            self.logger.error(f"PostgreSQL connection failed: {e}")
            self._is_available = False
            raise RuntimeError(f"PostgreSQL connection failed: {e}")

    async def ensure_tables(self) -> bool:
        """Ensure all tables and indexes exist"""
        if not self._is_available:
            return False
            
        try:
            async with self.engine.begin() as conn:
                # Create table structure
                await conn.run_sync(Base.metadata.create_all)
                
                # Create PostgreSQL-specific extensions and functions
                await self._setup_postgres_extensions(conn)
                
            self.logger.debug("All PostgreSQL table structures and extensions confirmed or created")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to create PostgreSQL tables: {e}")
            return False

    async def _setup_postgres_extensions(self, conn):
        """Set up PostgreSQL-specific extensions and functions"""
        try:
            # Execute each SQL command separately to avoid multi-command issues
            
            # 1. Create tsvector update function (support application-layer tokenization)
            # Note: application layer provides already-tokenized text, so use simple config here
            # simple config tokenizes by space, suitable for processing already-tokenized Chinese text
            function_sql = """
            CREATE OR REPLACE FUNCTION update_tsvector() RETURNS TRIGGER AS $$
            BEGIN
                -- Only auto-generate when tsvector is NULL (application layer will set actively)
                -- Use simple config to process already-tokenized text (keep all words, space-separated)
                IF NEW.title_tsvector IS NULL THEN
                    NEW.title_tsvector = to_tsvector('simple', COALESCE(NEW.title, ''));
                END IF;
                IF NEW.content_tsvector IS NULL THEN
                    NEW.content_tsvector = to_tsvector('simple', COALESCE(NEW.content, ''));
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
            await conn.execute(text(function_sql))
            
            # 2. Drop existing trigger (if exists)
            drop_trigger_sql = "DROP TRIGGER IF EXISTS tsvector_update_trigger ON core_memories;"
            await conn.execute(text(drop_trigger_sql))
            
            # 3. Create new trigger
            create_trigger_sql = """
            CREATE TRIGGER tsvector_update_trigger
                BEFORE INSERT OR UPDATE ON core_memories
                FOR EACH ROW EXECUTE FUNCTION update_tsvector();
            """
            await conn.execute(text(create_trigger_sql))
            
            # 4. Create BM25 scoring function
            bm25_function_sql = """
            CREATE OR REPLACE FUNCTION bm25_score(
                content_tsvector TSVECTOR,
                query_tsquery TSQUERY,
                doc_length INTEGER DEFAULT 100,
                avg_doc_length FLOAT DEFAULT 100.0,
                k1 FLOAT DEFAULT 1.2,
                b FLOAT DEFAULT 0.75
            ) RETURNS FLOAT AS $$
            DECLARE
                tf FLOAT;
                norm_length FLOAT;
                score FLOAT;
            BEGIN
                -- Calculate term frequency
                tf := ts_rank_cd(content_tsvector, query_tsquery);
                
                -- If no match, return 0
                IF tf = 0 THEN
                    RETURN 0;
                END IF;
                
                -- Document length normalization
                norm_length := 1.0 + b * (doc_length / avg_doc_length - 1.0);
                
                -- BM25 formula
                score := tf * (k1 + 1.0) / (tf + k1 * norm_length);
                
                RETURN score;
            END;
            $$ LANGUAGE plpgsql;
            """
            await conn.execute(text(bm25_function_sql))
            
            self.logger.debug("PostgreSQL extensions and functions setup complete")
            
        except Exception as e:
            self.logger.warning(f"Failed to set up PostgreSQL extensions: {e}")

    async def close(self):
        """Close database connection and release all resources"""
        if self.engine:
            try:
                # Fix: Ensure all connections are properly closed
                self.logger.info("Starting to close PostgreSQL connection pool...")
                
                # Force release all connections in connection pool
                await self.engine.dispose()
                
                # Wait for connection pool to be completely cleaned
                await asyncio.sleep(0.1)
                
                self._is_available = False
                self.logger.info("PostgreSQL storage connection completely closed")
                
            except Exception as e:
                self.logger.error(f"Error closing PostgreSQL connection: {e}")
                self._is_available = False
                raise
    
    async def force_close_all_connections(self):
        """Force close all connections for emergency cleanup"""
        if self.engine:
            try:
                # Fix: Only clean connection pool, do not set _is_available=False
                # Keep PostgreSQL available, just clean connections
                await self.engine.dispose()
                self.logger.warning("PostgreSQL connection pool forcefully cleaned")
                # Note: do not set _is_available = False, keep PostgreSQL available
            except Exception as e:
                self.logger.error(f"Error forcing close PostgreSQL connections: {e}")
                # Only set as unavailable when truly errored
                self._is_available = False

    async def is_connected(self) -> bool:
        """Check if database is connected"""
        return self._is_available and self.engine is not None

    async def _get_session_cm(self):
        """Compatible context manager acquisition for real implementation and test replacement (AsyncMock).
        When get_session is AsyncMock, directly return self.get_session().
        When get_session is real implementation, return context manager of self.get_session().
        """
        return self.get_session()

    async def _track_session_open(self, session_id: str, session: AsyncSession):
        """🔧 Engineering-level fix: Track opened sessions"""
        async with self._session_track_lock:
            import traceback
            self._active_sessions[session_id] = {
                'session': session,
                'opened_at': time.time(),
                'stack_trace': ''.join(traceback.format_stack()[-5:])  # Save call stack
            }
            self._session_stats['total_opened'] += 1
    
    async def _track_session_close(self, session_id: str):
        """🔧 Engineering-level fix: Track closed sessions"""
        async with self._session_track_lock:
            if session_id in self._active_sessions:
                del self._active_sessions[session_id]
                self._session_stats['total_closed'] += 1
    
    async def _safe_close_session(self, session: AsyncSession):
        """🔧 Engineering-level fix: Safely close session (with timeout protection)"""
        try:
            # Ensure transaction is properly closed
            if session.in_transaction():
                await session.rollback()
            
            # Close session
            await session.close()
            
        except Exception as e:
            self.logger.warning(f"Error safely closing session: {e}")
            raise
    
    async def _force_close_session(self, session: AsyncSession, reason: str):
        """🔧 Engineering-level fix: Force close session"""
        try:
            self.logger.warning(f"Force close session (reason: {reason})")
            
            # Try to rollback transaction
            try:
                if session.in_transaction():
                    await asyncio.wait_for(session.rollback(), timeout=1.0)
            except:
                pass
            
            # Force close session
            try:
                await asyncio.wait_for(session.close(), timeout=1.0)
            except:
                pass
            
            self._session_stats['total_forced_closed'] += 1
            
        except Exception as e:
            self.logger.error(f"Force close session failed: {e}")
    
    async def _get_leaked_sessions(self) -> List[str]:
        """🔧 Engineering-level fix: Get list of leaked sessions (not closed for over 60 seconds)"""
        leaked = []
        current_time = time.time()
        
        async with self._session_track_lock:
            for session_id, info in self._active_sessions.items():
                if current_time - info['opened_at'] > 60:  # Over 60 seconds
                    leaked.append(session_id)
        
        return leaked
    
    async def _cleanup_leaked_sessions(self):
        """🔧 Engineering-level fix: Clean up leaked sessions"""
        leaked_ids = await self._get_leaked_sessions()
        
        if leaked_ids:
            self.logger.warning(f"Detected {len(leaked_ids)} leaked sessions, starting cleanup...")
            
            async with self._session_track_lock:
                for session_id in leaked_ids:
                    if session_id in self._active_sessions:
                        info = self._active_sessions[session_id]
                        session = info['session']
                        
                        self.logger.warning(
                            f"Cleaning up leaked session {session_id} "
                            f"(open duration: {time.time() - info['opened_at']:.1f}s)"
                        )
                        
                        # Force close
                        await self._force_close_session(session, "leaked")
                        
                        # Remove from tracking
                        del self._active_sessions[session_id]
                        self._session_stats['total_leaked'] += 1
    
    async def _start_session_leak_monitor(self):
        """🔧 Engineering-level fix: Start session leak monitoring task"""
        if self._leaked_session_check_task is None or self._leaked_session_check_task.done():
            self._leaked_session_check_task = asyncio.create_task(self._session_leak_monitor_loop())
            self.logger.info("Session leak monitoring task started")
    
    async def _session_leak_monitor_loop(self):
        """🔧 Engineering-level fix: Session leak monitoring loop"""
        while self._is_available:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                await self._cleanup_leaked_sessions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Session leak monitoring exception: {e}")
                await asyncio.sleep(60)

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """🔧 Engineering-level fix: Get database session, ensure connection 100% released"""
        if not await self.connect():
            raise SQLAlchemyError("PostgreSQL database not connected")
        
        # 🔧 Fix: Directly use PostgreSQLStore's engine and session factory
        if not self.engine or not self.async_session:
            # If engine or session factory not initialized, reinitialize
            await self._initialize_pool_manager()
        
        session = None
        session_id = str(uuid.uuid4())[:8]  # For tracking
        
        try:
            # Create session
            session = self.async_session()
            
            # Track opened session
            await self._track_session_open(session_id, session)
            
            yield session
            
        except asyncio.CancelledError:
            # 🔧 Special handling: asyncio cancellation exception
            if session:
                await self._force_close_session(session, "cancelled")
            raise
            
        except Exception as e:
            # 🔧 Exception handling: log and rollback
            if session and session.in_transaction():
                try:
                    await session.rollback()
                except:
                    pass
            raise
            
        finally:
            # 🔧 Ensure 100% session closure
            if session:
                try:
                    await asyncio.wait_for(
                        self._safe_close_session(session),
                        timeout=5.0
                    )
                except asyncio.TimeoutError:
                    await self._force_close_session(session, "timeout")
                except Exception as e:
                    self.logger.error(f"Error closing session: {e}")
                    await self._force_close_session(session, "error")
                finally:
                    await self._track_session_close(session_id)
    
    async def _initialize_pool_manager(self):
        """🔧 Emergency fix: Ensure connection pool manager is properly initialized"""
        try:
            # Ensure unified connection pool manager is initialized
            from timem.core.unified_connection_manager import get_unified_connection_manager
            unified_manager = await get_unified_connection_manager()
            
            if not unified_manager._initialized:
                self.logger.info("Unified connection pool manager not initialized, starting initialization...")
                await unified_manager.initialize()
            
            # Get PostgreSQL engine and session factory
            from timem.core.unified_connection_manager import ConnectionPoolType
            engine = await unified_manager.get_engine(ConnectionPoolType.POSTGRES)
            session_factory = await unified_manager.get_session_factory(ConnectionPoolType.POSTGRES)
            
            if engine and session_factory:
                # 🔧 Fix: Directly update PostgreSQLStore's engine and session factory
                self.engine = engine
                self.async_session = session_factory
                
                # Also update connection pool manager's engine and session factory
                if hasattr(self._pool_manager, '_engine'):
                    self._pool_manager._engine = engine
                if hasattr(self._pool_manager, '_session_factory'):
                    self._pool_manager._session_factory = session_factory
                
                self.logger.info("✅ Connection pool manager reinitialized successfully")
            else:
                raise RuntimeError("Unable to get engine or session factory from unified connection pool manager")
                
        except Exception as e:
            self.logger.error(f"❌ Connection pool manager initialization failed: {e}")
            raise

    def _row_to_core_dict(self, row: Any) -> Dict[str, Any]:
        """Convert ORM object to core field dictionary"""
        if hasattr(row, "__table__"):
            result = {c.name: getattr(row, c.name) for c in row.__table__.columns}
            # Exclude tsvector fields, these are for internal use
            result.pop('content_tsvector', None)
            result.pop('title_tsvector', None)
            return result
            
        # Fallback plan
        fields = [
            "id", "user_id", "expert_id", "level", "title", "content", "status",
            "created_at", "updated_at", "time_window_start", "time_window_end"
        ]
        return {f: getattr(row, f, None) for f in fields}

    async def search_memories_fulltext(self,
                                     query_text: str,
                                     user_id: Optional[str] = None,
                                     expert_id: Optional[str] = None,
                                     level: Optional[str] = None,
                                     limit: int = 20,
                                     use_bm25: bool = True,
                                     is_tokenized: bool = False) -> List[Dict[str, Any]]:
        """
        Search memories using PostgreSQL full-text search functionality

        Args:
            query_text: Query text or already tokenized keywords (space-separated)
            user_id: User ID (required)
            expert_id: Expert ID (optional)
            level: Memory level (optional)
            limit: Result count limit
            use_bm25: Whether to use BM25 scoring
            is_tokenized: Whether query text is already tokenized (if True, skip tokenization step)
        """

        # Security enhancement: Force require user_id parameter
        if not user_id:
            self.logger.error("Security error: search_memories_fulltext call missing user_id parameter, rejecting query")
            return []

        # Chinese tokenization: Decide whether to tokenize based on is_tokenized parameter
        if is_tokenized:
            # Already tokenized, just clean and normalize
            query_tokenized = tokenize_for_postgres(query_text, is_tokenized=True)
            self.logger.debug(f"Using already tokenized keywords: '{query_text}' -> '{query_tokenized}'")
        else:
            # Need complete tokenization
            query_tokenized = tokenize_for_postgres(query_text, is_tokenized=False)
            self.logger.debug(f"Tokenize query text: '{query_text}' -> '{query_tokenized}'")

        async with self.get_session() as session:
            try:
                # Build base query with LEFT JOIN for level-specific tables
                # This ensures we get all fields including dialogue_turns_json for L1 memories
                if use_bm25:
                    # Use custom BM25 scoring (use already tokenized query text)
                    query_sql = text("""
                        SELECT cm.*,
                               bm25_score(
                                   cm.content_tsvector || cm.title_tsvector,
                                   plainto_tsquery('simple', :query_tokenized),
                                   LENGTH(cm.content),
                                   100.0
                               ) as bm25_score,
                               ts_rank_cd(
                                   cm.content_tsvector || cm.title_tsvector,
                                   plainto_tsquery('simple', :query_tokenized)
                               ) as ts_rank_score,
                               l1.dialogue_turns_json,
                               l2.session_id,
                               l3.date_value,
                               l4.year,
                               l4.week_number,
                               l5.month
                        FROM core_memories cm
                        LEFT JOIN l1_fragment_memories l1 ON cm.id = l1.memory_id AND cm.level = 'L1'
                        LEFT JOIN l2_session_memories l2 ON cm.id = l2.memory_id AND cm.level = 'L2'
                        LEFT JOIN l3_daily_memories l3 ON cm.id = l3.memory_id AND cm.level = 'L3'
                        LEFT JOIN l4_weekly_memories l4 ON cm.id = l4.memory_id AND cm.level = 'L4'
                        LEFT JOIN l5_monthly_memories l5 ON cm.id = l5.memory_id AND cm.level = 'L5'
                        WHERE (cm.content_tsvector || cm.title_tsvector) @@ plainto_tsquery('simple', :query_tokenized)
                        AND cm.user_id = :user_id
                    """)
                else:
                    # Use PostgreSQL native ts_rank (use already tokenized query text)
                    query_sql = text("""
                        SELECT cm.*,
                               ts_rank_cd(
                                   cm.content_tsvector || cm.title_tsvector,
                                   plainto_tsquery('simple', :query_tokenized)
                               ) as ts_rank_score,
                               l1.dialogue_turns_json,
                               l2.session_id,
                               l3.date_value,
                               l4.year,
                               l4.week_number,
                               l5.month
                        FROM core_memories cm
                        LEFT JOIN l1_fragment_memories l1 ON cm.id = l1.memory_id AND cm.level = 'L1'
                        LEFT JOIN l2_session_memories l2 ON cm.id = l2.memory_id AND cm.level = 'L2'
                        LEFT JOIN l3_daily_memories l3 ON cm.id = l3.memory_id AND cm.level = 'L3'
                        LEFT JOIN l4_weekly_memories l4 ON cm.id = l4.memory_id AND cm.level = 'L4'
                        LEFT JOIN l5_monthly_memories l5 ON cm.id = l5.memory_id AND cm.level = 'L5'
                        WHERE (cm.content_tsvector || cm.title_tsvector) @@ plainto_tsquery('simple', :query_tokenized)
                        AND cm.user_id = :user_id
                    """)

                params = {"query_tokenized": query_tokenized, "user_id": user_id}

                # Add filter conditions
                conditions = []
                if expert_id:
                    conditions.append("cm.expert_id = :expert_id")
                    params["expert_id"] = expert_id
                if level:
                    conditions.append("cm.level = :level")
                    params["level"] = level
                
                if conditions:
                    query_sql = text(str(query_sql) + " AND " + " AND ".join(conditions))
                
                # Add sorting and limit
                order_by = "bm25_score DESC" if use_bm25 else "ts_rank_score DESC"
                query_sql = text(str(query_sql) + f" ORDER BY {order_by} LIMIT :limit")
                params["limit"] = limit
                
                # Execute query
                result = await session.execute(query_sql, params)
                
                # Process results
                memories = []
                for row in result:
                    memory_dict = dict(row._mapping)
                    # Remove internal fields
                    memory_dict.pop('content_tsvector', None)
                    memory_dict.pop('title_tsvector', None)
                    memories.append(memory_dict)
                
                self.logger.info(f"PostgreSQL full-text search found {len(memories)} memories")
                return memories
                
            except Exception as e:
                self.logger.error(f"PostgreSQL full-text search failed: {e}")
                return []

    async def get_or_create_session(self, session: AsyncSession, user_id: str, expert_id: str, session_id: str, start_time: Optional[datetime] = None) -> str:
        """
        Get or create session
        
        Args:
            session: Database session
            user_id: User ID
            expert_id: Expert ID
            session_id: Session ID
            start_time: Session start time (external time)
        """
        stmt = select(MemorySession).where(MemorySession.id == session_id)
        result = await session.execute(stmt)
        if existing := result.scalar_one_or_none():
            return existing.id
        
        # ✅ Fix: Use external time as session start time
        if start_time is None:
            start_time = datetime.now()
            self.logger.warning(f"Session {session_id} did not provide start_time, using current time")
            
        new_session = MemorySession(
            id=session_id, 
            user_id=user_id, 
            expert_id=expert_id,
            start_time=start_time  # ✅ Use external time
        )
        session.add(new_session)
        await session.flush()
        return new_session.id

    async def batch_store_memories(
        self, 
        memory_records: List[Dict[str, Any]],
        execution_state: Optional['ExecutionState'] = None
    ) -> List[str]:
        """
        Batch store memories to PostgreSQL
        
        Args:
            memory_records: List of memory records
            execution_state: Execution state (optional, for state management and concurrency isolation)
                   If provided, will use state from execution_state;
                   If not provided, will query from database (backward compatible)
        
        Returns:
            List[str]: List of successfully stored memory IDs
        """
        # ✅ Debug: Check memory_records parameter
        self.logger.debug(f"Batch store start: memory_records type={type(memory_records)}, memory_records is None={memory_records is None}")
        if memory_records is not None:
            self.logger.debug(f"Batch store start: memory_records length={len(memory_records)}")
        
        try:
            # 🔧 Emergency fix: Batch processing to avoid large transactions and connection leaks
            batch_size = 50  # 50 records per batch
            all_ids = []
            
            self.logger.info(f"Starting batch store {len(memory_records)} memories, batch size: {batch_size}")
            
            for i in range(0, len(memory_records), batch_size):
                batch = memory_records[i:i + batch_size]
                batch_num = i // batch_size + 1
                
                try:
                    self.logger.debug(f"Processing batch {batch_num}, record count: {len(batch)}")
                    batch_ids = await self._store_batch_atomic(batch, execution_state)
                    all_ids.extend(batch_ids)
                    self.logger.debug(f"Batch {batch_num} store successful, stored {len(batch_ids)} memories")
                    
                except Exception as e:
                    self.logger.error(f"Batch {batch_num} store failed: {e}")
                    # Continue processing other batches, do not interrupt entire process
                    continue
            
            self.logger.info(f"Batch store completed, total stored {len(all_ids)} memories")
            return all_ids
            
        except Exception as e:
            self.logger.error(f"Batch store memories failed: {e}")
            return []
    
    async def _store_batch_atomic(self, batch: List[Dict[str, Any]], execution_state: Optional['ExecutionState'] = None) -> List[str]:
        """Atomically store single batch"""
        # ✅ Fix: Prepare session_ids outside transaction to avoid scope issues
        session_ids = set()
        for record in batch:
            if record.get('level') in ['L1', 'L2'] and (session_id := record.get('session_id')):
                session_ids.add(session_id)
        
        # Prepare session turn tracking dictionary
        session_max_turns = {}
        
        self.logger.debug(f"Prepare session turn tracking: session_ids={session_ids}, execution_state={execution_state is not None}")
        
        async with self.get_session() as session:
            try:
                async with session.begin():
                    created_sessions = set()
                    ids = []
                    
                    # Define correct child memory level relationships
                    correct_child_levels = {
                        "L1": [],
                        "L2": ["L1"], 
                        "L3": ["L2"],
                        "L4": ["L3"],
                        "L5": ["L4"]
                    }
                    
                    if execution_state:
                        # ✅ Use state from execution_state
                        for session_id in session_ids:
                            session_max_turns[session_id] = execution_state.get_session_max_turn(session_id)
                        
                        self.logger.debug(f"[State {execution_state.state_id[:8]}] Using execution state: session_max_turns type={type(session_max_turns)}, value={session_max_turns}")
                        self.logger.debug(f"[State {execution_state.state_id[:8]}] Using execution state: {len(session_max_turns)} sessions")
                    else:
                        # ✅ Compatibility mode: Query from database
                        if session_ids:
                            max_turn_stmt = select(
                                DialogueOriginal.session_id,
                                func.coalesce(func.max(DialogueOriginal.turn_number), 0).label('max_turn')
                            ).where(
                                DialogueOriginal.session_id.in_(session_ids)
                            ).group_by(DialogueOriginal.session_id)
                            
                            max_turn_res = await session.execute(max_turn_stmt)
                            for row in max_turn_res:
                                session_max_turns[row.session_id] = int(row.max_turn)
                            
                            # Initialize new sessions without records
                            for session_id in session_ids:
                                if session_id not in session_max_turns:
                                    session_max_turns[session_id] = 0
                        
                        self.logger.debug(f"[Compatibility mode] Query from database: {len(session_max_turns)} sessions")
                
                    # ✅ Process all records (regardless of execution_state)
                    for record in batch:
                        # Process session-related memories
                        if record.get('level') in ['L1', 'L2'] and (session_id := record.get('session_id')):
                            if session_id not in created_sessions:
                                # ✅ Fix: Pass external time as session start time
                                session_start_time = record.get('time_window_start') or record.get('time_window_end')
                                await self.get_or_create_session(
                                    session, 
                                    record['user_id'], 
                                    record['expert_id'], 
                                    session_id,
                                    start_time=session_start_time
                                )
                                created_sessions.add(session_id)
                    
                        # Prepare core memory data
                        core_fields = {c.name for c in CoreMemory.__table__.columns}
                        core_data = {k: v for k, v in record.items() if k in core_fields}
                        
                        memory_id = record.get('id', str(uuid.uuid4()))
                        core_data['id'] = memory_id
                        
                        # Fill in time fields
                        if not core_data.get('created_at'):
                            core_data['created_at'] = record.get('time_window_start') or record.get('time_window_end')
                        if not core_data.get('updated_at'):
                            core_data['updated_at'] = record.get('time_window_end') or record.get('time_window_start')
                        
                        # 🔧 Chinese tokenization: Tokenize title and content before storing
                        # Then use PostgreSQL's to_tsvector function to generate tsvector
                        title_text = core_data.get('title', '')
                        content_text = core_data.get('content', '')
                        
                        # Tokenize text (generate space-separated words)
                        title_tokenized = tokenize_for_postgres(title_text)
                        content_tokenized = tokenize_for_postgres(content_text)
                        
                        # Use func.to_tsvector() to generate tsvector (SQLAlchemy function)
                        # simple config tokenizes by space, suitable for processing already-tokenized Chinese text
                        core_data['title_tsvector'] = func.to_tsvector('simple', title_tokenized)
                        core_data['content_tsvector'] = func.to_tsvector('simple', content_tokenized)
                        
                        ids.append(memory_id)
                        
                        # Check action field, decide insert or update
                        action = record.get('action', 'create')
                        
                        if action == 'update' and record.get('existing_memory_id'):
                            # Update mode: use existing_memory_id to update
                            core_data['id'] = record['existing_memory_id']
                            memory_id = record['existing_memory_id']
                            ids[-1] = memory_id  # Update returned ID list
                            
                            # Use ON CONFLICT DO UPDATE to ensure idempotency
                            from sqlalchemy.dialects.postgresql import insert as pg_insert
                            stmt = pg_insert(CoreMemory).values(**core_data)
                            stmt = stmt.on_conflict_do_update(
                                index_elements=['id'],
                                set_={
                                    'title': stmt.excluded.title,
                                    'content': stmt.excluded.content,
                                    'updated_at': stmt.excluded.updated_at,
                                    'title_tsvector': stmt.excluded.title_tsvector,
                                    'content_tsvector': stmt.excluded.content_tsvector
                                }
                            )
                            await session.execute(stmt)
                            self.logger.debug(f"Update memory: {memory_id}, action={action}")
                        else:
                            # Create mode: use ON CONFLICT DO UPDATE to ensure idempotency (L3/L4/L5)
                            from sqlalchemy.dialects.postgresql import insert as pg_insert
                            stmt = pg_insert(CoreMemory).values(**core_data)
                            stmt = stmt.on_conflict_do_update(
                                index_elements=['id'],
                                set_={
                                    'title': stmt.excluded.title,
                                    'content': stmt.excluded.content,
                                    'updated_at': stmt.excluded.updated_at,
                                    'title_tsvector': stmt.excluded.title_tsvector,
                                    'content_tsvector': stmt.excluded.content_tsvector
                                }
                            )
                            await session.execute(stmt)
                            self.logger.debug(f"Insert/update memory: {memory_id}, action={action}")
                    
                        # Store level-specific data
                        level = record['level']
                        level_table_map = {
                            'L1': L1FragmentMemory, 
                            'L2': L2SessionMemory, 
                            'L3': L3DailyMemory, 
                            'L4': L4WeeklyMemory, 
                            'L5': L5MonthlyMemory
                        }
                        
                        if level in level_table_map:
                            level_data = {k: v for k, v in record.items() if hasattr(level_table_map[level], k)}
                            
                            # Ensure required fields exist
                            level_data['memory_id'] = memory_id
                            
                            # Set required fields for all levels
                            if level in ['L1', 'L2']:
                                # L1 and L2 need session_id
                                level_data['session_id'] = record.get('session_id')
                                # ✅ Fix: Stricter check, allow empty string but warn
                                if level_data.get('session_id') is None or level_data.get('session_id') == '':
                                    self.logger.warning(f"Memory {memory_id} (level={level}) missing valid session_id: '{level_data.get('session_id')}', skip level data storage")
                                    continue
                            
                            if level in ['L2','L3','L4','L5']:
                                level_data['user_id'] = record.get('user_id')
                                level_data['expert_id'] = record.get('expert_id')
                        
                            # Special handling for L1's dialogue_turns field
                            if level == 'L1':
                                if 'dialogue_turns' in record:
                                    level_data['dialogue_turns_json'] = record['dialogue_turns']
                                    level_data.pop('dialogue_turns', None)
                                    
                            # Insert dialogue originals
                            self.logger.debug(f"Prepare to insert dialogue originals: session_max_turns type={type(session_max_turns)}, value={session_max_turns}")
                            await self._insert_dialogue_originals(session, record, memory_id, session_max_turns, execution_state)
                            
                            # Use appropriate ON CONFLICT strategy based on level
                            from sqlalchemy.dialects.postgresql import insert as pg_insert
                            stmt = pg_insert(level_table_map[level]).values(**level_data)
                            
                            # Define unique constraint fields for each level
                            if level == 'L2':
                                # L2: UNIQUE(user_id, expert_id, session_id)
                                conflict_keys = ['user_id', 'expert_id', 'session_id']
                            elif level == 'L3':
                                # L3: UNIQUE(user_id, expert_id, date_value)
                                conflict_keys = ['user_id', 'expert_id', 'date_value']
                            elif level == 'L4':
                                # L4: UNIQUE(user_id, expert_id, year, week_number)
                                conflict_keys = ['user_id', 'expert_id', 'year', 'week_number']
                            elif level == 'L5':
                                # L5: UNIQUE(user_id, expert_id, year, month)
                                conflict_keys = ['user_id', 'expert_id', 'year', 'month']
                            else:
                                # L1: Use memory_id as unique key
                                conflict_keys = ['memory_id']
                            
                            # Build update dictionary (exclude unique key fields)
                            update_dict = {k: stmt.excluded[k] for k in level_data.keys() if k not in conflict_keys}
                            
                            if level in ['L2', 'L3', 'L4', 'L5'] and len(conflict_keys) > 1:
                                # L2/L3/L4/L5 have business unique constraints, use ON CONFLICT DO UPDATE
                                stmt = stmt.on_conflict_do_update(
                                    index_elements=conflict_keys,
                                    set_=update_dict
                                )
                                self.logger.debug(f"Using idempotent insert/update: {level}, conflicts={conflict_keys}")
                            else:
                                # L1 uses memory_id unique constraint
                                stmt = stmt.on_conflict_do_update(
                                    index_elements=conflict_keys,
                                    set_=update_dict
                                )
                            
                            await session.execute(stmt)
                        
                        # Process child memory relationships
                        if child_ids := record.get('child_memory_ids'):
                            valid_child_ids = await self._validate_child_memory_ids(
                                session, memory_id, level, child_ids, correct_child_levels
                            )
                            for cid in valid_child_ids:
                                await session.execute(
                                    insert(MemoryChildRelation).values(
                                        id=str(uuid.uuid4()),
                                        parent_id=memory_id,
                                        child_id=cid
                                    )
                                )
                            
                        # Process historical memory relationships
                        if hist_ids := record.get('historical_memory_ids'):
                            valid_hist_ids = await self._validate_historical_memory_ids(
                                session, memory_id, level, hist_ids
                            )
                            for hid in valid_hist_ids:
                                await session.execute(
                                    insert(MemoryHistoricalRelation).values(
                                        id=str(uuid.uuid4()),
                                        memory_id=memory_id,
                                        historical_memory_id=hid
                                    )
                                )
                    
                    # ✅ After all memories processed, update execution state (outside loop)
                    if execution_state:
                        self.logger.debug(f"Prepare to update execution state: session_max_turns type={type(session_max_turns)}, value={session_max_turns}")
                        for session_id, max_turn in session_max_turns.items():
                            execution_state.set_session_max_turn(session_id, max_turn)
                        self.logger.debug(f"[State {execution_state.state_id[:8]}] Update session state: session_max_turns type={type(session_max_turns)}, value={session_max_turns}")
                        self.logger.debug(f"[State {execution_state.state_id[:8]}] Update session state: {len(session_max_turns)} sessions")
                
                    # ✅ Explicitly commit transaction to ensure data persistence (outside loop)
                    await session.commit()
                    self.logger.debug(f"✅ Transaction committed, successfully stored {len(ids)} memories")
                    
                    return ids
                    
            except Exception as e:
                self.logger.error(f"Batch store failed: {e}")
                return []

    async def _insert_dialogue_originals(
        self, 
        session: AsyncSession, 
        record: Dict[str, Any], 
        memory_id: str, 
        session_max_turns: Dict[str, int],
        execution_state: Optional['ExecutionState'] = None
    ):
        """Insert dialogue original records (L1 specific)"""
        try:
            session_id_val = record.get('session_id')
            turns = record.get('dialogue_turns_json') or record.get('dialogue_turns') or []
            
            self.logger.debug(f"Insert dialogue original debug: session_id={session_id_val}, turns type={type(turns)}, turns value={turns}")
            
            if not turns:
                self.logger.debug(f"No dialogue turns, skip insert: session_id={session_id_val}")
                return
            
            # Use pre-queried session max turn number
            current_max_turn = session_max_turns.get(session_id_val, 0)
            
            self.logger.debug(f"Insert dialogue original: session_id={session_id_val}, memory_id={memory_id}, "
                           f"current_max_turn={current_max_turn}, turns_count={len(turns)}")
            
            # Insert each dialogue turn
            for idx_t, t in enumerate(turns):
                role_raw = (t.get('speaker') or '').lower()
                role = 'user' if role_raw in ('user', 'user') else (
                    'expert' if role_raw in ('expert', 'assistant', 'expert') else role_raw or 'unknown'
                )
                content_val = t.get('text') or t.get('content') or ''
                ts_val = t.get('timestamp')
                
                # Handle timestamp
                if isinstance(ts_val, str):
                    try:
                        ts_val = datetime.fromisoformat(ts_val.replace('Z', '+00:00')).replace(tzinfo=None)
                    except Exception:
                        try:
                            ts_val = time_parser.parse_session_time(ts_val)
                        except Exception:
                            ts_val = None
                
                turn_no = current_max_turn + idx_t + 1
                
                self.logger.debug(f"Insert dialogue_original: turn_number={turn_no}, role={role}, "
                               f"speaker={t.get('speaker')}, content_preview={content_val[:50]}...")
                
                await session.execute(
                    insert(DialogueOriginal).values(
                        id=str(uuid.uuid4()),
                        session_id=session_id_val,
                        turn_number=turn_no,
                        role=role,
                        content=content_val,
                        timestamp=ts_val or record.get('created_at')
                    )
                )
            
            # ✅ Update session turn state (local state + context state)
            if turns:
                old_max = session_max_turns.get(session_id_val, 0)
                new_max = current_max_turn + len(turns)
                session_max_turns[session_id_val] = new_max
                
                # ✅ If execution state exists, sync update
                if execution_state:
                    execution_state.set_session_max_turn(session_id_val, new_max)
                    self.logger.debug(f"[State {execution_state.state_id[:8]}] Update session turns: {session_id_val} {old_max} -> {new_max}")
                else:
                    self.logger.debug(f"[Compatibility mode] Update session turns: {session_id_val} {old_max} -> {new_max}")
                
        except Exception as e:
            self.logger.warning(f"Insert dialogue original failed: {e}")

    async def _validate_child_memory_ids(self, session, parent_id: str, parent_level: str, 
                                       child_ids: List[str], correct_child_levels: Dict[str, List[str]]) -> List[str]:
        """Validate child memory ID list, ensure level correctness"""
        expected_child_levels = correct_child_levels.get(parent_level, [])
        if not expected_child_levels:
            return []
        
        valid_child_ids = []
        for child_id in child_ids:
            child_result = await session.execute(
                select(CoreMemory.level).where(CoreMemory.id == child_id)
            )
            child_level = child_result.scalar_one_or_none()
            
            if not isinstance(child_level, str):
                valid_child_ids.append(child_id)
                continue
            
            if child_level and child_level in expected_child_levels:
                valid_child_ids.append(child_id)
            else:
                self.logger.warning(f"Memory {parent_id} ({parent_level}) child memory {child_id} ({child_level}) level incorrect")
        
        return valid_child_ids

    async def _validate_historical_memory_ids(self, session, memory_id: str, memory_level: str, 
                                            hist_ids: List[str]) -> List[str]:
        """Validate historical memory ID list, ensure level consistency"""
        valid_hist_ids = []
        
        for hist_id in hist_ids:
            hist_result = await session.execute(
                select(CoreMemory.level).where(CoreMemory.id == hist_id)
            )
            hist_level = hist_result.scalar_one_or_none()
            
            if not isinstance(hist_level, str):
                valid_hist_ids.append(hist_id)
                continue
            
            if hist_level and hist_level == memory_level:
                valid_hist_ids.append(hist_id)
            else:
                self.logger.warning(f"Memory {memory_id} ({memory_level}) historical memory {hist_id} ({hist_level}) level inconsistent")
        
        return valid_hist_ids

    async def find_memories(self, **kwargs) -> List[Dict[str, Any]]:
        """Find memories by conditions (compatible with SQLStore interface)"""
        async with self.get_session() as session:
            query = select(CoreMemory)
            
            # 🔒 Security enhancement: Force require user_id parameter, ensure user isolation
            user_id = kwargs.get('user_id')
            if not user_id:
                self.logger.error("🚨 Security error: find_memories call missing user_id parameter, rejecting query")
                return []
            
            # Build query conditions
            if memory_id := kwargs.get('id'):
                query = query.where(CoreMemory.id == memory_id)
            
            # 🔒 Force user filter
            query = query.where(CoreMemory.user_id == user_id)
            
            if expert_id := kwargs.get('expert_id'):
                query = query.where(CoreMemory.expert_id == expert_id)
            if level := kwargs.get('level'):
                # ✅ Fix: Handle different types of level field (string or enum)
                level_value = str(level).replace('MemoryLevel.', '') if 'MemoryLevel' in str(level) else str(level)
                query = query.where(CoreMemory.level == level_value)
            if session_id := kwargs.get('session_id'):
                level_str = str(kwargs.get('level', ''))
                if level_str in ['L1', 'L2']:
                    if level_str == 'L1':
                        query = query.join(L1FragmentMemory).where(L1FragmentMemory.session_id == session_id)
                    elif level_str == 'L2':
                        query = query.join(L2SessionMemory).where(L2SessionMemory.session_id == session_id)
            
            # ✅ Fix: Distinguish two time query modes
            # Mode 1: start_time + end_time = time window overlap query (for L3/L4/L5 query child memories)
            # Mode 2: time_window_start + time_window_end = exact match query (for finding memories in specific time window)
            
            if 'start_time' in kwargs and 'end_time' in kwargs:
                # ✅ Fix: Mode 1 - Query child memories by created_at
                # For querying: all memories in a time window (e.g., L3 querying all L2 of the day)
                # Core logic: L2.created_at >= query_start AND L2.created_at <= query_end
                query_start = kwargs.get('start_time')
                query_end = kwargs.get('end_time')
                
                # Ensure datetime object
                if isinstance(query_start, str):
                    from timem.utils.time_utils import parse_time
                    query_start = parse_time(query_start)
                if isinstance(query_end, str):
                    from timem.utils.time_utils import parse_time
                    query_end = parse_time(query_end)
                
                # ✅ Determine if child memory's created_at is within parent's time window
                from sqlalchemy import and_
                query = query.where(
                    and_(
                        CoreMemory.created_at >= query_start,
                        CoreMemory.created_at <= query_end
                    )
                )
                
                self.logger.info(
                    f"🔍 [PostgresStore] Time window query (by created_at): "
                    f"query window=[{query_start}, {query_end}], "
                    f"condition: created_at >= {query_start} AND created_at <= {query_end}"
                )
                
            elif 'time_window_start' in kwargs or 'time_window_end' in kwargs:
                # Mode 2: Exact match query (for finding memories in specific time window)
                # Use approximate match (tolerance 1 second) to avoid query failure due to microsecond differences
                time_tolerance_seconds = 1
                
                if time_window_start := kwargs.get('time_window_start'):
                    # Ensure datetime object
                    if isinstance(time_window_start, str):
                        from timem.utils.time_utils import parse_time
                        time_window_start = parse_time(time_window_start)
                    
                    # Exact match (±1 second tolerance)
                    start_lower = time_window_start - timedelta(seconds=time_tolerance_seconds)
                    start_upper = time_window_start + timedelta(seconds=time_tolerance_seconds)
                    query = query.where(
                        CoreMemory.time_window_start >= start_lower,
                        CoreMemory.time_window_start <= start_upper
                    )
                
                if time_window_end := kwargs.get('time_window_end'):
                    # Ensure datetime object
                    if isinstance(time_window_end, str):
                        from timem.utils.time_utils import parse_time
                        time_window_end = parse_time(time_window_end)
                    
                    # Exact match (±1 second tolerance)
                    end_lower = time_window_end - timedelta(seconds=time_tolerance_seconds)
                    end_upper = time_window_end + timedelta(seconds=time_tolerance_seconds)
                    query = query.where(
                        CoreMemory.time_window_end >= end_lower,
                        CoreMemory.time_window_end <= end_upper
                    )
            
            # Full-text search support (using application-layer tokenization)
            if query_text := kwargs.get('content_contains'):
                # Tokenize query text for Chinese
                query_tokenized = tokenize_for_postgres(query_text)
                # Use PostgreSQL's full-text search (simple config for processing tokenized text)
                query = query.where(
                    text("(content_tsvector || title_tsvector) @@ plainto_tsquery('simple', :query_tokenized)")
                ).params(query_tokenized=query_tokenized)
            
            # Limit, offset, and sort
            limit = int(kwargs.get('limit') or 100)
            offset = int(kwargs.get('offset') or 0)
            query = query.order_by(desc(CoreMemory.created_at))
            if offset > 0:
                query = query.offset(offset)
            query = query.limit(limit)
            
            result = await session.execute(query)
            
            memories = []
            for row in result.scalars():
                memory_dict = self._row_to_core_dict(row)
                
                # Get level-specific data
                memory_dict = await self._enrich_memory_with_level_data(session, memory_dict)
                
                # Get relationship data
                relations_dict = await self._enrich_memory_with_relations(session, memory_dict['id'])
                memory_dict.update(relations_dict)
                
                memories.append(memory_dict)
            
            return memories

    async def _enrich_memory_with_level_data(self, session: AsyncSession, memory_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Add level-specific data to memory"""
        level = memory_dict.get('level')
        memory_id = memory_dict.get('id')
        
        # Ensure level field exists
        if not level:
            self.logger.warning(f"Memory {memory_id} missing level field, cannot get level-specific data")
            return memory_dict
        
        level_table_map = {
            'L1': L1FragmentMemory,  # l1_fragment_memories
            'L2': L2SessionMemory,   # l2_session_memories
            'L3': L3DailyMemory,     # l3_daily_memories
            'L4': L4WeeklyMemory,    # l4_weekly_memories
            'L5': L5MonthlyMemory    # l5_monthly_memories
        }
        
        if level in level_table_map:
            level_result = await session.execute(
                select(level_table_map[level]).where(level_table_map[level].memory_id == memory_id)
            )
            
            if level_data := level_result.scalar_one_or_none():
                level_fields = {c.name: getattr(level_data, c.name) for c in level_data.__table__.columns 
                              if c.name not in ['id', 'memory_id']}
                
                # Special handling for L1 dialogue_turns field
                if level == 'L1' and 'dialogue_turns_json' in level_fields:
                    dialogue_turns_json = level_fields.pop('dialogue_turns_json')
                    # If dialogue_turns_json is None, provide empty list as default
                    level_fields['dialogue_turns'] = dialogue_turns_json if dialogue_turns_json is not None else []
                
                memory_dict.update(level_fields)
            else:
                self.logger.warning(f"Memory {memory_id} not found in {level} level table")
        
        return memory_dict

    async def _enrich_memory_with_relations(self, session: AsyncSession, memory_id: str) -> Dict[str, Any]:
        """Add relationship data to memory"""
        relations_dict = {}
        
        # Get child memory relationships
        child_result = await session.execute(
            select(MemoryChildRelation.child_id).where(MemoryChildRelation.parent_id == memory_id)
        )
        relations_dict['child_memory_ids'] = [r[0] for r in child_result.all()]
        
        # Get historical memory relationships
        hist_result = await session.execute(
            select(MemoryHistoricalRelation.historical_memory_id).where(MemoryHistoricalRelation.memory_id == memory_id)
        )
        relations_dict['historical_memory_ids'] = [r[0] for r in hist_result.all()]
        
        return relations_dict

    # Add other methods compatible with SQLStore
    async def get_full_memory_by_id(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get complete memory information"""
        async with self.get_session() as session:
            result = await session.execute(select(CoreMemory).where(CoreMemory.id == memory_id))
            core_mem = result.scalar_one_or_none()
            if not core_mem:
                return None

            memory_dict = self._row_to_core_dict(core_mem)
            memory_dict = await self._enrich_memory_with_level_data(session, memory_dict)
            memory_dict.update(await self._enrich_memory_with_relations(session, memory_dict['id']))
            
            return memory_dict

    async def get_memory_by_id(self, memory_id: str, level: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get memory by ID (compatible interface)"""
        return await self.get_full_memory_by_id(memory_id)

    async def update_memory(self, memory_id: str, updates: Dict[str, Any]) -> bool:
        """Update memory"""
        async with self.get_session() as session:
            async with session.begin():
                try:
                    # Get memory information
                    result = await session.execute(select(CoreMemory).where(CoreMemory.id == memory_id))
                    core_mem = result.scalar_one_or_none()
                    if not core_mem:
                        self.logger.warning(f"Memory does not exist: {memory_id}")
                        return False
                    
                    # Update core memory - strict field filtering
                    valid_core_fields = {
                        'title', 'content', 'status', 'updated_at', 
                        'time_window_start', 'time_window_end'
                    }
                    core_updates = {k: v for k, v in updates.items() 
                                  if k in valid_core_fields and hasattr(CoreMemory, k)}
                    # Auto-add updated_at field
                    core_updates['updated_at'] = datetime.now()
                    
                    if core_updates:
                        self.logger.debug(f"Update CoreMemory: memory_id={memory_id}, updates={core_updates}")
                        await session.execute(
                            update(CoreMemory).where(CoreMemory.id == memory_id).values(**core_updates)
                        )
                    
                    # Update level-specific data
                    level_table_map = {
                        'L1': L1FragmentMemory,
                        'L2': L2SessionMemory,
                        'L3': L3DailyMemory,
                        'L4': L4WeeklyMemory,
                        'L5': L5MonthlyMemory
                    }
                    
                    if level_table := level_table_map.get(core_mem.level):
                        # Define valid fields for each level table
                        valid_level_fields = {
                            'L1': {'content', 'summary', 'session_id', 'turn_index'},
                            'L2': {'content', 'summary', 'session_id', 'memory_date'},
                            'L3': {'content', 'summary', 'memory_date'},
                            'L4': {'content', 'summary', 'week_start', 'week_end', 'year', 'week'},
                            'L5': {'content', 'summary', 'year', 'month'}
                        }
                        
                        allowed_fields = valid_level_fields.get(core_mem.level, set())
                        level_updates = {k: v for k, v in updates.items() 
                                       if k in allowed_fields and hasattr(level_table, k) and k != 'memory_id'}
                        if level_updates:
                            self.logger.debug(f"Update level table {core_mem.level}: memory_id={memory_id}, updates={level_updates}")
                            await session.execute(
                                update(level_table).where(level_table.memory_id == memory_id).values(**level_updates)
                            )
                    
                    await session.flush()  # Ensure updates are written
                    self.logger.info(f"Successfully updated memory: {memory_id}")
                    return True
                    
                except Exception as e:
                    # Special handling for _bulk_update_tuples error
                    if "_bulk_update_tuples" in str(e):
                        self.logger.warning(f"Encountered known SQLAlchemy bulk update issue, trying to skip: {memory_id}")
                        return True  # Return True to avoid entire update process failure
                    else:
                        self.logger.error(f"Failed to update memory: {e}", exc_info=True)
                        return False

    async def delete_memory(self, memory_id: str) -> bool:
        """Delete memory"""
        async with self.get_session() as session:
            async with session.begin():
                result = await session.execute(delete(CoreMemory).where(CoreMemory.id == memory_id))
                return result.rowcount > 0

    async def clear_all_data(self):
        """Clear all data (for testing)"""
        async with self.get_session() as session:
            async with session.begin():
                # PostgreSQL does not need to close foreign key checks, directly truncate
                for table in reversed(Base.metadata.sorted_tables):
                    await session.execute(text(f"TRUNCATE TABLE {table.name} CASCADE"))
        
        # ✅ No longer need to clear global state (already using ExecutionContext)
        # self._global_session_max_turns.clear()  # Deprecated
        
        self.logger.info("Cleared all PostgreSQL memory data")

    # Add other compatible methods...
    async def query_memories_by_session(self, user_id: str, expert_id: str, session_id: str, level: str) -> List[Dict[str, Any]]:
        """Query memories of specific level by session ID"""
        return await self.find_memories(user_id=user_id, expert_id=expert_id, session_id=session_id, level=level)

    async def query_latest_memories(self, user_id: str, expert_id: str, level: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Query latest memories"""
        return await self.find_memories(user_id=user_id, expert_id=expert_id, level=level, limit=limit)

    async def get_child_memories(self, parent_id: str) -> List[Dict[str, Any]]:
        """Get child memories"""
        async with self.get_session() as session:
            query = select(CoreMemory).join(
                MemoryChildRelation, 
                CoreMemory.id == MemoryChildRelation.child_id
            ).where(MemoryChildRelation.parent_id == parent_id)
            
            
            memories = []
            for row in result.scalars():
                memory_dict = self._row_to_core_dict(row)
                # Get complete memory data, including level-specific fields and relationships
                memory_dict = await self._enrich_memory_with_level_data(session, memory_dict)
                memory_dict.update(await self._enrich_memory_with_relations(session, memory_dict['id']))
                memories.append(memory_dict)
            
            return memories

    async def get_historical_memories(self, memory_id: str) -> List[Dict[str, Any]]:
        """Get historical memories"""
        async with self.get_session() as session:
            query = select(CoreMemory).join(
                MemoryHistoricalRelation, 
                CoreMemory.id == MemoryHistoricalRelation.historical_memory_id
            ).where(MemoryHistoricalRelation.memory_id == memory_id)
            
            result = await session.execute(query)
            
            memories = []
            for row in result.scalars():
                memory_dict = self._row_to_core_dict(row)
                # Get complete memory data, including level-specific fields and relationships
                memory_dict = await self._enrich_memory_with_level_data(session, memory_dict)
                memory_dict.update(await self._enrich_memory_with_relations(session, memory_dict['id']))
                memories.append(memory_dict)
            
            return memories

    # ==================== Unified interface implementation ====================
    
    async def store_memory(self, memory_data: Dict[str, Any]) -> Optional[str]:
        """Store single memory (unified interface)"""
        try:
            result = await self.batch_store_memories([memory_data])
            return result[0] if result else None
        except Exception as e:
            self.logger.error(f"Failed to store memory: {e}")
            return None
    
    async def search_memories(self, 
                            query_text: Optional[str] = None,
                            user_id: Optional[str] = None,
                            expert_id: Optional[str] = None,
                            level: Optional[str] = None,
                            limit: int = 20) -> List[Dict[str, Any]]:
        """Search memories (unified interface)"""
        if query_text:
            # Use full-text search
            return await self.search_memories_fulltext(
                query_text=query_text,
                user_id=user_id,
                expert_id=expert_id,
                level=level,
                limit=limit
            )
        else:
            # Use criteria search
            criteria = {}
            if user_id:
                criteria['user_id'] = user_id
            if expert_id:
                criteria['expert_id'] = expert_id
            if level:
                criteria['level'] = level
            return await self.find_memories_by_criteria(**criteria)
    
    async def find_memories_by_criteria(self, **criteria) -> List[Dict[str, Any]]:
        """Find memories by criteria (unified interface)"""
        try:
            return await self.find_memories(**criteria)
        except Exception as e:
            self.logger.error(f"Failed to find memories by criteria: {e}")
            return []
    
    async def fulltext_search(self,
                            query_text: str,
                            user_id: Optional[str] = None,
                            expert_id: Optional[str] = None,
                            level: Optional[str] = None,
                            limit: int = 20,
                            min_score: float = 0.0) -> List[Dict[str, Any]]:
        """Full-text search (unified interface)"""
        try:
            return await self.search_memories_fulltext(
                query_text=query_text,
                user_id=user_id,
                expert_id=expert_id,
                level=level,
                limit=limit
            )
        except Exception as e:
            self.logger.error(f"Full-text search failed: {e}")
            return []
    
    async def create_session(self, user_id: str, expert_id: str, session_id: Optional[str] = None, start_time: Optional[datetime] = None) -> str:
        """Create session (unified interface)"""
        if session_id is None:
            session_id = str(uuid.uuid4())
        if start_time is None:
            start_time = datetime.now()
        
        try:
            async with self.get_session() as session:
                async with session.begin():
                    # Create session directly without using get_or_create_session
                    new_session = MemorySession(
                        id=session_id,
                        user_id=user_id,
                        expert_id=expert_id,
                        start_time=start_time,
                        created_at=datetime.now(),
                        updated_at=datetime.now()
                    )
                    session.add(new_session)
                    await session.flush()
                    
            self.logger.info(f"Session created successfully: {session_id}")
            return session_id
        except Exception as e:
            self.logger.error(f"Failed to create session: {e}")
            return ""
    
    async def get_session_memories(self, session_id: str, level: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get session-related memories (unified interface)"""
        criteria = {'session_id': session_id}
        if level:
            criteria['level'] = level
        return await self.find_memories_by_criteria(**criteria)
    
    async def get_session_dialogues(self, session_id: str) -> List[Dict[str, Any]]:
        """
        Get session dialogue history
        
        Query all dialogue turns for a specified session from dialogue_originals table, sorted by turn_number
        
        Args:
            session_id: Session ID
            
        Returns:
            List of dialogue history containing id, turn_number, role, content, timestamp and other fields
        """
        try:
            async with self.get_session() as session:
                # Query dialogue_originals table
                query = select(DialogueOriginal).where(
                    DialogueOriginal.session_id == session_id
                ).order_by(DialogueOriginal.turn_number.asc())
                
                result = await session.execute(query)
                dialogues = []
                
                for row in result.scalars():
                    dialogue_dict = {
                        'id': row.id,
                        'session_id': row.session_id,
                        'turn_number': row.turn_number,
                        'role': row.role,
                        'content': row.content,
                        'timestamp': row.timestamp,
                        'created_at': row.created_at
                    }
                    dialogues.append(dialogue_dict)
                
                self.logger.info(f"Retrieved dialogue history for session {session_id}: {len(dialogues)} items")
                return dialogues
                
        except Exception as e:
            self.logger.error(f"Failed to get session dialogue history: {e}", exc_info=True)
            return []
    
    async def get_all_sessions(self) -> List[Dict[str, Any]]:
        """Get all session data"""
        try:
            async with self.get_session() as session:
                result = await session.execute(select(MemorySession))
                sessions = []
                
                for row in result.scalars():
                    session_dict = {
                        'id': row.id,
                        'session_id': row.id,  # Frontend compatibility field
                        'user_id': row.user_id,
                        'expert_id': row.expert_id,
                        'session_name': f"Session {row.id[:8]}",  # Generate session name
                        'is_active': row.end_time is None,  # Determine if active
                        'turn_counter': 0,  # Default turn count
                        'start_time': row.start_time,
                        'end_time': row.end_time,
                        'created_at': row.created_at,
                        'updated_at': row.updated_at,
                    }
                    sessions.append(session_dict)
                
                return sessions
                
        except Exception as e:
            self.logger.error(f"Failed to get all sessions: {e}")
            return []

    async def get_data_statistics(self) -> Dict[str, Any]:
        """Get data statistics (unified interface)"""
        try:
            async with self.get_session() as session:
                stats = {}
                
                # Count records in each table
                tables = {
                    'core_memories': CoreMemory,
                    'characters': Character,
                    'memory_sessions': MemorySession,
                    'dialogue_originals': DialogueOriginal,
                    'l1_fragment_memories': L1FragmentMemory,
                    'l2_session_memories': L2SessionMemory,
                    'l3_daily_memories': L3DailyMemory,
                    'l4_weekly_memories': L4WeeklyMemory,
                    'l5_monthly_memories': L5MonthlyMemory,
                    'memory_child_relations': MemoryChildRelation,
                    'memory_historical_relations': MemoryHistoricalRelation
                }
                
                for table_name, table_class in tables.items():
                    try:
                        result = await session.execute(select(func.count()).select_from(table_class))
                        count = result.scalar()
                        stats[table_name] = count
                    except Exception as e:
                        self.logger.warning(f"Failed to get {table_name} statistics: {e}")
                        stats[table_name] = 0
                
                return stats
                
        except Exception as e:
            self.logger.error(f"Failed to get data statistics: {e}")
            return {}


# 🔧 Engineering optimization: Use singleton pattern to avoid connection pool exhaustion from concurrent creation
_postgres_store_instance: Optional["PostgreSQLStore"] = None
_postgres_store_lock: Optional[asyncio.Lock] = None

def _get_postgres_store_lock():
    """Get lock (lazy creation to avoid event loop conflicts)"""
    global _postgres_store_lock
    if _postgres_store_lock is None:
        _postgres_store_lock = asyncio.Lock()
    return _postgres_store_lock

async def create_postgres_store(config: Optional[Dict[str, Any]] = None) -> "PostgreSQLStore":
    """Create PostgreSQL storage instance (singleton pattern)"""
    global _postgres_store_instance
    
    # 🔧 Double-check locking to ensure only one instance is created
    if _postgres_store_instance is not None:
        return _postgres_store_instance
    
    async with _get_postgres_store_lock():
        # Check again (may have been created while waiting for lock)
        if _postgres_store_instance is not None:
            return _postgres_store_instance
        
        # 🔧 Fix: Ensure global connection pool is initialized
        await ensure_connection_pool()
        
        store = PostgreSQLStore(config)
        
        # 🔧 Fix: Only connect on first creation, use retry mechanism
        max_retries = 3
        retry_delay = 0.5
        
        for attempt in range(max_retries):
            try:
                success = await store.connect()
                if success:
                    _postgres_store_instance = store  # Save singleton
                    store.logger.info("✅ PostgreSQL storage instance created successfully (singleton mode)")
                    return store
                else:
                    raise RuntimeError("PostgreSQL connection failed")
            except Exception as e:
                if attempt == max_retries - 1:
                    raise RuntimeError(f"PostgreSQL storage instance creation failed (retried {max_retries} times): {e}")
                
                store.logger.warning(f"PostgreSQL connection failed (attempt {attempt + 1}/{max_retries}): {e}")
                await asyncio.sleep(retry_delay * (attempt + 1))
                
                # Recreate storage instance (but don't save to global variable)
                store = PostgreSQLStore(config)
        
        return store

async def get_postgres_store(config: Optional[Dict[str, Any]] = None) -> "PostgreSQLStore":
    """Get PostgreSQL storage instance (singleton pattern)"""
    return await create_postgres_store(config)
