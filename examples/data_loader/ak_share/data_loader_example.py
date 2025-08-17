#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Stock Data Loader Example

This example demonstrates how to use the stock data loader to:
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
from data_loader import DataLoader, DataLoaderFactory
from proxy.clash.proxy import ClashConfigParser, ClashController
import data_loader.ak_share.constant as constant


def main():
    """Main function to demonstrate stock data loading"""

    # Initialize configuration loader with examples config
    init_config_loader("examples/config/config.yaml")

    # Load configuration
    start_date = get_config().get("data_loader.start_date")
    end_date = get_config().get("data_loader.end_date")
    retry_times = get_config().get("data_loader.retry_times")
    sleep_seconds = get_config().get("data_loader.sleep_seconds")
    stock_limit = 10

    # Create data loader using factory
    data_loader: DataLoader = DataLoaderFactory.create_data_loader("akshare")

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
    stock_list = data_loader.get_stock_list()
    # For testing purposes, only use the first N stocks
    stock_list = stock_list.head(stock_limit)
    print(f"Stock list:\n{stock_list}")

    # Set proxy controller if available
    if proxy_controller is not None:
        data_loader.set_proxy_controller(proxy_controller)

    # Generate independent CSV file for each stock
    data_loader.get_stock_histories(
        stock_list=stock_list,
        period=constant.PERIOD_DAILY,
        adjust=constant.EN_STOCK_ADJUST_QFQ,
        start_date=start_date,
        end_date=end_date,
        retry_times=retry_times,
        sleep_seconds=sleep_seconds,
    )

    print("All stock data saved to individual CSV files in stock_data directory")

    # Example of getting financial indicators
    print("\n=== Financial Indicators Example ===")
    try:
        financial_data = data_loader.get_financial_indicators(symbol="000001")
        print(f"Financial indicators for 000001:\n{financial_data.head()}")
    except Exception as e:
        print(f"Failed to get financial indicators: {e}")

    # Example of getting shareholder surplus
    print("\n=== Shareholder Surplus Example ===")
    try:
        shareholder_surplus_data = data_loader.get_shareholder_surplus(symbol="000001")
        print(f"Shareholder surplus for 000001:\n{shareholder_surplus_data.head()}")
    except Exception as e:
        print(f"Failed to get shareholder surplus: {e}")


if __name__ == "__main__":
    main()
