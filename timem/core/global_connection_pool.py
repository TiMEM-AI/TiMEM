"""
TiMem Global Connection Pool Manager
Enterprise-grade PostgreSQL connection pool management designed for high-concurrency scenarios

Core Features:
1. Global singleton connection pool management
2. Intelligent connection allocation and recovery
3. Connection pool health monitoring and automatic recovery
4. Concurrency-safe session management
5. Connection leak detection and automatic repair
"""

import asyncio
import threading
import time
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Dict, Optional, AsyncGenerator, Set, Any
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import QueuePool
from sqlalchemy import text, event
from sqlalchemy.exc import SQLAlchemyError, DisconnectionError

from timem.utils.logging import get_logger
from timem.utils.config_manager import get_storage_config


class ConnectionPoolStatus(Enum):
    """Connection pool status enumeration"""
    INITIALIZING = "initializing"
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    FAILED = "failed"


@dataclass
class ConnectionPoolMetrics:
    """Connection pool performance metrics"""
    total_connections: int = 0
    active_connections: int = 0
    idle_connections: int = 0
    overflow_connections: int = 0
    
    # Performance metrics
    utilization_rate: float = 0.0
    avg_wait_time: float = 0.0
    max_wait_time: float = 0.0
    
    # Error statistics
    connection_errors: int = 0
    timeout_errors: int = 0
    leak_count: int = 0
    
    # Status information
    status: ConnectionPoolStatus = ConnectionPoolStatus.INITIALIZING
    last_health_check: float = 0.0
    uptime: float = 0.0
    
    def get_summary(self) -> Dict[str, Any]:
        """Get metrics summary"""
        return {
            'total_connections': self.total_connections,
            'utilization_rate': round(self.utilization_rate * 100, 1),
            'status': self.status.value,
            'connection_errors': self.connection_errors,
            'leak_count': self.leak_count,
            'avg_wait_time_ms': round(self.avg_wait_time * 1000, 2),
            'uptime_hours': round(self.uptime / 3600, 2)
        }


class SessionTracker:
    """Session tracker - used to detect connection leaks"""
    
    def __init__(self):
        self._active_sessions: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self.logger = get_logger(f"{__name__}.SessionTracker")
    
    def track_session(self, session: AsyncSession, context: str = "unknown"):
        """Track session creation"""
        session_id = id(session)
        with self._lock:
            self._active_sessions[session_id] = {
                'session': weakref.ref(session),
                'created_at': time.time(),
                'context': context,
                'thread_id': threading.get_ident()
            }
            self.logger.debug(f"Track session creation: {session_id} (context: {context})")
    
    def untrack_session(self, session: AsyncSession):
        """Untrack session"""
        session_id = id(session)
        with self._lock:
            if session_id in self._active_sessions:
                session_info = self._active_sessions.pop(session_id)
                duration = time.time() - session_info['created_at']
                self.logger.debug(f"Untrack session: {session_id} (lifetime: {duration:.2f}s)")
    
    def detect_leaks(self, max_session_age: float = 300.0) -> int:
        """Detect connection leaks"""
        current_time = time.time()
        leaked_sessions = []
        
        with self._lock:
            for session_id, info in list(self._active_sessions.items()):
                # Check if session is still alive
                session_ref = info['session']
                if session_ref() is None:
                    # Session has been garbage collected but not properly untracked
                    leaked_sessions.append(session_id)
                    continue
                
                # Check if session has timed out
                session_age = current_time - info['created_at']
                if session_age > max_session_age:
                    leaked_sessions.append(session_id)
                    self.logger.warning(
                        f"Detected potential leaked session: {session_id} "
                        f"(age: {session_age:.1f}s, context: {info['context']})"
                    )
        
        # Clean up leaked session records
        with self._lock:
            for session_id in leaked_sessions:
                self._active_sessions.pop(session_id, None)
        
        if leaked_sessions:
            self.logger.error(f"Found {len(leaked_sessions)} potential leaked sessions")
        
        return len(leaked_sessions)
    
    def get_active_session_count(self) -> int:
        """Get active session count"""
        with self._lock:
            return len(self._active_sessions)


class GlobalConnectionPoolManager:
    """
    Global Connection Pool Manager - Enterprise-grade PostgreSQL connection pool management
    
    Design Principles:
    1. Global singleton pattern - avoid multiple connection pool conflicts
    2. Thread-safe design - support high-concurrency access
    3. Automatic failure recovery - connection pool health monitoring
    4. Connection leak detection - automatic detection and repair
    5. Performance monitoring - comprehensive metrics collection
    """
    
    _instance: Optional['GlobalConnectionPoolManager'] = None
    _lock = threading.RLock()
    
    # High-concurrency optimization configuration
    PRODUCTION_CONFIG = {
        'pool_size': 25,                    # Base connection pool size
        'max_overflow': 25,                 # Maximum overflow connections
        'pool_timeout': 60,                 # Connection acquisition timeout (seconds)
        'pool_recycle': 3600,               # Connection recycle time (seconds)
        'pool_pre_ping': True,              # Connection pre-check
        'pool_reset_on_return': 'rollback', # Reset state on connection return
        'connect_args': {
            # Minimize connection parameters to avoid compatibility issues
        }
    }
    
    def __new__(cls):
        """Singleton pattern implementation"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize connection pool manager"""
        if self._initialized:
            return
            
        with self._lock:
            if self._initialized:
                return
                
            self.logger = get_logger(__name__)
            self._engine: Optional[AsyncEngine] = None
            self._session_factory: Optional[async_sessionmaker] = None
            self._metrics = ConnectionPoolMetrics()
            self._session_tracker = SessionTracker()
            
            # Monitoring task
            self._monitor_task: Optional[asyncio.Task] = None
            self._monitor_interval = 30.0  # 30-second monitoring interval
            self._shutdown_event = asyncio.Event()
            
            # Initialization time
            self._start_time = time.time()
            
            self._initialized = True
            self.logger.info("Global connection pool manager instance created successfully")
    
    async def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """
        Initialize connection pool
        
        Args:
            config: Connection pool configuration, uses default if None
            
        Returns:
            bool: Whether initialization was successful
        """
        if self._engine is not None:
            self.logger.info("Connection pool already initialized, skipping duplicate initialization")
            return True
        
        try:
            # Merge configuration
            final_config = self._merge_config(config)
            
            # Create database connection URL
            db_url = self._build_database_url(final_config)
            
            # Create async engine - use NullPool to avoid QueuePool asyncio compatibility issues
            from sqlalchemy.pool import NullPool
            self._engine = create_async_engine(
                db_url,
                poolclass=NullPool,  # Use NullPool to avoid asyncio compatibility issues
                connect_args=final_config['connect_args'],
                echo=False,  # Disable SQL echo in production
                future=True
            )
            
            # Create session factory
            self._session_factory = async_sessionmaker(
                self._engine,
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,  # Manually control flush timing
                autocommit=False
            )
            
            # Test connection
            await self._test_connection()
            
            # Start monitoring task
            await self._start_monitoring()
            
            # Update status
            self._metrics.status = ConnectionPoolStatus.HEALTHY
            self._metrics.last_health_check = time.time()
            
            self.logger.info(
                f"✅ Global connection pool initialized successfully - "
                f"Base connections: {final_config['pool_size']}, "
                f"Max connections: {final_config['pool_size'] + final_config['max_overflow']}"
            )
            return True
            
        except Exception as e:
            self._metrics.status = ConnectionPoolStatus.FAILED
            self._metrics.connection_errors += 1
            self.logger.error(f"❌ Global connection pool initialization failed: {e}", exc_info=True)
            return False
    
    def _merge_config(self, user_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge user configuration and default configuration"""
        # Get base configuration from config file
        storage_config = get_storage_config()
        postgres_config = storage_config.get('sql', {}).get('postgres', {})
        
        # Configuration merge priority: user config > config file > production defaults
        final_config = self.PRODUCTION_CONFIG.copy()
        final_config.update(postgres_config)
        # Same precedence as the other engines: POSTGRES_* env beats YAML.
        from storage.postgres_env import apply_postgres_env

        final_config = apply_postgres_env(final_config)

        if user_config:
            final_config.update(user_config)
        
        return final_config
    
    def _build_database_url(self, config: Dict[str, Any]) -> str:
        """Build database connection URL"""
        host = config.get('host', 'localhost')
        port = config.get('port', 5432)
        user = config.get('user', 'timem_user')
        password = config.get('password', 'timem_password')
        database = config.get('database', 'timem_db')
        
        return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"
    
    async def _test_connection(self):
        """Test database connection"""
        if not self._engine:
            raise RuntimeError("Engine not initialized")
        
        async with self._engine.connect() as conn:
            result = await conn.execute(text("SELECT 1 as test"))
            test_value = result.scalar()
            if test_value != 1:
                raise RuntimeError("Database connection test failed")
    
    async def _start_monitoring(self):
        """Start connection pool monitoring task"""
        if self._monitor_task and not self._monitor_task.done():
            return
            
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        self.logger.info("Connection pool monitoring task started")
    
    async def _monitor_loop(self):
        """Monitoring loop"""
        try:
            while not self._shutdown_event.is_set():
                try:
                    await self._update_metrics()
                    await self._check_pool_health()
                    await self._detect_connection_leaks()
                    
                except Exception as e:
                    self.logger.error(f"Monitoring task exception: {e}", exc_info=True)
                
                # Wait for next monitoring
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self._monitor_interval
                    )
                    break  # Received shutdown signal
                except asyncio.TimeoutError:
                    continue  # Timeout, continue monitoring
                    
        except Exception as e:
            self.logger.error(f"Monitoring loop exited abnormally: {e}", exc_info=True)
    
    async def _update_metrics(self):
        """Update connection pool metrics"""
        if not self._engine:
            return
        
        pool = self._engine.pool
        current_time = time.time()
        
        # Try to get connection pool statistics
        try:
            pool_size = 0
            checked_out = 0
            checked_in = 0
            overflow = 0
            
            # Method 1: Call pool methods directly
            if hasattr(pool, 'size') and callable(pool.size):
                try:
                    pool_size = pool.size()
                except Exception:
                    pass
            
            if hasattr(pool, 'checkedout') and callable(pool.checkedout):
                try:
                    checked_out = pool.checkedout()
                except Exception:
                    pass
            
            if hasattr(pool, 'checkedin') and callable(pool.checkedin):
                try:
                    checked_in = pool.checkedin()
                except Exception:
                    pass
            
            if hasattr(pool, 'overflow') and callable(pool.overflow):
                try:
                    overflow = pool.overflow()
                except Exception:
                    pass
            
            # Method 2: If method 1 fails, try accessing internal _pool object
            if pool_size == 0 and checked_out == 0 and checked_in == 0:
                if hasattr(pool, '_pool'):
                    inner_pool = pool._pool
                    if hasattr(inner_pool, 'size') and callable(inner_pool.size):
                        try:
                            pool_size = inner_pool.size()
                        except Exception:
                            pass
                    if hasattr(inner_pool, 'checkedout') and callable(inner_pool.checkedout):
                        try:
                            checked_out = inner_pool.checkedout()
                        except Exception:
                            pass
                    if hasattr(inner_pool, 'checkedin') and callable(inner_pool.checkedin):
                        try:
                            checked_in = inner_pool.checkedin()
                        except Exception:
                            pass
                    if hasattr(inner_pool, 'overflow') and callable(inner_pool.overflow):
                        try:
                            overflow = inner_pool.overflow()
                        except Exception:
                            pass
            
            # If data was successfully retrieved
            if pool_size > 0 or checked_out > 0 or checked_in > 0:
                self._metrics.total_connections = pool_size + overflow
                self._metrics.active_connections = checked_out
                self._metrics.idle_connections = checked_in
                self._metrics.overflow_connections = overflow
                
                # Calculate utilization rate
                if self._metrics.total_connections > 0:
                    self._metrics.utilization_rate = (
                        self._metrics.active_connections / self._metrics.total_connections
                    )
                else:
                    self._metrics.utilization_rate = 0.0
            else:
                # Unable to get statistics
                pool_class = pool.__class__.__name__
                self.logger.debug(f"Connection pool type {pool_class} does not support statistics collection")
                self._metrics.total_connections = 0
                self._metrics.active_connections = 0
                self._metrics.idle_connections = 0
                self._metrics.overflow_connections = 0
                self._metrics.utilization_rate = 0.0
                
        except Exception as e:
            self.logger.error(f"Failed to update connection pool metrics: {e}")
            # Set default values to avoid crash
            self._metrics.total_connections = 0
            self._metrics.active_connections = 0
            self._metrics.idle_connections = 0
            self._metrics.overflow_connections = 0
            self._metrics.utilization_rate = 0.0
        
        # Update time
        self._metrics.last_health_check = current_time
        self._metrics.uptime = current_time - self._start_time
    
    async def _check_pool_health(self):
        """Check connection pool health status"""
        if not self._engine:
            self._metrics.status = ConnectionPoolStatus.FAILED
            return
        
        utilization = self._metrics.utilization_rate
        error_count = self._metrics.connection_errors
        
        # Determine status based on utilization rate and error count
        if error_count > 10:
            self._metrics.status = ConnectionPoolStatus.CRITICAL
        elif utilization > 0.9 or error_count > 5:
            self._metrics.status = ConnectionPoolStatus.WARNING
        else:
            self._metrics.status = ConnectionPoolStatus.HEALTHY
        
        # Log critical status changes
        if self._metrics.status in [ConnectionPoolStatus.WARNING, ConnectionPoolStatus.CRITICAL]:
            self.logger.warning(
                f"Connection pool status: {self._metrics.status.value} - "
                f"Utilization: {utilization:.1%}, Error count: {error_count}"
            )
    
    async def _detect_connection_leaks(self):
        """Detect connection leaks"""
        leak_count = self._session_tracker.detect_leaks(max_session_age=300.0)
        self._metrics.leak_count += leak_count
        
        if leak_count > 0:
            self.logger.error(f"Detected {leak_count} connection leaks")
    
    @asynccontextmanager
    async def get_managed_session(self, 
                                context: str = "unknown",
                                timeout: float = 30.0) -> AsyncGenerator[AsyncSession, None]:
        """
        Get managed session - automatically handle connection acquisition, tracking, and release
        
        Args:
            context: Session context information for debugging and monitoring
            timeout: Session timeout in seconds
            
        Yields:
            AsyncSession: Database session
        """
        if not self._session_factory:
            raise RuntimeError("Connection pool not initialized")
        
        session = None
        session_created_at = time.time()
        
        try:
            # Create session
            session = self._session_factory()
            
            # Track session
            self._session_tracker.track_session(session, context)
            
            self.logger.debug(f"Create session: {id(session)} (context: {context})")
            
            # Timeout check task
            timeout_task = asyncio.create_task(asyncio.sleep(timeout))
            
            try:
                yield session
            finally:
                # Cancel timeout task
                timeout_task.cancel()
                
                # Check session timeout
                session_duration = time.time() - session_created_at
                if session_duration > timeout:
                    self.logger.warning(
                        f"Session {id(session)} execution time too long: {session_duration:.2f}s "
                        f"(timeout threshold: {timeout}s, context: {context})"
                    )
                
        except Exception as e:
            self._metrics.connection_errors += 1
            self.logger.error(
                f"Session {id(session) if session else 'None'} exception: {e} "
                f"(context: {context})", exc_info=True
            )
            raise
            
        finally:
            # Clean up session
            if session:
                try:
                    await session.close()
                    self._session_tracker.untrack_session(session)
                    self.logger.debug(f"Close session: {id(session)} (context: {context})")
                except Exception as e:
                    self.logger.error(f"Session close exception: {e}", exc_info=True)
    
    async def execute_with_retry(self,
                               operation,
                               max_retries: int = 3,
                               retry_delay: float = 1.0,
                               context: str = "unknown") -> Any:
        """
        Execute operation with retry
        
        Args:
            operation: Async operation function to execute
            max_retries: Maximum number of retries
            retry_delay: Retry delay in seconds
            context: Operation context
            
        Returns:
            Operation result
        """
        last_exception = None
        
        for attempt in range(max_retries + 1):
            try:
                async with self.get_managed_session(context=f"{context}_attempt_{attempt}") as session:
                    return await operation(session)
                    
            except (SQLAlchemyError, DisconnectionError) as e:
                last_exception = e
                self._metrics.connection_errors += 1
                
                if attempt < max_retries:
                    wait_time = retry_delay * (2 ** attempt)  # Exponential backoff
                    self.logger.warning(
                        f"Operation failed, preparing retry (attempt {attempt + 1}/{max_retries + 1}): {e} "
                        f"(wait time: {wait_time}s, context: {context})"
                    )
                    await asyncio.sleep(wait_time)
                else:
                    self.logger.error(
                        f"Operation finally failed (attempt {max_retries + 1}): {e} "
                        f"(context: {context})", exc_info=True
                    )
                    
            except Exception as e:
                # Non-database exceptions, no retry
                last_exception = e
                self.logger.error(f"Operation exception, no retry: {e} (context: {context})", exc_info=True)
                break
        
        # Retries exhausted, raise last exception
        if last_exception:
            raise last_exception
    
    def get_metrics(self) -> ConnectionPoolMetrics:
        """Get connection pool metrics"""
        return self._metrics
    
    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get connection pool metrics summary"""
        return self._metrics.get_summary()
    
    async def health_check(self) -> bool:
        """Connection pool health check"""
        try:
            if not self._engine:
                return False
                
            async with self.get_managed_session(context="health_check", timeout=5.0) as session:
                result = await session.execute(text("SELECT 1"))
                return result.scalar() == 1
                
        except Exception as e:
            self.logger.error(f"Health check failed: {e}")
            return False
    
    async def force_cleanup(self):
        """
        Force cleanup connection pool
        
        Ensure complete release of all resources, support hot reload
        """
        self.logger.info("🧹 Starting forced cleanup of connection pool...")
        
        try:
            # 1. Stop monitoring task
            if self._monitor_task and not self._monitor_task.done():
                self.logger.info("📋 Stopping monitoring task...")
                self._shutdown_event.set()
                self._monitor_task.cancel()
                
                try:
                    await asyncio.wait_for(self._monitor_task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                
                self.logger.info("✅ Monitoring task stopped")
            
            # 2. Release database engine
            if self._engine:
                self.logger.info("📋 Releasing database engine...")
                await self._engine.dispose()
                self.logger.info("✅ Connection pool engine released")
            
            # 3. Reset all states
            self._engine = None
            self._session_factory = None
            self._metrics = ConnectionPoolMetrics()
            self._shutdown_event.clear()
            
            self.logger.info("✅ Connection pool forced cleanup completed")
            
        except Exception as e:
            self.logger.error(f"❌ Connection pool cleanup exception: {e}", exc_info=True)
    
    async def cleanup(self):
        """Clean up connection pool (compatibility method)"""
        await self.force_cleanup()
    
    async def shutdown(self):
        """Shutdown connection pool (service registry compatibility method)"""
        await self.force_cleanup()
    
    async def __aenter__(self):
        """Async context manager entry"""
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.force_cleanup()


# Global instance
_global_pool_manager: Optional[GlobalConnectionPoolManager] = None


async def get_global_pool_manager() -> GlobalConnectionPoolManager:
    """Get global connection pool manager instance"""
    global _global_pool_manager
    
    if _global_pool_manager is None:
        _global_pool_manager = GlobalConnectionPoolManager()
        
        # Ensure initialization
        if not await _global_pool_manager.initialize():
            raise RuntimeError("Global connection pool manager initialization failed")
    
    return _global_pool_manager


# Convenience interface functions

async def get_global_session(context: str = "default") -> AsyncGenerator[AsyncSession, None]:
    """Get global session"""
    manager = await get_global_pool_manager()
    async with manager.get_managed_session(context=context) as session:
        yield session


async def execute_with_global_retry(operation, context: str = "default", max_retries: int = 3):
    """Execute operation with retry using global connection pool"""
    manager = await get_global_pool_manager()
    return await manager.execute_with_retry(operation, max_retries=max_retries, context=context)


async def get_global_pool_metrics() -> Dict[str, Any]:
    """Get global connection pool metrics"""
    try:
        manager = await get_global_pool_manager()
        return manager.get_metrics_summary()
    except Exception as e:
        return {
            'error': str(e),
            'status': 'failed',
            'total_connections': 0,
            'utilization_rate': 0.0
        }


async def global_pool_health_check() -> bool:
    """Global connection pool health check"""
    try:
        manager = await get_global_pool_manager()
        return await manager.health_check()
    except Exception:
        return False
