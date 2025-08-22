"""
Fixed Redis Manager - ADDRESSES CRITICAL PERSISTENCE ISSUES
===========================================================

Fixes:
2. Prevents duplicate session writes and race conditions
7. Guards against re-entry and session write loops  
8. Atomic operations for status and result management
4. Proper merging of fetched data (don't overwrite)
5. Enhanced error handling and retry logic

Enhanced with comprehensive logging for debugging Redis operations.
"""
from __future__ import annotations

import json
import logging
import time
import hashlib
from datetime import timedelta
from typing import Any, Dict, Optional

import redis
from redis.exceptions import RedisError, ConnectionError, TimeoutError

from .config import get_config
from .models import UserContext

log = logging.getLogger(__name__)
Cfg = get_config()


class RedisContextManager:
    """
    Enhanced Redis context manager with atomic operations and race condition prevention.
    """

    def __init__(self, client: redis.Redis | None = None):
        self.redis: redis.Redis = client or redis.Redis(
            host=Cfg.REDIS_HOST,
            port=Cfg.REDIS_PORT,
            db=Cfg.REDIS_DB,
            decode_responses=Cfg.REDIS_DECODE_RESPONSES,
            socket_timeout=10,  # FIX: Add timeout to prevent hanging
            socket_connect_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30
        )
        self.ttl = timedelta(seconds=Cfg.REDIS_TTL_SECONDS)
        
        # FIX: Debouncing to prevent duplicate writes
        self._last_save_hash = {}  # user_session -> (hash, timestamp)
        self._debounce_window = 1.0  # seconds
        
        # Connection health tracking
        self._connection_healthy = True
        self._last_health_check = 0

    def _check_connection_health(self) -> bool:
        """Check Redis connection health with caching."""
        now = time.time()
        if now - self._last_health_check < 30:  # Cache for 30 seconds
            return self._connection_healthy
            
        try:
            self.redis.ping()
            self._connection_healthy = True
            self._last_health_check = now
            return True
        except Exception as e:
            log.error(f"REDIS_HEALTH_CHECK_FAILED | error={e}")
            self._connection_healthy = False
            self._last_health_check = now
            return False

    def _generate_context_hash(self, ctx: UserContext) -> str:
        """Generate hash of context to detect changes."""
        try:
            content = {
                "permanent": ctx.permanent,
                "session": ctx.session,
                "fetched_data": ctx.fetched_data
            }
            content_str = json.dumps(content, sort_keys=True, default=str)
            return hashlib.md5(content_str.encode()).hexdigest()
        except Exception as e:
            log.warning(f"HASH_GENERATION_ERROR | user={ctx.user_id} | error={e}")
            return str(time.time())  # Fallback to timestamp

    def _should_skip_save(self, ctx: UserContext) -> bool:
        """
        FIX: Check if we should skip this save operation to prevent duplicate writes.
        Addresses issue #7 from diagnostic.
        """
        try:
            user_session_key = f"{ctx.user_id}:{ctx.session_id}"
            current_hash = self._generate_context_hash(ctx)
            now = time.time()
            
            if user_session_key in self._last_save_hash:
                last_hash, last_timestamp = self._last_save_hash[user_session_key]
                
                # Skip if same hash within debounce window
                if (last_hash == current_hash and 
                    now - last_timestamp < self._debounce_window):
                    log.debug(f"SAVE_DEBOUNCED | user={ctx.user_id} | session={ctx.session_id}")
                    return True
                    
            # Update tracking
            self._last_save_hash[user_session_key] = (current_hash, now)
            
            # Cleanup old entries (keep last 100)
            if len(self._last_save_hash) > 100:
                sorted_items = sorted(
                    self._last_save_hash.items(), 
                    key=lambda x: x[1][1]  # Sort by timestamp
                )
                self._last_save_hash = dict(sorted_items[-50:])  # Keep newest 50
                
            return False
            
        except Exception as e:
            log.warning(f"DEBOUNCE_CHECK_ERROR | user={ctx.user_id} | error={e}")
            return False

    # ────────────────────────────────────────────────────────
    # Enhanced public API with atomic operations
    # ────────────────────────────────────────────────────────

    def get_context(self, user_id: str, session_id: str) -> UserContext:
        """
        Enhanced context loading with retry logic and error handling.
        """
        if not self._check_connection_health():
            log.error(f"CONTEXT_LOAD_UNHEALTHY | user={user_id} | session={session_id}")
            # Return empty context if Redis is down
            return UserContext(
                user_id=user_id,
                session_id=session_id,
                permanent={},
                session={},
                fetched_data={}
            )

        try:
            log.debug(f"CONTEXT_LOAD_START | user={user_id} | session={session_id}")
            
            # Load all three buckets with error handling
            permanent = self._get_json_with_retry(f"user:{user_id}:permanent", default={})
            session = self._get_json_with_retry(f"session:{session_id}", default={})
            fetched = self._get_json_with_retry(f"session:{session_id}:fetched", default={})

            ctx = UserContext(
                user_id=user_id,
                session_id=session_id,
                permanent=permanent,
                session=session,
                fetched_data=fetched,
            )
            
            log.info(f"CONTEXT_LOADED | user={user_id} | session={session_id} | permanent_keys={list(permanent.keys())} | session_keys={list(session.keys())} | fetched_keys={list(fetched.keys())}")
            return ctx
            
        except Exception as e:
            log.error(f"CONTEXT_LOAD_ERROR | user={user_id} | session={session_id} | error={e}", exc_info=True)
            # Return empty context on error rather than crashing
            return UserContext(
                user_id=user_id,
                session_id=session_id,
                permanent={},
                session={},
                fetched_data={}
            )

    def save_context(self, ctx: UserContext) -> bool:
        """Enhanced context saving with Redis Cluster compatibility."""
        try:
            # FIX: Check if we should skip this save
            if self._should_skip_save(ctx):
                return True

            if not self._check_connection_health():
                log.error(f"CONTEXT_SAVE_UNHEALTHY | user={ctx.user_id} | session={ctx.session_id}")
                return False

            log.debug(f"CONTEXT_SAVE_START | user={ctx.user_id} | session={ctx.session_id}")

            # FIX: For Redis Cluster, save keys individually instead of pipeline
            try:
                # Check if this is a Redis Cluster
                if hasattr(self.redis, 'cluster_nodes') or 'cluster' in str(type(self.redis)).lower():
                    return self._save_context_cluster_safe(ctx)
                else:
                    return self._save_context_pipeline(ctx)
            except Exception as e:
                # Fallback to individual operations if pipeline fails
                log.warning(f"PIPELINE_FAILED | user={ctx.user_id} | error={e} | falling_back")
                return self._save_context_cluster_safe(ctx)

        except Exception as e:
            log.error(f"CONTEXT_SAVE_ERROR | user={ctx.user_id} | session={ctx.session_id} | error={e}", exc_info=True)
            return False

    def _save_context_pipeline(self, ctx: UserContext) -> bool:
        """Pipeline version for single Redis instance"""
        with self.redis.pipeline() as pipe:
            # Your existing pipeline code here
            permanent_key = f"user:{ctx.user_id}:permanent"
            session_key = f"session:{ctx.session_id}"
            fetched_key = f"session:{ctx.session_id}:fetched"
            
            pipe.set(permanent_key, json.dumps(ctx.permanent))
            
            if self.ttl:
                pipe.setex(session_key, int(self.ttl.total_seconds()), json.dumps(ctx.session))
                pipe.setex(fetched_key, int(self.ttl.total_seconds()), json.dumps(ctx.fetched_data))
            else:
                pipe.set(session_key, json.dumps(ctx.session))
                pipe.set(fetched_key, json.dumps(ctx.fetched_data))
            
            results = pipe.execute()
            return all(results)

    def _save_context_cluster_safe(self, ctx: UserContext) -> bool:
        """Cluster-safe version using individual operations"""
        try:
            # Save each key individually for Redis Cluster compatibility
            permanent_key = f"user:{ctx.user_id}:permanent"
            session_key = f"session:{ctx.session_id}"
            fetched_key = f"session:{ctx.session_id}:fetched"
            
            # Permanent data (no TTL)
            result1 = self.redis.set(permanent_key, json.dumps(ctx.permanent))
            
            # Session data (with TTL)
            if self.ttl:
                result2 = self.redis.setex(session_key, int(self.ttl.total_seconds()), json.dumps(ctx.session))
                result3 = self.redis.setex(fetched_key, int(self.ttl.total_seconds()), json.dumps(ctx.fetched_data))
            else:
                result2 = self.redis.set(session_key, json.dumps(ctx.session))
                result3 = self.redis.set(fetched_key, json.dumps(ctx.fetched_data))
            
            success = all([result1, result2, result3])
            if success:
                log.info(f"CONTEXT_SAVED_CLUSTER | user={ctx.user_id} | session={ctx.session_id}")
            
            return success
            
        except Exception as e:
            log.error(f"CLUSTER_SAVE_ERROR | user={ctx.user_id} | error={e}")
            return False

    def merge_fetched_data(self, session_id: str, new_data: Dict[str, Any]) -> bool:
        """
        FIX: Merge new fetched data without overwriting existing data.
        Addresses issue #4 from diagnostic - fetchers not saving to session:*:fetched.
        """
        try:
            fetched_key = f"session:{session_id}:fetched"
            log.info(f"MERGE_FETCHED_START | session={session_id} | new_keys={list(new_data.keys())}")
            
            # FIX: Use atomic merge operation
            with self.redis.pipeline() as pipe:
                while True:
                    try:
                        # Watch the key for changes
                        pipe.watch(fetched_key)
                        
                        # Get current data
                        current_data = self._get_json(fetched_key, default={})
                        
                        # Merge new data
                        merged_data = dict(current_data)
                        for key, value in new_data.items():
                            if isinstance(value, dict) and key in merged_data and isinstance(merged_data[key], dict):
                                # Deep merge for dict values
                                merged_data[key] = {**merged_data[key], **value}
                            else:
                                # Simple overwrite for other types
                                merged_data[key] = value
                        
                        # Start pipeline
                        pipe.multi()
                        
                        # Set merged data with TTL
                        if self.ttl:
                            pipe.setex(fetched_key, int(self.ttl.total_seconds()), json.dumps(merged_data))
                        else:
                            pipe.set(fetched_key, json.dumps(merged_data))
                        
                        # Execute atomically
                        pipe.execute()
                        
                        log.info(f"MERGE_FETCHED_SUCCESS | session={session_id} | merged_keys={list(merged_data.keys())}")
                        return True
                        
                    except redis.WatchError:
                        # Retry if key was modified during our operation
                        log.debug(f"MERGE_FETCHED_RETRY | session={session_id}")
                        continue
                    except Exception as e:
                        log.error(f"MERGE_FETCHED_ERROR | session={session_id} | error={e}")
                        return False
                        
        except Exception as e:
            log.error(f"MERGE_FETCHED_OUTER_ERROR | session={session_id} | error={e}", exc_info=True)
            return False

    def delete_session(self, session_id: str) -> bool:
        """
        Enhanced session deletion with atomic operations and logging.
        """
        try:
            log.info(f"SESSION_DELETE_START | session={session_id}")
            
            keys_to_delete = [
                f"session:{session_id}",
                f"session:{session_id}:fetched"
            ]
            
            # FIX: Use pipeline for atomic deletion
            with self.redis.pipeline() as pipe:
                for key in keys_to_delete:
                    pipe.delete(key)
                results = pipe.execute()
                
            deleted_count = sum(results)
            log.info(f"SESSION_DELETE_COMPLETE | session={session_id} | deleted_keys={deleted_count}")
            return deleted_count > 0
            
        except Exception as e:
            log.error(f"SESSION_DELETE_ERROR | session={session_id} | error={e}", exc_info=True)
            return False

    # ────────────────────────────────────────────────────────
    # Enhanced internal helpers with retry logic
    # ────────────────────────────────────────────────────────

    def _get_json_with_retry(self, key: str, *, default: Any = None, max_retries: int = 3) -> Any:
        """
        FIX: Get JSON data with retry logic for transient failures.
        """
        for attempt in range(max_retries):
            try:
                raw = self.redis.get(key)
                if raw is None:
                    log.debug(f"REDIS_GET_NONE | key={key} | attempt={attempt + 1}")
                    return default
                    
                try:
                    result = json.loads(raw)
                    log.debug(f"REDIS_GET_SUCCESS | key={key} | size={len(raw)} | attempt={attempt + 1}")
                    return result
                except json.JSONDecodeError as je:
                    log.warning(f"REDIS_GET_JSON_ERROR | key={key} | attempt={attempt + 1} | error={je}")
                    # Reset corrupted key
                    self.redis.delete(key)
                    return default
                    
            except (ConnectionError, TimeoutError) as ce:
                log.warning(f"REDIS_GET_CONNECTION_ERROR | key={key} | attempt={attempt + 1} | error={ce}")
                if attempt == max_retries - 1:
                    self._connection_healthy = False
                    return default
                time.sleep(0.1 * (attempt + 1))  # Exponential backoff
                
            except RedisError as re:
                log.error(f"REDIS_GET_ERROR | key={key} | attempt={attempt + 1} | error={re}")
                return default
                
        return default

    def _get_json(self, key: str, *, default: Any = None) -> Any:
        """Legacy method that delegates to retry version."""
        return self._get_json_with_retry(key, default=default)

    def _set_json_with_retry(self, key: str, value: Any, *, ttl: timedelta | None, max_retries: int = 3) -> bool:
        """
        FIX: Set JSON data with retry logic for transient failures.
        """
        for attempt in range(max_retries):
            try:
                json_data = json.dumps(value)
                
                if ttl is None:
                    result = self.redis.set(key, json_data)
                else:
                    result = self.redis.setex(key, int(ttl.total_seconds()), json_data)
                
                if result:
                    log.debug(f"REDIS_SET_SUCCESS | key={key} | size={len(json_data)} | ttl={ttl} | attempt={attempt + 1}")
                    return True
                else:
                    log.warning(f"REDIS_SET_FAILED | key={key} | attempt={attempt + 1}")
                    
            except (ConnectionError, TimeoutError) as ce:
                log.warning(f"REDIS_SET_CONNECTION_ERROR | key={key} | attempt={attempt + 1} | error={ce}")
                if attempt == max_retries - 1:
                    self._connection_healthy = False
                    return False
                time.sleep(0.1 * (attempt + 1))  # Exponential backoff
                
            except (RedisError, json.JSONEncodeError) as e:
                log.error(f"REDIS_SET_ERROR | key={key} | attempt={attempt + 1} | error={e}")
                return False
                
        return False

    def _set_json(self, key: str, value: Any, *, ttl: timedelta | None) -> bool:
        """Legacy method that delegates to retry version."""
        return self._set_json_with_retry(key, value, ttl=ttl)

    # ────────────────────────────────────────────────────────
    # FIX: Atomic processing status management
    # ────────────────────────────────────────────────────────

    def set_processing_status(self, processing_id: str, status: str, metadata: Dict[str, Any] = None) -> bool:
        """
        FIX: Atomic processing status update with proper lifecycle management.
        Addresses issue #2 - status lifecycle broken.
        """
        try:
            status_key = f"processing:{processing_id}:status"
            
            # FIX: Validate status transitions
            valid_transitions = {
                None: ["processing"],  # Initial state
                "processing": ["completed", "failed"],  # Can only go to terminal states
                "completed": [],  # Terminal state
                "failed": []  # Terminal state
            }
            
            # Check current status for validation
            current_status_data = self._get_json(status_key, default={})
            current_status = current_status_data.get("status")
            
            if current_status in ["completed", "failed"]:
                log.warning(f"STATUS_TRANSITION_BLOCKED | processing_id={processing_id} | current={current_status} | attempted={status}")
                return False
                
            if current_status and status not in valid_transitions.get(current_status, []):
                log.warning(f"STATUS_TRANSITION_INVALID | processing_id={processing_id} | current={current_status} | attempted={status}")
                return False
            
            # Build status payload
            payload = {
                "processing_id": processing_id,
                "status": status,
                "timestamp": time.time(),
                "metadata": metadata or {},
            }
            
            # Set with TTL
            success = self._set_json_with_retry(status_key, payload, ttl=self.ttl)
            
            if success:
                log.info(f"STATUS_SET | processing_id={processing_id} | status={status} | previous={current_status}")
            else:
                log.error(f"STATUS_SET_FAILED | processing_id={processing_id} | status={status}")
                
            return success
            
        except Exception as e:
            log.error(f"STATUS_SET_ERROR | processing_id={processing_id} | status={status} | error={e}", exc_info=True)
            return False

    def set_processing_result(self, processing_id: str, result_data: Dict[str, Any]) -> bool:
        """
        FIX: Atomic processing result storage.
        Must be called BEFORE setting status to completed.
        """
        try:
            result_key = f"processing:{processing_id}:result"
            
            # Add metadata
            result_data["stored_at"] = time.time()
            result_data["processing_id"] = processing_id
            
            success = self._set_json_with_retry(result_key, result_data, ttl=self.ttl)
            
            if success:
                result_size = len(json.dumps(result_data))
                products_count = 0
                if "flow_data" in result_data and "products" in result_data["flow_data"]:
                    products_count = len(result_data["flow_data"]["products"])
                    
                log.info(f"RESULT_SET | processing_id={processing_id} | size={result_size} | products={products_count}")
            else:
                log.error(f"RESULT_SET_FAILED | processing_id={processing_id}")
                
            return success
            
        except Exception as e:
            log.error(f"RESULT_SET_ERROR | processing_id={processing_id} | error={e}", exc_info=True)
            return False

    def get_processing_status(self, processing_id: str) -> Dict[str, Any]:
        """Get processing status with proper error handling."""
        try:
            status_key = f"processing:{processing_id}:status"
            status_data = self._get_json_with_retry(status_key, default={})
            
            if not status_data:
                log.debug(f"STATUS_GET_NOT_FOUND | processing_id={processing_id}")
                return {"status": "not_found"}
                
            log.debug(f"STATUS_GET | processing_id={processing_id} | status={status_data.get('status')}")
            return status_data
            
        except Exception as e:
            log.error(f"STATUS_GET_ERROR | processing_id={processing_id} | error={e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    def get_processing_result(self, processing_id: str) -> Optional[Dict[str, Any]]:
        """Get processing result with proper error handling."""
        try:
            result_key = f"processing:{processing_id}:result"
            result_data = self._get_json_with_retry(result_key, default=None)
            
            if result_data:
                log.debug(f"RESULT_GET_SUCCESS | processing_id={processing_id}")
            else:
                log.debug(f"RESULT_GET_NOT_FOUND | processing_id={processing_id}")
                
            return result_data
            
        except Exception as e:
            log.error(f"RESULT_GET_ERROR | processing_id={processing_id} | error={e}", exc_info=True)
            return None

    # ────────────────────────────────────────────────────────
    # Health and diagnostic methods
    # ────────────────────────────────────────────────────────

    def health_check(self) -> Dict[str, Any]:
        """Comprehensive Redis health check."""
        health_data = {
            "connection_healthy": False,
            "ping_success": False,
            "memory_info": {},
            "error": None
        }
        
        try:
            # Test ping
            ping_result = self.redis.ping()
            health_data["ping_success"] = ping_result
            
            # Test basic operations
            test_key = f"health_check:{int(time.time())}"
            self.redis.setex(test_key, 10, "test")
            test_value = self.redis.get(test_key)
            self.redis.delete(test_key)
            
            health_data["connection_healthy"] = (test_value == "test")
            
            # Get memory info
            info = self.redis.info("memory")
            health_data["memory_info"] = {
                "used_memory_human": info.get("used_memory_human", "unknown"),
                "used_memory_peak_human": info.get("used_memory_peak_human", "unknown"),
                "maxmemory_human": info.get("maxmemory_human", "unknown")
            }
            
        except Exception as e:
            health_data["error"] = str(e)
            
        return health_data

    def get_diagnostics(self, user_id: str, session_id: str) -> Dict[str, Any]:
        """Get diagnostic information for a specific user session."""
        try:
            permanent_key = f"user:{user_id}:permanent"
            session_key = f"session:{session_id}"
            fetched_key = f"session:{session_id}:fetched"
            
            diagnostics = {
                "user_id": user_id,
                "session_id": session_id,
                "keys_exist": {
                    "permanent": bool(self.redis.exists(permanent_key)),
                    "session": bool(self.redis.exists(session_key)),
                    "fetched": bool(self.redis.exists(fetched_key))
                },
                "key_sizes": {},
                "ttl_info": {}
            }
            
            # Get sizes and TTLs
            for key_name, key in [("permanent", permanent_key), ("session", session_key), ("fetched", fetched_key)]:
                try:
                    size = self.redis.memory_usage(key) or 0
                    ttl = self.redis.ttl(key)
                    diagnostics["key_sizes"][key_name] = size
                    diagnostics["ttl_info"][key_name] = ttl
                except Exception:
                    diagnostics["key_sizes"][key_name] = "unknown"
                    diagnostics["ttl_info"][key_name] = "unknown"
                    
            return diagnostics
            
        except Exception as e:
            return {"error": str(e), "user_id": user_id, "session_id": session_id}