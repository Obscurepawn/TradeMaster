"""
Configuration loader for TradeMaster project.
This module provides functionality to load and access configuration values from YAML files.
"""

import os
import yaml
from typing import Any, Dict, Optional

# Default configuration file path
DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")


class ConfigLoader:
    """Configuration loader class."""

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the configuration loader.

        Args:
            config_path (str, optional): Path to the configuration file.
                                         If not provided, uses the default path.
        """
        self.config_path = config_path or DEFAULT_CONFIG_PATH
        self._config = None
        self.load_config()

    def load_config(self) -> None:
        """Load configuration from the YAML file."""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as file:
                self._config = yaml.safe_load(file)
        except FileNotFoundError:
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
        except yaml.YAMLError as e:
            raise ValueError(f"Error parsing YAML configuration file: {e}")

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Get a configuration value using a dot-separated key path.

        Args:
            key_path (str): Dot-separated path to the configuration value (e.g., "clash.host").
            default (Any): Default value to return if the key is not found.

        Returns:
            Any: The configuration value or default if not found.
        """
        if self._config is None:
            raise RuntimeError("Configuration not loaded. Call load_config() first.")

        keys = key_path.split('.')
        value = self._config

        try:
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default

    def get_clash_config(self) -> Dict[str, Any]:
        """Get Clash proxy configuration."""
        return {
            "config_path": self.get("clash.config_path"),
            "host": self.get("clash.host"),
            "port": self.get("clash.port"),
            "secret": self.get("clash.secret")
        }

    def get_data_fetching_config(self) -> Dict[str, Any]:
        """Get data fetching configuration."""
        return {
            "start_date": self.get("data_fetching.start_date"),
            "end_date": self.get("data_fetching.end_date"),
            "retry_times": self.get("data_fetching.retry_times"),
            "sleep_seconds": self.get("data_fetching.sleep_seconds"),
            "stock_limit": self.get("data_fetching.stock_limit")
        }

    def get_logging_config(self) -> Dict[str, Any]:
        """Get logging configuration."""
        return {
            "level": self.get("logging.level"),
            "format": self.get("logging.format"),
            "file_path": self.get("logging.file_path")
        }

    def get_data_storage_config(self) -> Dict[str, Any]:
        """Get data storage configuration."""
        return {
            "stock_data_path": self.get("data_storage.stock_data_path")
        }


# Global configuration loader instance
_config_loader: Optional[ConfigLoader] = None


def get_config_loader(config_path: Optional[str] = None) -> ConfigLoader:
    """
    Get the global configuration loader instance.
    
    Args:
        config_path (str, optional): Path to the configuration file.
                                     If not provided, uses the default path.

    Returns:
        ConfigLoader: The global configuration loader instance.
    """
    global _config_loader
    if _config_loader is None or config_path is not None:
        _config_loader = ConfigLoader(config_path)
    return _config_loader


def get_config_value(key_path: str, default: Any = None) -> Any:
    """
    Get a configuration value using the global configuration loader.

    Args:
        key_path (str): Dot-separated path to the configuration value (e.g., "clash.host").
        default (Any): Default value to return if the key is not found.

    Returns:
        Any: The configuration value or default if not found.
    """
    loader = get_config_loader()
    return loader.get(key_path, default)
