import akshare as ak
import pandas as pd
import time
import os
import random
from tqdm import tqdm
from typing import Optional

# Import parent class
import data_loader
from data_loader.data_loader import DataLoader

# Import local modules
from . import constants
from logger.logger import get_logger
from config.config_loader import get_config

logger = get_logger(__name__)


class AkshareDataLoader(DataLoader):

    def __init__(self):
        super().__init__()

    def get_stock_list(self) -> pd.DataFrame:
        """
        Get A-share stock list

        Returns:
            pd.DataFrame: Stock list data
        """
        stock_info = ak.stock_info_a_code_name()
        return stock_info

    def get_stock_history(
        self,
        symbol: str,
        name: str = "",
        period: str = constants.PERIOD_DAILY,
        adjust: str = constants.EN_STOCK_ADJUST_QFQ,
        start_date: str = "",
        end_date: str = "",
        retry_times: int = 3,
        sleep_seconds: float = 1,
    ) -> pd.DataFrame:
        """
        Get historical data for a single stock

        Args:
            symbol (str): Stock symbol
            name (str): Stock name
            period (str): Period, default is "daily", options are "daily", "weekly", "monthly"
            adjust (str): Adjustment type, default is "qfq", options are "qfq", "hfq", "none"
            start_date (str): Start date, format "YYYYMMDD"
            end_date (str): End date, format "YYYYMMDD"
            retry_times (int): Retry times, default 3
            sleep_seconds (float): Sleep seconds between requests, default 0.2

        Returns:
            pd.DataFrame: Stock historical data
        """

        for retry_time in range(retry_times):
            # Add randomization to sleep time (±20%)
            random_sleep = sleep_seconds * random.uniform(0.8, 1.2)
            time.sleep(random_sleep)

            try:
                df = ak.stock_zh_a_hist(
                    symbol=symbol,
                    period=period,
                    start_date=start_date,
                    end_date=end_date,
                    adjust=adjust,
                )
                if df.empty:
                    logger.warning(
                        f"Unable to find historical data for this stock. code={symbol}, name={name}. Skipping."
                    )
                    raise Exception(f"Unable to find historical data for this stock. code={symbol}, name={name}.")
                df[constants.CN_CODE] = symbol
                df[constants.CN_NAME] = name
                df = self._calculate_technical_indicators(df)
                return df
            except Exception as e:
                logger.error(
                    f"Fail to get stock historical data. code={symbol}, name={name}, retry_time={retry_time}. err={str(e)}"
                )
                continue
        raise Exception(
            f"Fail to get stock historical data after max retries. code={symbol}, name={name}, retry_time={retry_times}."
        )

    def get_stock_histories(
        self,
        stock_list: pd.DataFrame,
        period: str = constants.PERIOD_DAILY,
        adjust: str = constants.EN_STOCK_ADJUST_QFQ,
        start_date: str = "",
        end_date: str = "",
        retry_times: int = 3,
        sleep_seconds: float = 1,
    ) -> pd.DataFrame:
        """
        Get historical data for multiple stocks

        Args:
            stock_list (pd.DataFrame): Stock list
            period (str): Period, default is "daily", options are "daily", "weekly", "monthly"
            adjust (str): Adjustment type, default is "qfq", options are "qfq", "hfq", "none"
            start_date (str): Start date, format "YYYYMMDD"
            end_date (str): End date, format "YYYYMMDD"
            retry_times (int): Retry times, default 3
            sleep_seconds (float): Sleep seconds between requests, default 0.2

        Returns:
            pd.DataFrame: Combined stock historical data
        """

        # Ensure stock_data directory exists
        stock_data_path = get_config().get_data_storage_stock_data_path()
        os.makedirs(stock_data_path, exist_ok=True)

        result = pd.DataFrame()
        with tqdm(total=len(stock_list), desc="Fetching stock historical data") as pbar:
            for _, (symbol, name) in enumerate(stock_list.values):
                try:
                    df = self.get_stock_history(
                        symbol=symbol,
                        name=name,
                        period=period,
                        adjust=adjust,
                        start_date=start_date,
                        end_date=end_date,
                        retry_times=retry_times,
                        sleep_seconds=sleep_seconds,
                    )
                    if df is not None:
                        df = df.rename(columns=constants.RENAME_DICT)
                        filename = f"{stock_data_path}/{data_loader.format_stock_filename(symbol, name, start_date, end_date)}"
                        df.to_parquet(path=filename)
                        result = result.merge(df)
                        pbar.set_description(
                            f"Data fetched successfully. symbol={symbol}_name={name}...",
                            False,
                        )
                    else:
                        pbar.set_description(
                            f"Skipped. symbol={symbol}_name={name} fetched nothing",
                            False,
                        )
                    pbar.update(1)
                except Exception as e:
                    logger.error(
                        f"Fail to get stock historical data. code={symbol}, name={name}, retry_times={retry_times}. err={str(e)}"
                    )
                    continue
        return result

    def get_financial_indicators(
        self, symbol: str, indicator: str = constants.FINANCIAL_INDICATOR_BY_REPORT
    ) -> pd.DataFrame:
        """
        Get financial indicators for a stock and save to disk

        Args:
            symbol (str): Stock symbol
            indicator (str): Indicator type, default is "by_report", options are "by_quarter"

        Returns:
            pd.DataFrame: Financial indicators data
        """
        try:
            financial_data = ak.stock_financial_abstract_ths(symbol=symbol)
            if financial_data.empty:
                logger.error(
                    f"No financial indicators found for stock {symbol}. raise exception."
                )
                raise RuntimeError(f"No financial indicators found for stock {symbol}.")
            rename_dict = {
                cn_col: en_col
                for cn_col, en_col in constants.FINANCIAL_INDICATORS_RENAME_DICT.items()
                if cn_col in financial_data.columns
            }
            financial_data = financial_data.rename(columns=rename_dict)

            stock_data_path = get_config().get_data_storage_stock_data_path()
            os.makedirs(stock_data_path, exist_ok=True)
            filename = f"{stock_data_path}/{data_loader.format_stock_financial_abstract_ths_filename(symbol, start_date="", end_date="")}"
            financial_data.to_parquet(path=filename)
            logger.info(f"Saved financial indicators for stock {symbol} to {filename}")
            return financial_data
        except Exception as e:
            logger.error(
                f"Fail to get financial indicators for stock {symbol}: {str(e)}"
            )
            raise

    def _calculate_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate technical indicators

        Args:
            df (pd.DataFrame): Stock data

        Returns:
            pd.DataFrame: Stock data with technical indicators
        """
        df = df.sort_values(by=constants.CN_DATE)
        df.reset_index(drop=True, inplace=True)

        df[constants.MA5] = df[constants.CN_CLOSE].rolling(window=5).mean()
        df[constants.MA10] = df[constants.CN_CLOSE].rolling(window=10).mean()
        df[constants.MA20] = df[constants.CN_CLOSE].rolling(window=20).mean()
        df[constants.MA60] = df[constants.CN_CLOSE].rolling(window=60).mean()

        exp12 = df[constants.CN_CLOSE].ewm(span=12, adjust=False).mean()
        exp26 = df[constants.CN_CLOSE].ewm(span=26, adjust=False).mean()
        df[constants.DIF] = exp12 - exp26
        df[constants.DEA] = df[constants.DIF].ewm(span=9, adjust=False).mean()
        df[constants.MACD] = 2 * (df[constants.DIF] - df[constants.DEA])

        # Calculate RSI
        delta = df[constants.CN_CLOSE].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)

        avg_gain = gain.rolling(window=14).mean()
        avg_loss = loss.rolling(window=14).mean()

        rs = avg_gain / avg_loss
        df[constants.RSI] = 100 - (100 / (1 + rs))

        # Calculate Bollinger Bands
        df[constants.MIDDLE_BAND] = df[constants.CN_CLOSE].rolling(window=20).mean()
        std = df[constants.CN_CLOSE].rolling(window=20).std()
        df[constants.UPPER_BAND] = df[constants.MIDDLE_BAND] + (std * 2)
        df[constants.LOWER_BAND] = df[constants.MIDDLE_BAND] - (std * 2)

        # Calculate volume moving averages
        df[constants.VMA5] = df[constants.CN_VOLUME].rolling(window=5).mean()
        df[constants.VMA10] = df[constants.CN_VOLUME].rolling(window=10).mean()

        low_min = df[constants.CN_LOW].rolling(window=9).min()
        high_max = df[constants.CN_HIGH].rolling(window=9).max()
        rsv = (df[constants.CN_CLOSE] - low_min) / (high_max - low_min) * 100
        df[constants.K] = rsv.ewm(com=2).mean()
        df[constants.D] = df[constants.K].ewm(com=2).mean()
        df[constants.J] = 3 * df[constants.K] - 2 * df[constants.D]

        df[constants.BIAS5] = (
            (df[constants.CN_CLOSE] - df[constants.MA5]) / df[constants.MA5] * 100
        )
        df[constants.BIAS10] = (
            (df[constants.CN_CLOSE] - df[constants.MA10]) / df[constants.MA10] * 100
        )

        return df
