import unittest
import os
import logging
from src.config.schema import LoggingConfig
from src.config.logging_config import setup_logging

class TestLogging(unittest.TestCase):
    def setUp(self):
        self.log_file = "test_results/test_backtest.log"
        if os.path.exists(self.log_file):
            os.remove(self.log_file)
            
    def tearDown(self):
        # Clean up logger handlers to avoid interference between tests
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        if os.path.exists(self.log_file):
            os.remove(self.log_file)
        if os.path.exists("test_results"):
            try:
                os.rmdir("test_results")
            except OSError:
                pass

    def test_setup_logging_file_creation(self):
        config = LoggingConfig(
            level="INFO",
            file_path=self.log_file,
            console=True
        )
        setup_logging(config)
        
        test_logger = logging.getLogger("test_logger")
        test_message = "Test log message for file creation"
        test_logger.info(test_message)
        
        # Verify file exists
        self.assertTrue(os.path.exists(self.log_file))
        
        # Verify content
        with open(self.log_file, 'r') as f:
            content = f.read()
            self.assertIn(test_message, content)
            self.assertIn("INFO", content)

    def test_logging_level_respects_config(self):
        config = LoggingConfig(
            level="ERROR",
            file_path=self.log_file,
            console=False
        )
        setup_logging(config)
        
        test_logger = logging.getLogger("test_logger_level")
        test_logger.info("This should NOT be logged")
        test_logger.error("This SHOULD be logged")
        
        with open(self.log_file, 'r') as f:
            content = f.read()
            self.assertNotIn("This should NOT be logged", content)
            self.assertIn("This SHOULD be logged", content)

    def test_load_config_with_logging(self):
        from src.config.settings import load_config
        import yaml
        
        test_config_path = "test_results/test_config.yaml"
        test_data = {
            "start_date": "2023-01-01",
            "end_date": "2023-01-02",
            "initial_cash": 100000,
            "strategy_name": "PESmallCap",
            "baseline": "sh000300",
            "logging": {
                "level": "DEBUG",
                "file_path": self.log_file,
                "console": False
            }
        }
        
        if not os.path.exists("test_results"):
            os.makedirs("test_results")
            
        with open(test_config_path, 'w') as f:
            yaml.dump(test_data, f)
            
        config = load_config(test_config_path)
        self.assertEqual(config.logging.level, "DEBUG")
        self.assertEqual(config.logging.file_path, self.log_file)
        self.assertFalse(config.logging.console)
        
        if os.path.exists(test_config_path):
            os.remove(test_config_path)

if __name__ == '__main__':
    unittest.main()
