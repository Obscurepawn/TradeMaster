import unittest
from unittest.mock import patch, MagicMock
import sys
import os
import pandas as pd

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from data_loader.data_loader_factory import DataLoaderFactory


class TestFinancialIndicators(unittest.TestCase):
    """Test cases for financial indicators and shareholder surplus"""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create sample financial data for testing
        self.sample_financial_data = pd.DataFrame(
            {
                "报告期": ["2023-12-31", "2022-12-31", "2021-12-31"],
                "净利润": [1000000, 900000, 800000],
                "净利润同比增长率": [11.11, 12.5, 14.29],
                "营业总收入": [10000000, 9000000, 8000000],
                "每股净资产": [5.5, 5.2, 4.9],
                "净资产收益率": [15.5, 14.8, 13.9],
            }
        )

        # Create sample valuation data for testing
        self.sample_valuation_data = pd.DataFrame(
            {
                "date": ["2023-01-01", "2023-01-02", "2023-01-03"],
                "value": [1000000, 1050000, 1100000],
            }
        )

        # Create sample stock list
        self.sample_stock_list = pd.DataFrame(
            {"code": ["000001", "000002"], "name": ["平安银行", "万科A"]}
        )

    @patch("data_loader.ak_share.akshare_data_loader.ak.stock_financial_abstract_ths")
    def test_get_financial_indicators(self, mock_financial_data):
        """Test getting financial indicators"""
        mock_financial_data.return_value = self.sample_financial_data

        # Create data loader
        data_loader = DataLoaderFactory.create_data_loader("akshare")

        # Test the method
        result = data_loader.get_financial_indicators(symbol="000001")

        # Verify the result
        pd.testing.assert_frame_equal(result, self.sample_financial_data)
        mock_financial_data.assert_called_once_with(symbol="000001")

    @patch("data_loader.ak_share.akshare_data_loader.ak.stock_financial_abstract_ths")
    def test_get_financial_indicators_exception(self, mock_financial_data):
        """Test getting financial indicators with exception"""
        mock_financial_data.side_effect = Exception("Network error")

        # Create data loader
        data_loader = DataLoaderFactory.create_data_loader("akshare")

        # Test the method raises exception
        with self.assertRaises(Exception) as context:
            data_loader.get_financial_indicators(symbol="000001")

        self.assertIn("Network error", str(context.exception))
        mock_financial_data.assert_called_once_with(symbol="000001")

    @patch("data_loader.ak_share.akshare_data_loader.ak.stock_financial_report_sina")
    def test_get_shareholder_surplus(self, mock_report_sina):
        """Test getting shareholder surplus"""
        # Create sample cash flow data
        sample_cashflow_data = pd.DataFrame(
            {
                "报表日期": ["2023-12-31", "2022-12-31", "2021-12-31"],
                "购建固定资产、无形资产和其他长期资产支付的现金": [200000, 180000, 160000],
            }
        )

        # Create sample income data
        sample_income_data = pd.DataFrame(
            {
                "报表日期": ["2023-12-31", "2022-12-31", "2021-12-31"],
                "净利润": [800000, 700000, 600000],
                "资产减值准备": [50000, 45000, 40000],
                "固定资产折旧、油气资产折耗、生产性生物资产折旧": [100000, 90000, 80000],
            }
        )

        # Setup mock to return different data based on the symbol parameter
        mock_report_sina.side_effect = [sample_cashflow_data, sample_income_data]

        # Create data loader
        data_loader = DataLoaderFactory.create_data_loader("akshare")

        # Test the method
        result = data_loader.get_shareholder_surplus(symbol="000001")

        # Verify the result
        expected_data = pd.DataFrame(
            {
                "report_date": ["2023-12-31", "2022-12-31", "2021-12-31"],
                "shareholder_surplus": [75.0, 65.5, 56.0]  # (800000 + (50000+100000) - 200000)/10000 = 75.0, etc.
            }
        )
        pd.testing.assert_frame_equal(result.reset_index(drop=True), expected_data)

    @patch("data_loader.ak_share.akshare_data_loader.ak.stock_financial_report_sina")
    def test_get_shareholder_surplus_exception(self, mock_report_sina):
        """Test getting shareholder surplus with exception"""
        mock_report_sina.side_effect = Exception("Network error")

        # Create data loader
        data_loader = DataLoaderFactory.create_data_loader("akshare")

        # Test the method raises exception
        with self.assertRaises(Exception) as context:
            data_loader.get_shareholder_surplus(symbol="000001")

        self.assertIn("Network error", str(context.exception))



if __name__ == "__main__":
    unittest.main()
