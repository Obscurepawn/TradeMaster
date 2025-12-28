import duckdb
import pandas as pd
from datetime import date
import os

class CacheManager:
    def __init__(self, db_path: str = "data/market_data.duckdb"):
        self.db_path = db_path
        if not os.path.exists("data"):
            os.makedirs("data")
            
        self.conn = duckdb.connect(self.db_path)
        self.init_schema()

    def init_schema(self):
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

    def load_daily_bars(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        query = """
            SELECT date, open, high, low, close, volume 
            FROM daily_bars 
            WHERE code = ? AND date >= ? AND date <= ?
            ORDER BY date
        """
        try:
            df = self.conn.execute(query, [code, start_date, end_date]).fetchdf()
            if not df.empty:
                df['date'] = pd.to_datetime(df['date']).dt.date
                df.set_index('date', inplace=True)
            return df
        except Exception as e:
            print(f"Cache Load Error: {e}")
            return pd.DataFrame()

    def save_daily_bars(self, code: str, df: pd.DataFrame):
        if df.empty:
            return
            
        # Add code column
        df_to_save = df.reset_index().copy()
        df_to_save['code'] = code
        # Ensure columns match schema order/names
        # Assuming df has 'date' from reset_index, and OHLCV columns
        
        # DuckDB append
        try:
            self.conn.register('temp_df', df_to_save)
            self.conn.execute("""
                INSERT OR IGNORE INTO daily_bars 
                SELECT code, date, open, high, low, close, volume FROM temp_df
            """)
            self.conn.unregister('temp_df')
        except Exception as e:
            print(f"Cache Save Error: {e}")

    def close(self):
        self.conn.close()
