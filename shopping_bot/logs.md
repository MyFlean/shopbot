# Smart Logging System - Usage Guide & Examples

## 🎯 Philosophy

**Clean, contextual, configurable.** Show what matters when it matters.

## 📊 Log Levels (Least to Most Verbose)

### 1. **MINIMAL** - Only Critical Flow
```bash
# Set via environment variable
export BOT_LOG_LEVEL=MINIMAL

# Example output:
13:45:23 | 🚀 QUERY_START | 'show me laptops under $500' | req=343434_134523 | session=false
13:45:24 | 🎯 FLOW | NEW_ASSESSMENT | req=343434_134523
13:45:25 | ❓ ASK_USER | ASK_USER_BUDGET | req=343434_134523
13:45:30 | ✅ RESPONSE | question | req=343434_134523
```

### 2. **STANDARD** - Key Decisions (Default)
```bash
export BOT_LOG_LEVEL=STANDARD

# Example output:
13:45:23 | 🚀 QUERY_START | 'show me laptops under $500' | req=343434_134523 | session=false
13:45:24 | 🎯 FLOW | NEW_ASSESSMENT | req=343434_134523
13:45:24 | 🧠 INTENT | A→A1→Recommendation | req=343434_134523 | mapped=recommendation
13:45:25 | 📋 REQUIREMENTS | need 6 items | req=343434_134523 | ask=3 | fetch=3
13:45:25 | ❓ ASK_USER | ASK_USE_CASE | req=343434_134523
13:45:30 | ✅ RESPONSE | question | req=343434_134523 | sections=false
```

### 3. **DETAILED** - Include Performance & Context
```bash
export BOT_LOG_LEVEL=DETAILED

# Example output:
13:45:23 | 🚀 QUERY_START | 'show me laptops under $500' | req=343434_134523 | session=false
13:45:24 | 🎯 FLOW | NEW_ASSESSMENT | req=343434_134523
13:45:24 | 🧠 INTENT | A→A1→Recommendation | req=343434_134523 | mapped=recommendation
13:45:25 | 📋 REQUIREMENTS | need 6 items | req=343434_134523 | ask=3 | fetch=3
13:45:25 | 📊 REQUIREMENTS_DETAIL | breakdown | req=343434_134523 | asking=['ASK_USE_CASE', 'ASK_USER_BUDGET', 'ASK_USER_PREFERENCES'] | missing=['ASK_USE_CASE', 'ASK_USER_BUDGET', 'ASK_USER_PREFERENCES', 'FETCH_PRODUCT_INVENTORY', 'FETCH_USER_PROFILE', 'FETCH_PURCHASE_HISTORY']
13:45:25 | ❓ ASK_USER | ASK_USE_CASE | req=343434_134523
13:45:26 | ⚡ PERF | generate_contextual_questions | req=343434_134523 | duration_ms=450 | size=1240
13:45:30 | ✅ RESPONSE | question | req=343434_134523 | sections=false
```

### 4. **DEBUG** - Everything Including API Calls
```bash
export BOT_LOG_LEVEL=DEBUG

# Example output:
13:45:23 | 🚀 QUERY_START | 'show me laptops under $500' | req=343434_134523 | session=false
13:45:24 | 🔍 STATE | session_state | req=343434_134523 | session=0 | fetched_data=0
13:45:24 | 📡 API | anthropic.classify_intent | req=343434_134523 | status=started
13:45:24 | ✅ API | anthropic.classify_intent | req=343434_134523 | status=success
13:45:24 | 🎯 FLOW | NEW_ASSESSMENT | req=343434_134523
13:45:24 | 🧠 INTENT | A→A1→Recommendation | req=343434_134523 | mapped=recommendation
# ... and much more detail
```

## 🚀 Quick Start

### 1. Add to your existing bot_core.py:
```python
from .utils.smart_logger import get_smart_logger

class ShoppingBotCore:
    def __init__(self, context_mgr: RedisContextManager) -> None:
        self.ctx_mgr = context_mgr
        self.llm_service = LLMService()
        self.smart_log = get_smart_logger('bot_core')  # Add this line
```

### 2. Update your run.py:
```python
from shopping_bot.utils.smart_logger import configure_logging, LogLevel

if __name__ == "__main__":
    # Configure logging
    configure_logging(level=LogLevel.STANDARD)
    
    # Start your app
    app.run(host="0.0.0.0", port=8080, debug=True)
```

### 3. Set log level via environment:
```bash
# In terminal
export BOT_LOG_LEVEL=STANDARD
python3 run.py

# Or inline
BOT_LOG_LEVEL=DETAILED python3 run.py
```

## 🎮 Runtime Control

### Change Log Level Dynamically:
```python
# In your code
from shopping_bot.utils.smart_logger import get_smart_logger, LogLevel

logger = get_smart_logger('bot_core')
logger.set_level(LogLevel.DETAILED)  # Change on the fly
```

### Environment Variables:
```bash
# Available levels
BOT_LOG_LEVEL=MINIMAL     # Quietest
BOT_LOG_LEVEL=STANDARD    # Default - recommended
BOT_LOG_LEVEL=DETAILED    # More info
BOT_LOG_LEVEL=DEBUG       # Everything (noisy)
```

## 📝 Log Format Breakdown

Each log follows this pattern:
```
TIMESTAMP | EMOJI CATEGORY | MESSAGE | key=value | key=value
```

### Key Components:
- **TIMESTAMP**: `13:45:23` (HH:MM:SS)
- **EMOJI**: Visual category identifier
- **CATEGORY**: Operation type (QUERY_START, FLOW, INTENT, etc.)
- **MESSAGE**: Human-readable description
- **req=ID**: Request tracking ID (last 6 digits of user_id + timestamp)
- **Additional context**: Relevant key-value pairs

## 🏷️ Emoji Legend

| Emoji | Category | When |
|-------|----------|------|
| 🚀 | QUERY_START | New user query received |
| 🎯 | FLOW | Major decision points |
| 🧠 | INTENT | Intent classification results |
| 📋 | REQUIREMENTS | What data we need |
| 📊 | REQUIREMENTS_DETAIL | Detailed breakdown |
| ❓ | ASK_USER | Asking user a question |
| 🔍 | DATA_OPS | Data fetching operations |
| ✅ | RESPONSE | Successful response |
| 🔄 | CONTEXT | Context/session changes |
| ⚡ | PERF | Performance metrics |
| ❌ | ERROR | Errors occurred |
| ⚠️ | WARNING | Non-fatal issues |
| 🔍 | STATE | Debug state info |
| 📡 | API | External API calls |

## 🎛️ Customization Examples

### Add Logging to Your Own Modules:
```python
from shopping_bot.utils.smart_logger import get_smart_logger

class MyCustomService:
    def __init__(self):
        self.smart_log = get_smart_logger('my_service')
    
    async def process_something(self, user_id: str, data: dict):
        self.smart_log.query_start(user_id, "processing custom data", False)
        
        try:
            result = await self._do_work(data)
            self.smart_log.response_generated(user_id, "success")
            return result
        except Exception as e:
            self.smart_log.error_occurred(user_id, type(e).__name__, "process_something", str(e))
            raise
```

### Use Decorators for Automatic Logging:
```python
from shopping_bot.utils.smart_logger import log_method

class MyService:
    @log_method("custom_operation")
    async def my_method(self, ctx: UserContext):
        # Method automatically logged at DEBUG level
        return "result"
```

## 🔧 Integration with Existing Code

### Minimal Changes Required:
1. Add `self.smart_log = get_smart_logger('module_name')` to `__init__`
2. Replace verbose logging with clean smart log calls
3. Configure logging level in `run.py`

### Before (Noisy):
```python
log.info("🚀 PROCESSING_QUERY_START | req_id=%s | user_id=%s | query_len=%d | has_session=%s", 
         request_id, ctx.user_id, len(query), bool(ctx.session))
log.debug("📝 QUERY_CONTENT | req_id=%s | query='%s'", request_id, query[:200])
```

### After (Clean):
```python
self.smart_log.query_start(ctx.user_id, query, bool(ctx.session))
```

## 🎯 Best Practices

### 1. **Choose the Right Level**:
- **Development**: `DETAILED` for debugging
- **Staging**: `STANDARD` for monitoring
- **Production**: `STANDARD` or `MINIMAL`

### 2. **Request Tracking**:
- Each user interaction gets a unique short ID
- Follow the `req=` parameter to trace complete flows
- IDs are automatically generated and managed

### 3. **Performance Monitoring**:
- Data fetch sizes and durations logged at `DETAILED` level
- Use for identifying slow operations
- Automatic timing for decorated methods

### 4. **Error Handling**:
- Errors always logged regardless of level
- Include operation context and error type
- Structured for easy monitoring integration

## 🔍 Debugging Workflows

### Find a Specific User Issue:
```bash
# Filter logs by user (last 6 digits)
grep "343434" bot.log

# Filter by request ID
grep "343434_134523" bot.log
```

### Monitor Performance:
```bash
# Look for slow operations
grep "PERF" bot.log | grep "duration_ms"

# Monitor data fetch success rates
grep "DATA_OPS" bot.log
```

### Track Conversation Flows:
```bash
# Follow a complete user journey
grep "req=343434_134523" bot.log | head -20
```

## 🚨 Production Considerations

### Performance Impact:
- **MINIMAL/STANDARD**: Negligible overhead
- **DETAILED**: ~2-5% overhead due to size calculations
- **DEBUG**: Higher overhead, not recommended for production

### Log Storage:
- Structured format perfect for log aggregation
- Easy to parse with tools like ELK, Splunk, etc.
- Consider log rotation for high-volume systems

### Monitoring Integration:
- Error logs include structured data for alerting
- Performance metrics available for dashboards
- Request IDs enable distributed tracing