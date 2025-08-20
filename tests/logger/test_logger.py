#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Test cases for logger module
"""

import unittest
import sys
import os
import logging

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from logger.logger import get_logger


class TestLogger(unittest.TestCase):
    """Test cases for logger module"""

    def test_get_logger_returns_logger(self):
        """Test that get_logger returns a Logger instance"""
        logger = get_logger(__name__)
        self.assertIsInstance(logger, logging.Logger)

    def test_get_logger_returns_same_instance(self):
        """Test that get_logger returns the same instance for the same name"""
        logger1 = get_logger("test_logger")
        logger2 = get_logger("test_logger")
        self.assertIs(logger1, logger2)

    def test_get_logger_different_names(self):
        """Test that get_logger returns different instances for different names"""
        logger1 = get_logger("test_logger_1")
        logger2 = get_logger("test_logger_2")
        self.assertIsNot(logger1, logger2)

    def test_logger_has_correct_name(self):
        """Test that the logger has the correct name"""
        logger = get_logger("test_module")
        self.assertEqual(logger.name, "test_module")

    def test_logger_has_handlers(self):
        """Test that the logger has handlers"""
        logger = get_logger(__name__)
        self.assertGreater(len(logger.handlers), 0)


if __name__ == "__main__":
    unittest.main()
