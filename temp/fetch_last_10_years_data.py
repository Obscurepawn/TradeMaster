#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Fetch Last 10 Years Stock Data Script

This script fetches the last 10 years of daily stock data for all A-share stocks
and saves them as individual CSV files.
"""

import sys
import os
from datetime import datetime, timedelta

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from config.config_loader import init_config_loader, get_config
from data_loader.data_loader import DataLoader
from data_loader.data_loader_factory import DataLoaderFactory
from proxy.clash.proxy import ClashConfigParser, ClashController
import data_loader.ak_share.constants as constants


def main():
    """Main function to fetch last 10 years of stock data"""
    # Install HTTP request hooks
    from utils.request_hook import install_user_agent_hooks
    install_user_agent_hooks()

    # Initialize configuration loader with examples config
    init_config_loader("examples/config.yaml")

    # Calculate date range for last 10 years
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365 * 10)

    # Format dates as strings (YYYYMMDD)
    start_date_str = start_date.strftime("%Y%m%d")
    end_date_str = end_date.strftime("%Y%m%d")

    print(f"Fetching stock data from {start_date_str} to {end_date_str}")

    # Load configuration
    retry_times = get_config().get_data_loader_retry_times()
    sleep_seconds = get_config().get_data_loader_sleep_seconds()

    # Create data loader using factory
    data_loader: DataLoader = DataLoaderFactory.create_data_loader("akshare")

    # Initialize proxy controller
    proxy_controller = None
    try:
        config_path = get_config().get_clash_config_path()
        host, port, secret = ClashConfigParser.parse_config(config_path)
        proxy_controller = ClashController(host, port, secret)
        print("Proxy controller initialized successfully")
    except Exception as e:
        print(f"Fail to initialize proxy controller: {e}")
        proxy_controller = None

    # Get stock list
    print("Fetching stock list...")
    stock_list = data_loader.get_stock_list()
    print(f"Found {len(stock_list)} stocks")

    # Fetch data for all stocks (no limit)
    print("Fetching historical data for all stocks...")
    data_loader.get_stock_histories(
        stock_list=stock_list,
        period=constants.PERIOD_DAILY,
        adjust=constants.EN_STOCK_ADJUST_QFQ,
        start_date=start_date_str,
        end_date=end_date_str,
        retry_times=retry_times,
        sleep_seconds=sleep_seconds,
        proxy_controller=proxy_controller,
    )

    print("All stock data saved to individual CSV files in stock_data directory")


if __name__ == "__main__":
    main()
