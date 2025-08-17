import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import akshare as ak
import pandas as pd

# Import constants directly
REPORT_DATE = "报告日"
CAPITAL_EXPENDITURE = "购建固定资产、无形资产和其他长期资产支付的现金"
ASSET_IMPAIRMENT = "资产减值损失"
DEPRECIATION = "折旧费"
NET_PROFIT = "净利润"
CASH_FLOW_STATEMENT = "现金流量表"
INCOME_STATEMENT = "利润表"

def debug_financial_fields(symbol="000001"):
    """Debug financial fields to check actual column names"""
    print(f"=== Debugging Financial Fields for {symbol} ===")

    try:
        # Get cash flow statement
        print("\n1. Cash Flow Statement:")
        df_cashflow = ak.stock_financial_report_sina(
            stock=symbol,
            symbol=CASH_FLOW_STATEMENT,
        )
        print(f"Shape: {df_cashflow.shape}")
        print("All columns:")
        for i, col in enumerate(df_cashflow.columns):
            print(f"  {i+1:2d}. {col}")

        # Check for capital expenditure field
        print(f"\nLooking for capital expenditure field: '{CAPITAL_EXPENDITURE}'")
        capital_exp_cols = [col for col in df_cashflow.columns if '购建固定资产' in col or '资本支出' in col]
        print(f"Found similar columns: {capital_exp_cols}")

        # Get income statement
        print("\n2. Income Statement:")
        df_income = ak.stock_financial_report_sina(
            stock=symbol,
            symbol=INCOME_STATEMENT,
        )
        print(f"Shape: {df_income.shape}")
        print("All columns:")
        for i, col in enumerate(df_income.columns):
            print(f"  {i+1:2d}. {col}")

        # Check for required fields
        print(f"\nLooking for required fields:")
        print(f"  Net profit: '{NET_PROFIT}' - {'Found' if NET_PROFIT in df_income.columns else 'Not found'}")
        print(f"  Asset impairment: '{ASSET_IMPAIRMENT}' - {'Found' if ASSET_IMPAIRMENT in df_income.columns else 'Not found'}")
        print(f"  Depreciation: '{DEPRECIATION}' - {'Found' if DEPRECIATION in df_income.columns else 'Not found'}")

        # Look for similar fields
        asset_impairment_cols = [col for col in df_income.columns if '资产减值' in col]
        depreciation_cols = [col for col in df_income.columns if '折旧' in col]
        print(f"  Similar to asset impairment: {asset_impairment_cols}")
        print(f"  Similar to depreciation: {depreciation_cols}")

        # Check data sample
        print("\n3. Data Sample:")
        if NET_PROFIT in df_income.columns:
            print("Net profit sample:")
            print(df_income[[REPORT_DATE, NET_PROFIT]].head())

        if ASSET_IMPAIRMENT in df_income.columns:
            print("Asset impairment sample:")
            print(df_income[[REPORT_DATE, ASSET_IMPAIRMENT]].head())

        if DEPRECIATION in df_income.columns:
            print("Depreciation sample:")
            print(df_income[[REPORT_DATE, DEPRECIATION]].head())

    except Exception as e:
        print(f"Error in debugging financial fields: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_financial_fields()
