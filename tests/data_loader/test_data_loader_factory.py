#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Test cases for data loader factory
"""

import unittest
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from data_loader.data_loader_factory import DataLoaderFactory
from data_loader.data_loader import DataLoader
from data_loader.ak_share.akshare_data_loader import AkshareDataLoader


class TestDataLoaderFactory(unittest.TestCase):
    """Test cases for data loader factory"""

    def test_create_akshare_data_loader(self):
        """Test creating AkshareDataLoader instance"""
        loader = DataLoaderFactory.create_data_loader("akshare")
        self.assertIsInstance(loader, AkshareDataLoader)
        self.assertIsInstance(loader, DataLoader)

    def test_create_data_loader_case_insensitive(self):
        """Test creating data loader with different case"""
        loader1 = DataLoaderFactory.create_data_loader("akshare")
        loader2 = DataLoaderFactory.create_data_loader("AKSHARE")
        loader3 = DataLoaderFactory.create_data_loader("AkShare")

        self.assertIsInstance(loader1, AkshareDataLoader)
        self.assertIsInstance(loader2, AkshareDataLoader)
        self.assertIsInstance(loader3, AkshareDataLoader)

    def test_create_unsupported_data_loader(self):
        """Test creating unsupported data loader raises ValueError"""
        with self.assertRaises(ValueError) as context:
            DataLoaderFactory.create_data_loader("unsupported")

        self.assertIn("Unsupported data loader type", str(context.exception))


if __name__ == "__main__":
    unittest.main()
