# Data Model

## Entities

### Config
User-defined parameters for a backtest run.

| Field | Type | Description |
|-------|------|-------------|
| `start_date` | Date | Start of backtest period. |
| `end_date` | Date | End of backtest period. |
| `initial_cash` | Float | Starting capital. |
| `strategy_name` | String | Name of the strategy class. |
| `baseline` | List[String] | One or more ticker symbols for comparison. |
| `universe` | List[String] | List of stock codes to consider. |

### BacktestResult
Summary of the simulation.

| Field | Type | Description |
|-------|------|-------------|
| `total_return` | Float | Percentage return. |
| `equity_curve` | List[Float] | Normalized strategy account value (starts at 1.0). |
| `baselines` | Dict[str, List[Float]] | Normalized baseline curves (e.g. {"sh000300": [...]}). |
| `dates` | List[Date] | Chronological dates for the curves. |
