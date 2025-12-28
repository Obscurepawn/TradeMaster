# Data Model: Logging Configuration

## Configuration Schema (Extension to `BacktestConfig`)

The `BacktestConfig` dataclass and the YAML schema will be extended to include a `logging` section.

### Entities

#### `LoggingConfig` (Nested in `BacktestConfig`)
- `level`: String (default: "INFO"). Options: DEBUG, INFO, WARNING, ERROR, CRITICAL.
- `file_path`: Optional String. If provided, logs will be appended to this file.
- `console`: Boolean (default: true). Whether to output to the console.

### YAML Example
```yaml
logging:
  level: "DEBUG"
  file_path: "results/backtest.log"
  console: true
```
