#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Test cases for Akshare data loader
"""

import unittest
import sys
import os
import pandas as pd
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from data_loader.data_loader_factory import DataLoaderFactory
from data_loader.ak_share.akshare_data_loader import AkshareDataLoader


class TestAkshareDataLoader(unittest.TestCase):
    """Test cases for Akshare data loader"""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create sample data for testing
        self.sample_data = pd.DataFrame({
            "日期": ["2023-01-01", "2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05"],
            "开盘": [100.0, 101.0, 102.0, 103.0, 104.0],
            "收盘": [101.0, 102.0, 103.0, 104.0, 105.0],
            "最高": [102.0, 103.0, 104.0, 105.0, 106.0],
            "最低": [99.0, 100.0, 101.0, 102.0, 103.0],
            "成交量": [1000, 1100, 1200, 1300, 1400],
        })

        # Create sample stock list
        self.sample_stock_list = pd.DataFrame({
            "code": ["000001", "000002"],
            "name": ["平安银行", "万科A"]
        })

        # Initialize data loader
        self.data_loader = DataLoaderFactory.create_data_loader("akshare")

    def test_calculate_technical_indicators(self):
        """Test calculation of technical indicators"""
        result = self.data_loader._calculate_technical_indicators(self.sample_data.copy())

        # Check that new columns are added
        expected_columns = [
            "MA5", "MA10", "MA20", "MA60", "DIF", "DEA", "MACD", "RSI",
            "Middle_Band", "Upper_Band", "Lower_Band", "VMA5", "VMA10",
            "K", "D", "J", "BIAS5", "BIAS10",
        ]

        for column in expected_columns:
            self.assertIn(column, result.columns)

        # Check that data is sorted and reset
        self.assertEqual(result.index[0], 0)
        self.assertEqual(result.index[-1], len(result) - 1)

    @patch("data_loader.ak_share.akshare_data_loader.ak.stock_info_a_code_name")
    def test_get_stock_list(self, mock_stock_info):
        """Test getting stock list"""
        mock_stock_info.return_value = self.sample_stock_list

        result = self.data_loader.get_stock_list()
        pd.testing.assert_frame_equal(result, self.sample_stock_list)
        mock_stock_info.assert_called_once()

    @patch("data_loader.ak_share.akshare_data_loader.ak.stock_zh_a_hist")
    @patch("data_loader.ak_share.akshare_data_loader.time.sleep")
    def test_get_stock_history_success(self, mock_sleep, mock_stock_hist):
        """Test successful stock history retrieval"""
        mock_stock_hist.return_value = self.sample_data.copy()

        result = self.data_loader.get_stock_history(
            symbol="000001",
            name="平安银行",
            period="daily",
            adjust="qfq",
            start_date="20230101",
            end_date="20230105",
        )

        self.assertIsNotNone(result)
        self.assertIsInstance(result, pd.DataFrame)
        mock_stock_hist.assert_called_once()
        mock_sleep.assert_called_once()

    @patch("data_loader.ak_share.akshare_data_loader.ak.stock_zh_a_hist")
    @patch("data_loader.ak_share.akshare_data_loader.time.sleep")
    def test_get_stock_history_empty_data(self, mock_sleep, mock_stock_hist):
        """Test stock history retrieval with empty data"""
        mock_stock_hist.return_value = pd.DataFrame()

        with self.assertRaises(Exception) as context:
            self.data_loader.get_stock_history(
                symbol="000001",
                name="平安银行",
                period="daily",
                adjust="qfq",
                start_date="20230101",
                end_date="20230105",
                retry_times=1,
            )

        self.assertIn("Fail to get stock historical data after max retries", str(context.exception))
        mock_stock_hist.assert_called_once()
        mock_sleep.assert_called_once()

    @patch("data_loader.ak_share.akshare_data_loader.ak.stock_zh_a_hist")
    @patch("data_loader.ak_share.akshare_data_loader.time.sleep")
    def test_get_stock_history_exception(self, mock_sleep, mock_stock_hist):
        """Test stock history retrieval with exception"""
        mock_stock_hist.side_effect = Exception("Network error")

        with self.assertRaises(Exception) as context:
            self.data_loader.get_stock_history(
                symbol="000001",
                name="平安银行",
                period="daily",
                adjust="qfq",
                start_date="20230101",
                end_date="20230105",
                retry_times=1,
            )

        self.assertIn("Fail to get stock historical data after max retries", str(context.exception))
        mock_stock_hist.assert_called_once()
        mock_sleep.assert_called_once()

    def test_get_financial_indicators_not_implemented(self):
        """Test that get_financial_indicators is not implemented in base class"""
        # This test is to ensure the base class method raises NotImplementedError
        with self.assertRaises(NotImplementedError):
            super(AkshareDataLoader, self.data_loader).get_financial_indicators("000001")


if __name__ == "__main__":
    unittest.main()
