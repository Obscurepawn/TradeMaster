import logging
import os
import sys

# Add parent directory to path to import config module
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from config.config_loader import get_config_value

# Configure log format
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Create logger
def get_logger(name: str, log_file: str = None) -> logging.Logger:
    """
    Get logger instance

    Args:
        name: Logger name
        log_file: Log file path (optional)

    Returns:
        logging.Logger: Configured logger instance
    """
    # Create logger instance
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Avoid duplicate handlers
    if not logger.handlers:
        # Create console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        # Create file handler
        if log_file is None:
            # Use log file path from configuration
            log_file = get_config_value("logging.file_path", "logs/app.log")

        # Ensure directory exists for log file
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.INFO)

        # Create formatter
        formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

        # Set handler formatter
        console_handler.setFormatter(formatter)
        file_handler.setFormatter(formatter)

        # Add handlers to logger
        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

    return logger
