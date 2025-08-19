"""
Centralised configuration management.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Type

BASE_DIR = Path(__file__).resolve().parent.parent


class BaseConfig:
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-change-me")
    JSON_SORT_KEYS: bool = False

    # Redis
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", 6379))
    REDIS_DB: int = int(os.getenv("REDIS_DB", 0))
    REDIS_DECODE_RESPONSES: bool = True
    REDIS_TTL_SECONDS: int = int(os.getenv("REDIS_TTL_SECONDS", 3600))

    # Anthropic
    ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")

    # LLM
    LLM_MODEL: str = os.getenv("LLM_MODEL", "claude-3-sonnet-20240229")
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", 0.1))
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", 1000))

    # History / follow-up
    HISTORY_MAX_SNAPSHOTS: int = int(os.getenv("HISTORY_MAX_SNAPSHOTS", 5))

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # ─────────────────────────────────────────────────────────
    # Background Processing
    # ─────────────────────────────────────────────────────────
    BACKGROUND_PROCESSING_TTL = int(os.getenv('BACKGROUND_PROCESSING_TTL', 7200))  # 2 hours
    
    FRONTEND_WEBHOOK_URL = os.getenv(
        'FRONTEND_WEBHOOK_URL', 
        "https://ui-885484857389.asia-south2.run.app/backend-response"
    )
    
    # WhatsApp Flow IDs / Token
    WHATSAPP_FLOW_ID = os.getenv('WHATSAPP_FLOW_ID', '1093082415928891')
    WHATSAPP_PRODUCTS_FLOW_ID = os.getenv('WHATSAPP_PRODUCTS_FLOW_ID', 'your-products-flow-id')
    WHATSAPP_RESULTS_FLOW_ID = os.getenv('WHATSAPP_RESULTS_FLOW_ID', 'your-results-flow-id')
    WHATSAPP_PRODUCT_RECOMMENDATIONS_FLOW_ID = os.getenv(
        'WHATSAPP_PRODUCT_RECOMMENDATIONS_FLOW_ID',
        '799205369204924'
    )
    # NEW: the Flow token issued by Meta for the published Flow
    WHATSAPP_FLOW_TOKEN = os.getenv('WHATSAPP_FLOW_TOKEN', None)


class DevelopmentConfig(BaseConfig):
    DEBUG: bool = True
    FRONTEND_WEBHOOK_URL = os.getenv(
        'FRONTEND_WEBHOOK_URL', 
        "https://ui-885484857389.asia-south2.run.app/backend-response"
    )


class ProductionConfig(BaseConfig):
    DEBUG: bool = False
    REDIS_TTL_SECONDS: int = int(os.getenv("REDIS_TTL_SECONDS", 900))
    FRONTEND_WEBHOOK_URL = os.getenv(
        'FRONTEND_WEBHOOK_URL', 
        "https://ui-885484857389.asia-south2.run.app/backend-response"
    )


class TestingConfig(BaseConfig):
    TESTING: bool = True
    REDIS_DB: int = 15
    FRONTEND_WEBHOOK_URL = os.getenv(
        'FRONTEND_WEBHOOK_URL', 
        "https://ui-885484857389.asia-south2.run.app/backend-response"
    )


def get_config() -> type[BaseConfig]:
    env = os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")).lower()
    mapping = {
        "development": DevelopmentConfig,
        "production": ProductionConfig,
        "testing": TestingConfig,
        "test": TestingConfig,
    }
    return mapping.get(env, DevelopmentConfig)
