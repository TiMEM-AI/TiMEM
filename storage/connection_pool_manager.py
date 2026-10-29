"""
TiMem Connection Pool Manager
Unified management of all database connections, solving connection pool leak issues
"""

import asyncio
import time
import threading
from typing import Dict, Any, Optional, List
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine, AsyncSession
from sqlalchemy.pool import QueuePool
from sqlalchemy import event
from sqlalchemy.exc import DisconnectionError, OperationalError

from timem.utils.logging import get_logger
from timem.utils.config_manager import get_storage_config

logger = get_logger(__name__)


class ConnectionPoolManager:
    """
    Global connection pool manager
    
    Solves connection pool leak issues caused by multiple PostgreSQLStore instances:
    1. Singleton pattern ensures only one global connection pool
    2. Intelligent connection allocation and recovery
    3. Connection pool health monitoring and automatic recovery
    4. Forced connection release in exceptional cases
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
            
        self._initialized = True
        self._engine: Optional[AsyncEngine] = None
        self._session_factory = None
        self._config = None
        self._monitoring_task = None
        self._is_monitoring = False
        self._connection_stats = {
            'total_created': 0,
            'total_closed': 0,
            'current_active': 0,
            'leaked_connections': 0,
            'last_cleanup': 0
        }
        self._cleanup_lock = asyncio.Lock()
        
        logger.info("Connection pool manager initialization complete")
    
    async def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize connection pool"""
        if self._engine is not None:
            return True
            
        try:
            if config is None:
                storage_config = get_storage_config()
                config = storage_config.get('sql', {}).get('postgres', {})
            
            self._config = config
            from storage.postgres_env import apply_postgres_env

            apply_postgres_env(config)
            
            # Build connection URL
            db_url = f"postgresql+asyncpg://{config['user']}:{config['password']}@{config['host']}:{config['port']}/{config['database']}"
            
            # Optimize connection pool configuration
            pool_size = config.get('pool_size', 20)
            max_overflow = config.get('max_overflow', 30)
            pool_timeout = config.get('pool_timeout', 30)
            pool_recycle = config.get('pool_recycle', 300)
            
            logger.info(f"Initialize PostgreSQL connection pool: pool_size={pool_size}, max_overflow={max_overflow}")
            
            # Create engine
            self._engine = create_async_engine(
                db_url,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_timeout=pool_timeout,
                pool_recycle=pool_recycle,
                pool_pre_ping=True,
                echo=False,
                connect_args={
                    "command_timeout": 60,
                    "server_settings": {
                        "application_name": f"TiMem_ConnectionPool_{int(time.time())}",
                        "jit": "off"  # Disable JIT to improve connection stability
                    }
                }
            )
            
            # Set connection event listeners
            self._setup_connection_listeners()
            
            # Create session factory
            from sqlalchemy.ext.asyncio import async_sessionmaker
            self._session_factory = async_sessionmaker(
                bind=self._engine,
                expire_on_commit=False,
                class_=AsyncSession
            )
            
            # Start monitoring task
            await self._start_monitoring()
            
            logger.info("✅ Connection pool manager initialization successful")
            return True
            
        except Exception as e:
            logger.error(f"Connection pool manager initialization failed: {e}")
            return False
    
    def _setup_connection_listeners(self):
        """Set connection event listeners"""
        @event.listens_for(self._engine.sync_engine, "connect")
        def on_connect(dbapi_connection, connection_record):
            self._connection_stats['total_created'] += 1
            logger.debug(f"Connection created: total={self._connection_stats['total_created']}")
        
        @event.listens_for(self._engine.sync_engine, "checkout")
        def on_checkout(dbapi_connection, connection_record, connection_proxy):
            self._connection_stats['current_active'] += 1
            logger.debug(f"Connection checked out: active={self._connection_stats['current_active']}")
        
        @event.listens_for(self._engine.sync_engine, "checkin")
        def on_checkin(dbapi_connection, connection_record):
            self._connection_stats['current_active'] = max(0, self._connection_stats['current_active'] - 1)
            logger.debug(f"Connection checked in: active={self._connection_stats['current_active']}")
        
        @event.listens_for(self._engine.sync_engine, "close")
        def on_close(dbapi_connection, connection_record):
            self._connection_stats['total_closed'] += 1
            logger.debug(f"Connection closed: total={self._connection_stats['total_closed']}")
    
    async def _start_monitoring(self):
        """Start connection pool monitoring"""
        if self._is_monitoring:
            return
            
        self._is_monitoring = True
        self._monitoring_task = asyncio.create_task(self._monitor_connections())
        logger.info("Connection pool monitoring started")
    
    async def _monitor_connections(self):
        """Monitor connection pool status"""
        while self._is_monitoring:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                
                if self._engine is None:
                    continue
                
                # Get connection pool statistics
                stats = self.get_connection_stats()
                
                # Extract key metrics
                pool_size = stats.get('pool_size', 0)
                checked_in = stats.get('checked_in', 0)
                checked_out = stats.get('checked_out', 0)
                overflow = stats.get('overflow', 0)
                utilization = stats.get('utilization_percent', 0.0)
                
                # Log status (INFO level for easy viewing)
                logger.info(
                    f"📊 Connection pool status: total={pool_size}, checked_out={checked_out}, "
                    f"checked_in={checked_in}, overflow={overflow}, utilization={utilization:.1f}%"
                )
                
                # Check for connection leaks
                if utilization > 90:
                    logger.warning(
                        f"⚠️ Connection pool utilization too high: {utilization:.1f}%, possible connection leak "
                        f"(checked_out={checked_out}/{pool_size+overflow})"
                    )
                    await self._force_cleanup_leaked_connections()
                elif utilization > 75:
                    logger.warning(
                        f"⚠️ Connection pool utilization high: {utilization:.1f}% "
                        f"(checked_out={checked_out}/{pool_size+overflow})"
                    )
                
                # Check for long-running unreleased connections
                if checked_out > 0:
                    await self._check_long_running_connections()
                
            except Exception as e:
                logger.error(f"Connection pool monitoring exception: {e}")
                await asyncio.sleep(60)  # Wait longer on error
    
    async def _force_cleanup_leaked_connections(self):
        """Force cleanup of leaked connections"""
        async with self._cleanup_lock:
            try:
                logger.warning("Starting forced cleanup of leaked connections...")
                
                # Get status before cleanup
                before_stats = self.get_connection_stats()
                
                # Force release connection pool
                if self._engine:
                    await self._engine.dispose()
                    await asyncio.sleep(1)  # Wait for connections to be released
                    
                    # Recreate engine
                    await self._recreate_engine()
                
                # Get status after cleanup
                after_stats = self.get_connection_stats()
                
                logger.info(f"Connection cleanup complete: before={before_stats['current_active']}, after={after_stats['current_active']}")
                
            except Exception as e:
                logger.error(f"Failed to force cleanup connections: {e}")
    
    async def _recreate_engine(self):
        """Recreate engine"""
        try:
            if self._config:
                await self.initialize(self._config)
        except Exception as e:
            logger.error(f"Failed to recreate engine: {e}")
    
    async def _check_long_running_connections(self):
        """Check long-running connections"""
        # Here you can implement more complex connection timeout checking logic
        pass
    
    @asynccontextmanager
    async def get_session(self):
        """Get database session, ensure connection is properly released"""
        if not self._engine:
            raise RuntimeError("Connection pool not initialized")
        
        session = None
        try:
            session = self._session_factory()
            yield session
        except Exception as e:
            logger.error(f"Database session operation failed: {e}")
            if session:
                try:
                    if session.in_transaction():
                        await session.rollback()
                except Exception as rollback_error:
                    logger.warning(f"Failed to rollback transaction: {rollback_error}")
            raise
        finally:
            if session:
                try:
                    # Ensure transaction is properly closed
                    if session.in_transaction():
                        await session.rollback()
                    
                    # Close session
                    await session.close()
                    
                    # Verify connection is released
                    await asyncio.sleep(0.01)
                    
                except Exception as e:
                    logger.warning(f"Error closing database session: {e}")
                    # Force close sync session
                    try:
                        if hasattr(session, 'sync_session'):
                            session.sync_session.close()
                    except Exception:
                        pass
    
    def get_connection_stats(self) -> Dict[str, Any]:
        """Get connection statistics"""
        if self._engine and self._engine.pool:
            pool = self._engine.pool
            
            # Try to get connection pool statistics
            try:
                pool_size = pool.size() if hasattr(pool, 'size') and callable(pool.size) else 0
                
                # Try multiple methods to get connection pool status
                checked_in = 0
                checked_out = 0
                
                # Method 1: Direct call to checkedin/checkedout (for QueuePool)
                if hasattr(pool, 'checkedin') and callable(pool.checkedin):
                    try:
                        checked_in = pool.checkedin()
                    except Exception:
                        pass
                
                if hasattr(pool, 'checkedout') and callable(pool.checkedout):
                    try:
                        checked_out = pool.checkedout()
                    except Exception:
                        pass
                
                # Method 2: Access internal _pool object (for AsyncAdaptedQueuePool)
                if checked_in == 0 and checked_out == 0:
                    if hasattr(pool, '_pool'):
                        inner_pool = pool._pool
                        if hasattr(inner_pool, 'checkedin') and callable(inner_pool.checkedin):
                            try:
                                checked_in = inner_pool.checkedin()
                            except Exception:
                                pass
                        if hasattr(inner_pool, 'checkedout') and callable(inner_pool.checkedout):
                            try:
                                checked_out = inner_pool.checkedout()
                            except Exception:
                                pass
                
                # Method 3: Use statistics estimation
                if checked_in == 0 and checked_out == 0:
                    checked_out = self._connection_stats.get('current_active', 0)
                    checked_in = max(0, pool_size - checked_out)
                
                # Get overflow configuration
                max_overflow = getattr(pool, '_max_overflow', getattr(pool, 'max_overflow', 0))
                if max_overflow == -1:  # -1 means unlimited
                    max_overflow = 0
                
                overflow = max(0, checked_out - pool_size)
                total_connections = pool_size + max_overflow
                utilization = (checked_out / total_connections * 100) if total_connections > 0 else 0
                
                return {
                    'pool_size': pool_size,
                    'checked_in': checked_in,
                    'checked_out': checked_out,
                    'overflow': overflow,
                    'total_connections': total_connections,
                    'utilization_percent': round(utilization, 2),
                    'status': 'exhausted' if checked_out >= total_connections else 'healthy',
                    'stats': self._connection_stats.copy()
                }
            except Exception as e:
                logger.warning(f"Failed to get connection pool statistics: {e}")
                return {
                    'pool_size': 0,
                    'checked_in': 0,
                    'checked_out': 0,
                    'overflow': 0,
                    'total_connections': 0,
                    'utilization_percent': 0.0,
                    'status': 'error',
                    'stats': self._connection_stats.copy()
                }
        else:
            return {
                'pool_size': 0,
                'checked_in': 0,
                'checked_out': 0,
                'overflow': 0,
                'total_connections': 0,
                'utilization_percent': 0.0,
                'status': 'disconnected',
                'stats': self._connection_stats.copy()
            }
    
    async def cleanup_all_connections(self):
        """Clean up all connections"""
        async with self._cleanup_lock:
            try:
                logger.info("Starting cleanup of all connections...")
                
                if self._engine:
                    await self._engine.dispose()
                    await asyncio.sleep(1)
                
                # Reset statistics
                self._connection_stats = {
                    'total_created': 0,
                    'total_closed': 0,
                    'current_active': 0,
                    'leaked_connections': 0,
                    'last_cleanup': time.time()
                }
                
                logger.info("✅ All connections cleaned up successfully")
                
            except Exception as e:
                logger.error(f"Failed to clean up connections: {e}")
    
    async def shutdown(self):
        """Shutdown connection pool manager"""
        try:
            self._is_monitoring = False
            if self._monitoring_task:
                self._monitoring_task.cancel()
                try:
                    await self._monitoring_task
                except asyncio.CancelledError:
                    pass
            
            await self.cleanup_all_connections()
            self._engine = None
            self._session_factory = None
            
            logger.info("Connection pool manager shutdown")
            
        except Exception as e:
            logger.error(f"Failed to shutdown connection pool manager: {e}")


# Global connection pool manager instance
_connection_pool_manager: Optional[ConnectionPoolManager] = None


def get_connection_pool_manager() -> ConnectionPoolManager:
    """Get global connection pool manager instance"""
    global _connection_pool_manager
    if _connection_pool_manager is None:
        _connection_pool_manager = ConnectionPoolManager()
    return _connection_pool_manager


async def initialize_connection_pool(config: Optional[Dict[str, Any]] = None) -> bool:
    """Initialize global connection pool"""
    manager = get_connection_pool_manager()
    return await manager.initialize(config)


async def shutdown_connection_pool():
    """Shutdown global connection pool"""
    global _connection_pool_manager
    if _connection_pool_manager:
        await _connection_pool_manager.shutdown()
        _connection_pool_manager = None
