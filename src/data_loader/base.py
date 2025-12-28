import logging
from datetime import date, timedelta
from typing import List, Set, Tuple
import pandas as pd
from src.contracts.interfaces import DataSource
from src.data_loader.cache import CacheManager

logger = logging.getLogger(__name__)


class DataLoader:
    """Orchestrator for data loading with caching and gap filling.

    This class coordinates between a CacheManager and a DataSource implementation
    to provide an efficient way to retrieve market data while minimizing remote calls.
    """

    def __init__(self, data_source: DataSource, cache: CacheManager, use_cache: bool = True):
        """Initializes the DataLoader.

        Args:
            data_source: Implementation of the DataSource interface for remote fetching.
            cache: The CacheManager instance for local storage.
            use_cache: Whether to use local disk cache.
        """
        self.data_source = data_source
        self.cache = cache
        self.use_cache = use_cache

    def _find_gaps(self, start_date: date, end_date: date, existing_df: pd.DataFrame, known_empties: Set[date]) -> List[Tuple[date, date]]:
        """Identifies missing segments in the data range.

        Args:
            start_date: Start of the requested range.
            end_date: End of the requested range.
            existing_df: DataFrame containing already cached data.
            known_empties: Set of dates confirmed to have no remote data.

        Returns:
            A list of (gap_start, gap_end) tuples.
        """
        gaps = []
        curr = start_date
        gap_start = None

        while curr <= end_date:
            has_data = curr in existing_df.index
            is_known_empty = curr in known_empties

            if not has_data and not is_known_empty:
                if gap_start is None:
                    gap_start = curr
            else:
                if gap_start is not None:
                    gaps.append((gap_start, curr - timedelta(days=1)))
                    gap_start = None
            curr += timedelta(days=1)

        if gap_start is not None:
            gaps.append((gap_start, end_date))
        return gaps

    def _fill_gaps(self, code: str, start_date: date, end_date: date, is_index: bool = False):
        """Checks for gaps and fills them by fetching from the remote source.

        Args:
            code: The security identifier.
            start_date: Start of the requested range.
            end_date: End of the requested range.
            is_index: Whether the security is an index.
        """
        cached_df = self.cache.load_daily_bars(code, start_date, end_date)
        empty_dates = set(self.cache.load_empty_dates(code, start_date, end_date))

        gaps = self._find_gaps(start_date, end_date, cached_df, empty_dates)

        for g_start, g_end in gaps:
            if is_index:
                df = self.data_source.fetch_index_daily(code, g_start, g_end)
            else:
                df = self.data_source.fetch_daily_bars(code, g_start, g_end)

            if not df.empty:
                self.cache.save_daily_bars(code, df)
                # Mark missing dates within the requested range as empty
                fetched_dates = set(df.index)
                curr = g_start
                empty_list = []
                while curr <= g_end:
                    if curr not in fetched_dates:
                        empty_list.append(curr)
                    curr += timedelta(days=1)
                if empty_list:
                    self.cache.save_empty_dates(code, empty_list)
            else:
                # Mark whole range as empty
                curr = g_start
                empty_list = []
                while curr <= g_end:
                    empty_list.append(curr)
                    curr += timedelta(days=1)
                self.cache.save_empty_dates(code, empty_list)

    def get_daily_bars(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """Retrieves daily bar data, checking cache first if enabled.

        Args:
            code: The stock symbol.
            start_date: Start of the range.
            end_date: End of the range.

        Returns:
            A pandas DataFrame with the bar data.
        """
        if not self.use_cache:
            return self.data_source.fetch_daily_bars(code, start_date, end_date)

        self._fill_gaps(code, start_date, end_date, is_index=False)
        return self.cache.load_daily_bars(code, start_date, end_date)

    def get_index_daily(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """Retrieves daily index data, checking cache first if enabled.

        Args:
            code: The index symbol.
            start_date: Start of the range.
            end_date: End of the range.

        Returns:
            A pandas DataFrame with the index data.
        """
        if not self.use_cache:
            return self.data_source.fetch_index_daily(code, start_date, end_date)

        self._fill_gaps(code, start_date, end_date, is_index=True)
        return self.cache.load_daily_bars(code, start_date, end_date)
