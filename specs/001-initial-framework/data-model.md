# Data Model

## Entities

### Config
User-defined parameters for a backtest run.

| Field | Type | Description |
|-------|------|-------------|
| `start_date` | Date | Start of backtest period. |
| `end_date` | Date | End of backtest period. |
| `initial_cash` | Float | Starting capital (e.g., 100,000.0). |
| `strategy_name` | String | Name of the strategy class to load. |
| `benchmark` | String | Ticker symbol for benchmark (e.g., "sh000300"). |
| `universe` | List[String] | (Optional) List of stock codes to consider. |

### MarketData (DuckDB Table)
Daily OHLCV data for stocks.

| Column | Type | Description |
|--------|------|-------------|
| `date` | Date | Trade date. |
| `code` | String | Stock symbol (e.g., "600000"). |
| `open` | Float | Opening price. |
| `high` | Float | Highest price. |
| `low` | Float | Lowest price. |
| `close` | Float | Closing price. |
| `volume` | Float | Trade volume. |
| `amount` | Float | Trade amount/turnover. |

### Position
Current holding in the portfolio.

| Field | Type | Description |
|-------|------|-------------|
| `code` | String | Stock symbol. |
| `quantity` | Integer | Number of shares held. |
| `avg_cost` | Float | Average buy price. |
| `current_price` | Float | Latest known price. |

### Trade
Record of a specialized transaction.

| Field | Type | Description |
|-------|------|-------------|
| `date` | Date | Transaction date. |
| `code` | String | Stock symbol. |
| `direction` | Enum | BUY / SELL. |
| `quantity` | Integer | Number of shares. |
| `price` | Float | Execution price. |
| `cost` | Float | Total transaction cost (incl. fees). |
| `commission` | Float | Trading fee. |

### BacktestResult
Summary of the simulation.

| Field | Type | Description |
|-------|------|-------------|
| `total_return` | Float | Percentage return. |
| `max_drawdown` | Float | Maximum percentage loss from peak. |
| `sharpe_ratio` | Float | Risk-adjusted return metric. |
| `equity_curve` | List[Date, Float] | Daily account value series. |