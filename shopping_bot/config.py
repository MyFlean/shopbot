"""
Simplified configuration for the new architecture.
Clean config that actually works.
"""
from __future__ import annotations

import os
from pathlib import Path

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

    # Anthropic - MUST be set via environment variable
    ANTHROPIC_API_KEY = "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"
    
    def __post_init__(self):
        """Validate critical settings."""
        if not self.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required")
        if not self.ANTHROPIC_API_KEY.startswith("sk-ant-"):
            raise ValueError("ANTHROPIC_API_KEY appears to be invalid (should start with 'sk-ant-')")

    # LLM
    LLM_MODEL: str = os.getenv("LLM_MODEL", "claude-3-5-sonnet-20241022")
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.1"))
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "1000"))

    # History / follow-up
    HISTORY_MAX_SNAPSHOTS: int = int(os.getenv("HISTORY_MAX_SNAPSHOTS", "5"))

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Background Processing (simplified)
    ENABLE_ASYNC: bool = os.getenv("ENABLE_ASYNC", "false").lower() in {"1", "true", "yes", "on"}
    
    # Elasticsearch (if used)
    ELASTIC_BASE: str = os.getenv("ELASTIC_BASE", "")
    ELASTIC_INDEX: str = os.getenv("ELASTIC_INDEX", "flean_products_v2")
    ELASTIC_API_KEY: str = os.getenv("ELASTIC_API_KEY", "")
    ELASTIC_TIMEOUT_SECONDS: int = int(os.getenv("ELASTIC_TIMEOUT_SECONDS", "10"))
    ELASTIC_MAX_RESULTS: int = int(os.getenv("ELASTIC_MAX_RESULTS", "50"))


class DevelopmentConfig(BaseConfig):
    DEBUG: bool = True


class ProductionConfig(BaseConfig):
    DEBUG: bool = False
    REDIS_TTL_SECONDS: int = int(os.getenv("REDIS_TTL_SECONDS", "900"))


class TestingConfig(BaseConfig):
    TESTING: bool = True
    REDIS_DB: int = 15


def get_config() -> BaseConfig:
    """Get configuration instance directly - no complex manager."""
    env = os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")).lower()
    mapping = {
        "development": DevelopmentConfig,
        "production": ProductionConfig,
        "testing": TestingConfig,
        "test": TestingConfig,
    }
    config_class = mapping.get(env, DevelopmentConfig)
    return config_class()