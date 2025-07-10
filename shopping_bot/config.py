# shopping_bot/config.py
"""
Centralised configuration management.

• Loads from environment with sane defaults
• Supports separate Dev / Prod / Test configs
• Exposes a factory `get_config()` used by the Flask app factory
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Type

BASE_DIR = Path(__file__).resolve().parent.parent


class BaseConfig:
    """Shared settings for all environments."""
    # Flask
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

    # LLM model + generation params
    LLM_MODEL: str = os.getenv("LLM_MODEL", "claude-3-sonnet-20240229")
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", 0.1))
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", 1000))

    # Other
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


class DevelopmentConfig(BaseConfig):
    DEBUG: bool = True


class ProductionConfig(BaseConfig):
    DEBUG: bool = False
    # Example: tighten security or timeouts in prod
    REDIS_TTL_SECONDS: int = int(os.getenv("REDIS_TTL_SECONDS", 900))


class TestingConfig(BaseConfig):
    TESTING: bool = True
    REDIS_DB: int = 15  # isolate tests


def get_config() -> Type[BaseConfig]:
    """Selects the correct Config subclass based on FLASK_ENV / APP_ENV."""
    env = os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")).lower()
    mapping = {
        "development": DevelopmentConfig,
        "production": ProductionConfig,
        "testing":    TestingConfig,
        "test":       TestingConfig,
    }
    return mapping.get(env, DevelopmentConfig)
