import logging
from datetime import date
import pandas as pd
import akshare as ak
from src.contracts.interfaces import DataSource

logger = logging.getLogger(__name__)


class AkshareSource(DataSource):
    """Remote data source implementation using the Akshare library.

    This class focuses strictly on fetching data from remote APIs.
    Orchestration and caching are handled by the DataLoader.
    """

    def fetch_daily_bars(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """Fetch daily OHLCV data for a specific stock code from Akshare.

        Args:
            code: The stock symbol (e.g., 'SH600000').
            start_date: The beginning of the date range.
            end_date: The end of the date range.

        Returns:
            A pandas DataFrame with index=Date and columns=['open', 'high', 'low', 'close', 'volume'].
        """
        if start_date > end_date:
            return pd.DataFrame()

        logger.info(f"[Remote] Fetching {code} ({start_date} to {end_date})...")

        try:
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
            logger.error(f"Error fetching {code} from Akshare: {e}")
            return pd.DataFrame()

    def fetch_index_daily(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """Fetch daily data for an index from Akshare.

        Args:
            code: The index symbol (e.g., 'sh000300').
            start_date: The beginning of the date range.
            end_date: The end of the date range.

        Returns:
            A pandas DataFrame with index=Date and columns matching stock bars.
        """
        if start_date > end_date:
            return pd.DataFrame()

        logger.info(f"[Remote] Fetching index {code} ({start_date} to {end_date})...")

        try:
            df = ak.stock_zh_index_daily_em(symbol=code)
            df['date'] = pd.to_datetime(df['date']).dt.date
            df.set_index('date', inplace=True)
            mask = (df.index >= start_date) & (df.index <= end_date)
            df = df.loc[mask]
            
            # Ensure columns match expected format if necessary
            # For index data, Akshare usually provides open, high, low, close, volume, etc.
            return df
        except Exception as e:
            logger.error(f"Error fetching index {code} from Akshare: {e}")
            return pd.DataFrame()