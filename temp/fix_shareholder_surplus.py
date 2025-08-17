import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import akshare as ak
import pandas as pd
import numpy as np

# Import constants directly
REPORT_DATE = "报告日"
CAPITAL_EXPENDITURE = "购建固定资产、无形资产和其他长期资产支付的现金"
ASSET_IMPAIRMENT = "资产减值损失"
DEPRECIATION = "折旧费"
NET_PROFIT = "净利润"
CASH_FLOW_STATEMENT = "现金流量表"
INCOME_STATEMENT = "利润表"

def fix_shareholder_surplus(symbol="000001"):
    """Fix shareholder surplus calculation by finding alternative fields"""
    print(f"=== Fixing Shareholder Surplus for {symbol} ===")

    try:
        # Get cash flow statement
        print("\n1. Cash Flow Statement:")
        df_cashflow = ak.stock_financial_report_sina(
            stock=symbol,
            symbol=CASH_FLOW_STATEMENT,
        )
        print(f"Shape: {df_cashflow.shape}")

        # Extract capital expenditure field
        df_cashflow = df_cashflow[[REPORT_DATE, CAPITAL_EXPENDITURE]]
        df_cashflow = df_cashflow.rename(
            columns={CAPITAL_EXPENDITURE: "capital_expenditure"}
        )
        print("Cash flow data sample:")
        print(df_cashflow.head())

        # Get income statement
        print("\n2. Income Statement:")
        df_income = ak.stock_financial_report_sina(
            stock=symbol,
            symbol=INCOME_STATEMENT,
        )
        print(f"Shape: {df_income.shape}")

        # Check all columns for depreciation-related fields
        print("\nLooking for depreciation-related fields:")
        depreciation_cols = [col for col in df_income.columns if '折旧' in col or '摊销' in col]
        print(f"Depreciation-related columns: {depreciation_cols}")

        # Check all columns for asset impairment-related fields
        print("\nLooking for asset impairment-related fields:")
        asset_impairment_cols = [col for col in df_income.columns if '资产减值' in col]
        print(f"Asset impairment-related columns: {asset_impairment_cols}")

        # Try to find alternative fields for depreciation
        # Common fields that might contain depreciation information
        possible_depreciation_fields = [
            "折旧费",
            "固定资产折旧",
            "无形资产摊销",
            "长期待摊费用摊销",
            "投资性房地产折旧",
        ]

        found_depreciation_fields = []
        for field in possible_depreciation_fields:
            if field in df_income.columns:
                found_depreciation_fields.append(field)
                print(f"Found depreciation field: {field}")
                print(df_income[[REPORT_DATE, field]].head())

        # Try to find alternative fields for asset impairment
        possible_impairment_fields = [
            "资产减值损失",
            "信用减值损失",
            "其他资产减值损失",
        ]

        found_impairment_fields = []
        for field in possible_impairment_fields:
            if field in df_income.columns:
                found_impairment_fields.append(field)
                print(f"Found impairment field: {field}")
                print(df_income[[REPORT_DATE, field]].head())

        # Extract required data
        required_fields = [REPORT_DATE, NET_PROFIT]
        if found_depreciation_fields:
            required_fields.extend(found_depreciation_fields)
        if found_impairment_fields:
            required_fields.extend(found_impairment_fields)

        df_income_filtered = df_income[required_fields]
        print("\nFiltered income data:")
        print(df_income_filtered.head())

        # Calculate total depreciation (sum of all depreciation-related fields)
        if found_depreciation_fields or found_impairment_fields:
            df_income_filtered["depreciation"] = 0
            for field in found_depreciation_fields:
                df_income_filtered["depreciation"] += df_income_filtered[field].fillna(0)
            for field in found_impairment_fields:
                df_income_filtered["depreciation"] += df_income_filtered[field].fillna(0)

            print("\nDepreciation calculation:")
            print(df_income_filtered[[REPORT_DATE, "depreciation"] + found_depreciation_fields + found_impairment_fields].head(10))

        # Merge cash flow and income data
        print("\n3. Merging Data:")
        df_merge = pd.merge(
            df_cashflow,
            df_income_filtered[[REPORT_DATE, NET_PROFIT, "depreciation"]] if "depreciation" in df_income_filtered.columns else df_income_filtered[[REPORT_DATE, NET_PROFIT]],
            on=REPORT_DATE,
            how='outer'
        )
        print("Merged data:")
        print(df_merge.head(10))

        # Calculate shareholder surplus
        print("\n4. Shareholder Surplus Calculation:")
        if "depreciation" in df_merge.columns:
            df_merge["shareholder_surplus"] = (
                df_merge[NET_PROFIT].fillna(0)
                + df_merge["depreciation"].fillna(0)
                - df_merge["capital_expenditure"].fillna(0)
            ) / 10000  # Convert to 10,000 yuan
        else:
            # If we can't calculate depreciation, use just net profit and capital expenditure
            df_merge["shareholder_surplus"] = (
                df_merge[NET_PROFIT].fillna(0)
                - df_merge["capital_expenditure"].fillna(0)
            ) / 10000  # Convert to 10,000 yuan

        print("Shareholder surplus calculation result:")
        result = df_merge[[REPORT_DATE, "shareholder_surplus"]].sort_values(REPORT_DATE, ascending=False)
        print(result.head(10))

        # Check if all values are NaN
        if result["shareholder_surplus"].isnull().all():
            print("All shareholder surplus values are NaN!")
        else:
            print("Some valid shareholder surplus values found.")
            # Show non-NaN values
            non_nan_values = result[result["shareholder_surplus"].notnull()]
            if not non_nan_values.empty:
                print("Non-NaN values:")
                print(non_nan_values.head(10))

    except Exception as e:
        print(f"Error in fixing shareholder surplus: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    fix_shareholder_surplus()
