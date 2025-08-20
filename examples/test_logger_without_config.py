#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Test script to verify logger behavior without config initialization
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from logger.logger import get_logger

if __name__ == "__main__":
    print("Testing logger without config initialization...")

    # This should work now without throwing an exception
    # but will use a default log file path
    try:
        logger = get_logger(__name__)
        logger.info("Logger created successfully without config initialization")
        logger.warning("This is a warning message")
        logger.error("This is an error message")
        print("SUCCESS: Logger works without config initialization")
    except Exception as e:
        print(f"ERROR: {e}")
