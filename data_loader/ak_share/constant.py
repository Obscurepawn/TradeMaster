CN_CODE = "股票代码"
CN_NAME = "股票名称"
CN_SHORT_CODE = "代码"
CN_SHORT_NAME = "名称"
CN_DATE = "日期"
CN_OPEN = "开盘"
CN_CLOSE = "收盘"
CN_HIGH = "最高"
CN_LOW = "最低"
CN_VOLUME = "成交量"
CN_AMOUNT = "成交额"
CN_TURNOVER_RATE = "换手率"
CN_AMPLITUDE = "振幅"
CN_CHANGE_PCT = "涨跌幅"
CN_CHANGE_AMT = "涨跌额"


EN_CODE = "code"
EN_NAME = "name"
EN_DATE = "date"
EN_OPEN = "open"
EN_CLOSE = "close"
EN_HIGH = "high"
EN_LOW = "low"
EN_VOLUME = "volume"
EN_AMOUNT = "amount"
EN_TURNOVER_RATE = "turnover_rate"
EN_AMPLITUDE = "amplitude"
EN_CHANGE_PCT = "change_pct"
EN_CHANGE_AMT = "change_amt"

MA5 = "ma5"
MA10 = "ma10"
MA20 = "ma20"
MA60 = "ma60"

DIF = "dif"
DEA = "dea"
MACD = "macd"

RSI = "rsi"

MIDDLE_BAND = "middle_band"
UPPER_BAND = "upper_band"
LOWER_BAND = "lower_band"


VMA5 = "vma5"
VMA10 = "vma10"

K = "k"
D = "d"
J = "j"

BIAS5 = "bias5"
BIAS10 = "bias10"

STOCK_HISTORY = "stock_history"
CN_STOCK_ADJUST = "复权"
EN_STOCK_ADJUST_QFQ = "qfq"
EN_STOCK_ADJUST_HFQ = "hfq"
PERIOD_DAILY = "daily"

# Financial indicators constants
FINANCIAL_INDICATOR_BY_REPORT = "by_report"
FINANCIAL_INDICATOR_BY_QUARTER = "by_quarter"

# Shareholder surplus constants
CASH_FLOW_STATEMENT = "现金流量表"
INCOME_STATEMENT = "利润表"
ANNUAL_REPORT = "年报"
REPORT_DATE = "报告日"
CASH_FLOW_OPS = "经营活动产生的现金流量净额"
CAPITAL_EXPENDITURE = "购建固定资产、无形资产和其他长期资产支付的现金"
ASSET_IMPAIRMENT = "资产减值损失"
DEPRECIATION = "折旧费"
NET_PROFIT = "净利润"

# Alternative capital expenditure field names
CAPITAL_EXPENDITURE_ALTERNATIVE_1 = "购建固定资产、无形资产和其他长期资产所支付的现金"

# Additional depreciation and impairment fields
FIXED_ASSET_DEPRECIATION = "固定资产折旧"
INTANGIBLE_ASSET_AMORTIZATION = "无形资产摊销"
LONG_TERM_PREPAID_EXPENSES_AMORTIZATION = "长期待摊费用摊销"
INVESTMENT_PROPERTY_DEPRECIATION = "投资性房地产折旧"
CREDIT_IMPAIRMENT_LOSS = "信用减值损失"
OTHER_ASSET_IMPAIRMENT_LOSSES = "其他资产减值损失"

# Shareholder surplus valuation constants
VALUATION_INDICATOR_TOTAL_MARKET_CAP = "total_market_cap"
VALUATION_INDICATOR_PE_TTM = "pe_ttm"
VALUATION_INDICATOR_PE_STATIC = "pe_static"
VALUATION_INDICATOR_PB = "pb"
VALUATION_INDICATOR_PC = "pc"

VALUATION_PERIOD_1Y = "1y"
VALUATION_PERIOD_3Y = "3y"
VALUATION_PERIOD_5Y = "5y"
VALUATION_PERIOD_10Y = "10y"
VALUATION_PERIOD_ALL = "all"

# Data loader types
DATA_LOADER_AKSHARE = "akshare"

RENAME_DICT = {
    CN_CODE: EN_CODE,
    CN_NAME: EN_NAME,
    CN_SHORT_NAME: EN_NAME,
    CN_SHORT_CODE: EN_CODE,
    CN_DATE: EN_DATE,
    CN_OPEN: EN_OPEN,
    CN_CLOSE: EN_CLOSE,
    CN_HIGH: EN_HIGH,
    CN_LOW: EN_LOW,
    CN_VOLUME: EN_VOLUME,
    CN_AMOUNT: EN_AMOUNT,
    CN_TURNOVER_RATE: EN_TURNOVER_RATE,
    CN_AMPLITUDE: EN_AMPLITUDE,
    CN_CHANGE_PCT: EN_CHANGE_PCT,
    CN_CHANGE_AMT: EN_CHANGE_AMT,
}
