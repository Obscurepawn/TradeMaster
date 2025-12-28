# Proxy Agent Context

## Responsibility
Provides a redundant, saturated anti-anti-scraping defense system for all network requests. This module ensures that all remote data fetching (via `requests` or libraries like `akshare`) is protected by browser fingerprinting, proxy rotation, and random jitter.

## Implementation
- `manager.py`: Manages proxy rotation via Clash API. Features proactive health checks (latency tests) and node blacklisting for failed proxies.
- `hook.py`: Implements a global monkeypatch for `requests.Session.request`. Automatically injects headers, enforces proxies, and adds random jitter (0.5s - 2.0s) to all outgoing requests.
- `headers.py`: Provides realistic browser fingerprints, including modern headers like `sec-ch-ua` and `sec-fetch-*` metadata to mimic Chrome, Edge, and Safari.
- Initialization: Call `init_anti_scraping()` in the main entry point to activate the global protection.

## Testing
- `test_manager.py`: Verifies Clash API interaction and health check logic.
- `test_hook.py`: Ensures global request interception and injection of headers/proxies.
- `test_headers.py`: Validates consistency and variety of browser fingerprints.
- `test_hook_real.py`: (External) Verifies end-to-end bypassing success against `httpbin.org`.