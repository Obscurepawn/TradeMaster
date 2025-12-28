# Data Model: Proxy Manager State

## ProxyManager Internal State
| Field | Type | Description |
|-------|------|-------------|
| `config_path` | `Optional[str]` | Path to Clash config. |
| `is_enabled` | `bool` | Whether the manager is successfully initialized with a valid config. |
| `proxies` | `List[Dict]` | List of loaded proxy nodes. |
