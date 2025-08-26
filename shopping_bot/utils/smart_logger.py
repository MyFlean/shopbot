# shopping_bot/utils/smart_logger.py
"""
Smart, modular logging system for the shopping bot.
Provides clean, contextual logs with configurable verbosity levels.
"""

import logging
import functools
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from enum import Enum

class LogLevel(Enum):
    MINIMAL = 1      # Only critical flow events
    STANDARD = 2     # Key decisions and state changes  
    DETAILED = 3     # Include data sizes and timing
    DEBUG = 4        # Everything including API calls

class SmartLogger:
    def __init__(self, name: str, level: LogLevel = LogLevel.STANDARD):
        self.logger = logging.getLogger(name)
        self.level = level
        self._request_contexts = {}
        
    def set_level(self, level: LogLevel):
        """Change logging verbosity at runtime"""
        self.level = level
        
    def _should_log(self, required_level: LogLevel) -> bool:
        """Check if we should log at this level"""
        return self.level.value >= required_level.value
        
    def _format_request_id(self, user_id: str) -> str:
        """Generate clean request ID"""
        timestamp = datetime.now().strftime('%H%M%S')
        return f"{user_id[-6:]}_{timestamp}"
        
    def _clean_log(self, level: str, emoji: str, category: str, message: str, **kwargs):
        """Internal clean logging method"""
        # Format key-value pairs cleanly
        details = " | ".join([f"{k}={v}" for k, v in kwargs.items() if v is not None])
        if details:
            full_message = f"{emoji} {category} | {message} | {details}"
        else:
            full_message = f"{emoji} {category} | {message}"
            
        getattr(self.logger, level.lower())(full_message)
    
    # ═══════════════════════════════════════════════════════════
    # HIGH-LEVEL FLOW EVENTS (Always shown except MINIMAL level)
    # ═══════════════════════════════════════════════════════════
    
    def query_start(self, user_id: str, query: str, has_session: bool):
        """Log start of query processing"""
        if not self._should_log(LogLevel.MINIMAL):
            return
            
        req_id = self._format_request_id(user_id)
        self._request_contexts[user_id] = req_id
        
        query_preview = query[:50] + "..." if len(query) > 50 else query
        self._clean_log("info", "🚀", "QUERY_START", f"'{query_preview}'", 
                       req=req_id, session=has_session)
    
    def flow_decision(self, user_id: str, decision: str, reason: str = None):
        """Log major flow decisions"""
        if not self._should_log(LogLevel.MINIMAL):
            return
            
        req_id = self._request_contexts.get(user_id, "unknown")
        self._clean_log("info", "🎯", "FLOW", decision, req=req_id, reason=reason)
    
    def intent_classified(self, user_id: str, intent_hierarchy: tuple, mapped_intent: str):
        """Log intent classification results"""
        if not self._should_log(LogLevel.STANDARD):
            return
            
        req_id = self._request_contexts.get(user_id, "unknown")
        l1, l2, l3 = intent_hierarchy
        self._clean_log("info", "🧠", "INTENT", f"{l1}→{l2}→{l3}", 
                       req=req_id, mapped=mapped_intent)
    
    def requirements_assessed(self, user_id: str, missing_data: List[str], ask_first: List[str]):
        """Log what we need to collect"""
        if not self._should_log(LogLevel.STANDARD):
            return
            
        req_id = self._request_contexts.get(user_id, "unknown")
        ask_count = len(ask_first)
        fetch_count = len(missing_data) - ask_count
        
        self._clean_log("info", "📋", "REQUIREMENTS", 
                       f"need {len(missing_data)} items", 
                       req=req_id, ask=ask_count, fetch=fetch_count)
        
        # Show details at higher verbosity
        if self._should_log(LogLevel.DETAILED):
            self._clean_log("debug", "📊", "REQUIREMENTS_DETAIL", 
                           "breakdown", req=req_id, 
                           asking=ask_first, missing=missing_data)
    
    def user_question(self, user_id: str, asking_for: str):
        """Log when we ask user a question"""
        if not self._should_log(LogLevel.MINIMAL):
            return
            
        req_id = self._request_contexts.get(user_id, "unknown")
        self._clean_log("info", "❓", "ASK_USER", asking_for, req=req_id)
    
    def data_operations(self, user_id: str, operations: List[str], success_count: int = None):
        """Log data fetching operations"""
        if not self._should_log(LogLevel.STANDARD):
            return
            
        req_id = self._request_contexts.get(user_id, "unknown")
        if success_count is not None:
            status = f"{success_count}/{len(operations)} successful"
        else:
            status = f"fetching {len(operations)} items"
            
        self._clean_log("info", "🔍", "DATA_OPS", status, 
                       req=req_id, functions=operations)
    
    def response_generated(self, user_id: str, response_type: str, has_sections: bool = False, elapsed_time: float = None):
        """Log successful response generation"""
        if not self._should_log(LogLevel.MINIMAL):
            return
            
        req_id = self._request_contexts.get(user_id, "unknown")
        extras = {"req": req_id, "sections": has_sections}
        if elapsed_time is not None:
            extras["time"] = f"{elapsed_time:.3f}s"
        
        self._clean_log("info", "✅", "RESPONSE", response_type, **extras)
        
        # Clean up request context
        self._request_contexts.pop(user_id, None)
    
    def memory_operation(self, user_id: str, operation: str, details: Dict[str, Any] = None):
        """Log memory/context operations like last_recommendation storage"""
        if not self._should_log(LogLevel.STANDARD):
            return
            
        req_id = self._request_contexts.get(user_id, "unknown")
        self._clean_log("info", "💾", "MEMORY", operation, req=req_id, **(details or {}))
    
    def follow_up_decision(self, user_id: str, decision: str, effective_intent: str = None, reason: str = None):
        """Log follow-up classification and handling decisions"""
        if not self._should_log(LogLevel.STANDARD):
            return
            
        req_id = self._request_contexts.get(user_id, "unknown")
        extras = {"req": req_id, "intent": effective_intent}
        if reason:
            extras["reason"] = reason
        
        self._clean_log("info", "🔄", "FOLLOW_UP", decision, **extras)
    
    def background_decision(self, user_id: str, mode: str, reason: str = None):
        """Log sync vs async processing decisions"""
        if not self._should_log(LogLevel.STANDARD):
            return
            
        req_id = self._request_contexts.get(user_id, "unknown")
        self._clean_log("info", "⚙️", "PROCESSING", mode, req=req_id, reason=reason)

    # ═══════════════════════════════════════════════════════════
    # DETAILED EVENTS (Only at DETAILED level and above)
    # ═══════════════════════════════════════════════════════════
    
    def context_change(self, user_id: str, change_type: str, details: Dict[str, Any] = None):
        """Log context/session changes"""
        if not self._should_log(LogLevel.DETAILED):
            return
            
        req_id = self._request_contexts.get(user_id, "unknown")
        self._clean_log("debug", "🔄", "CONTEXT", change_type, req=req_id, **(details or {}))
    
    def performance_metric(self, user_id: str, operation: str, duration_ms: int = None, data_size: int = None):
        """Log performance metrics"""
        if not self._should_log(LogLevel.DETAILED):
            return
            
        req_id = self._request_contexts.get(user_id, "unknown")
        self._clean_log("debug", "⚡", "PERF", operation, req=req_id, 
                       duration_ms=duration_ms, size=data_size)
    
    def error_occurred(self, user_id: str, error_type: str, operation: str, error_msg: str = None):
        """Log errors with context"""
        # Errors are always logged regardless of level
        req_id = self._request_contexts.get(user_id, "unknown")
        self._clean_log("error", "❌", "ERROR", f"{error_type} in {operation}", 
                       req=req_id, msg=error_msg)
    
    def warning(self, user_id: str, warning_type: str, details: str = None):
        """Log warnings"""
        if not self._should_log(LogLevel.STANDARD):
            return
            
        req_id = self._request_contexts.get(user_id, "unknown")
        self._clean_log("warning", "⚠️", "WARNING", warning_type, req=req_id, details=details)
    
    # ═══════════════════════════════════════════════════════════
    # DEBUG EVENTS (Only at DEBUG level)
    # ═══════════════════════════════════════════════════════════
    
    def debug_state(self, user_id: str, state_name: str, state_data: Dict[str, Any]):
        """Log detailed state information"""
        if not self._should_log(LogLevel.DEBUG):
            return
            
        req_id = self._request_contexts.get(user_id, "unknown")
        # Only show keys and counts, not full data
        summary = {k: len(v) if isinstance(v, (list, dict, str)) else str(v)[:20] 
                  for k, v in state_data.items()}
        self._clean_log("debug", "🔍", "STATE", state_name, req=req_id, **summary)
    
    def api_call(self, user_id: str, service: str, operation: str, status: str = "started"):
        """Log API calls"""
        if not self._should_log(LogLevel.DEBUG):
            return
            
        req_id = self._request_contexts.get(user_id, "unknown")
        emoji = "📡" if status == "started" else "✅" if status == "success" else "❌"
        self._clean_log("debug", emoji, "API", f"{service}.{operation}", 
                       req=req_id, status=status)


# ═══════════════════════════════════════════════════════════════════════════════
# DECORATORS FOR AUTOMATIC LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

def log_method(operation_name: str = None):
    """Decorator to automatically log method entry/exit"""
    def decorator(func):
        @functools.wraps(func)
        async def async_wrapper(self, *args, **kwargs):
            # Try to extract user_id from context
            user_id = "unknown"
            for arg in args:
                if hasattr(arg, 'user_id'):
                    user_id = arg.user_id
                    break
            
            op_name = operation_name or func.__name__
            
            if hasattr(self, 'smart_log'):
                self.smart_log.debug_state(user_id, f"{op_name}_start", {})
                
            try:
                result = await func(self, *args, **kwargs)
                if hasattr(self, 'smart_log'):
                    self.smart_log.debug_state(user_id, f"{op_name}_complete", {})
                return result
            except Exception as e:
                if hasattr(self, 'smart_log'):
                    self.smart_log.error_occurred(user_id, type(e).__name__, op_name, str(e))
                raise
                
        @functools.wraps(func)
        def sync_wrapper(self, *args, **kwargs):
            # Similar logic for sync methods
            user_id = "unknown"
            for arg in args:
                if hasattr(arg, 'user_id'):
                    user_id = arg.user_id
                    break
            
            op_name = operation_name or func.__name__
            
            try:
                result = func(self, *args, **kwargs)
                return result
            except Exception as e:
                if hasattr(self, 'smart_log'):
                    self.smart_log.error_occurred(user_id, type(e).__name__, op_name, str(e))
                raise
        
        # Return appropriate wrapper based on whether function is async
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    return decorator


# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL LOGGER INSTANCE AND CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Global logger instances for different modules
_loggers = {}

def get_smart_logger(module_name: str, level: LogLevel = None) -> SmartLogger:
    """Get or create a smart logger for a module"""
    if module_name not in _loggers:
        # Use environment variable or default to STANDARD
        import os
        default_level = getattr(LogLevel, os.getenv('BOT_LOG_LEVEL', 'STANDARD').upper(), LogLevel.STANDARD)
        _loggers[module_name] = SmartLogger(module_name, level or default_level)
    
    if level:
        _loggers[module_name].set_level(level)
        
    return _loggers[module_name]

def configure_logging(level: LogLevel = LogLevel.STANDARD, 
                     format_string: str = None,
                     silence_external: bool = True):
    """Configure the entire logging system"""
    import logging
    import sys
    
    # Default format
    if not format_string:
        format_string = '%(asctime)s | %(message)s'
    
    # Configure root logger
    logging.basicConfig(
        level=logging.DEBUG if level == LogLevel.DEBUG else logging.INFO,
        format=format_string,
        datefmt='%H:%M:%S',
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    
    # Silence noisy external libraries
    if silence_external:
        logging.getLogger('httpcore').setLevel(logging.WARNING)
        logging.getLogger('httpx').setLevel(logging.WARNING)
        logging.getLogger('anthropic').setLevel(logging.WARNING)
        logging.getLogger('werkzeug').setLevel(logging.WARNING)
        logging.getLogger('urllib3').setLevel(logging.WARNING)
    
    # Set level for all existing smart loggers
    for smart_logger in _loggers.values():
        smart_logger.set_level(level)
    
    print(f"🔧 Smart logging configured at {level.name} level")