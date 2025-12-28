import logging
import sys
import os
from src.config.schema import LoggingConfig

def setup_logging(config: LoggingConfig):
    """Sets up global logging configuration.

    Configures handlers for console and file output based on the provided
    LoggingConfig.

    Args:
        config: The logging configuration object.
    """
    handlers = []
    
    if config.console:
        stream_handler = logging.StreamHandler(sys.stdout)
        handlers.append(stream_handler)
        
    if config.file_path:
        # Ensure directory exists
        log_dir = os.path.dirname(config.file_path)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        file_handler = logging.FileHandler(config.file_path)
        handlers.append(file_handler)
        
    # Standard format for TradeMaster
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    for handler in handlers:
        handler.setFormatter(formatter)
        
    logging.basicConfig(
        level=getattr(logging, config.level.upper(), logging.INFO),
        handlers=handlers,
        force=True  # Ensure we override any default config
    )
