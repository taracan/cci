"""Configuration loader with AWS Secret Manager integration.

This module loads configuration from multiple sources in order of priority:
1. AWS Secret Manager (if configured)
2. Environment variables
3. Hardcoded defaults

Usage:
    from config_loader import Config
    
    config = Config()
    secret_key = config.get("SECRET_KEY")
    database_url = config.get("DATABASE_URL")
"""

import os
import json
from typing import Any, Optional, Dict

from aws_secret_manager import get_secret, configured_secret_name

# Sentinel for "not found in any source" (distinct from an explicit None).
_MISSING = object()


class Config:
    """Configuration loader with AWS Secret Manager support."""
    
    def __init__(self, secret_name: Optional[str] = None, strict_mode: bool = False):
        """Initialize config loader.
        
        Args:
            secret_name: Name of the secret in AWS Secret Manager.
                        Defaults to the AWS_SECRET_NAME env var. If neither is
                        set, AWS is not contacted (env vars/defaults only).
            strict_mode: If True, raise error if required config is missing.
                        If False, use defaults when AWS and env vars are unavailable.
        """
        self.secret_name = secret_name or configured_secret_name()
        self.strict_mode = strict_mode
        self._aws_secret_cache: Optional[Dict[str, Any]] = None
        self._load_aws_secret()

    def _load_aws_secret(self) -> None:
        """Load secret from AWS Secret Manager if explicitly configured."""
        if not self.secret_name:
            return
        self._aws_secret_cache = get_secret(self.secret_name)

    def _get_raw(self, key: str, required: bool = False) -> Any:
        """Return the unconverted value for key, or _MISSING if not found."""
        if self._aws_secret_cache is not None and key in self._aws_secret_cache:
            return self._aws_secret_cache[key]

        env_value = os.getenv(key)
        if env_value is not None:
            return env_value

        if required or self.strict_mode:
            source = f"AWS Secret Manager ('{self.secret_name}')" if self.secret_name \
                else "AWS Secret Manager (set AWS_SECRET_NAME to enable)"
            raise ValueError(
                f"Required configuration '{key}' is missing. "
                f"Set it in {source}, "
                f"as an environment variable, or provide a default."
            )
        return _MISSING
    
    def get(self, key: str, default: Any = None, required: bool = False) -> Any:
        """Get configuration value with fallback chain.
        
        Priority:
            1. AWS Secret Manager (if loaded)
            2. Environment variable
            3. Default value (if provided and not required)
        
        Args:
            key: Configuration key name
            default: Default value if not found elsewhere
            required: If True, raise error when value is missing (overrides default)
        
        Returns:
            Configuration value
        
        Raises:
            ValueError: If required=True and config value is not found in any source
        """
        value = self._get_raw(key, required=required)
        if value is _MISSING:
            return default
        return self._convert_value(value)
    
    def get_required(self, key: str) -> Any:
        """Get a required configuration value.
        
        Raises error if not found in any source.
        
        Args:
            key: Configuration key name
        
        Returns:
            Configuration value
        
        Raises:
            ValueError: If config value is not found
        """
        return self.get(key, required=True)
    
    def get_str(self, key: str, default: str = "", required: bool = False) -> str:
        """Get string configuration value, preserved exactly (no numeric coercion).

        e.g. a secret of "00123" stays "00123", "1e3" stays "1e3".
        """
        value = self._get_raw(key, required=required)
        if value is _MISSING or value is None:
            return default
        return str(value)
    
    def get_required_str(self, key: str) -> str:
        """Get a required string configuration value."""
        return self.get_str(key, required=True)
    
    def get_int(self, key: str, default: int = 0, required: bool = False) -> int:
        """Get integer configuration value."""
        try:
            value = self.get(key, default, required=required)
            return int(value)
        except (ValueError, TypeError):
            if required:
                raise ValueError(f"Configuration '{key}' must be an integer")
            return default
    
    def get_required_int(self, key: str) -> int:
        """Get a required integer configuration value."""
        return self.get_int(key, required=True)
    
    def get_float(self, key: str, default: float = 0.0, required: bool = False) -> float:
        """Get float configuration value."""
        try:
            value = self.get(key, default, required=required)
            return float(value)
        except (ValueError, TypeError):
            if required:
                raise ValueError(f"Configuration '{key}' must be a float")
            return default
    
    def get_required_float(self, key: str) -> float:
        """Get a required float configuration value."""
        return self.get_float(key, required=True)
    
    def get_bool(self, key: str, default: bool = False, required: bool = False) -> bool:
        """Get boolean configuration value."""
        value = self.get(key, default, required=required)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)
    
    def get_required_bool(self, key: str) -> bool:
        """Get a required boolean configuration value."""
        return self.get_bool(key, required=True)
    
    def _convert_value(self, value: Any) -> Any:
        """Convert string value to appropriate type."""
        if value is None:
            return None
        
        if isinstance(value, bool):
            return value
        
        if isinstance(value, (int, float)):
            return value
        
        # Try to convert to int
        try:
            return int(value)
        except (ValueError, TypeError):
            pass
        
        # Try to convert to float
        try:
            return float(value)
        except (ValueError, TypeError):
            pass
        
        # Return as string
        return str(value)
    
    def get_all(self) -> Dict[str, Any]:
        """Get all configuration values as a dictionary."""
        # Start with AWS secret if available
        config = dict(self._aws_secret_cache) if self._aws_secret_cache else {}
        
        # Override with environment variables
        for key in os.environ:
            if key not in config:
                config[key] = os.environ[key]
        
        return config


# Global config instance for convenience
_config: Optional[Config] = None


def get_config(secret_name: Optional[str] = None, strict_mode: bool = False) -> Config:
    """Get global config instance.
    
    Args:
        secret_name: Name of the secret in AWS Secret Manager
        strict_mode: If True, raise error if required config is missing
    """
    global _config
    if _config is None or (_config.strict_mode != strict_mode):
        _config = Config(secret_name, strict_mode=strict_mode)
    return _config


def config_get(key: str, default: Any = None, required: bool = False) -> Any:
    """Convenience function to get config value using global instance."""
    return get_config().get(key, default, required=required)