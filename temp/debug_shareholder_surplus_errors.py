import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import akshare as ak
import pandas as pd
from data_loader.ak_share import constant

def debug_cashflow_fields(symbol="000002"):
    """Debug cashflow fields for a given stock symbol"""
    print(f"=== Debugging Cashflow Fields for {symbol} ===")

    try:
        # Get cash flow statement
        print("\n1. Cash Flow Statement:")
        df_cashflow = ak.stock_financial_report_sina(
            stock=symbol,
            symbol=constant.CASH_FLOW_STATEMENT,
        )
        print(f"Shape: {df_cashflow.shape}")
        print("All columns:")
        for i, col in enumerate(df_cashflow.columns):
            print(f"  {i+1:2d}. {col}")

        # Check for capital expenditure field
        print(f"\nLooking for capital expenditure field: '{constant.CAPITAL_EXPENDITURE}'")
        if constant.CAPITAL_EXPENDITURE in df_cashflow.columns:
            print("Found!")
            print("Sample data:")
            print(df_cashflow[[constant.REPORT_DATE, constant.CAPITAL_EXPENDITURE]].head())
        else:
            print("Not found!")
            # Look for similar fields
            capital_exp_cols = [col for col in df_cashflow.columns if '购建固定资产' in col or '资本支出' in col]
            print(f"Similar columns: {capital_exp_cols}")

    except Exception as e:
        print(f"Error in debugging cashflow fields: {e}")
        import traceback
        traceback.print_exc()

def debug_dtype_issues(symbol="600000"):
    """Debug dtype issues for a given stock symbol"""
    print(f"\n=== Debugging Dtype Issues for {symbol} ===")

    try:
        # Get income statement
        df_income = ak.stock_financial_report_sina(
            stock=symbol,
            symbol=constant.INCOME_STATEMENT,
        )
        print(f"Income statement shape: {df_income.shape}")

        # Check for depreciation fields
        possible_depreciation_fields = [
            constant.DEPRECIATION,  # 折旧费
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
                print(f"  Dtype: {df_income[field].dtype}")
                print(f"  Sample values: {df_income[field].head().tolist()}")

        # Check for impairment fields
        possible_impairment_fields = [
            constant.ASSET_IMPAIRMENT,  # 资产减值损失
            "信用减值损失",
            "其他资产减值损失",
        ]

        found_impairment_fields = []
        for field in possible_impairment_fields:
            if field in df_income.columns:
                found_impairment_fields.append(field)
                print(f"Found impairment field: {field}")
                print(f"  Dtype: {df_income[field].dtype}")
                print(f"  Sample values: {df_income[field].head().tolist()}")

    except Exception as e:
        print(f"Error in debugging dtype issues: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_cashflow_fields("000002")
    debug_dtype_issues("600000")
