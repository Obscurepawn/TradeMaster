#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Stock Information Example

This example demonstrates how to use the stock information functions to:
1. Get basic information for a stock (price, market cap, industry, etc.)
2. Get detailed profile information for a stock (company profile, business scope, etc.)
"""

import sys
import os

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from data_loader.ak_share.impl import (
    get_stock_basic_info,
    get_stock_profile_info
)
import pandas as pd


def main():
    """Main function to demonstrate stock information retrieval"""
    # Test stock symbol
    symbol = "000001"  # Ping An Bank
    stock_name = "平安银行"

    print(f"=== Stock Information Demo for {symbol} ({stock_name}) ===\n")

    # Get basic information
    print("1. Getting basic stock information...")
    try:
        basic_info = get_stock_basic_info(symbol)
        print("Basic Information:")
        print(basic_info.to_string(index=False))
        print()
    except Exception as e:
        print(f"Failed to get basic information: {e}\n")

    # Get profile information
    print("2. Getting detailed profile information...")
    try:
        profile_info = get_stock_profile_info(symbol)
        print("Profile Information:")
        # Display key columns
        key_columns = ['公司名称', '英文名称', '证券代码', '行业', '成立日期', '上市日期']
        for col in key_columns:
            if col in profile_info.columns:
                print(f"{col}: {profile_info[col].iloc[0]}")
        print()

        # Show business scope if available
        if '经营范围' in profile_info.columns:
            scope = profile_info['经营范围'].iloc[0]
            print(f"Business Scope (first 100 chars): {scope[:100]}...")
        print()
    except Exception as e:
        print(f"Failed to get profile information: {e}\n")

    print("=== Demo completed ===")


if __name__ == "__main__":
    main()
