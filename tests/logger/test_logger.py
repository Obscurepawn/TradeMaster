import unittest
import os
import tempfile
import shutil
from unittest.mock import patch
import logging

# Add project root to path
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from logger.logger import get_logger

class TestLogger(unittest.TestCase):
    """Test cases for logger module"""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Use a fixed directory for testing
        self.test_dir = os.path.join(os.path.dirname(__file__), 'test_logs')
        os.makedirs(self.test_dir, exist_ok=True)

    def tearDown(self):
        """Tear down test fixtures after each test method."""
        # Clean up test directory
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_get_logger_creates_logger(self):
        """Test that get_logger creates a logger instance"""
        # Use unique logger name to avoid cache
        logger = get_logger("test_logger_unique_1")
        self.assertIsNotNone(logger)
        self.assertEqual(logger.name, "test_logger_unique_1")

    def test_get_logger_with_custom_log_file(self):
        """Test that get_logger creates a logger with custom log file"""
        custom_log_file = os.path.join(self.test_dir, "custom.log")
        # Use unique logger name to avoid cache
        logger = get_logger("test_logger_unique_2", custom_log_file)
        self.assertIsNotNone(logger)
        self.assertEqual(logger.name, "test_logger_unique_2")

    def test_get_logger_returns_same_instance(self):
        """Test that get_logger returns the same instance for the same name"""
        logger1 = get_logger("test_logger_unique_3")
        logger2 = get_logger("test_logger_unique_3")
        self.assertIs(logger1, logger2)

    def test_logger_writes_to_console_and_file(self):
        """Test that logger writes to both console and file"""
        logger_name = "test_logger_unique_4"
        # Ensure logs directory exists in project root directory
        project_root = os.path.join(os.path.dirname(__file__), '..', '..')
        logs_dir = os.path.join(project_root, "logs")
        logs_dir = os.path.abspath(logs_dir)
        os.makedirs(logs_dir, exist_ok=True)
        logger = get_logger(logger_name)

        # Log a message
        test_message = "Test log message"
        logger.info(test_message)

        # Force all handlers to flush
        for handler in logger.handlers:
            handler.flush()

        # Close all handlers to ensure data is written
        for handler in logger.handlers:
            handler.close()

        # Check that log file was created in logs directory
        log_file = os.path.join(logs_dir, f"{logger_name}.log")
        self.assertTrue(os.path.exists(log_file))

        # Check that message is in log file
        with open(log_file, 'r', encoding='utf-8') as f:
            content = f.read()
            self.assertIn(test_message, content)

    def test_logger_writes_to_custom_file(self):
        """Test that logger writes to custom log file"""
        # Ensure test directory exists
        os.makedirs(self.test_dir, exist_ok=True)

        custom_log_file = os.path.join(self.test_dir, "custom_test.log")
        print(f"Custom log file path: {custom_log_file}")
        print(f"Test directory exists: {os.path.exists(self.test_dir)}")

        # Use unique logger name to avoid cache
        logger_name = "test_logger_unique_5"
        logger = get_logger(logger_name, custom_log_file)
        print(f"Logger created with {len(logger.handlers)} handlers")

        # Log a message
        test_message = "Test log message"
        logger.info(test_message)
        print(f"Message logged: {test_message}")

        # Force all handlers to flush
        for handler in logger.handlers:
            handler.flush()
        print("Handlers flushed")

        # Close all handlers to ensure data is written
        for handler in logger.handlers:
            handler.close()
        print("Handlers closed")

        # Check that custom log file was created
        print(f"Custom log file exists: {os.path.exists(custom_log_file)}")
        if not os.path.exists(custom_log_file):
            # List files in test directory
            files = os.listdir(self.test_dir)
            print(f"Files in test directory: {files}")

        self.assertTrue(os.path.exists(custom_log_file), f"Custom log file {custom_log_file} was not created")

        # Check that message is in log file
        with open(custom_log_file, 'r', encoding='utf-8') as f:
            content = f.read()
            self.assertIn(test_message, content)

    def test_logger_different_levels(self):
        """Test that logger handles different log levels"""
        logger_name = "test_logger_unique_6"
        # Ensure logs directory exists in project root directory
        project_root = os.path.join(os.path.dirname(__file__), '..', '..')
        logs_dir = os.path.join(project_root, "logs")
        logs_dir = os.path.abspath(logs_dir)
        os.makedirs(logs_dir, exist_ok=True)
        logger = get_logger(logger_name)

        # Log messages at different levels
        logger.debug("Debug message")
        logger.info("Info message")
        logger.warning("Warning message")
        logger.error("Error message")

        # Force all handlers to flush
        for handler in logger.handlers:
            handler.flush()

        # Close all handlers to ensure data is written
        for handler in logger.handlers:
            handler.close()

        # Check that log file contains the messages
        log_file = os.path.join(logs_dir, f"{logger_name}.log")
        self.assertTrue(os.path.exists(log_file))

        with open(log_file, 'r', encoding='utf-8') as f:
            content = f.read()
            # Note: debug messages might not be logged depending on level configuration
            self.assertIn("Info message", content)
            self.assertIn("Warning message", content)
            self.assertIn("Error message", content)

    def test_logger_multiple_handlers(self):
        """Test that logger doesn't create duplicate handlers"""
        logger_name = "test_logger_unique_7"
        logger = get_logger(logger_name)
        initial_handler_count = len(logger.handlers)

        # Get logger again (should return same instance)
        logger2 = get_logger(logger_name)
        final_handler_count = len(logger2.handlers)

        # Handler count should be the same
        self.assertEqual(initial_handler_count, final_handler_count)

        # Should be the same logger instance
        self.assertIs(logger, logger2)


if __name__ == '__main__':
    unittest.main()
