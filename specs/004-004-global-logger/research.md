# Research: Global Logging Implementation

## Decisions

### Decision: Python `logging` Library
- **Choice**: Use the standard library `logging` module.
- **Rationale**: Mandated by the project constitution (Principle II). It is robust, well-documented, and requires no external dependencies.
- **Alternatives considered**: `loguru` was considered for its simplicity, but rejected to avoid unnecessary dependencies and adhere to the constitution.

### Decision: Centralized Initialization
- **Choice**: Create a `src/config/logging_config.py` to handle setup.
- **Rationale**: Ensures that logging is configured consistently before any other module attempts to log.
- **Alternatives considered**: Initializing in `main.py` only. Rejected because library modules should be usable independently with a default configuration if needed.

### Decision: Log Level Configuration
- **Choice**: Support levels (DEBUG, INFO, WARNING, ERROR, CRITICAL) configurable via `config.yaml`.
- **Rationale**: Flexibility for developers to toggle verbosity without code changes.

### Decision: Thread/Process Safety
- **Choice**: Standard `FileHandler` and `StreamHandler`.
- **Rationale**: The current backtest engine is single-threaded. If multi-processing is added later (e.g., for parameter sweeps), we will transition to `QueueHandler`.
