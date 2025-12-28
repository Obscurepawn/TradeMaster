# Data Model: Anti-Anti-Scraping Components

## 1. BrowserFingerprint (Internal Object)
Represents a consistent set of headers for a single request/session.

| Field | Type | Description |
|-------|------|-------------|
| `user_agent` | `str` | Standard User-Agent string |
| `sec_ch_ua` | `str` | Client Hints matching UA |
| `sec_ch_ua_platform` | `str` | e.g., "Windows", "macOS" |
| `accept_language` | `str` | Typically "zh-CN,zh;q=0.9,en;q=0.8" |
| `sec_fetch_site` | `str` | "same-origin", "cross-site", etc. |
| `sec_fetch_mode` | `str` | "navigate", "cors", etc. |
| `sec_fetch_dest` | `str` | "document", "empty", etc. |

## 2. ProxyNode (External via Clash)
State tracked for each proxy node.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Name of node in Clash |
| `last_check` | `datetime` | Last health check timestamp |
| `latency` | `int` | Last measured latency in ms |
| `is_alive` | `bool` | Current health status |
| `failure_count` | `int` | Sequential failures for blacklisting |

## 3. GlobalHookConfig (Configuration)
Settings for the anti-scraping system.

| Field | Type | Description |
|-------|------|-------------|
| `enabled` | `bool` | Toggle the whole system |
| `proxy_url` | `str` | Clash HTTP proxy URL (e.g., http://127.0.0.1:7890) |
| `clash_api` | `str` | Clash Controller URL (e.g., http://127.0.0.1:9090) |
| `jitter_range` | `tuple` | (min, max) delay in seconds |
| `rotation_freq` | `int` | Rotate proxy every N requests |
