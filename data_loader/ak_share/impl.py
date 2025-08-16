import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime

from tqdm import tqdm
import constant
import time
import os

# Import logger module
import sys
import os
import random
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from logger.logger import get_logger

# Import proxy module
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from proxy.clash.proxy import ClashConfigParser, ClashController

# Import request hook module
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from utils.request_hook import install_hooks, RequestHookContext, get_random_user_agent

# Import config module
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from config.config_loader import get_config_value

# Create logger instance
logger = get_logger(__name__)


def calculate_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
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


def get_zh_a_stock_list():
    stock_info = ak.stock_info_a_code_name()
    return stock_info


def get_zh_a_stock_history(
    symbol: str,
    name: str,
    period: str,
    adjust: str,
    start_date: str,
    end_date: str,
    retry_times: int = 3,
    sleep_seconds: float = 0.2,
) -> pd.DataFrame:
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
            df = calculate_technical_indicators(df)
            return df
        except Exception as e:
            logger.error(
                f"Failed to get stock historical data. code={symbol}, name={name}, retry_time={retry_time}. err={str(e)}"
            )
            continue
    raise Exception(
        f"Failed to get stock historical data after max retries. code={symbol}, name={name}, retry_time={retry_times}."
    )


def get_zh_a_stock_histories(
    stock_list: pd.DataFrame,
    period: str,
    adjust: str,
    start_date: str,
    end_date: str,
    retry_times: int = 3,
    sleep_seconds: float = 0.2,
    proxy_controller = None,
):
    # Ensure stock_data directory exists
    stock_data_path = get_config_value("data_storage.stock_data_path", "stock_data")
    os.makedirs(stock_data_path, exist_ok=True)

    with tqdm(total=len(stock_list), desc="Fetching stock historical data") as pbar:
        for _, (symbol, name) in enumerate(stock_list.values):
            try:
                df = get_zh_a_stock_history(
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

                    # Save file
                    df.to_csv(filename, index=False, encoding="utf_8_sig")

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
                        f"Skipped. symbol={symbol}_name={name} fetched nothing", False
                    )
                pbar.update(1)
            except Exception as e:
                logger.error(
                    f"Failed to get stock historical data. code={symbol}, name={name}, retry_times={retry_times}. err={str(e)}"
                )
                continue


if __name__ == "__main__":
    # Install HTTP request hooks
    install_hooks()

    # Load configuration
    start_date = get_config_value("data_fetching.start_date")
    end_date = get_config_value("data_fetching.end_date")
    retry_times = get_config_value("data_fetching.retry_times")
    sleep_seconds = get_config_value("data_fetching.sleep_seconds")
    stock_limit = get_config_value("data_fetching.stock_limit")

    # Initialize proxy controller
    try:
        config_path = get_config_value("clash.config_path")
        host = get_config_value("clash.host")
        port = get_config_value("clash.port")
        secret = get_config_value("clash.secret")

        # Parse config file to get actual host, port, and secret
        host, port, secret = ClashConfigParser.parse_config(config_path)
        proxy_controller = ClashController(host, port, secret)
        logger.info("Proxy controller initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize proxy controller: {e}")
        proxy_controller = None

    stock_list = get_zh_a_stock_list()
    # For testing purposes, only use the first N stocks
    stock_list = stock_list.head(stock_limit)
    logger.info(f"stock_list=\n{stock_list}")

    # Generate independent CSV file for each stock
    get_zh_a_stock_histories(
        stock_list=stock_list,
        period=constant.PERIOD_DAILY,
        adjust=constant.EN_STOCK_ADJUST_QFQ,
        start_date=start_date,
        end_date=end_date,
        retry_times=retry_times,
        sleep_seconds=sleep_seconds,
        proxy_controller=proxy_controller,
    )

    logger.info("All stock data saved to individual CSV files in stock_data directory")
