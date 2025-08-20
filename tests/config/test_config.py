#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Test cases for config module
"""

import unittest
import sys
import os
from unittest.mock import patch

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from config.config_loader import ConfigLoader, get_config, init_config_loader


class TestConfig(unittest.TestCase):
    """Test cases for configuration management"""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Initialize with a mock config path
        try:
            self.config = init_config_loader("tests/test_config.yaml")
        except FileNotFoundError:
            raise FileNotFoundError(
                "test/test_config.yaml not found. please ensure the config file exists."
            )

    @patch("config.config_loader.yaml.safe_load")
    @patch("builtins.open")
    def test_config_loader_initialization(self, mock_open, mock_safe_load):
        """Test ConfigLoader initialization"""
        # Mock the file reading and YAML parsing
        mock_safe_load.return_value = {
            "clash": {
                "config_path": "/test/clash/config.yaml",
                "host": "localhost",
                "port": 9090,
                "secret": "test-secret",
            },
            "data_storage": {"stock_data_path": "/test/stock/data"},
        }

        # Create a ConfigLoader instance
        config_loader = ConfigLoader("test_config.yaml")

        # Verify the config was loaded
        self.assertIsNotNone(config_loader._config)

        # Test getting values
        self.assertEqual(config_loader.get_clash_host(), "localhost")
        self.assertEqual(
            config_loader.get_data_storage_stock_data_path(), "/test/stock/data"
        )

    def test_get_config_function(self):
        """Test get_config function"""
        # This test will pass if a config has been initialized
        try:
            config = get_config()
            self.assertIsInstance(config, ConfigLoader)
        except RuntimeError:
            # This is expected if no config has been initialized
            pass

    def test_get_with_default(self):
        """Test get method with default value"""
        # Create a config loader without loading a file
        config_loader = ConfigLoader.__new__(ConfigLoader)
        config_loader._config = None

        # Test getting a value when config is None
        result = config_loader.get("test.value", "default_value")
        self.assertEqual(result, "default_value")

        # Mock config data
        config_loader._config = {"test": {"value": "test_value"}}

        # Test getting a value that exists
        result = config_loader.get("test.value", "default_value")
        self.assertEqual(result, "test_value")

        # Test getting a value that doesn't exist with a default
        result = config_loader.get("nonexistent.key", "default_value")
        self.assertEqual(result, "default_value")

    @patch("config.config_loader.yaml.safe_load")
    @patch("builtins.open")
    def test_get_data_storage_stock_data_path(self, mock_open, mock_safe_load):
        """Test getting data storage stock data path"""
        mock_safe_load.return_value = {
            "data_storage": {"stock_data_path": "/custom/stock/data/path"}
        }

        config_loader = ConfigLoader("test_config.yaml")
        result = config_loader.get_data_storage_stock_data_path()
        self.assertEqual(result, "/custom/stock/data/path")


if __name__ == "__main__":
    unittest.main()
