# Feature Spec: Move Clash Config Path to YAML

## User Requirements
Currently, the Clash configuration file path is specified via the `CLASH_CONFIG_PATH` environment variable. This should be moved to the project's YAML configuration file (e.g., `config_mvp.yaml`) for better management and consistency.

## Current State
- `ProxyManager` in `src/proxy/manager.py` uses `os.environ.get("CLASH_CONFIG_PATH")`.
- `BacktestConfig` and `LoggingConfig` exist but don't include proxy settings.

## Goals
1. Add `proxy` configuration section to the YAML schema.
2. Include `clash_config_path` in the `proxy` section.
3. Update `ProxyManager` to accept the path from the configuration.
4. Update `init_anti_scraping` in `src/proxy/hook.py` to handle the configuration.
5. Update `config_mvp.yaml` with the new configuration item.
