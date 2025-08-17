#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shareholder Surplus Calculation Example Script
Demonstrates how to use AkshareDataLoader to get shareholder surplus data
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from data_loader.data_loader_factory import DataLoaderFactory
import pandas as pd


def main():
    """Main function"""
    print("Shareholder Surplus Calculation Example")
    print("=" * 50)

    # Create data loader
    data_loader = DataLoaderFactory.create_data_loader("akshare")

    # Test stock symbols
    test_symbols = ["000001", "000002", "600000"]

    for symbol in test_symbols:
        try:
            print(f"\nFetching shareholder surplus data for stock {symbol}...")

            # Get shareholder surplus data
            shareholder_surplus = data_loader.get_shareholder_surplus(symbol=symbol)

            if not shareholder_surplus.empty:
                print(f"Shareholder surplus data for stock {symbol}:")
                print(shareholder_surplus.head(10))  # Show first 10 records

                # Show the latest shareholder surplus value
                latest_surplus = shareholder_surplus.iloc[0]
                print(f"\nLatest shareholder surplus value: {latest_surplus['shareholder_surplus']:.2f} million yuan")
                print(f"Report date: {latest_surplus['report_date']}")
            else:
                print(f"No shareholder surplus data available for stock {symbol}")

        except Exception as e:
            print(f"Error fetching shareholder surplus data for stock {symbol}: {e}")

    print("\n" + "=" * 50)
    print("Example completed")


if __name__ == "__main__":
    main()
