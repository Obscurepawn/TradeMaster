#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Test runner for the TradeMaster project
"""

import unittest
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import test modules
from tests.config.test_config import TestConfig
from tests.data_loader.test_data_loader_factory import TestDataLoaderFactory
from tests.data_loader.ak_share.test_akshare_data_loader import TestAkshareDataLoader
from tests.logger.test_logger import TestLogger
from tests.proxy.clash.test_proxy import TestProxy

if __name__ == "__main__":
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add tests to suite
    suite.addTests(loader.loadTestsFromTestCase(TestConfig))
    suite.addTests(loader.loadTestsFromTestCase(TestDataLoaderFactory))
    suite.addTests(loader.loadTestsFromTestCase(TestAkshareDataLoader))
    suite.addTests(loader.loadTestsFromTestCase(TestLogger))
    suite.addTests(loader.loadTestsFromTestCase(TestProxy))

    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Exit with error code if tests failed
    sys.exit(not result.wasSuccessful())
