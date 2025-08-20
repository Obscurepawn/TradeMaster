#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Example of how to properly initialize configuration and use logger
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.config_loader import init_config_loader
from logger.logger import get_logger

# Initialize configuration loader with actual config file
# This should be done before getting any logger instances
config_loader = init_config_loader("config/config.yaml")

# Now we can get a logger instance
logger = get_logger(__name__)

if __name__ == "__main__":
    logger.info("Logger example started")
    logger.warning("This is a warning message")
    logger.error("This is an error message")
    logger.info("Logger example finished")
