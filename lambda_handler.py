"""
Lambda handler for Shopbot Service
Wraps Flask application with serverless-wsgi adapter for AWS Lambda
"""
import json
import os
import logging
import time
import threading
import boto3
import serverless_wsgi
from shopping_bot import create_app
from aws_lambda_powertools import Logger, Tracer, Metrics
from aws_lambda_powertools.utilities.typing import LambdaContext

# Initialize AWS Lambda Powertools
# Use default JSON format (logger_formatter parameter removed for compatibility)
logger = Logger(service="shopbot-service")
tracer = Tracer(service="shopbot-service")
metrics = Metrics(service="shopbot-service", namespace="ShopbotService")

# Track initialization timing
_init_start_time = time.time()
logger.info("MODULE_IMPORT_START", extra={"timestamp": _init_start_time})

# Async secrets loading state
_secrets_lock = threading.Lock()
_secrets_loading = False
_secrets_loaded = False
_secrets_load_error = None


def get_secrets():
    """Retrieve secrets from AWS Secrets Manager using standard endpoint (resolves to public endpoint)"""
    start_time = time.time()
    secret_name = os.getenv('SECRETS_MANAGER_SECRET', 'flean-services/shopbot')
    redis_secret_name = os.getenv('REDIS_SECRET_NAME', 'flean-services/redis')
    region = os.getenv('AWS_REGION', 'ap-south-1')
    
    logger.info("SECRETS_LOAD_START", extra={"secret_name": secret_name, "region": region})
    
    try:
        client_start = time.time()
        # Configure client with shorter timeout and retries
        from botocore.config import Config
        config = Config(
            connect_timeout=3,
            read_timeout=3,
            retries={'max_attempts': 1}
        )
        # Use standard endpoint - Lambda outside VPC uses public endpoint automatically
        client = boto3.client('secretsmanager', region_name=region, config=config)
        client_time = time.time() - client_start
        logger.info("SECRETS_CLIENT_CREATED", extra={"duration_ms": client_time * 1000})
        
        # Load shopbot secrets (required)
        try:
            get_start = time.time()
            response = client.get_secret_value(SecretId=secret_name)
            get_time = time.time() - get_start
            logger.info("SECRETS_GET_COMPLETE", extra={"duration_ms": get_time * 1000})
            
            parse_start = time.time()
            secret = json.loads(response['SecretString'])
            parse_time = time.time() - parse_start
            
            # Set environment variables for Flask app
            env_start = time.time()
            for key, value in secret.items():
                if value:
                    # Map secret keys to environment variable names
                    env_key = key.upper()
                    # Handle special mappings
                    if key == "ES_API_KEY":
                        # Also set ELASTIC_API_KEY for backward compatibility
                        os.environ["ES_API_KEY"] = str(value)
                        os.environ["ELASTIC_API_KEY"] = str(value)
                    else:
                        os.environ[env_key] = str(value)
            env_time = time.time() - env_start
            
            logger.info("Shopbot secrets retrieved successfully from {}".format(secret_name))
        except Exception as ui_error:
            logger.error("Failed to load shopbot secrets: {}: {}".format(type(ui_error).__name__, str(ui_error)))
            raise  # Re-raise as shopbot secrets are critical
        
        # Load Redis secrets (optional - can fail gracefully)
        try:
            redis_response = client.get_secret_value(SecretId=redis_secret_name)
            redis_secret = json.loads(redis_response['SecretString'])
            
            # Map Redis secret keys to environment variables
            redis_mapping = {
                'host': 'REDIS_HOST',
                'port': 'REDIS_PORT',
                'password': 'REDIS_PASSWORD',
                'db': 'REDIS_DB'
            }
            
            for secret_key, env_key in redis_mapping.items():
                if secret_key in redis_secret and redis_secret[secret_key] is not None:
                    os.environ[env_key] = str(redis_secret[secret_key])
            
            logger.info("Redis secrets retrieved successfully from {}".format(redis_secret_name))
            redis_host = os.getenv('REDIS_HOST', 'NOT_SET')
            redis_port = os.getenv('REDIS_PORT', 'NOT_SET')
            redis_db = os.getenv('REDIS_DB', 'NOT_SET')
            redis_password_set = 'YES' if os.getenv('REDIS_PASSWORD') else 'NO'
            logger.info("Redis configured: host={}, port={}, db={}, password_set={}".format(
                redis_host, redis_port, redis_db, redis_password_set))
            
            # Warn if Redis host is still localhost (secrets not loaded properly)
            if redis_host == 'localhost' or redis_host == 'NOT_SET':
                logger.error("⚠️ WARNING: Redis host is '{}' - secrets may not have loaded correctly! Check Secrets Manager configuration.".format(redis_host))
            
        except Exception as redis_error:
            logger.error("❌ Could not retrieve Redis secrets from {}: {}: {}".format(
                redis_secret_name, type(redis_error).__name__, str(redis_error)), exc_info=True)
            logger.warning("Continuing with existing Redis configuration if available")
            # Log current Redis config to help debug
            logger.info("Current Redis config: host={}, port={}".format(
                os.getenv('REDIS_HOST', 'NOT_SET'), os.getenv('REDIS_PORT', 'NOT_SET')))
        
        total_time = time.time() - start_time
        logger.info("SECRETS_LOAD_SUCCESS", extra={
            "keys_loaded": list(secret.keys()),
            "client_time_ms": client_time * 1000,
            "get_time_ms": get_time * 1000,
            "parse_time_ms": parse_time * 1000,
            "env_time_ms": env_time * 1000,
            "total_time_ms": total_time * 1000
        })
        return secret
    except Exception as e:
        total_time = time.time() - start_time
        logger.error("SECRETS_LOAD_ERROR", extra={
            "error": str(e),
            "duration_ms": total_time * 1000
        }, exc_info=True)
        return {}


# Lazy initialization to avoid init phase timeout
_app = None

def _load_secrets_async():
    """Load secrets in background thread"""
    global _secrets_loaded, _secrets_load_error, _secrets_loading
    
    with _secrets_lock:
        if _secrets_loaded or _secrets_loading:
            return
        _secrets_loading = True
    
    try:
        logger.info("SECRETS_LOAD_ASYNC_START")
        get_secrets()
        with _secrets_lock:
            _secrets_loaded = True
            _secrets_load_error = None
        logger.info("SECRETS_LOAD_ASYNC_SUCCESS")
        # Verify ANTHROPIC_API_KEY was loaded
        if not os.getenv('ANTHROPIC_API_KEY'):
            logger.warning("ANTHROPIC_API_KEY not found in secrets, will fail on first request")
    except Exception as e:
        with _secrets_lock:
            _secrets_load_error = str(e)
        logger.error("SECRETS_LOAD_ASYNC_ERROR", extra={"error": str(e)}, exc_info=True)
    finally:
        with _secrets_lock:
            _secrets_loading = False

def _wait_for_secrets(timeout_seconds=5.0):
    """Wait for secrets to load with timeout. Returns True if loaded, False if timeout."""
    start_time = time.time()
    
    while time.time() - start_time < timeout_seconds:
        with _secrets_lock:
            if _secrets_loaded:
                # Verify Redis secrets are actually set
                redis_host = os.getenv("REDIS_HOST", "localhost")
                if redis_host != "localhost":
                    logger.info(f"Secrets loaded and verified: REDIS_HOST={redis_host}")
                    return True
                else:
                    logger.warning("Secrets marked as loaded but REDIS_HOST is still 'localhost'")
            if _secrets_load_error:
                logger.warning(f"Secrets load failed: {_secrets_load_error}")
                return False
        
        time.sleep(0.1)  # Check every 100ms
    
    # Final check - log what we have
    redis_host = os.getenv("REDIS_HOST", "localhost")
    logger.warning(f"Secrets load timeout after {timeout_seconds}s | REDIS_HOST={redis_host}")
    return False

def get_app(require_secrets=True, secrets_timeout=5.0):
    """Lazy app initialization to avoid init phase timeout
    
    Args:
        require_secrets: If True, wait for secrets to load before creating app
        secrets_timeout: Maximum time to wait for secrets (seconds)
    """
    global _app, _secrets_loaded, _secrets_loading, _secrets_load_error
    
    app_start_time = time.time()
    logger.info("GET_APP_START", extra={"require_secrets": require_secrets})
    
    if _app is not None:
        # App is cached, but we still need to verify secrets are loaded for critical endpoints
        if require_secrets and os.getenv('AWS_LAMBDA_FUNCTION_NAME'):
            redis_host = os.getenv("REDIS_HOST", "localhost")
            logger.info(f"GET_APP_CACHED | Checking secrets | REDIS_HOST={redis_host} | _secrets_loaded={_secrets_loaded}")
            
            if redis_host == "localhost":
                logger.warning("GET_APP_CACHED | App cached but REDIS_HOST is 'localhost' - loading secrets now")
                # Always reload secrets if Redis host is localhost (secrets may not have loaded properly)
                try:
                    get_secrets()
                    # Update the flag after successful load
                    with _secrets_lock:
                        _secrets_loaded = True
                        _secrets_load_error = None
                    
                    # Verify Redis host is now set
                    redis_host = os.getenv("REDIS_HOST", "localhost")
                    if redis_host != "localhost":
                        logger.info(f"GET_APP_CACHED | Secrets loaded successfully | REDIS_HOST={redis_host}")
                    else:
                        logger.error("GET_APP_CACHED | Secrets loaded but REDIS_HOST is still 'localhost' - secret may be missing 'host' key")
                        # Log what we got from secrets
                        logger.error(f"GET_APP_CACHED | REDIS_PORT={os.getenv('REDIS_PORT', 'NOT_SET')} | REDIS_DB={os.getenv('REDIS_DB', 'NOT_SET')}")
                        raise RuntimeError("Redis secrets not loaded - REDIS_HOST is still 'localhost' after loading secrets")
                except Exception as e:
                    with _secrets_lock:
                        _secrets_load_error = str(e)
                    logger.error(f"GET_APP_CACHED | Failed to load secrets: {e}", exc_info=True)
                    raise
            else:
                logger.info(f"GET_APP_CACHED | Secrets verified | REDIS_HOST={redis_host}")
        
        logger.info("GET_APP_CACHED", extra={"duration_ms": (time.time() - app_start_time) * 1000})
        return _app
    
    # Start async secrets loading if not already started
    if os.getenv('AWS_LAMBDA_FUNCTION_NAME'):
        with _secrets_lock:
            if not _secrets_loaded and not _secrets_loading:
                # For critical endpoints, load secrets synchronously to ensure they're available
                if require_secrets:
                    logger.info("SECRETS_LOAD_SYNC_START | loading secrets synchronously for critical endpoint")
                    try:
                        get_secrets()
                        # Already holding _secrets_lock - do not nest (threading.Lock is not re-entrant)
                        _secrets_loaded = True
                        _secrets_load_error = None
                        logger.info("SECRETS_LOAD_SYNC_SUCCESS")
                        # Verify Redis secrets are set
                        redis_host = os.getenv("REDIS_HOST", "localhost")
                        if redis_host == "localhost":
                            logger.error("SECRETS_LOAD_SYNC_WARNING | REDIS_HOST is still 'localhost' after loading secrets")
                        else:
                            logger.info(f"SECRETS_LOAD_SYNC_VERIFIED | REDIS_HOST={redis_host}")
                    except Exception as e:
                        # Already holding _secrets_lock
                        _secrets_load_error = str(e)
                        logger.error(f"SECRETS_LOAD_SYNC_ERROR | error={e}", exc_info=True)
                        raise
                else:
                    # For non-critical endpoints, load asynchronously
                    thread = threading.Thread(target=_load_secrets_async, daemon=True)
                    thread.start()
                    logger.info("SECRETS_LOAD_ASYNC_STARTED")
        
        # Wait for secrets if required (for async case)
        if require_secrets:
            if not _wait_for_secrets(secrets_timeout):
                redis_host = os.getenv("REDIS_HOST", "localhost")
                logger.error(f"SECRETS_NOT_LOADED | REDIS_HOST={redis_host} | proceeding may cause errors")
                # Don't proceed if Redis host is still localhost for critical endpoints
                if redis_host == "localhost":
                    raise RuntimeError("Redis secrets not loaded - REDIS_HOST is still 'localhost'")
    
    # Determine config based on environment
    config_name = 'lambda' if os.getenv('AWS_LAMBDA_FUNCTION_NAME') else 'production'
    logger.info("CREATE_APP_START", extra={"config_name": config_name})
    
    # Create Flask app instance (reused across invocations for better performance)
    create_start = time.time()
    try:
        _app = create_app(config_name)
        create_time = time.time() - create_start
        total_time = time.time() - app_start_time
        logger.info("CREATE_APP_SUCCESS", extra={
            "create_time_ms": create_time * 1000,
            "total_time_ms": total_time * 1000
        })
    except Exception as e:
        create_time = time.time() - create_start
        total_time = time.time() - app_start_time
        logger.error("CREATE_APP_ERROR", extra={
            "error": str(e),
            "create_time_ms": create_time * 1000,
            "total_time_ms": total_time * 1000
        }, exc_info=True)
        raise
    
    return _app

# Track module import completion
_module_import_time = time.time() - _init_start_time
logger.info("MODULE_IMPORT_COMPLETE", extra={"duration_ms": _module_import_time * 1000})

# For backward compatibility, create app at module level but catch init timeout
# NOTE: This will likely timeout during init phase, so app will be None
# and will be created lazily in the handler
app = None
logger.info("MODULE_LEVEL_APP_SKIPPED", extra={"reason": "Lazy initialization to avoid init timeout"})


@logger.inject_lambda_context(log_event=True)
@tracer.capture_lambda_handler
@metrics.log_metrics(capture_cold_start_metric=True)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """
    AWS Lambda handler for Shopbot Service
    
    Args:
        event: API Gateway HTTP API event
        context: Lambda context object
        
    Returns:
        API Gateway HTTP API response
    """
    try:
        # For health endpoint, try to get app but don't block if secrets are slow
        # Check if this is a basic health check request (exact match, not chat/health)
        request_path = event.get("requestContext", {}).get("http", {}).get("path", "")
        is_health_check = request_path == "/rs/health" or request_path == "/health"
        
        if is_health_check:
            # For health checks, return immediately without waiting for app initialization
            # This prevents timeouts if secrets are slow to load
            logger.info("HEALTH_CHECK_REQUEST | returning basic health response")
            return {
                "statusCode": 200,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*"
                },
                "body": json.dumps({
                    "status": "healthy",
                    "service": "shopbot",
                    "message": "Service operational"
                })
            }
        
        # Determine if this endpoint requires secrets
        # Critical endpoints: chat, search, product endpoints
        request_path = event.get("requestContext", {}).get("http", {}).get("path", "")
        is_critical_endpoint = any(path in request_path for path in [
            "/rs/chat", "/rs/search", "/rs/api/v1/products", "/rs/flow"
        ])
        
        # For critical endpoints, wait for secrets (with timeout)
        # For non-critical endpoints, proceed without waiting
        if is_critical_endpoint:
            logger.info("CRITICAL_ENDPOINT | waiting for secrets")
            current_app = get_app(require_secrets=True, secrets_timeout=5.0)  # Increased timeout
            
            # Double-check Redis host is set after getting app
            redis_host_check = os.getenv("REDIS_HOST", "localhost")
            if redis_host_check == "localhost":
                logger.error(f"CRITICAL_ENDPOINT | REDIS_HOST is still 'localhost' after get_app | This should not happen!")
                # Force reload secrets one more time
                logger.info("CRITICAL_ENDPOINT | Force reloading secrets")
                try:
                    get_secrets()
                    redis_host_check = os.getenv("REDIS_HOST", "localhost")
                    if redis_host_check == "localhost":
                        raise RuntimeError("Redis secrets failed to load - REDIS_HOST is still 'localhost' after force reload")
                    logger.info(f"CRITICAL_ENDPOINT | Secrets force reloaded successfully | REDIS_HOST={redis_host_check}")
                except Exception as e:
                    logger.error(f"CRITICAL_ENDPOINT | Force reload failed: {e}", exc_info=True)
                    raise
        else:
            logger.info("NON_CRITICAL_ENDPOINT | proceeding without waiting for secrets")
            current_app = get_app(require_secrets=False)
        
        # Ensure headers exist for serverless-wsgi compatibility
        # API Gateway HTTP API v2.0 may send headers as None or missing
        if "headers" not in event or event.get("headers") is None:
            event["headers"] = {}
        
        # Ensure queryStringParameters exists
        if "queryStringParameters" not in event:
            event["queryStringParameters"] = None
        
        # Log request details
        request_context = event.get("requestContext", {})
        http_info = request_context.get("http", {})
        
        logger.info("Lambda invocation", extra={
            "request_id": context.aws_request_id,
            "function_name": context.function_name,
            "function_version": context.function_version,
            "route": event.get("routeKey", "unknown"),
            "method": http_info.get("method", "unknown"),
            "path": http_info.get("path", "unknown"),
            "has_headers": "headers" in event
        })
        
        # Add custom metrics
        metrics.add_metric(name="RequestCount", unit="Count", value=1)
        
        # Process request through serverless-wsgi adapter
        # serverless-wsgi handles API Gateway HTTP API v2.0 events
        response = serverless_wsgi.handle_request(current_app, event, context)
        
        # Log response
        status_code = response.get("statusCode", 500)
        logger.info("Lambda response", extra={
            "status_code": status_code,
            "request_id": context.aws_request_id
        })
        
        # Add error metric if status >= 400
        if status_code >= 400:
            metrics.add_metric(name="ErrorCount", unit="Count", value=1)
        
        return response
        
    except Exception as e:
        logger.error(f"Lambda handler error: {e}", exc_info=True)
        metrics.add_metric(name="ErrorCount", unit="Count", value=1)
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json"
            },
            "body": json.dumps({
                "error": "Internal server error",
                "message": str(e) if os.getenv('FLASK_DEBUG') == 'true' else "An error occurred"
            })
        }

