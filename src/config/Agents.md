# Config Agent Context

## Responsibility
Manages the loading, validation, and schema definition for backtest configuration.

## Implementation
- `settings.py`: Orchestrates YAML loading and provides the `load_config` utility. Now parses the `logging` section into a `LoggingConfig` object.
- `schema.py`: Defines the `BacktestConfig` and `LoggingConfig` data structures.
- `logging_config.py`: Provides the `setup_logging` utility to initialize the global logger based on configuration.

## Testing
- `test_config.py`: Validates schema enforcement and handling of missing or malformed configuration files.