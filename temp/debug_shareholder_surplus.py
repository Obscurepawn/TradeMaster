#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Debug Shareholder Surplus Script

This script is used to debug why shareholder surplus calculation returns NaN values.
"""

import sys
import os
import pandas as pd

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import akshare as ak
from data_loader.ak_share import constant

def debug_shareholder_surplus(symbol="000001"):
    """Debug shareholder surplus calculation for a given stock symbol"""
    print(f"=== Debugging Shareholder Surplus for {symbol} ===")

    try:
        # Get cash flow statement
        print("\n1. Cash Flow Statement:")
        df_cashflow = ak.stock_financial_report_sina(
            stock=symbol,
            symbol=constant.CASH_FLOW_STATEMENT,
        )
        print(f"Shape: {df_cashflow.shape}")
        print(f"Columns: {df_cashflow.columns.tolist()}")
        print("First few rows:")
        print(df_cashflow.head())

        # Check if required columns exist
        required_cashflow_cols = [constant.REPORT_DATE, constant.CAPITAL_EXPENDITURE]
        missing_cols = [col for col in required_cashflow_cols if col not in df_cashflow.columns]
        if missing_cols:
            print(f"Missing columns in cash flow data: {missing_cols}")
        else:
            print(f"Found required columns: {required_cashflow_cols}")
            # Extract required data
            cashflow_data = df_cashflow[required_cashflow_cols]
            print("Cash flow data sample:")
            print(cashflow_data.head())

            # Check for NaN values
            print("NaN values in cash flow data:")
            print(cashflow_data.isnull().sum())

        # Get income statement
        print("\n2. Income Statement:")
        df_income = ak.stock_financial_report_sina(
            stock=symbol,
            symbol=constant.INCOME_STATEMENT,
        )
        print(f"Shape: {df_income.shape}")
        print(f"Columns: {df_income.columns.tolist()}")
        print("First few rows:")
        print(df_income.head())

        # Check if required columns exist
        required_income_cols = [constant.REPORT_DATE, constant.NET_PROFIT, constant.ASSET_IMPAIRMENT, constant.DEPRECIATION]
        missing_cols = [col for col in required_income_cols if col not in df_income.columns]
        if missing_cols:
            print(f"Missing columns in income data: {missing_cols}")
        else:
            print(f"Found required columns: {required_income_cols}")
            # Extract required data
            income_data = df_income[required_income_cols]
            print("Income data sample:")
            print(income_data.head())

            # Check for NaN values
            print("NaN values in income data:")
            print(income_data.isnull().sum())

            # Calculate depreciation (asset impairment + depreciation)
            income_data["depreciation"] = income_data[constant.ASSET_IMPAIRMENT] + income_data[constant.DEPRECIATION]
            print("Depreciation calculation:")
            print(income_data[[constant.REPORT_DATE, constant.ASSET_IMPAIRMENT, constant.DEPRECIATION, "depreciation"]].head())

        # If both dataframes have required columns, try merging
        if not missing_cols:  # Both dataframes have required columns
            print("\n3. Merging Data:")
            # Rename columns for clarity
            cashflow_data = cashflow_data.rename(
                columns={constant.CAPITAL_EXPENDITURE: "capital_expenditure"}
            )

            # Merge cash flow and income data
            df_merge = pd.merge(
                cashflow_data,
                income_data[[constant.REPORT_DATE, constant.NET_PROFIT, "depreciation"]],
                on=constant.REPORT_DATE,
                how='outer'  # Use outer join to see all data
            )
            print("Merged data:")
            print(df_merge.head(10))

            # Check for NaN values after merge
            print("NaN values after merge:")
            print(df_merge.isnull().sum())

            # Try calculating shareholder surplus
            print("\n4. Shareholder Surplus Calculation:")
            try:
                # Calculate shareholder surplus (in 10,000 yuan)
                df_merge["shareholder_surplus"] = (
                    df_merge[constant.NET_PROFIT]
                    + df_merge["depreciation"]
                    - df_merge["capital_expenditure"]
                ) / 10000  # Convert to 10,000 yuan

                print("Shareholder surplus calculation result:")
                result = df_merge[
                    [constant.REPORT_DATE, "shareholder_surplus"]
                ].sort_values(constant.REPORT_DATE, ascending=False)
                print(result.head(10))

                # Check if all values are NaN
                if result["shareholder_surplus"].isnull().all():
                    print("All shareholder surplus values are NaN!")
                else:
                    print("Some valid shareholder surplus values found.")

            except Exception as e:
                print(f"Error in shareholder surplus calculation: {e}")

    except Exception as e:
        print(f"Error in debugging shareholder surplus: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_shareholder_surplus()
