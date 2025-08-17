import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime
import time
import os
import random
from tqdm import tqdm
from typing import Optional

# Import parent class
from data_loader.data_loader import DataLoader

# Import local modules
from . import constant
from logger.logger import get_logger
from proxy.clash.proxy import ClashConfigParser, ClashController
from utils.request_hook import RequestHookContext
from config.config_loader import get_config

logger = get_logger(__name__)


class AkshareDataLoader(DataLoader):
    """Akshare data loader implementation"""

    def __init__(self):
        """Initialize Akshare data loader"""
        super().__init__()

    def get_stock_list(self) -> pd.DataFrame:
        """
        Get A-share stock list

        Returns:
            pd.DataFrame: Stock list data
        """
        stock_info = ak.stock_info_a_code_name()
        return stock_info

    def get_stock_basic_info(self, symbol: str) -> pd.DataFrame:
        """
        Get basic stock information

        Args:
            symbol (str): Stock symbol

        Returns:
            pd.DataFrame: Basic stock information
        """
        try:
            # Get basic information from EastMoney
            basic_info = ak.stock_individual_info_em(symbol=symbol)
            logger.info(f"Retrieved basic info for stock {symbol}")
            return basic_info
        except Exception as e:
            logger.error(f"Failed to get basic info for stock {symbol}: {str(e)}")
            raise

    def get_stock_profile_info(self, symbol: str) -> pd.DataFrame:
        """
        Get stock profile information

        Args:
            symbol (str): Stock symbol

        Returns:
            pd.DataFrame: Stock profile information
        """
        try:
            # Get profile information from CNINFO
            profile_info = ak.stock_profile_cninfo(symbol=symbol)
            logger.info(f"Retrieved profile info for stock {symbol}")
            return profile_info
        except Exception as e:
            logger.error(f"Failed to get profile info for stock {symbol}: {str(e)}")
            raise

    def get_stock_history(self, symbol: str, **kwargs) -> Optional[pd.DataFrame]:
        """
        Get historical data for a single stock

        Args:
            symbol (str): Stock symbol
            **kwargs: Other parameters, including:
                - name (str): Stock name
                - period (str): Period
                - adjust (str): Adjustment type
                - start_date (str): Start date
                - end_date (str): End date
                - retry_times (int): Retry times, default 3
                - sleep_seconds (float): Sleep seconds, default 0.2

        Returns:
            Optional[pd.DataFrame]: Stock historical data, returns None if failed to retrieve
        """
        # 提取参数
        name = kwargs.get("name", "")
        period = kwargs.get("period", "daily")
        adjust = kwargs.get("adjust", "qfq")
        start_date = kwargs.get("start_date", "")
        end_date = kwargs.get("end_date", "")
        retry_times = kwargs.get("retry_times", 3)
        sleep_seconds = kwargs.get("sleep_seconds", 0.2)

        for retry_time in range(retry_times):
            # Add randomization to sleep time (±20%)
            random_sleep = sleep_seconds * random.uniform(0.8, 1.2)
            time.sleep(random_sleep)

            try:
                # Use request hook context to set a random User-Agent for this request
                with RequestHookContext() as ctx:
                    logger.debug(f"Using User-Agent: {ctx.user_agent}")

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
                    return None
                df[constant.CN_CODE] = symbol
                df[constant.CN_NAME] = name
                df = self.calculate_technical_indicators(df)
                return df
            except Exception as e:
                logger.error(
                    f"Failed to get stock historical data. code={symbol}, name={name}, retry_time={retry_time}. err={str(e)}"
                )
                continue
        raise Exception(
            f"Failed to get stock historical data after max retries. code={symbol}, name={name}, retry_time={retry_times}."
        )

    def get_stock_histories(self, stock_list: pd.DataFrame, **kwargs) -> None:
        """
        Get historical data for multiple stocks

        Args:
            stock_list (pd.DataFrame): Stock list
            **kwargs: Other parameters, including:
                - period (str): Period
                - adjust (str): Adjustment type
                - start_date (str): Start date
                - end_date (str): End date
                - retry_times (int): Retry times, default 3
                - sleep_seconds (float): Sleep seconds, default 0.2
                - proxy_controller: Proxy controller
        """
        # 提取参数
        period = kwargs.get("period", "daily")
        adjust = kwargs.get("adjust", "qfq")
        start_date = kwargs.get("start_date", "")
        end_date = kwargs.get("end_date", "")
        retry_times = kwargs.get("retry_times", 3)
        sleep_seconds = kwargs.get("sleep_seconds", 0.2)
        proxy_controller = kwargs.get("proxy_controller", None)

        # Ensure stock_data directory exists
        stock_data_path = get_config().get("data_storage.stock_data_path", "stock_data")
        os.makedirs(stock_data_path, exist_ok=True)

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
                        # Rename columns
                        df = df.rename(columns=constant.RENAME_DICT)

                        # Generate filename (including time range)
                        filename = f"{stock_data_path}/{symbol}_{name}_{start_date}_{end_date}.csv"

                        # Save file without whitespace
                        df.to_csv(
                            filename,
                            index=False,
                            encoding="utf_8_sig",
                            quoting=1,  # Quote all fields
                            quotechar='"',
                            lineterminator="\n",
                            sep=",",
                        )

                        pbar.set_description(
                            f"Data fetched successfully. symbol={symbol}_name={name}...",
                            False,
                        )

                        # Change proxy IP after successfully fetching data
                        if proxy_controller is not None:
                            try:
                                proxy_controller.change_random_proxy()
                            except Exception as e:
                                logger.error(f"Failed to change proxy IP: {e}")
                    else:
                        pbar.set_description(
                            f"Skipped. symbol={symbol}_name={name} fetched nothing",
                            False,
                        )
                    pbar.update(1)
                except Exception as e:
                    logger.error(
                        f"Failed to get stock historical data. code={symbol}, name={name}, retry_times={retry_times}. err={str(e)}"
                    )
                    continue

    def calculate_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate technical indicators

        Args:
            df (pd.DataFrame): Stock data

        Returns:
            pd.DataFrame: Stock data with technical indicators
        """
        df = df.sort_values(by=constant.CN_DATE)
        df.reset_index(drop=True, inplace=True)

        df["MA5"] = df[constant.CN_CLOSE].rolling(window=5).mean()
        df["MA10"] = df[constant.CN_CLOSE].rolling(window=10).mean()
        df["MA20"] = df[constant.CN_CLOSE].rolling(window=20).mean()
        df["MA60"] = df[constant.CN_CLOSE].rolling(window=60).mean()

        exp12 = df[constant.CN_CLOSE].ewm(span=12, adjust=False).mean()
        exp26 = df[constant.CN_CLOSE].ewm(span=26, adjust=False).mean()
        df["DIF"] = exp12 - exp26
        df["DEA"] = df["DIF"].ewm(span=9, adjust=False).mean()
        df["MACD"] = 2 * (df["DIF"] - df["DEA"])

        # Calculate RSI
        delta = df[constant.CN_CLOSE].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)

        avg_gain = gain.rolling(window=14).mean()
        avg_loss = loss.rolling(window=14).mean()

        rs = avg_gain / avg_loss
        df["RSI"] = 100 - (100 / (1 + rs))

        # Calculate Bollinger Bands
        df["Middle_Band"] = df[constant.CN_CLOSE].rolling(window=20).mean()
        std = df[constant.CN_CLOSE].rolling(window=20).std()
        df["Upper_Band"] = df["Middle_Band"] + (std * 2)
        df["Lower_Band"] = df["Middle_Band"] - (std * 2)

        # Calculate volume moving averages
        df["VMA5"] = df[constant.CN_VOLUME].rolling(window=5).mean()
        df["VMA10"] = df[constant.CN_VOLUME].rolling(window=10).mean()

        low_min = df[constant.CN_LOW].rolling(window=9).min()
        high_max = df[constant.CN_HIGH].rolling(window=9).max()
        rsv = (df[constant.CN_CLOSE] - low_min) / (high_max - low_min) * 100
        df["K"] = rsv.ewm(com=2).mean()
        df["D"] = df["K"].ewm(com=2).mean()
        df["J"] = 3 * df["K"] - 2 * df["D"]

        df["BIAS5"] = (df[constant.CN_CLOSE] - df["MA5"]) / df["MA5"] * 100
        df["BIAS10"] = (df[constant.CN_CLOSE] - df["MA10"]) / df["MA10"] * 100

        return df

    def get_financial_indicators(
        self, symbol: str, indicator: str = "by_report"
    ) -> pd.DataFrame:
        """
        Get financial indicators for a stock

        Args:
            symbol (str): Stock symbol
            indicator (str): Indicator type, default is "by_report", options are "by_quarter"

        Returns:
            pd.DataFrame: Financial indicators data
        """
        try:
            # Use THS financial indicators interface
            financial_data = ak.stock_financial_abstract_ths(symbol=symbol)
            logger.info(f"Retrieved financial indicators for stock {symbol}")
            return financial_data
        except Exception as e:
            logger.error(
                f"Failed to get financial indicators for stock {symbol}: {str(e)}"
            )
            raise

    def get_shareholder_surplus(self, symbol: str) -> pd.DataFrame:
        """
        Get shareholder surplus data
        Shareholder surplus = Net profit + Depreciation and amortization - Capital expenditure

        Args:
            symbol (str): Stock symbol

        Returns:
            pd.DataFrame: Shareholder surplus data with columns ['report_date', 'shareholder_surplus']
        """
        try:
            # Get cash flow statement (annual)
            df_cashflow = ak.stock_financial_report_sina(
                stock=symbol,
                symbol=constant.CASH_FLOW_STATEMENT,
                report_type=constant.ANNUAL_REPORT,
            )

            # Extract capital expenditure field
            df_cashflow = df_cashflow[
                [constant.REPORT_DATE, constant.CAPITAL_EXPENDITURE]
            ]
            df_cashflow = df_cashflow.rename(
                columns={constant.CAPITAL_EXPENDITURE: "capital_expenditure"}
            )

            # Get income statement
            df_income = ak.stock_financial_report_sina(
                stock=symbol,
                symbol=constant.INCOME_STATEMENT,
                report_type=constant.ANNUAL_REPORT,
            )

            # Extract net profit and depreciation fields
            df_income = df_income[
                [
                    constant.REPORT_DATE,
                    constant.NET_PROFIT,
                    constant.ASSET_IMPAIRMENT,
                    constant.DEPRECIATION,
                ]
            ]
            df_income["depreciation"] = (
                df_income[constant.ASSET_IMPAIRMENT] + df_income[constant.DEPRECIATION]
            )

            # Merge cash flow statement and income statement
            df_merge = pd.merge(
                df_cashflow,
                df_income[[constant.REPORT_DATE, constant.NET_PROFIT, "depreciation"]],
                on=constant.REPORT_DATE,
            )

            # Calculate shareholder surplus (in 10,000 yuan)
            df_merge["shareholder_surplus"] = (
                df_merge[constant.NET_PROFIT]
                + df_merge["depreciation"]
                - df_merge["capital_expenditure"]
            ) / 10000  # Convert to 10,000 yuan

            # Format output
            result = df_merge[
                [constant.REPORT_DATE, "shareholder_surplus"]
            ].sort_values(constant.REPORT_DATE, ascending=False)
            result = result.rename(columns={constant.REPORT_DATE: "report_date"})

            logger.info(f"Retrieved shareholder surplus for stock {symbol}")
            return result
        except Exception as e:
            logger.error(
                f"Failed to get shareholder surplus for stock {symbol}: {str(e)}"
            )
            raise
