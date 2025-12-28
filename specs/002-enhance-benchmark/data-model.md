# Data Model: Baseline Enhancement

## Entities

### Config (Updated)
| Field | Type | Description |
|-------|------|-------------|
| `start_date` | Date | Start of backtest period. |
| `end_date` | Date | End of backtest period. |
| `initial_cash` | Float | Starting capital. |
| `strategy_name` | String | Name of the strategy class. |
| `baseline` | Union[str, List[str]] | One or more ticker symbols for comparison. |
| `universe` | List[String] | List of stock codes to consider. |

### BacktestResult (Updated)
| Field | Type | Description |
|-------|------|-------------|
| `equity_curve` | List[float] | Strategy account value series. |
| `baselines` | Dict[str, List[float]] | Mapping of baseline symbols to their normalized yield curves. |
| `dates` | List[date] | Shared date index. |
