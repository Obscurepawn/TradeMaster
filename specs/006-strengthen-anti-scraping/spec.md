# Feature Spec: Strengthen anti-anti-scraping mechanism

## User Requirements
Strengthen the HTTP request anti-anti-scraping mechanism with as many defensive measures as possible (saturated, redundant defense) and ensure it is active in the main execution path.

## Current State
- `RequestHook` class exists but is not integrated into `AkshareLoader` or `main.py`.
- `ProxyManager` handles Clash node rotation but `akshare` requests aren't forced to use it.
- Only basic `User-Agent` rotation is implemented in `RequestHook`.

## Goals
1. Implement advanced header rotation (User-Agent, Accept-Language, Referer, Sec-Fetch headers, etc.).
2. Implement request jitter (random delays).
3. Implement a global monkeypatch/hook for `requests` to ensure all calls (including third-party libraries like `akshare`) are intercepted.
4. Ensure Clash proxy is enforced for all external requests.
5. Add session persistence/cookie handling if beneficial.
6. Verify activation in the main execution path.
