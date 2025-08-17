import unittest
from unittest.mock import patch, MagicMock
import sys
import os
import pandas as pd

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

# Mock the constant module import
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "data_loader", "ak_share"
    ),
)

from data_loader.ak_share.impl import (
    calculate_technical_indicators,
    get_zh_a_stock_list,
    get_zh_a_stock_history,
    get_zh_a_stock_histories,
)


class TestStockDataFetcher(unittest.TestCase):
    """Test cases for stock data fetcher module"""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create sample data for testing
        self.sample_data = pd.DataFrame(
            {
                "日期": [
                    "2023-01-01",
                    "2023-01-02",
                    "2023-01-03",
                    "2023-01-04",
                    "2023-01-05",
                ],
                "开盘": [100.0, 101.0, 102.0, 103.0, 104.0],
                "收盘": [101.0, 102.0, 103.0, 104.0, 105.0],
                "最高": [102.0, 103.0, 104.0, 105.0, 106.0],
                "最低": [99.0, 100.0, 101.0, 102.0, 103.0],
                "成交量": [1000, 1100, 1200, 1300, 1400],
            }
        )

        # Create sample stock list
        self.sample_stock_list = pd.DataFrame(
            {"code": ["000001", "000002"], "name": ["平安银行", "万科A"]}
        )

    def test_calculate_technical_indicators(self):
        """Test calculation of technical indicators"""
        result = calculate_technical_indicators(self.sample_data.copy())

        # Check that new columns are added
        expected_columns = [
            "MA5",
            "MA10",
            "MA20",
            "MA60",
            "DIF",
            "DEA",
            "MACD",
            "RSI",
            "Middle_Band",
            "Upper_Band",
            "Lower_Band",
            "VMA5",
            "VMA10",
            "K",
            "D",
            "J",
            "BIAS5",
            "BIAS10",
        ]

        for column in expected_columns:
            self.assertIn(column, result.columns)

        # Check that data is sorted and reset
        self.assertEqual(result.index[0], 0)
        self.assertEqual(result.index[-1], len(result) - 1)

    @patch("data_loader.ak_share.impl.ak.stock_info_a_code_name")
    def test_get_zh_a_stock_list(self, mock_stock_info):
        """Test getting stock list"""
        mock_stock_info.return_value = self.sample_stock_list

        result = get_zh_a_stock_list()
        pd.testing.assert_frame_equal(result, self.sample_stock_list)
        mock_stock_info.assert_called_once()

    @patch("data_loader.ak_share.impl.ak.stock_zh_a_hist")
    @patch("data_loader.ak_share.impl.time.sleep")
    def test_get_zh_a_stock_history_success(self, mock_sleep, mock_stock_hist):
        """Test successful stock history retrieval"""
        mock_stock_hist.return_value = self.sample_data.copy()

        result = get_zh_a_stock_history(
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

    @patch("data_loader.ak_share.impl.ak.stock_zh_a_hist")
    @patch("data_loader.ak_share.impl.time.sleep")
    def test_get_zh_a_stock_history_empty_data(self, mock_sleep, mock_stock_hist):
        """Test stock history retrieval with empty data"""
        mock_stock_hist.return_value = pd.DataFrame()

        result = get_zh_a_stock_history(
            symbol="000001",
            name="平安银行",
            period="daily",
            adjust="qfq",
            start_date="20230101",
            end_date="20230105",
        )

        self.assertIsNone(result)
        mock_stock_hist.assert_called_once()
        mock_sleep.assert_called_once()

    @patch("data_loader.ak_share.impl.ak.stock_zh_a_hist")
    @patch("data_loader.ak_share.impl.time.sleep")
    def test_get_zh_a_stock_history_exception(self, mock_sleep, mock_stock_hist):
        """Test stock history retrieval with exception"""
        mock_stock_hist.side_effect = Exception("Network error")

        with self.assertRaises(Exception) as context:
            get_zh_a_stock_history(
                symbol="000001",
                name="平安银行",
                period="daily",
                adjust="qfq",
                start_date="20230101",
                end_date="20230105",
                retry_times=1,
            )

        self.assertIn(
            "Failed to get stock historical data after max retries",
            str(context.exception),
        )
        mock_stock_hist.assert_called_once()
        mock_sleep.assert_called_once()

    @patch("data_loader.ak_share.impl.get_zh_a_stock_history")
    @patch("data_loader.ak_share.impl.tqdm")
    def test_get_zh_a_stock_histories_success(self, mock_tqdm, mock_get_history):
        """Test successful retrieval of multiple stock histories"""
        # Mock tqdm to return a simple context manager
        mock_tqdm_instance = MagicMock()
        mock_tqdm.return_value.__enter__.return_value = mock_tqdm_instance

        # Mock get_zh_a_stock_history to return sample data
        mock_get_history.return_value = self.sample_data.copy()

        # Mock proxy controller
        mock_proxy_controller = MagicMock()

        # Create a temporary directory for testing
        import tempfile
        import shutil

        temp_dir = tempfile.mkdtemp()

        try:
            # Ensure the stock_data directory exists
            with patch("data_loader.ak_share.impl.get_config") as mock_get_config:
                mock_instance = MagicMock()
                mock_instance.get.return_value = temp_dir
                mock_get_config.return_value = mock_instance
                # Call the function
                get_zh_a_stock_histories(
                    stock_list=self.sample_stock_list,
                    period="daily",
                    adjust="qfq",
                    start_date="20230101",
                    end_date="20230105",
                    proxy_controller=mock_proxy_controller,
                )

                # Verify calls
                self.assertEqual(mock_get_history.call_count, 2)
                mock_proxy_controller.change_random_proxy.assert_called()
        finally:
            # Clean up temporary directory
            shutil.rmtree(temp_dir)

    @patch("data_loader.ak_share.impl.get_zh_a_stock_history")
    @patch("data_loader.ak_share.impl.tqdm")
    def test_get_zh_a_stock_histories_with_none_result(
        self, mock_tqdm, mock_get_history
    ):
        """Test retrieval of multiple stock histories with None result"""
        # Mock tqdm to return a simple context manager
        mock_tqdm_instance = MagicMock()
        mock_tqdm.return_value.__enter__.return_value = mock_tqdm_instance

        # Mock get_zh_a_stock_history to return None
        mock_get_history.return_value = None

        # Mock proxy controller
        mock_proxy_controller = MagicMock()

        # Create a temporary directory for testing
        import tempfile
        import shutil

        temp_dir = tempfile.mkdtemp()

        try:
            # Ensure the stock_data directory exists
            with patch("data_loader.ak_share.impl.get_config") as mock_get_config:
                mock_instance = MagicMock()
                mock_instance.get.return_value = temp_dir
                mock_get_config.return_value = mock_instance
                # Call the function
                get_zh_a_stock_histories(
                    stock_list=self.sample_stock_list,
                    period="daily",
                    adjust="qfq",
                    start_date="20230101",
                    end_date="20230105",
                    proxy_controller=mock_proxy_controller,
                )

                # Verify calls
                self.assertEqual(mock_get_history.call_count, 2)
        finally:
            # Clean up temporary directory
            shutil.rmtree(temp_dir)

    @patch("data_loader.ak_share.impl.get_zh_a_stock_history")
    @patch("data_loader.ak_share.impl.tqdm")
    def test_get_zh_a_stock_histories_exception_handling(
        self, mock_tqdm, mock_get_history
    ):
        """Test exception handling in multiple stock histories retrieval"""
        # Mock tqdm to return a simple context manager
        mock_tqdm_instance = MagicMock()
        mock_tqdm.return_value.__enter__.return_value = mock_tqdm_instance

        # Mock get_zh_a_stock_history to raise an exception
        mock_get_history.side_effect = Exception("Network error")

        # Mock proxy controller
        mock_proxy_controller = MagicMock()

        # Create a temporary directory for testing
        import tempfile
        import shutil

        temp_dir = tempfile.mkdtemp()

        try:
            # Ensure the stock_data directory exists
            with patch("data_loader.ak_share.impl.get_config") as mock_get_config:
                mock_instance = MagicMock()
                mock_instance.get.return_value = temp_dir
                mock_get_config.return_value = mock_instance
                # Call the function - should not raise exception
                get_zh_a_stock_histories(
                    stock_list=self.sample_stock_list,
                    period="daily",
                    adjust="qfq",
                    start_date="20230101",
                    end_date="20230105",
                    proxy_controller=mock_proxy_controller,
                )

                # Verify calls
                self.assertEqual(mock_get_history.call_count, 2)
        finally:
            # Clean up temporary directory
            shutil.rmtree(temp_dir)


if __name__ == "__main__":
    unittest.main()
