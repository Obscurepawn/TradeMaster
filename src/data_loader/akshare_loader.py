from datetime import date, timedelta
import pandas as pd
import akshare as ak
from src.contracts.interfaces import DataSource
from src.data_loader.cache import CacheManager
from src.proxy.manager import ProxyManager

class AkshareLoader(DataSource):
    def __init__(self):
        self.cache = CacheManager()
        self.proxy_manager = ProxyManager()

    def _fetch_remote_and_save(self, code: str, start_date: date, end_date: date, is_index: bool = False):
        """内部辅助方法：下载并保存数据"""
        if start_date > end_date:
            return

        print(f"[Remote] Fetching {'index ' if is_index else ''}{code} ({start_date} to {end_date})...")
        self.proxy_manager.rotate_proxy()
        
        try:
            if is_index:
                df = ak.stock_zh_index_daily_em(symbol=code)
                df['date'] = pd.to_datetime(df['date']).dt.date
                df.set_index('date', inplace=True)
                mask = (df.index >= start_date) & (df.index <= end_date)
                df = df.loc[mask]
            else:
                symbol = code[-6:]
                start_str = start_date.strftime("%Y%m%d")
                end_str = end_date.strftime("%Y%m%d")
                df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_str, end_date=end_str, adjust="qfq")
                
                if df.empty: return
                
                df.rename(columns={
                    '日期': 'date', '开盘': 'open', '最高': 'high', '最低': 'low', '收盘': 'close', '成交量': 'volume'
                }, inplace=True)
                df['date'] = pd.to_datetime(df['date']).dt.date
                df.set_index('date', inplace=True)
            
            if not df.empty:
                self.cache.save_daily_bars(code, df)
        except Exception as e:
            print(f"Error fetching {code}: {e}")

    def get_daily_bars(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        # 1. 尝试从缓存加载
        cached_df = self.cache.load_daily_bars(code, start_date, end_date)
        
        if cached_df.empty:
            # 全量下载
            self._fetch_remote_and_save(code, start_date, end_date)
        else:
            cache_min = cached_df.index.min()
            cache_max = cached_df.index.max()
            
            # 2. 检查并填充前缀 (如果请求的开始日期更早)
            if start_date < cache_min:
                self._fetch_remote_and_save(code, start_date, cache_min - timedelta(days=1))
            
            # 3. 检查并填充后缀 (如果请求的结束日期更晚)
            if end_date > cache_max:
                self._fetch_remote_and_save(code, cache_max + timedelta(days=1), end_date)
        
        # 4. 从缓存返回最终结果 (包含新补全的数据)
        final_df = self.cache.load_daily_bars(code, start_date, end_date)
        if not final_df.empty:
            print(f"[Cache] Successfully loaded {code} ({start_date} - {end_date})")
        return final_df

    def get_index_daily(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        # 同样的增量逻辑应用到指数获取
        cached_df = self.cache.load_daily_bars(code, start_date, end_date)
        
        if cached_df.empty:
            self._fetch_remote_and_save(code, start_date, end_date, is_index=True)
        else:
            cache_min = cached_df.index.min()
            cache_max = cached_df.index.max()
            if start_date < cache_min:
                self._fetch_remote_and_save(code, start_date, cache_min - timedelta(days=1), is_index=True)
            if end_date > cache_max:
                self._fetch_remote_and_save(code, cache_max + timedelta(days=1), end_date, is_index=True)
        
        return self.cache.load_daily_bars(code, start_date, end_date)
