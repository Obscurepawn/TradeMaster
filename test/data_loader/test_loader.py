import unittest
from unittest.mock import MagicMock, patch
from datetime import date
import pandas as pd
from src.data_loader.akshare_loader import AkshareLoader

class TestAkshareLoader(unittest.TestCase):
    def setUp(self):
        # We need to patch CacheManager and ProxyManager inside AkshareLoader
        self.cache_patcher = patch('src.data_loader.akshare_loader.CacheManager')
        self.proxy_patcher = patch('src.data_loader.akshare_loader.ProxyManager')
        
        self.mock_cache_cls = self.cache_patcher.start()
        self.mock_proxy_cls = self.proxy_patcher.start()
        
        self.loader = AkshareLoader()
        
    def tearDown(self):
        self.cache_patcher.stop()
        self.proxy_patcher.stop()

    def test_load_bars_cache_hit(self):
        # Setup Cache Hit
        mock_df = pd.DataFrame({'close': [10.0]}, index=[date(2023, 1, 1)])
        self.loader.cache.load_daily_bars.return_value = mock_df
        
        df = self.loader.get_daily_bars("sh600000", date(2023, 1, 1), date(2023, 1, 1))
        
        self.assertFalse(df.empty)
        self.assertEqual(df.iloc[0]['close'], 10.0)
        # Verify remote NOT called
        # We can't easily verify Akshare NOT called without patching akshare itself, 
        # but we can verify cache.load was called.
        self.loader.cache.load_daily_bars.assert_called_once()

    @patch('src.data_loader.akshare_loader.ak')
    def test_load_bars_cache_miss(self, mock_ak):
        # Setup Cache Miss
        self.loader.cache.load_daily_bars.return_value = pd.DataFrame()
        
        # Setup Akshare return
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
        
        df = self.loader.get_daily_bars("sh600000", date(2023, 1, 1), date(2023, 1, 1))
        
        self.assertFalse(df.empty)
        self.assertEqual(df.iloc[0]['close'], 10.5)
        
        # Verify Proxy Rotated
        self.loader.proxy_manager.rotate_proxy.assert_called_once()
        # Verify Cache Saved
        self.loader.cache.save_daily_bars.assert_called_once()

if __name__ == '__main__':
    unittest.main()