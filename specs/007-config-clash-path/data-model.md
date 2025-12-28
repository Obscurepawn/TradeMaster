# Data Model: Proxy Configuration

## 1. ProxyConfig (New)
Configuration section for proxy-related settings.

| Field | Type | Description |
|-------|------|-------------|
| `clash_config_path` | `Optional[str]` | Filesystem path to the Clash config file (proxies.yaml) |
| `selector_name` | `str` | Clash selector group name (default: "Proxy") |

## 2. Updated BacktestConfig
Added the `proxy` field.

| Field | Type | Description |
|-------|------|-------------|
| ... | ... | ... |
| `proxy` | `ProxyConfig` | Proxy settings |
