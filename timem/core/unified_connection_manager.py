"""
Unified Connection Manager - Fundamental architectural solution

Core component for solving connection pool exhaustion in parallel testing:
1. Global single connection pool: Only one PostgreSQL connection pool for the entire application
2. Intelligent connection allocation: Dynamically allocate connections based on concurrency needs
3. Connection lifecycle management: Automatically clean up and recycle connections
4. Concurrency safety: Thread-safe connection pool operations
5. Monitoring and diagnostics: Real-time connection pool status monitoring

Design Principles:
- Single responsibility: Only responsible for connection pool management
- High cohesion: All connection-related logic centralized
- Low coupling: Decoupled from business logic
- Extensibility: Support multiple storage types
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, Union
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
import threading
import uuid

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncEngine, AsyncSession
from sqlalchemy import text
from sqlalchemy.pool import QueuePool

from timem.utils.logging import get_logger
from timem.utils.config_manager import get_config

logger = get_logger(__name__)


class ConnectionPoolType(Enum):
    """Connection pool type enumeration"""
    POSTGRES = "postgres"
    MYSQL = "mysql"
    VECTOR = "vector"
    CACHE = "cache"


@dataclass
class ConnectionPoolConfig:
    """Connection pool configuration"""
    pool_type: ConnectionPoolType
    host: str
    port: int
    user: str
    password: str
    database: str
    pool_size: int = 50  # 🔧 Engineering optimization: 25 -> 50 (supports 20 concurrent tests)
    max_overflow: int = 50  # 🔧 Engineering optimization: 35 -> 50 (total 100 connections)
    pool_timeout: int = 60  # Optimization: 30 -> 60 seconds
    pool_recycle: int = 1800  # Optimization: 3600 -> 1800 seconds (30 minutes)
    pool_pre_ping: bool = True
    echo: bool = False
    
    def get_connection_url(self) -> str:
        """Get connection URL"""
        if self.pool_type == ConnectionPoolType.POSTGRES:
            return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"
        elif self.pool_type == ConnectionPoolType.MYSQL:
            return f"mysql+aiomysql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}?charset=utf8mb4"
        else:
            raise ValueError(f"Unsupported connection pool type: {self.pool_type}")


@dataclass
class ConnectionPoolStats:
    """Connection pool statistics"""
    pool_type: str
    pool_size: int
    checked_in: int
    checked_out: int
    overflow: int
    total_connections: int
    utilization_percent: float
    status: str
    last_updated: float
    error_count: int = 0
    warning_count: int = 0


class UnifiedConnectionManager:
    """
    Unified Connection Manager
    
    Core Features:
    1. Global single connection pool management
    2. Intelligent connection allocation and recovery
    3. Connection pool health monitoring
    4. Concurrency-safe operations
    5. Automatic failure recovery
    """
    
    _instance: Optional['UnifiedConnectionManager'] = None
    _lock = asyncio.Lock()
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(UnifiedConnectionManager, cls).__new__(cls)
            cls._instance._initialize_instance()
        return cls._instance
    
    def _initialize_instance(self):
        """Initialize instance variables"""
        self._engines: Dict[ConnectionPoolType, AsyncEngine] = {}
        self._session_factories: Dict[ConnectionPoolType, async_sessionmaker] = {}
        self._configs: Dict[ConnectionPoolType, ConnectionPoolConfig] = {}
        self._stats: Dict[ConnectionPoolType, ConnectionPoolStats] = {}
        self._monitoring_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        self._logger = get_logger(__name__)
        self._thread_local = threading.local()
        
    async def initialize(self, configs: Optional[Dict[ConnectionPoolType, ConnectionPoolConfig]] = None) -> bool:
        """
        Initialize connection pool manager
        
        Args:
            configs: Connection pool configuration dictionary, loads from config file if None
            
        Returns:
            bool: Whether initialization was successful
        """
        async with self._lock:
            if self._initialized:
                return True
                
            try:
                if configs is None:
                    configs = await self._load_configs_from_file()
                
                self._logger.info("Starting unified connection pool manager initialization...")
                
                # Initialize various types of connection pools
                for pool_type, config in configs.items():
                    await self._initialize_pool(pool_type, config)
                
                # 🔧 Emergency fix: Verify connection pool availability
                await self._verify_pool_health()
                
                # Start monitoring task
                await self._start_monitoring()
                
                self._initialized = True
                self._logger.info("✅ Unified connection pool manager initialized successfully")
                
                # Print initialization statistics
                await self._print_initialization_stats()
                
                return True
                
            except Exception as e:
                self._logger.error(f"Unified connection pool manager initialization failed: {e}")
                await self._cleanup_all_pools()
                return False
    
    async def _load_configs_from_file(self) -> Dict[ConnectionPoolType, ConnectionPoolConfig]:
        """Load connection pool configuration from config file"""
        config = get_config()
        storage_config = config.get("storage", {})
        
        configs = {}
        
        # PostgreSQL configuration
        postgres_config = storage_config.get("sql", {}).get("postgres", {})
        if postgres_config:
            # Same precedence as storage/connection_pool_manager.py: env beats the YAML.
            from storage.postgres_env import apply_postgres_env

            postgres_config = apply_postgres_env(dict(postgres_config))
            configs[ConnectionPoolType.POSTGRES] = ConnectionPoolConfig(
                pool_type=ConnectionPoolType.POSTGRES,
                host=postgres_config.get("host", "localhost"),
                port=postgres_config.get("port", 5432),
                user=postgres_config.get("user", "timem_user"),
                password=postgres_config.get("password", "timem_password"),
                database=postgres_config.get("database", "timem_db"),
                pool_size=postgres_config.get("pool_size", 50),  # 🔧 Engineering optimization: 25 -> 50
                max_overflow=postgres_config.get("max_overflow", 50),  # 🔧 Engineering optimization: 35 -> 50
                pool_timeout=postgres_config.get("pool_timeout", 60),  # Optimization: 30 -> 60 seconds
                pool_recycle=postgres_config.get("pool_recycle", 1800),  # Optimization: 3600 -> 1800 seconds (30 minutes)
                pool_pre_ping=postgres_config.get("pool_pre_ping", True),
                echo=postgres_config.get("echo", False)
            )
        
        return configs
    
    async def _initialize_pool(self, pool_type: ConnectionPoolType, config: ConnectionPoolConfig):
        """Initialize single connection pool"""
        try:
            self._logger.info(f"Initializing {pool_type.value} connection pool...")
            
            # Create engine
            engine = create_async_engine(
                config.get_connection_url(),
                pool_size=config.pool_size,
                max_overflow=config.max_overflow,
                pool_timeout=config.pool_timeout,
                pool_recycle=config.pool_recycle,
                pool_pre_ping=config.pool_pre_ping,
                echo=config.echo
                # 🔧 Fix: Remove incompatible connection pool configuration parameters
            )
            
            # Create session factory
            session_factory = async_sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=False
            )
            
            # Test connection
            await self._test_connection(engine, pool_type)
            
            # Save configuration and instances
            self._engines[pool_type] = engine
            self._session_factories[pool_type] = session_factory
            self._configs[pool_type] = config
            
            # Initialize statistics
            self._stats[pool_type] = ConnectionPoolStats(
                pool_type=pool_type.value,
                pool_size=config.pool_size,
                checked_in=config.pool_size,
                checked_out=0,
                overflow=0,
                total_connections=config.pool_size,
                utilization_percent=0.0,
                status="healthy",
                last_updated=time.time()
            )
            
            self._logger.info(f"✅ {pool_type.value} connection pool initialized successfully (pool size: {config.pool_size}, overflow: {config.max_overflow})")
            
        except Exception as e:
            self._logger.error(f"❌ {pool_type.value} connection pool initialization failed: {e}")
            raise
    
    async def _test_connection(self, engine: AsyncEngine, pool_type: ConnectionPoolType):
        """Test connection pool connection"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                self._logger.info(f"{pool_type.value} connection test successful")
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                self._logger.warning(f"{pool_type.value} connection test failed (attempt {attempt + 1}/{max_retries}): {e}")
                await asyncio.sleep(1.0 * (attempt + 1))
    
    async def _verify_pool_health(self):
        """🔧 Emergency fix: Verify connection pool health status"""
        try:
            self._logger.info("Starting connection pool health verification...")
            
            # Verify PostgreSQL connection pool
            if ConnectionPoolType.POSTGRES in self._engines:
                engine = self._engines[ConnectionPoolType.POSTGRES]
                session_factory = self._session_factories[ConnectionPoolType.POSTGRES]
                
                # Test connection pool availability
                async with session_factory() as session:
                    result = await session.execute(text("SELECT 1 as test"))
                    test_value = result.scalar()
                    assert test_value == 1, "Connection pool test query returned abnormally"
                
                # Test connection pool statistics
                pool = engine.pool
                try:
                    if hasattr(pool, 'size'):
                        self._logger.info(f"PostgreSQL connection pool size: {pool.size()}")
                    if hasattr(pool, 'checkedin'):
                        self._logger.info(f"PostgreSQL returned connections: {pool.checkedin()}")
                    if hasattr(pool, 'checkedout'):
                        self._logger.info(f"PostgreSQL checked out connections: {pool.checkedout()}")
                except Exception as pool_error:
                    self._logger.debug(f"Connection pool statistics method call failed: {pool_error}")
                    self._logger.info("PostgreSQL connection pool type does not support statistics display")
                
                self._logger.info("✅ PostgreSQL connection pool health verification passed")
            
            self._logger.info("✅ All connection pool health verification completed")
            
        except Exception as e:
            self._logger.error(f"❌ Connection pool health verification failed: {e}")
            raise
    
    async def _start_monitoring(self):
        """Start connection pool monitoring task"""
        if self._monitoring_task is None or self._monitoring_task.done():
            self._monitoring_task = asyncio.create_task(self._monitor_connections())
            self._logger.info("Connection pool monitoring task started")
    
    async def _monitor_connections(self):
        """Monitor connection pool status"""
        while not self._shutdown_event.is_set():
            try:
                await self._update_all_stats()
                await asyncio.sleep(30)  # Monitor every 30 seconds
            except Exception as e:
                self._logger.error(f"Connection pool monitoring exception: {e}")
                await asyncio.sleep(60)  # Wait longer on error
    
    async def _update_all_stats(self):
        """Update all connection pool statistics"""
        for pool_type in self._engines.keys():
            await self._update_pool_stats(pool_type)
    
    async def _update_pool_stats(self, pool_type: ConnectionPoolType):
        """Update single connection pool statistics"""
        try:
            engine = self._engines.get(pool_type)
            if not engine:
                return
            
            pool = engine.pool
            
            # Check connection pool type to avoid NullPool errors
            if hasattr(pool, 'size') and hasattr(pool, 'checkedin') and hasattr(pool, 'checkedout'):
                try:
                    pool_size = pool.size()
                    checked_in = pool.checkedin()
                    checked_out = pool.checkedout()
                    max_overflow = getattr(pool, 'max_overflow', 0)
                    overflow = max(0, checked_out - pool_size)
                    total_connections = pool_size + overflow
                    utilization_percent = (checked_out / (pool_size + max_overflow)) * 100 if (pool_size + max_overflow) > 0 else 0
                    
                    # Determine status
                    if utilization_percent >= 90:
                        status = "critical"
                    elif utilization_percent >= 75:
                        status = "warning"
                    else:
                        status = "healthy"
                    
                    # Update statistics
                    self._stats[pool_type] = ConnectionPoolStats(
                        pool_type=pool_type.value,
                        pool_size=pool_size,
                        checked_in=checked_in,
                        checked_out=checked_out,
                        overflow=overflow,
                        total_connections=total_connections,
                        utilization_percent=utilization_percent,
                        status=status,
                        last_updated=time.time(),
                        error_count=self._stats.get(pool_type, ConnectionPoolStats(
                            pool_type=pool_type.value, pool_size=0, checked_in=0, checked_out=0,
                            overflow=0, total_connections=0, utilization_percent=0.0,
                            status="unknown", last_updated=0
                        )).error_count,
                        warning_count=self._stats.get(pool_type, ConnectionPoolStats(
                            pool_type=pool_type.value, pool_size=0, checked_in=0, checked_out=0,
                            overflow=0, total_connections=0, utilization_percent=0.0,
                            status="unknown", last_updated=0
                        )).warning_count
                    )
                    
                    # Log status (using INFO level for easy viewing)
                    self._logger.info(
                        f"📊 {pool_type.value} connection pool: total={pool_size}, checked_out={checked_out}, "
                        f"checked_in={checked_in}, overflow={overflow}, utilization={utilization_percent:.1f}%"
                    )
                    
                    # Log warnings or errors
                    if status == "critical":
                        self._logger.error(f"⚠️ {pool_type.value} connection pool critically overloaded: utilization {utilization_percent:.1f}%")
                        self._stats[pool_type].error_count += 1
                    elif status == "warning":
                        self._logger.warning(f"⚠️ {pool_type.value} connection pool approaching overload: utilization {utilization_percent:.1f}%")
                        self._stats[pool_type].warning_count += 1
                        
                except Exception as pool_error:
                    self._logger.debug(f"Connection pool statistics method call failed: {pool_error}")
            else:
                # If connection pool doesn't support statistics, try using internal _pool object
                if hasattr(pool, '_pool'):
                    inner_pool = pool._pool
                    if hasattr(inner_pool, 'size') and hasattr(inner_pool, 'checkedin') and hasattr(inner_pool, 'checkedout'):
                        try:
                            pool_size = inner_pool.size()
                            checked_in = inner_pool.checkedin()
                            checked_out = inner_pool.checkedout()
                            max_overflow = getattr(inner_pool, 'max_overflow', getattr(inner_pool, '_max_overflow', 0))
                            overflow = max(0, checked_out - pool_size)
                            total_connections = pool_size + max_overflow
                            utilization_percent = (checked_out / (pool_size + max_overflow)) * 100 if (pool_size + max_overflow) > 0 else 0
                            
                            # Determine status
                            if utilization_percent >= 90:
                                status = "critical"
                            elif utilization_percent >= 75:
                                status = "warning"
                            else:
                                status = "healthy"
                            
                            # Update statistics
                            self._stats[pool_type] = ConnectionPoolStats(
                                pool_type=pool_type.value,
                                pool_size=pool_size,
                                checked_in=checked_in,
                                checked_out=checked_out,
                                overflow=overflow,
                                total_connections=total_connections,
                                utilization_percent=utilization_percent,
                                status=status,
                                last_updated=time.time(),
                                error_count=self._stats.get(pool_type, ConnectionPoolStats(
                                    pool_type=pool_type.value, pool_size=0, checked_in=0, checked_out=0,
                                    overflow=0, total_connections=0, utilization_percent=0.0,
                                    status="unknown", last_updated=0
                                )).error_count,
                                warning_count=self._stats.get(pool_type, ConnectionPoolStats(
                                    pool_type=pool_type.value, pool_size=0, checked_in=0, checked_out=0,
                                    overflow=0, total_connections=0, utilization_percent=0.0,
                                    status="unknown", last_updated=0
                                )).warning_count
                            )
                            
                            # Log status (using INFO level for easy viewing)
                            self._logger.info(
                                f"📊 {pool_type.value} connection pool: total={pool_size}, checked_out={checked_out}, "
                                f"checked_in={checked_in}, overflow={overflow}, utilization={utilization_percent:.1f}%"
                            )
                            
                            # Log warnings or errors
                            if status == "critical":
                                self._logger.error(f"⚠️ {pool_type.value} connection pool critically overloaded: utilization {utilization_percent:.1f}%")
                                self._stats[pool_type].error_count += 1
                            elif status == "warning":
                                self._logger.warning(f"⚠️ {pool_type.value} connection pool approaching overload: utilization {utilization_percent:.1f}%")
                                self._stats[pool_type].warning_count += 1
                            
                            return
                        except Exception as inner_error:
                            self._logger.debug(f"Internal connection pool statistics failed: {inner_error}")
                
                # Set default statistics
                self._logger.debug(f"{pool_type.value} connection pool type does not support statistics collection")
                self._stats[pool_type] = ConnectionPoolStats(
                    pool_type=pool_type.value,
                    pool_size=0,
                    checked_in=0,
                    checked_out=0,
                    overflow=0,
                    total_connections=0,
                    utilization_percent=0.0,
                    status="unknown",
                    last_updated=time.time(),
                    error_count=0,
                    warning_count=0
                )
                    
        except Exception as e:
            self._logger.error(f"Failed to update {pool_type.value} connection pool statistics: {e}")
    
    async def _print_initialization_stats(self):
        """Print initialization statistics"""
        self._logger.info("=" * 60)
        self._logger.info("Unified connection pool manager initialization completed")
        self._logger.info("=" * 60)
        
        for pool_type, stats in self._stats.items():
            self._logger.info(f"{pool_type.value.upper()} CONNECTION POOL:")
            self._logger.info(f"  - Pool size: {stats.pool_size}")
            self._logger.info(f"  - Max overflow: {self._configs[pool_type].max_overflow}")
            self._logger.info(f"  - Max connections: {stats.pool_size + self._configs[pool_type].max_overflow}")
            self._logger.info(f"  - Status: {stats.status}")
        
        self._logger.info("=" * 60)
    
    @asynccontextmanager
    async def get_session(self, pool_type: ConnectionPoolType = ConnectionPoolType.POSTGRES):
        """
        Get context manager for database session
        
        Args:
            pool_type: Connection pool type
            
        Yields:
            AsyncSession: Database session
        """
        if not self._initialized:
            raise RuntimeError("Connection pool manager not initialized")
        
        session_factory = self._session_factories.get(pool_type)
        if not session_factory:
            raise ValueError(f"Connection pool for {pool_type.value} not found")
        
        session = None
        try:
            session = session_factory()
            yield session
        except Exception as e:
            if session:
                await session.rollback()
            self._logger.error(f"Database session exception: {e}")
            raise
        finally:
            if session:
                await session.close()
    
    async def get_engine(self, pool_type: ConnectionPoolType = ConnectionPoolType.POSTGRES) -> Optional[AsyncEngine]:
        """Get database engine"""
        return self._engines.get(pool_type)
    
    async def get_session_factory(self, pool_type: ConnectionPoolType = ConnectionPoolType.POSTGRES) -> Optional[async_sessionmaker]:
        """Get session factory"""
        return self._session_factories.get(pool_type)
    
    async def get_stats(self, pool_type: Optional[ConnectionPoolType] = None) -> Union[ConnectionPoolStats, Dict[ConnectionPoolType, ConnectionPoolStats]]:
        """Get connection pool statistics"""
        if pool_type:
            return self._stats.get(pool_type)
        return self._stats.copy()
    
    async def is_healthy(self, pool_type: ConnectionPoolType = ConnectionPoolType.POSTGRES) -> bool:
        """Check if connection pool is healthy"""
        stats = self._stats.get(pool_type)
        if not stats:
            return False
        return stats.status in ["healthy", "warning"]
    
    async def cleanup_all_pools(self):
        """
        Clean up all connection pools
        
        Ensure complete cleanup of all resources, including monitoring tasks and database connections
        """
        self._logger.info("🧹 Starting cleanup of all connection pools...")
        
        try:
            # 1. Stop monitoring task
            if self._monitoring_task and not self._monitoring_task.done():
                self._logger.info("📋 Stopping connection pool monitoring task...")
                self._shutdown_event.set()
                self._monitoring_task.cancel()
                
                try:
                    await asyncio.wait_for(self._monitoring_task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                
                self._logger.info("✅ Monitoring task stopped")
            
            # 2. Clean up all engines
            if self._engines:
                self._logger.info(f"📋 Cleaning up {len(self._engines)} database engines...")
                for pool_type, engine in list(self._engines.items()):
                    try:
                        await engine.dispose()
                        self._logger.info(f"✅ {pool_type.value} connection pool cleaned up")
                    except Exception as e:
                        self._logger.error(f"❌ {pool_type.value} connection pool cleanup failed: {e}")
            
            # 3. Reset state
            self._engines.clear()
            self._session_factories.clear()
            self._configs.clear()
            self._stats.clear()
            self._initialized = False
            self._shutdown_event.clear()
            
            self._logger.info("✅ All connection pools cleaned up")
            
        except Exception as e:
            self._logger.error(f"❌ Exception occurred while cleaning up connection pools: {e}", exc_info=True)
    
    async def _cleanup_all_pools(self):
        """Internal cleanup method (backward compatible)"""
        await self.cleanup_all_pools()
    
    async def force_cleanup_pool(self, pool_type: ConnectionPoolType):
        """Force cleanup specified connection pool"""
        engine = self._engines.get(pool_type)
        if engine:
            try:
                await engine.dispose()
                self._logger.warning(f"Force cleanup {pool_type.value} connection pool")
            except Exception as e:
                self._logger.error(f"Force cleanup {pool_type.value} connection pool failed: {e}")
    
    def get_connection_id(self) -> str:
        """Get connection ID for current thread (for debugging)"""
        if not hasattr(self._thread_local, 'connection_id'):
            self._thread_local.connection_id = str(uuid.uuid4())[:8]
        return self._thread_local.connection_id


# Global instance management
_unified_connection_manager: Optional[UnifiedConnectionManager] = None
_manager_lock = asyncio.Lock()


async def get_unified_connection_manager() -> UnifiedConnectionManager:
    """Get unified connection pool manager singleton"""
    global _unified_connection_manager
    
    if _unified_connection_manager is None:
        async with _manager_lock:
            if _unified_connection_manager is None:
                _unified_connection_manager = UnifiedConnectionManager()
                await _unified_connection_manager.initialize()
    
    return _unified_connection_manager


async def cleanup_unified_connection_manager():
    """Clean up unified connection pool manager"""
    global _unified_connection_manager
    
    if _unified_connection_manager:
        async with _manager_lock:
            if _unified_connection_manager:
                await _unified_connection_manager.cleanup_all_pools()
                _unified_connection_manager = None


# Convenience functions
async def get_postgres_session():
    """Convenience function to get PostgreSQL session"""
    manager = await get_unified_connection_manager()
    return manager.get_session(ConnectionPoolType.POSTGRES)


async def get_postgres_engine():
    """Convenience function to get PostgreSQL engine"""
    manager = await get_unified_connection_manager()
    return await manager.get_engine(ConnectionPoolType.POSTGRES)


async def get_postgres_session_factory():
    """Convenience function to get PostgreSQL session factory"""
    manager = await get_unified_connection_manager()
    return await manager.get_session_factory(ConnectionPoolType.POSTGRES)


async def get_connection_stats():
    """Convenience function to get connection pool statistics"""
    manager = await get_unified_connection_manager()
    return await manager.get_stats()


async def is_connection_healthy():
    """Convenience function to check if connection pool is healthy"""
    manager = await get_unified_connection_manager()
    return await manager.is_healthy(ConnectionPoolType.POSTGRES)
