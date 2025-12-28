# Tasks: Move Clash Config Path to YAML

## Phase 1: Configuration Schema & Loading
- [X] Add `ProxyConfig` dataclass to `src/config/schema.py`.
- [X] Update `BacktestConfig` to include `proxy: ProxyConfig` in `src/config/schema.py`.
- [X] Update `load_config` in `src/config/settings.py` to parse the `proxy` section from YAML.
- [X] Verify configuration loading with a unit test.

## Phase 2: Proxy Manager & Integration
- [X] Update `ProxyManager.__init__` in `src/proxy/manager.py` to remove `os.environ` usage.
- [X] Update `src/main.py` to:
    - Instantiate `ProxyManager` using `config.proxy.clash_config_path`.
    - Pass the `ProxyManager` instance to `init_anti_scraping(proxy_manager)`.
- [X] Update `config_mvp.yaml` with a placeholder or real path for `clash_config_path`.

## Phase 3: Verification
- [X] Run all tests to ensure no regressions.
- [X] Verify end-to-end functionality by running a backtest (even if proxy is disabled, it shouldn't crash).
