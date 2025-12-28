import logging
import duckdb
import pandas as pd
from datetime import date
from typing import List
import os

logger = logging.getLogger(__name__)


class CacheManager:
    """Manages local storage of market data using DuckDB.

    This class handles database connection, schema initialization, and
    CRUD operations for daily bar data.

    Attributes:
        db_path: Filesystem path to the DuckDB database file.
        conn: The active DuckDB connection object.
    """

    def __init__(self, db_path: str = "data/market_data.duckdb"):
        """Initializes CacheManager and opens database connection.

        Args:
            db_path: Path to the .duckdb file.
        """
        self.db_path = db_path
        if not os.path.exists("data"):
            os.makedirs("data")

        self.conn = duckdb.connect(self.db_path)
        self.init_schema()

    def init_schema(self):
        """Creates the necessary tables if they do not already exist."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_bars (
                code VARCHAR,
                date DATE,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume DOUBLE,
                PRIMARY KEY (code, date)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS empty_dates (
                code VARCHAR,
                date DATE,
                PRIMARY KEY (code, date)
            )
        """)

    def load_daily_bars(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """Queries the database for bar data in a specific date range.

        Args:
            code: The security symbol.
            start_date: Beginning of the range (inclusive).
            end_date: End of the range (inclusive).

        Returns:
            A pandas DataFrame with the results.
        """
        query = """
            SELECT date, open, high, low, close, volume 
            FROM daily_bars 
            WHERE code = ? AND date >= ? AND date <= ?
            ORDER BY date
        """
        try:
            df = self.conn.execute(
                query, [code, start_date, end_date]).fetchdf()
            if not df.empty:
                df['date'] = pd.to_datetime(df['date']).dt.date
                df.set_index('date', inplace=True)
            return df
        except Exception as e:
            logger.error(f"Cache Load Error: {e}")
            return pd.DataFrame()

    def load_empty_dates(self, code: str, start_date: date, end_date: date) -> List[date]:
        """Loads dates known to have no data for a specific security.

        Args:
            code: The security symbol.
            start_date: Beginning of the range.
            end_date: End of the range.

        Returns:
            A list of date objects.
        """
        query = "SELECT date FROM empty_dates WHERE code = ? AND date >= ? AND date <= ?"
        try:
            res = self.conn.execute(
                query, [code, start_date, end_date]).fetchall()
            return [r[0].date() if hasattr(r[0], 'date') else r[0] for r in res]
        except Exception as e:
            logger.error(f"Empty Dates Load Error: {e}")
            return []

    def save_empty_dates(self, code: str, dates: List[date]):
        """Records dates that have been confirmed to have no market data.

        Args:
            code: The security symbol.
            dates: List of dates to record as empty.
        """
        if not dates:
            return

        try:
            for d in dates:
                self.conn.execute(
                    "INSERT OR IGNORE INTO empty_dates (code, date) VALUES (?, ?)",
                    [code, d]
                )
        except Exception as e:
            logger.error(f"Empty Dates Save Error: {e}")

    def save_daily_bars(self, code: str, df: pd.DataFrame):
        """Persists bar data to the database, ignoring duplicates.

        Args:
            code: The security symbol.
            df: A pandas DataFrame containing OHLCV data with a date index.
        """
        if df.empty:
            return

        # Add code column
        df_to_save = df.reset_index().copy()
        df_to_save['code'] = code

        # DuckDB append
        try:
            self.conn.register('temp_df', df_to_save)
            self.conn.execute("""
                INSERT OR IGNORE INTO daily_bars 
                SELECT code, date, open, high, low, close, volume FROM temp_df
            """)
            self.conn.unregister('temp_df')
        except Exception as e:
            logger.error(f"Cache Save Error: {e}")

    def close(self):
        """Closes the DuckDB connection."""
        self.conn.close()
