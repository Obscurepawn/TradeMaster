from datetime import date
import pandas as pd
import akshare as ak
from src.contracts.interfaces import DataSource
from src.data_loader.cache import CacheManager
from src.proxy.manager import ProxyManager

class AkshareLoader(DataSource):
    def __init__(self):
        self.cache = CacheManager()
        self.proxy_manager = ProxyManager()

    def get_daily_bars(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """
        Fetches daily bars.
        1. Check Cache
        2. If missing, Fetch from Akshare (with Proxy rotation)
        3. Save to Cache
        """
        # 1. Try Cache
        cached_df = self.cache.load_daily_bars(code, start_date, end_date)
        
        # Determine if we have full coverage. 
        # Simplification: If cache has any data, return it. 
        # Ideally we check if [start, end] is fully covered.
        if not cached_df.empty:
            # Check if start and end match roughly
            if cached_df.index[0] <= start_date and cached_df.index[-1] >= end_date:
                print(f"[Cache] Hit {code} ({start_date} - {end_date})")
                return cached_df
            # Partial hit logic is complex. For MVP, if we have data, we assume it's what we need OR we re-fetch.
            # Let's trust cache for now if non-empty, or implement simple gaps later.
            print(f"[Cache] Partial hit {code}, using cached data.")
            return cached_df

        # 2. Fetch Remote
        print(f"[Remote] Fetching {code} from Akshare...")
        self.proxy_manager.rotate_proxy() # T022 Proactive Rotation
        
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")
        
        try:
            symbol = code[-6:] 
            df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_str, end_date=end_str, adjust="qfq")
            
            if df.empty:
                return pd.DataFrame()

            df.rename(columns={
                '日期': 'date',
                '开盘': 'open',
                '最高': 'high',
                '最低': 'low',
                '收盘': 'close',
                '成交量': 'volume'
            }, inplace=True)
            
            df['date'] = pd.to_datetime(df['date']).dt.date
            df.set_index('date', inplace=True)
            df = df[['open', 'high', 'low', 'close', 'volume']]
            
            # 3. Save to Cache
            self.cache.save_daily_bars(code, df)
            
            return df
            
        except Exception as e:
            print(f"Error fetching data for {code}: {e}")
            return pd.DataFrame()

    def get_index_daily(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        return pd.DataFrame() 