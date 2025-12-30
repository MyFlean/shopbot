"""
Lambda handler for Shopbot Service
Wraps Flask application with serverless-wsgi adapter for AWS Lambda
"""
import json
import os
import logging
import boto3
import serverless_wsgi
from shopping_bot import create_app
from aws_lambda_powertools import Logger, Tracer, Metrics
from aws_lambda_powertools.utilities.typing import LambdaContext

# Initialize AWS Lambda Powertools with readable text format
logger = Logger(
    service="shopbot-service",
    logger_formatter="text",  # Use text format instead of JSON for better readability
    log_level="INFO"
)
tracer = Tracer(service="shopbot-service")
metrics = Metrics(service="shopbot-service", namespace="ShopbotService")


def get_secrets():
    """Retrieve secrets from AWS Secrets Manager"""
    secret_name = os.getenv('SECRETS_MANAGER_SECRET', 'flean-services/shopbot')
    region = os.getenv('AWS_REGION', 'ap-south-1')
    
    try:
        client = boto3.client('secretsmanager', region_name=region)
        response = client.get_secret_value(SecretId=secret_name)
        secret = json.loads(response['SecretString'])
        
        # Set environment variables for Flask app
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
        
        logger.info("Secrets retrieved successfully", extra={
            "keys_loaded": list(secret.keys())
        })
        return secret
    except Exception as e:
        logger.warning(f"Could not retrieve secrets: {e}")
        return {}


# Retrieve secrets before creating app (only in Lambda)
_secrets_loaded = False
if os.getenv('AWS_LAMBDA_FUNCTION_NAME'):
    get_secrets()
    _secrets_loaded = True
    # Verify ANTHROPIC_API_KEY was loaded
    if not os.getenv('ANTHROPIC_API_KEY'):
        logger.warning("ANTHROPIC_API_KEY not found in secrets, will fail on first request")

# Determine config based on environment
config_name = 'lambda' if os.getenv('AWS_LAMBDA_FUNCTION_NAME') else 'production'

# Create Flask app instance (reused across invocations for better performance)
# Note: Config is loaded lazily in create_app, so secrets should be available
app = create_app(config_name)


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
        response = serverless_wsgi.handle_request(app, event, context)
        
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

