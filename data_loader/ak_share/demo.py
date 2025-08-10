import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime

from tqdm import tqdm
import constant
import time
import os


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

    # 计算RSI
    delta = df[constant.CN_CLOSE].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()

    rs = avg_gain / avg_loss
    df["RSI"] = 100 - (100 / (1 + rs))

    # 计算布林带
    df["Middle_Band"] = df[constant.CN_CLOSE].rolling(window=20).mean()
    std = df[constant.CN_CLOSE].rolling(window=20).std()
    df["Upper_Band"] = df["Middle_Band"] + (std * 2)
    df["Lower_Band"] = df["Middle_Band"] - (std * 2)

    # 计算成交量均线
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
        time.sleep(sleep_seconds)
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period=period,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
            if df.empty:
                print(
                    f"unable to find historical data for this stock code={symbol}, name={name}. skip it"
                )
                return None
            df[constant.CN_CODE] = symbol
            df[constant.CN_NAME] = name
            df = calculate_technical_indicators(df)
            return df
        except Exception as e:
            print(
                f"fail to get stock historical data. code={symbol},name={name}, retry_time={retry_time}. err={str(e)}"
            )
            continue
    raise Exception(
        f"failed to get stock historical data after max retries. code={symbol}, name={name}, retry_time={retry_times}."
    )


def get_zh_a_stock_histories(
    stock_list: pd.DataFrame,
    period: str,
    adjust: str,
    start_date: str,
    end_date: str,
    retry_times: int = 3,
    sleep_seconds: float = 0.2,
) -> pd.DataFrame:
    collector = pd.DataFrame()
    with tqdm(total=len(stock_list), desc="fetch stock historical data") as pbar:
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
                    collector = pd.concat([collector, df], ignore_index=True)
                    pbar.set_description(
                        f"fetch data successfully. symbol={symbol}_name={name}..."
                    )
                else:
                    pbar.set_description(
                        f"skip. symbol={symbol}_name={name} fetch nothing"
                    )
                pbar.update(1)
            except Exception as e:
                print(
                    f"fail to get stock historical data. code={symbol}, name={name}, retry_times={retry_times}. err={str(e)}"
                )
                continue
    return collector


if __name__ == "__main__":
    start_date = "20230807"
    end_date = "20250811"
    retry_times = 32
    sleep_seconds = 3

    stock_list = get_zh_a_stock_list()
    print(f"stock_list={stock_list}")

    all_data = get_zh_a_stock_histories(
        stock_list=stock_list,
        period=constant.PERIOD_DAILY,
        adjust=constant.EN_STOCK_ADJUST_QFQ,
        start_date=start_date,
        end_date=end_date,
        retry_times=retry_times,
        sleep_seconds=sleep_seconds,
    ).rename(columns=constant.RENAME_DICT)

    if not all_data.empty:
        os.makedirs("stock_data", exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"stock_data/AShareData_{timestamp}.csv"

        # save files
        all_data.to_csv(filename, index=False, encoding="utf_8_sig")
        print(f"file_saved_path={filename}")
        print(f"total_records_num={len(all_data)}")
    else:
        print("fetch empty data, no file saved.")
