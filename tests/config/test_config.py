"""
Test cases for configuration module
"""

import unittest
import os
import sys
import tempfile
import yaml

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from config.config_loader import ConfigLoader, init_config_loader, get_config


class TestConfigLoader(unittest.TestCase):
    """Test cases for ConfigLoader class"""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create a temporary config file for testing
        self.temp_config = {
            "test_section": {
                "string_value": "test_string",
                "int_value": 42,
                "float_value": 3.14,
                "bool_value": True,
                "list_value": [1, 2, 3],
                "nested": {
                    "nested_value": "nested_string"
                }
            }
        }

        # Create temporary file
        self.temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)
        yaml.dump(self.temp_config, self.temp_file)
        self.temp_file.close()

    def tearDown(self):
        """Tear down test fixtures after each test method."""
        # Remove temporary file
        os.unlink(self.temp_file.name)

    def test_config_loader_initialization(self):
        """Test ConfigLoader initialization"""
        loader = ConfigLoader(self.temp_file.name)
        self.assertIsInstance(loader, ConfigLoader)
        self.assertEqual(loader.config_path, self.temp_file.name)

    def test_config_loader_required_path(self):
        """Test ConfigLoader requires a path"""
        # Test that ConfigLoader requires a path argument
        with self.assertRaises(TypeError):
            ConfigLoader()

    def test_load_config(self):
        """Test loading configuration from file"""
        loader = ConfigLoader(self.temp_file.name)
        # Config should be loaded automatically in __init__
        self.assertIsNotNone(loader._config)

    def test_get_config_value(self):
        """Test getting configuration values"""
        loader = ConfigLoader(self.temp_file.name)

        # Test existing values
        self.assertEqual(loader.get("test_section.string_value"), "test_string")
        self.assertEqual(loader.get("test_section.int_value"), 42)
        self.assertEqual(loader.get("test_section.float_value"), 3.14)
        self.assertEqual(loader.get("test_section.bool_value"), True)
        self.assertEqual(loader.get("test_section.list_value"), [1, 2, 3])
        self.assertEqual(loader.get("test_section.nested.nested_value"), "nested_string")

        # Test non-existing value with default
        self.assertEqual(loader.get("non.existing.key", "default"), "default")

        # Test non-existing value without default
        self.assertIsNone(loader.get("non.existing.key"))

    def test_get_config_value_function(self):
        """Test get_config_value function"""
        # Set up the global loader with our test config
        loader = init_config_loader(self.temp_file.name)

        # Test existing values
        self.assertEqual(get_config().get("test_section.string_value"), "test_string")
        self.assertEqual(get_config().get("test_section.int_value"), 42)
        self.assertEqual(get_config().get("test_section.nested.nested_value"), "nested_string")

        # Test non-existing value with default
        self.assertEqual(get_config().get("non.existing.key", "default"), "default")

    def test_get_section_configs(self):
        """Test getting section configurations"""
        loader = ConfigLoader(self.temp_file.name)

        # Test getting test section
        test_section = loader.get("test_section")
        self.assertEqual(test_section["string_value"], "test_string")
        self.assertEqual(test_section["int_value"], 42)
        self.assertEqual(test_section["nested"]["nested_value"], "nested_string")

    def test_config_loader_error_handling(self):
        """Test ConfigLoader error handling"""
        # Test with non-existing file
        with self.assertRaises(FileNotFoundError):
            ConfigLoader("/path/that/does/not/exist.yaml")

        # Test with invalid YAML file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("invalid: yaml: content: [")
            temp_name = f.name

        with self.assertRaises(ValueError):
            ConfigLoader(temp_name)

        # Clean up
        os.unlink(temp_name)


if __name__ == '__main__':
    unittest.main()
