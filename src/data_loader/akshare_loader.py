import logging
from datetime import date, timedelta
import pandas as pd
import akshare as ak
from src.contracts.interfaces import DataSource
from src.data_loader.cache import CacheManager
from src.proxy.manager import ProxyManager

logger = logging.getLogger(__name__)

class AkshareLoader(DataSource):
    """Data loader implementation using the Akshare library.

    This loader fetches A-share market data and indices, incorporating
    local caching via CacheManager and proxy rotation via ProxyManager.
    """
    def __init__(self):
        """Initializes the AkshareLoader with cache and proxy managers."""
        self.cache = CacheManager()
        self.proxy_manager = ProxyManager()

    def _fetch_remote_and_save(self, code: str, start_date: date, end_date: date, is_index: bool = False):
        """Internal helper to download and persist data.

        Args:
            code: The security or index symbol.
            start_date: Beginning of the fetch range.
            end_date: End of the fetch range.
            is_index: Whether the symbol represents an index.
        """
        if start_date > end_date:
            return

        # Check if this range (day by day) has already been marked as empty to avoid partial re-fetching
        # For simplicity in this implementation, we fetch the range and then mark the missing ones
        
        logger.info(f"[Remote] Fetching {'index ' if is_index else ''}{code} ({start_date} to {end_date})...")
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
                
                if df is None or df.empty: 
                    # If whole range is empty, mark all dates in range as empty
                    curr = start_date
                    empty_list = []
                    while curr <= end_date:
                        empty_list.append(curr)
                        curr += timedelta(days=1)
                    self.cache.save_empty_dates(code, empty_list)
                    return
                
                df.rename(columns={
                    '日期': 'date', '开盘': 'open', '最高': 'high', '最低': 'low', '收盘': 'close', '成交量': 'volume'
                }, inplace=True)
                df['date'] = pd.to_datetime(df['date']).dt.date
                df.set_index('date', inplace=True)
            
            if not df.empty:
                self.cache.save_daily_bars(code, df)
                
                # Mark missing dates within the requested range as empty
                fetched_dates = set(df.index)
                curr = start_date
                empty_list = []
                while curr <= end_date:
                    if curr not in fetched_dates:
                        empty_list.append(curr)
                    curr += timedelta(days=1)
                if empty_list:
                    self.cache.save_empty_dates(code, empty_list)
            else:
                # Range returned empty DataFrame
                curr = start_date
                empty_list = []
                while curr <= end_date:
                    empty_list.append(curr)
                    curr += timedelta(days=1)
                self.cache.save_empty_dates(code, empty_list)

        except Exception as e:
            logger.error(f"Error fetching {code}: {e}")

    def get_daily_bars(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """Retrieves daily bar data, checking cache (data + empty) first.

        Args:
            code: The stock symbol.
            start_date: The start date for the data range.
            end_date: The end date for the data range.

        Returns:
            A pandas DataFrame containing OHLCV data.
        """
        # 1. Try loading from cache
        cached_df = self.cache.load_daily_bars(code, start_date, end_date)
        empty_dates = set(self.cache.load_empty_dates(code, start_date, end_date))
        
        # Helper to find missing segments
        def find_gaps(s: date, e: date, existing_df: pd.DataFrame, empties: set):
            gaps = []
            curr = s
            gap_start = None
            
            while curr <= e:
                has_data = curr in existing_df.index
                is_known_empty = curr in empties
                
                if not has_data and not is_known_empty:
                    if gap_start is None:
                        gap_start = curr
                else:
                    if gap_start is not None:
                        gaps.append((gap_start, curr - timedelta(days=1)))
                        gap_start = None
                curr += timedelta(days=1)
            
            if gap_start is not None:
                gaps.append((gap_start, e))
            return gaps

        gaps = find_gaps(start_date, end_date, cached_df, empty_dates)
        
        for g_start, g_end in gaps:
            self._fetch_remote_and_save(code, g_start, g_end)
        
        # 4. Return final result from cache
        final_df = self.cache.load_daily_bars(code, start_date, end_date)
        if not final_df.empty:
            logger.info(f"[Cache] Successfully loaded {code} ({start_date} - {end_date})")
        return final_df

    def get_index_daily(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """Retrieves daily index data with incremental loading.

        Args:
            code: The index symbol.
            start_date: The start date for the data range.
            end_date: The end date for the data range.

        Returns:
            A pandas DataFrame containing index data.
        """
        cached_df = self.cache.load_daily_bars(code, start_date, end_date)
        empty_dates = set(self.cache.load_empty_dates(code, start_date, end_date))

        def find_gaps(s: date, e: date, existing_df: pd.DataFrame, empties: set):
            gaps = []
            curr = s
            gap_start = None
            while curr <= e:
                if curr not in existing_df.index and curr not in empties:
                    if gap_start is None: gap_start = curr
                else:
                    if gap_start is not None:
                        gaps.append((gap_start, curr - timedelta(days=1)))
                        gap_start = None
                curr += timedelta(days=1)
            if gap_start is not None: gaps.append((gap_start, e))
            return gaps

        gaps = find_gaps(start_date, end_date, cached_df, empty_dates)
        for g_start, g_end in gaps:
            self._fetch_remote_and_save(code, g_start, g_end, is_index=True)
        
        return self.cache.load_daily_bars(code, start_date, end_date)
