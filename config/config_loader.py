"""
Configuration loader for TradeMaster project.
This module provides functionality to load and access configuration values from YAML files.
"""

import os
import yaml
from typing import Any, Dict, Optional


class ConfigLoader:
    """Configuration loader class."""

    def __init__(self, config_path: str):
        """
        Initialize the configuration loader.

        Args:
            config_path (str): Path to the configuration file.
        """
        self.config_path = config_path
        self._config = None
        self.load_config()

    def load_config(self) -> None:
        """Load configuration from the YAML file."""
        try:
            with open(self.config_path, "r", encoding="utf-8") as file:
                self._config = yaml.safe_load(file)
        except FileNotFoundError:
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
        except yaml.YAMLError as e:
            raise ValueError(f"Error parsing YAML configuration file: {e}")

    def get_clash_config(self) -> Dict[str, Any]:
        """Get Clash proxy configuration."""
        return {
            "config_path": self.get("clash.config_path"),
            "host": self.get("clash.host"),
            "port": self.get("clash.port"),
            "secret": self.get("clash.secret"),
        }

    def get_clash_config_path(self) -> str:
        """Get Clash config path."""
        return self.get("clash.config_path")

    def get_clash_host(self) -> str:
        """Get Clash host."""
        return self.get("clash.host")

    def get_clash_port(self) -> int:
        """Get Clash port."""
        return self.get("clash.port")

    def get_clash_secret(self) -> str:
        """Get Clash secret."""
        return self.get("clash.secret")

    def get_data_loader_config(self) -> Dict[str, Any]:
        """Get data loader configuration."""
        return {
            "start_date": self.get("data_loader.start_date"),
            "end_date": self.get("data_loader.end_date"),
            "retry_times": self.get("data_loader.retry_times"),
            "sleep_seconds": self.get("data_loader.sleep_seconds"),
        }

    def get_data_loader_start_date(self) -> str:
        """Get data loader start date."""
        return self.get("data_loader.start_date")

    def get_data_loader_end_date(self) -> str:
        """Get data loader end date."""
        return self.get("data_loader.end_date")

    def get_data_loader_retry_times(self) -> int:
        """Get data loader retry times."""
        return self.get("data_loader.retry_times")

    def get_data_loader_sleep_seconds(self) -> float:
        """Get data loader sleep seconds."""
        return self.get("data_loader.sleep_seconds")

    def get_logging_config(self) -> Dict[str, Any]:
        """Get logging configuration."""
        return {
            "level": self.get("logging.level"),
            "format": self.get("logging.format"),
            "file_path": self.get("logging.file_path"),
        }

    def get_logging_level(self) -> str:
        """Get logging level."""
        return self.get("logging.level")

    def get_logging_format(self) -> str:
        """Get logging format."""
        return self.get("logging.format")

    def get_logging_file_path(self) -> str:
        """Get logging file path."""
        return self.get("logging.file_path")

    def get_data_storage_config(self) -> Dict[str, Any]:
        """Get data storage configuration."""
        return {"stock_data_path": self.get("data_storage.stock_data_path")}

    def get_data_storage_stock_data_path(self) -> str:
        """Get data storage stock data path."""
        return self.get("data_storage.stock_data_path")

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Get a configuration value by key path.

        Args:
            key_path (str): Dot-separated path to the configuration value.
            default (Any): Default value to return if key is not found.

        Returns:
            Any: The configuration value or default if not found.
        """
        if self._config is None:
            return default

        keys = key_path.split('.')
        value = self._config

        try:
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default


# Global configuration loader instance
_config_loader: Optional[ConfigLoader] = None


def init_config_loader(config_path: str) -> ConfigLoader:
    """
    Get the global configuration loader instance.

    This function ensures that only one ConfigLoader instance exists at a time.
    If a ConfigLoader already exists, it will be replaced with a new one using
    the provided config_path.

    Args:
        config_path (str): Path to the configuration file.

    Returns:
        ConfigLoader: The global configuration loader instance.
    """
    global _config_loader
    # Create or replace the global instance with the provided config path
    _config_loader = ConfigLoader(config_path)
    return _config_loader


def get_config() -> ConfigLoader:
    """
    Get the global configuration loader instance.

    This function returns the current global ConfigLoader instance.
    If no instance has been created yet, it raises a RuntimeError.

    Returns:
        ConfigLoader: The global configuration loader instance.

    Raises:
        RuntimeError: If no configuration loader has been initialized.
    """
    global _config_loader
    if _config_loader is None:
        raise RuntimeError(
            "No configuration loader initialized. Call get_config_loader(config_path) first."
        )
    return _config_loader
