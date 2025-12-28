import unittest
from unittest.mock import MagicMock, patch
from datetime import date
import pandas as pd
from src.data_loader.base import DataLoader
from src.data_loader.akshare_loader import AkshareSource
from src.data_loader.cache import CacheManager


class TestDataLoader(unittest.TestCase):
    def setUp(self):
        self.mock_source = MagicMock(spec=AkshareSource)
        self.mock_cache = MagicMock(spec=CacheManager)
        self.loader = DataLoader(self.mock_source, self.mock_cache)

    def test_get_bars_cache_hit(self):
        # Setup Cache Hit
        mock_df = pd.DataFrame({'close': [10.0]}, index=[date(2023, 1, 1)])
        self.mock_cache.load_daily_bars.return_value = mock_df
        self.mock_cache.load_empty_dates.return_value = []

        df = self.loader.get_daily_bars("sh600000", date(2023, 1, 1), date(2023, 1, 1))

        self.assertFalse(df.empty)
        self.assertEqual(df.iloc[0]['close'], 10.0)
        # Verify remote NOT called
        self.mock_source.fetch_daily_bars.assert_not_called()
        self.assertGreaterEqual(self.mock_cache.load_daily_bars.call_count, 1)

    def test_get_bars_cache_miss(self):
        # Setup Cache Miss
        self.mock_cache.load_daily_bars.side_effect = [pd.DataFrame(), pd.DataFrame({'close': [10.5]}, index=[date(2023, 1, 1)])]
        self.mock_cache.load_empty_dates.return_value = []
        
        # Setup Source return
        mock_source_df = pd.DataFrame({'close': [10.5]}, index=[date(2023, 1, 1)])
        self.mock_source.fetch_daily_bars.return_value = mock_source_df

        df = self.loader.get_daily_bars("sh600000", date(2023, 1, 1), date(2023, 1, 1))

        self.assertFalse(df.empty)
        self.assertEqual(df.iloc[0]['close'], 10.5)
        
        # Verify Source called
        self.mock_source.fetch_daily_bars.assert_called_once()
        # Verify Cache saved
        self.mock_cache.save_daily_bars.assert_called_once()


class TestAkshareSource(unittest.TestCase):
    @patch('src.data_loader.akshare_loader.ak')
    def test_fetch_daily_bars(self, mock_ak):
        source = AkshareSource()
        
        mock_ak_df = pd.DataFrame({
            '日期': ['2023-01-01'],
            '开盘': [10.0],
            '最高': [11.0],
            '最低': [9.0],
            '收盘': [10.5],
            '成交量': [100]
        })
        mock_ak.stock_zh_a_hist.return_value = mock_ak_df
        
        df = source.fetch_daily_bars("sh600000", date(2023, 1, 1), date(2023, 1, 1))
        
        self.assertFalse(df.empty)
        self.assertEqual(df.iloc[0]['close'], 10.5)
        self.assertIn('close', df.columns)


if __name__ == '__main__':
    unittest.main()