#!/usr/bin/env python3
"""
Test runner script for running all unit tests
"""
import unittest
import sys
import os

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

def run_all_tests():
    """Run all tests in the tests directory"""
    # Create test suite manually
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add tests manually
    try:
        from tests.logger.test_logger import TestLogger
        suite.addTests(loader.loadTestsFromTestCase(TestLogger))
    except ImportError as e:
        print(f"Failed to import logger tests: {e}")

    try:
        from tests.proxy.clash.test_proxy import TestClashConfigParser, TestClashController
        suite.addTests(loader.loadTestsFromTestCase(TestClashConfigParser))
        suite.addTests(loader.loadTestsFromTestCase(TestClashController))
    except ImportError as e:
        print(f"Failed to import proxy tests: {e}")

    try:
        from tests.data_loader.ak_share.test_request_hook import TestRequestHook
        suite.addTests(loader.loadTestsFromTestCase(TestRequestHook))
    except ImportError as e:
        print(f"Failed to import request hook tests: {e}")

    try:
        from tests.data_loader.ak_share.test_stock_data_fetcher import TestStockDataFetcher
        suite.addTests(loader.loadTestsFromTestCase(TestStockDataFetcher))
    except ImportError as e:
        print(f"Failed to import stock data fetcher tests: {e}")

    try:
        from tests.config.test_config import TestConfigLoader
        suite.addTests(loader.loadTestsFromTestCase(TestConfigLoader))
    except ImportError as e:
        print(f"Failed to import config tests: {e}")

    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Return exit code based on test results
    return 0 if result.wasSuccessful() else 1

if __name__ == '__main__':
    exit_code = run_all_tests()
    sys.exit(exit_code)
