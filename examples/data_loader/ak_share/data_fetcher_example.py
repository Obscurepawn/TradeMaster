#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Stock Data Fetcher Example

This example demonstrates how to use the stock data fetcher to:
1. Get list of Chinese A-share stocks
2. Fetch historical data for stocks
3. Calculate technical indicators
4. Save data to CSV files
5. Use proxy rotation during data fetching
"""

import sys
import os

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from config.config_loader import init_config_loader, get_config
from data_loader.ak_share.impl import (
    get_zh_a_stock_list,
    get_zh_a_stock_histories
)
from proxy.clash.proxy import ClashConfigParser, ClashController
import data_loader.ak_share.constant as constant


def main():
    """Main function to demonstrate stock data fetching"""
    # Install HTTP request hooks
    from utils.request_hook import install_hooks
    install_hooks()

    # Initialize configuration loader with examples config
    init_config_loader("examples/config/config.yaml")

    # Load configuration
    start_date = get_config().get("data_fetching.start_date")
    end_date = get_config().get("data_fetching.end_date")
    retry_times = get_config().get("data_fetching.retry_times")
    sleep_seconds = get_config().get("data_fetching.sleep_seconds")
    stock_limit = get_config().get("data_fetching.stock_limit")

    # Initialize proxy controller
    proxy_controller = None
    try:
        config_path = get_config().get("clash.config_path")
        host, port, secret = ClashConfigParser.parse_config(config_path)
        proxy_controller = ClashController(host, port, secret)
        print("Proxy controller initialized successfully")
    except Exception as e:
        print(f"Failed to initialize proxy controller: {e}")
        proxy_controller = None

    # Get stock list
    stock_list = get_zh_a_stock_list()
    # For testing purposes, only use the first N stocks
    stock_list = stock_list.head(stock_limit)
    print(f"Stock list:\n{stock_list}")


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

    print("All stock data saved to individual CSV files in stock_data directory")


if __name__ == "__main__":
    main()
