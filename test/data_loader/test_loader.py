import unittest
from unittest.mock import patch
from datetime import date
import pandas as pd
from src.data_loader.akshare_loader import AkshareLoader


class TestAkshareLoader(unittest.TestCase):
    def setUp(self):
        # We need to patch CacheManager inside AkshareLoader
        self.cache_patcher = patch(
            'src.data_loader.akshare_loader.CacheManager')

        self.mock_cache_cls = self.cache_patcher.start()

        self.loader = AkshareLoader()

    def tearDown(self):
        self.cache_patcher.stop()

    def test_load_bars_cache_hit(self):
        # Setup Cache Hit
        mock_df = pd.DataFrame({'close': [10.0]}, index=[date(2023, 1, 1)])
        self.loader.cache.load_daily_bars.return_value = mock_df

        df = self.loader.get_daily_bars(
            "sh600000", date(2023, 1, 1), date(2023, 1, 1))

        self.assertFalse(df.empty)
        self.assertEqual(df.iloc[0]['close'], 10.0)
        # Verify cache.load was called
        self.assertGreaterEqual(
            self.loader.cache.load_daily_bars.call_count, 1)

    @patch('src.data_loader.akshare_loader.ak')
    def test_load_bars_cache_miss(self, mock_ak):
        # Setup Cache Miss for first call, Hit for second call
        mock_df = pd.DataFrame({'close': [10.5]}, index=[date(2023, 1, 1)])
        self.loader.cache.load_daily_bars.side_effect = [
            pd.DataFrame(), mock_df]
        self.loader.cache.load_empty_dates.return_value = []
        # Mocking the return of stock_zh_a_hist
        mock_ak_df = pd.DataFrame({
            '日期': ['2023-01-01'],
            '开盘': [10.0],
            '最高': [11.0],
            '最低': [9.0],
            '收盘': [10.5],
            '成交量': [100]
        })
        mock_ak.stock_zh_a_hist.return_value = mock_ak_df

        df = self.loader.get_daily_bars(
            "sh600000", date(2023, 1, 1), date(2023, 1, 1))

        self.assertFalse(df.empty)
        self.assertEqual(df.iloc[0]['close'], 10.5)

        # Verify Cache Saved
        self.loader.cache.save_daily_bars.assert_called_once()


if __name__ == '__main__':
    unittest.main()
