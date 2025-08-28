# shopping_bot/config.py
"""
Enhanced Configuration with UX System Settings
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BaseConfig:
    """Base configuration class with UX system settings."""
    
    # Core API Settings
    ANTHROPIC_API_KEY: str = "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"
    LLM_MODEL: str = "claude-3-5-sonnet-20241022"
    
    # Redis Settings
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: Optional[str] = None
    
    # Application Settings
    ENABLE_ASYNC: bool = True
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    
    # ─────────────────────────────────────────────────────────
    # UX SYSTEM SETTINGS
    # ─────────────────────────────────────────────────────────
    
    # Feature Flags
    UX_PATTERNS_ENABLED: bool = True
    UX_ROLLOUT_PERCENTAGE: int = 100  # 0-100% of users get UX patterns
    LEGACY_COMPATIBILITY_MODE: bool = True  # Always support v1 clients
    
    # UX Classification Settings
    UX_CONFIDENCE_THRESHOLD: float = 0.7  # Minimum confidence for UX classification
    UX_FALLBACK_TO_STANDARD: bool = True  # Fallback to standard responses on UX failure
    
    # Performance Settings
    UX_CACHE_TTL_SECONDS: int = 300  # Cache UX classifications for 5 minutes
    UX_MAX_RESPONSE_TIME_MS: int = 5000  # Max time allowed for UX generation
    UX_ENABLE_CACHING: bool = True  # Cache DPL/PSL/QR generation
    
    # A/B Testing & Analytics
    UX_AB_TEST_ENABLED: bool = False  # Enable A/B testing framework
    UX_ANALYTICS_SAMPLING_RATE: float = 0.1  # 10% of requests logged for analytics
    UX_METRICS_COLLECTION: bool = True  # Collect UX performance metrics
    
    # Template Settings
    UX_MAX_PRODUCTS_SPM: int = 1  # Single Product Module limit
    UX_MAX_PRODUCTS_CAROUSEL: int = 10  # Carousel limit
    UX_MAX_PRODUCTS_MPM: int = 20  # Multi-Product Module limit
    UX_MAX_QUICK_REPLIES: int = 4  # Quick replies limit
    
    # LLM Settings for UX
    UX_LLM_TEMPERATURE: float = 0.3  # Temperature for UX generation
    UX_LLM_MAX_TOKENS: int = 500  # Max tokens for UX responses
    UX_CLASSIFICATION_TEMPERATURE: float = 0.1  # Temperature for classification
    
    # Error Handling
    UX_RETRY_ATTEMPTS: int = 2  # Retry failed UX generations
    UX_CIRCUIT_BREAKER_THRESHOLD: int = 5  # Circuit breaker error threshold
    UX_CIRCUIT_BREAKER_TIMEOUT: int = 60  # Circuit breaker timeout (seconds)
    
    # ─────────────────────────────────────────────────────────
    # ELASTICSEARCH SETTINGS (Enhanced)
    # ─────────────────────────────────────────────────────────
    ELASTIC_BASE: str = ""
    ELASTIC_INDEX: str = "flean_products_v2"
    ELASTIC_API_KEY: str = ""
    ELASTIC_TIMEOUT_SECONDS: int = 10
    ELASTIC_MAX_RESULTS: int = 50
    
    # ─────────────────────────────────────────────────────────
    # BACKGROUND PROCESSING
    # ─────────────────────────────────────────────────────────
    BACKGROUND_QUEUE_SIZE: int = 100
    BACKGROUND_WORKER_COUNT: int = 3
    BACKGROUND_TIMEOUT_SECONDS: int = 30
    
    def __post_init__(self):
        """Validate configuration after initialization."""
        self._validate_ux_settings()
        self._validate_core_settings()
    
    def _validate_ux_settings(self) -> None:
        """Validate UX system settings."""
        if not 0 <= self.UX_ROLLOUT_PERCENTAGE <= 100:
            raise ValueError("UX_ROLLOUT_PERCENTAGE must be between 0 and 100")
        
        if not 0.0 <= self.UX_CONFIDENCE_THRESHOLD <= 1.0:
            raise ValueError("UX_CONFIDENCE_THRESHOLD must be between 0.0 and 1.0")
        
        if not 0.0 <= self.UX_ANALYTICS_SAMPLING_RATE <= 1.0:
            raise ValueError("UX_ANALYTICS_SAMPLING_RATE must be between 0.0 and 1.0")
        
        if self.UX_MAX_PRODUCTS_CAROUSEL > 10:
            raise ValueError("UX_MAX_PRODUCTS_CAROUSEL should not exceed 10 for optimal UX")
        
        if self.UX_MAX_QUICK_REPLIES > 4:
            raise ValueError("UX_MAX_QUICK_REPLIES should not exceed 4 for mobile UX")
    
    def _validate_core_settings(self) -> None:
        """Validate core settings."""
        if not self.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY is required")
        
        if not self.LLM_MODEL:
            raise ValueError("LLM_MODEL is required")


class DevelopmentConfig(BaseConfig):
    """Development configuration with UX debugging enabled."""
    
    DEBUG: bool = True
    LOG_LEVEL: str = "DEBUG"
    
    # UX Development Settings
    UX_PATTERNS_ENABLED: bool = True
    UX_ROLLOUT_PERCENTAGE: int = 100  # All users in development
    UX_CONFIDENCE_THRESHOLD: float = 0.5  # Lower threshold for testing
    UX_AB_TEST_ENABLED: bool = True  # Enable A/B testing in dev
    UX_ANALYTICS_SAMPLING_RATE: float = 1.0  # Log all requests in dev
    
    # Faster timeouts for development
    UX_MAX_RESPONSE_TIME_MS: int = 10000
    UX_CACHE_TTL_SECONDS: int = 60  # Shorter cache for testing
    
    # Enhanced logging
    UX_METRICS_COLLECTION: bool = True
    UX_ENABLE_CACHING: bool = False  # Disable caching for testing


class ProductionConfig(BaseConfig):
    """Production configuration with conservative UX settings."""
    
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    
    # UX Production Settings
    UX_PATTERNS_ENABLED: bool = True
    UX_ROLLOUT_PERCENTAGE: int = 50  # Gradual rollout in production
    UX_CONFIDENCE_THRESHOLD: float = 0.8  # Higher threshold for reliability
    UX_AB_TEST_ENABLED: bool = True
    UX_ANALYTICS_SAMPLING_RATE: float = 0.05  # 5% sampling in production
    
    # Production optimizations
    UX_ENABLE_CACHING: bool = True
    UX_CACHE_TTL_SECONDS: int = 600  # 10-minute cache
    UX_MAX_RESPONSE_TIME_MS: int = 3000  # Strict timeout
    
    # Circuit breaker settings
    UX_CIRCUIT_BREAKER_THRESHOLD: int = 10
    UX_CIRCUIT_BREAKER_TIMEOUT: int = 300  # 5-minute timeout


class TestingConfig(BaseConfig):
    """Testing configuration with UX system disabled."""
    
    DEBUG: bool = True
    LOG_LEVEL: str = "DEBUG"
    
    # Disable UX for consistent testing
    UX_PATTERNS_ENABLED: bool = False
    UX_ROLLOUT_PERCENTAGE: int = 0
    UX_ENABLE_CACHING: bool = False
    UX_METRICS_COLLECTION: bool = False
    
    # Fast timeouts for tests
    UX_MAX_RESPONSE_TIME_MS: int = 1000
    UX_CACHE_TTL_SECONDS: int = 1


class EnhancedConfigManager:
    """Enhanced configuration manager with runtime updates and monitoring."""
    
    def __init__(self, config: BaseConfig):
        self.config = config
        self._config_dict = {}
        self._load_from_environment()
    
    def _load_from_environment(self) -> None:
        """Load configuration from environment variables."""
        env_mappings = {
            # Core settings
            'ANTHROPIC_API_KEY': str,
            'LLM_MODEL': str,
            'REDIS_HOST': str,
            'REDIS_PORT': int,
            'REDIS_DB': int,
            'ENABLE_ASYNC': self._bool_from_env,
            'DEBUG': self._bool_from_env,
            
            # UX System settings
            'UX_PATTERNS_ENABLED': self._bool_from_env,
            'UX_ROLLOUT_PERCENTAGE': int,
            'UX_CONFIDENCE_THRESHOLD': float,
            'UX_ENABLE_CACHING': self._bool_from_env,
            'UX_AB_TEST_ENABLED': self._bool_from_env,
            'UX_ANALYTICS_SAMPLING_RATE': float,
            'UX_MAX_RESPONSE_TIME_MS': int,
            'UX_CACHE_TTL_SECONDS': int,
            
            # Template limits
            'UX_MAX_PRODUCTS_SPM': int,
            'UX_MAX_PRODUCTS_CAROUSEL': int,
            'UX_MAX_PRODUCTS_MPM': int,
            'UX_MAX_QUICK_REPLIES': int,
            
            # LLM settings
            'UX_LLM_TEMPERATURE': float,
            'UX_LLM_MAX_TOKENS': int,
            'UX_CLASSIFICATION_TEMPERATURE': float,
            
            # Elasticsearch
            'ELASTIC_BASE': str,
            'ELASTIC_INDEX': str,
            'ELASTIC_API_KEY': str,
            'ELASTIC_TIMEOUT_SECONDS': int,
        }
        
        for env_key, type_converter in env_mappings.items():
            env_value = os.getenv(env_key)
            if env_value is not None:
                try:
                    converted_value = type_converter(env_value)
                    setattr(self.config, env_key, converted_value)
                    self._config_dict[env_key] = converted_value
                except (ValueError, TypeError) as e:
                    print(f"Warning: Invalid value for {env_key}={env_value}: {e}")
    
    def _bool_from_env(self, value: str) -> bool:
        """Convert environment variable string to boolean."""
        return value.lower() in ('true', '1', 'yes', 'on', 'enabled')
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value with fallback to default."""
        # Check runtime overrides first
        if key in self._config_dict:
            return self._config_dict[key]
        
        # Check config object
        return getattr(self.config, key, default)
    
    def set(self, key: str, value: Any) -> None:
        """Set configuration value at runtime."""
        self._config_dict[key] = value
        if hasattr(self.config, key):
            setattr(self.config, key, value)
    
    def __getattr__(self, key: str) -> Any:
        """Support attribute access for backward compatibility."""
        if key in self._config_dict:
            return self._config_dict[key]
        
        if hasattr(self.config, key):
            return getattr(self.config, key)
        
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{key}'")
        """Get comprehensive UX system status."""
        return {
            "patterns_enabled": self.get("UX_PATTERNS_ENABLED"),
            "rollout_percentage": self.get("UX_ROLLOUT_PERCENTAGE"),
            "confidence_threshold": self.get("UX_CONFIDENCE_THRESHOLD"),
            "caching_enabled": self.get("UX_ENABLE_CACHING"),
            "ab_testing_enabled": self.get("UX_AB_TEST_ENABLED"),
            "analytics_sampling_rate": self.get("UX_ANALYTICS_SAMPLING_RATE"),
            "max_response_time_ms": self.get("UX_MAX_RESPONSE_TIME_MS"),
            "template_limits": {
                "spm": self.get("UX_MAX_PRODUCTS_SPM"),
                "carousel": self.get("UX_MAX_PRODUCTS_CAROUSEL"),
                "mpm": self.get("UX_MAX_PRODUCTS_MPM"),
                "quick_replies": self.get("UX_MAX_QUICK_REPLIES"),
            }
        }
    
    def update_ux_rollout(self, percentage: int) -> bool:
        """Update UX rollout percentage with validation."""
        if not 0 <= percentage <= 100:
            return False
        
        self.set("UX_ROLLOUT_PERCENTAGE", percentage)
        return True
    
    def toggle_ux_patterns(self) -> bool:
        """Toggle UX patterns on/off and return new state."""
        current_state = self.get("UX_PATTERNS_ENABLED", True)
        new_state = not current_state
        self.set("UX_PATTERNS_ENABLED", new_state)
        return new_state


# Configuration factory
def create_config() -> BaseConfig:
    """Create configuration based on environment."""
    env = os.getenv('APP_ENV', 'development').lower()
    
    config_classes = {
        'development': DevelopmentConfig,
        'production': ProductionConfig,
        'testing': TestingConfig,
        'test': TestingConfig,
    }
    
    config_class = config_classes.get(env, DevelopmentConfig)
    return config_class()


# Global configuration manager
_config_manager: Optional[EnhancedConfigManager] = None


def get_config() -> EnhancedConfigManager:
    """Get global configuration manager instance."""
    global _config_manager
    if _config_manager is None:
        base_config = create_config()
        _config_manager = EnhancedConfigManager(base_config)
    return _config_manager


def reset_config() -> None:
    """Reset global configuration (useful for testing)."""
    global _config_manager
    _config_manager = None


# Backward compatibility
def get_base_config() -> BaseConfig:
    """Get base configuration object (legacy compatibility)."""
    return get_config().config


# Environment variable validation on module import
def validate_required_env_vars() -> None:
    """Validate that required environment variables are set."""
    required_vars = [
        'ANTHROPIC_API_KEY',
        'REDIS_HOST',
    ]
    
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        print(f"Warning: Missing environment variables: {', '.join(missing)}")


# Validate on import (but don't fail)
try:
    validate_required_env_vars()
except Exception as e:
    print(f"Configuration validation warning: {e}")


# Export commonly used values
Cfg = get_config()