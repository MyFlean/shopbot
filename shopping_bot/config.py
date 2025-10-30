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
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    
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
    # Streaming (SSE/WebSocket) feature gate
    ENABLE_STREAMING: bool = os.getenv("ENABLE_STREAMING", "false").lower() in {"1", "true", "yes", "on"}
    
    # Elasticsearch (if used)
    # Prefer ES_URL; fallback to legacy ELASTIC_BASE; normalize leading '@' and whitespace
    _RAW_ES = os.getenv("ES_URL") or os.getenv("ELASTIC_BASE", "")
    ELASTIC_BASE: str = (_RAW_ES.strip().lstrip("@").strip())
    ELASTIC_INDEX: str = os.getenv("ELASTIC_INDEX", "products-v2")
    ELASTIC_API_KEY: str = os.getenv("ES_API_KEY") or os.getenv("ELASTIC_API_KEY", "")
    ELASTIC_TIMEOUT_SECONDS: int = int(os.getenv("ELASTIC_TIMEOUT_SECONDS", "10"))
    ELASTIC_MAX_RESULTS: int = int(os.getenv("ELASTIC_MAX_RESULTS", "50"))

    # Feature flags
    USE_COMBINED_CLASSIFY_ASSESS: bool = os.getenv("USE_COMBINED_CLASSIFY_ASSESS", "false").lower() in {"1", "true", "yes", "on"}
    USE_CONVERSATION_AWARE_CLASSIFIER: bool = os.getenv("USE_CONVERSATION_AWARE_CLASSIFIER", "false").lower() in {"1", "true", "yes", "on"}
    USE_TWO_CALL_ES_PIPELINE: bool = os.getenv("USE_TWO_CALL_ES_PIPELINE", "false").lower() in {"1", "true", "yes", "on"}
    # Ask-only mode: bypass assessment state machine except for sequential ASK_* prompts
    ASK_ONLY_MODE: bool = os.getenv("ASK_ONLY_MODE", "false").lower() in {"1", "true", "yes", "on"}
    # New: Use assessment only for ask_user; ignore for ES planning/anchoring
    USE_ASSESSMENT_FOR_ASK_ONLY: bool = os.getenv("USE_ASSESSMENT_FOR_ASK_ONLY", "false").lower() in {"1", "true", "yes", "on"}


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
    cfg = config_class()

    # Optional local override to emulate production flag behavior exactly.
    # Enable by running with: LOCAL_USE_PROD_FLAGS=true
    if os.getenv("LOCAL_USE_PROD_FLAGS", "false").lower() in {"1", "true", "yes", "on"}:
        # Mirrors the (previous) ECS task JSON values that produced ask-only flow
        # This helps reproduce prod behavior locally for RCA.
        try:
            cfg.ASK_ONLY_MODE = True
            cfg.USE_TWO_CALL_ES_PIPELINE = True
            cfg.USE_ASSESSMENT_FOR_ASK_ONLY = True
            cfg.USE_COMBINED_CLASSIFY_ASSESS = True
            cfg.USE_CONVERSATION_AWARE_CLASSIFIER = True

            # Model/token settings to match prod profile
            cfg.LLM_MODEL = os.getenv("LLM_MODEL", "claude-3-5-sonnet-20241022")
            cfg.LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1000"))

            # TTL/logging if needed
            cfg.REDIS_TTL_SECONDS = int(os.getenv("REDIS_TTL_SECONDS", "3600"))
            os.environ.setdefault("BOT_LOG_LEVEL", os.getenv("BOT_LOG_LEVEL", "STANDARD"))
        except Exception:
            # Silent fallback – never break startup due to local override
            pass

    # 🎯 DEBUG: Log critical config values at startup (once per process)
    import logging
    log = logging.getLogger(__name__)
    if not hasattr(get_config, '_logged_startup'):
        log.info(f"⚙️ CONFIG_STARTUP | env={env} | config_class={config_class.__name__}")
        log.info(f"🤖 LLM_CONFIG | model={cfg.LLM_MODEL} | temp={cfg.LLM_TEMPERATURE} | max_tokens={cfg.LLM_MAX_TOKENS}")
        log.info(f"⚙️ FEATURE_FLAGS | USE_COMBINED_CLASSIFY_ASSESS={cfg.USE_COMBINED_CLASSIFY_ASSESS} | USE_CONVERSATION_AWARE_CLASSIFIER={cfg.USE_CONVERSATION_AWARE_CLASSIFIER}")
        log.info(f"⚙️ FEATURE_FLAGS | USE_TWO_CALL_ES_PIPELINE={cfg.USE_TWO_CALL_ES_PIPELINE} | ASK_ONLY_MODE={cfg.ASK_ONLY_MODE} | USE_ASSESSMENT_FOR_ASK_ONLY={cfg.USE_ASSESSMENT_FOR_ASK_ONLY}")
        log.info(f"📡 STREAMING_CONFIG | enable_streaming={getattr(cfg, 'ENABLE_STREAMING', False)}")
        log.info(f"💾 REDIS_CONFIG | host={cfg.REDIS_HOST} | port={cfg.REDIS_PORT} | db={cfg.REDIS_DB} | ttl={cfg.REDIS_TTL_SECONDS}s")
        log.info(f"🔍 ES_CONFIG | index={cfg.ELASTIC_INDEX} | timeout={cfg.ELASTIC_TIMEOUT_SECONDS}s | max_results={cfg.ELASTIC_MAX_RESULTS}")
        log.info(f"📊 HISTORY_CONFIG | max_snapshots={cfg.HISTORY_MAX_SNAPSHOTS}")
        log.info(f"🚀 ASYNC_CONFIG | enable_async={cfg.ENABLE_ASYNC}")
        get_config._logged_startup = True

    return cfg