# Config Agent Context

## Responsibility
Manages the loading, validation, and schema definition for backtest configuration.

## Implementation
- `settings.py`: Orchestrates YAML loading and provides the `load_config` utility.
- `schema.py`: Defines the `BacktestConfig` data structure with type hints for all parameters (start_date, end_date, universe, etc.).
- Orchestration logic is documented via Google Style docstrings.

## Testing
- `test_config.py`: Validates schema enforcement and handling of missing or malformed configuration files.