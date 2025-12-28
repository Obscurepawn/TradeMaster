# Quickstart: Anti-Anti-Scraping Mechanism

## Overview
The anti-anti-scraping system is designed to be "invisible". Once enabled, it automatically monkeypatches the standard `requests` library to inject rotated headers, enforce Clash proxies, and add random jitter to all outgoing requests.

## Setup
Ensure your Clash client is running and the External Controller is enabled (default `127.0.0.1:9090`).

## Activation
In `src/main.py` (or any entry point), call `init_anti_scraping()`:

```python
from src.proxy.hook import init_anti_scraping

def main():
    # Initialize the global hook before any data fetching
    init_anti_scraping()
    
    # Rest of your application...
    # All requests here will be protected
```

## Features
1. **Dynamic Headers**: Automatically switches between Chrome, Firefox, and Safari fingerprints.
2. **Clash Rotation**: Checks node health and rotates proxies to prevent IP bans.
3. **Smart Jitter**: Adds non-linear random delays to simulate human browsing patterns.
4. **Library Support**: Works automatically with `akshare`, `pandas_datareader`, or any library using `requests`.

## Configuration
Settings can be adjusted in `src/proxy/hook.py` (or via config file in future versions):
- `JITTER_RANGE`: Default `(0.5, 2.0)` seconds.
- `ROTATION_THRESHOLD`: Rotate every N requests.
