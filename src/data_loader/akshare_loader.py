import logging
from datetime import date, timedelta
import pandas as pd
import akshare as ak
from src.contracts.interfaces import DataSource
from src.data_loader.cache import CacheManager

logger = logging.getLogger(__name__)


class AkshareLoader(DataSource):
    """Data loader implementation using the Akshare library.

    This loader fetches A-share market data and indices, incorporating
    local caching via CacheManager. Remote requests are automatically 
    protected by the global anti-scraping hook.
    """

    def __init__(self, use_cache: bool = True):
        """Initializes the AkshareLoader.

        Args:
            use_cache: Whether to use local DuckDB cache.
        """
        self.cache = CacheManager()
        self.use_cache = use_cache

    def _fetch_remote(self, code: str, start_date: date, end_date: date, is_index: bool = False) -> pd.DataFrame:
        """Internal helper to download data without saving to cache.

        Args:
            code: The security or index symbol.
            start_date: Beginning of the fetch range.
            end_date: End of the fetch range.
            is_index: Whether the symbol represents an index.

        Returns:
            A pandas DataFrame containing OHLCV data.
        """
        if start_date > end_date:
            return pd.DataFrame()

        logger.info(f"[Remote] Fetching {'index ' if is_index else ''}{code} ({start_date} to {end_date})...")

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
                    return pd.DataFrame()

                df.rename(columns={
                    '日期': 'date', '开盘': 'open', '最高': 'high', '最低': 'low', '收盘': 'close', '成交量': 'volume'
                }, inplace=True)
                df['date'] = pd.to_datetime(df['date']).dt.date
                df.set_index('date', inplace=True)
            return df
        except Exception as e:
            logger.error(f"Error fetching {code}: {e}")
            return pd.DataFrame()

    def _fetch_remote_and_save(self, code: str, start_date: date, end_date: date, is_index: bool = False) -> pd.DataFrame:
        """Download and optionally persist data to cache."""
        df = self._fetch_remote(code, start_date, end_date, is_index)

        if not self.use_cache:
            return df

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
            # Mark whole range as empty
            curr = start_date
            empty_list = []
            while curr <= end_date:
                empty_list.append(curr)
                curr += timedelta(days=1)
            self.cache.save_empty_dates(code, empty_list)

        return df

    def get_daily_bars(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """Retrieves daily bar data, checking cache (data + empty) first if enabled."""
        if not self.use_cache:
            return self._fetch_remote(code, start_date, end_date)

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
        return self.cache.load_daily_bars(code, start_date, end_date)

    def get_index_daily(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """Retrieves daily index data with incremental loading."""
        if not self.use_cache:
            return self._fetch_remote(code, start_date, end_date, is_index=True)

        cached_df = self.cache.load_daily_bars(code, start_date, end_date)
        empty_dates = set(self.cache.load_empty_dates(code, start_date, end_date))

        def find_gaps(s: date, e: date, existing_df: pd.DataFrame, empties: set):
            gaps = []
            curr = s
            gap_start = None
            while curr <= e:
                if curr not in existing_df.index and curr not in empties:
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
            self._fetch_remote_and_save(code, g_start, g_end, is_index=True)

        return self.cache.load_daily_bars(code, start_date, end_date)
