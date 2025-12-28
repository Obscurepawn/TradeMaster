# Feature Spec: Robust Clash Proxy Rotation in Hook

## User Requirements
Check if Clash proxy node switching logic is included in the HTTP request hook. If missing, add it.
Ensure that if `clash_config_path` is not provided or the file does not exist:
1. Proxy rotation logic is skipped.
2. An error log is generated.

## Current State
- `GlobalRequestHook` in `src/proxy/hook.py` calls `rotate_proxy()` every 5 requests.
- `ProxyManager` in `src/proxy/manager.py` handles rotation.
- `ProxyManager.load_proxies()` currently logs a warning if the config is missing, but doesn't prevent `rotate_proxy` from being called (though `rotate_proxy` returns early if `proxies` list is empty).

## Goals
1. Explicitly check for `clash_config_path` existence within `ProxyManager`.
2. Ensure an `ERROR` level log is emitted if the config is missing or invalid.
3. Ensure `rotate_proxy` does nothing if the configuration is invalid.
4. Verify the integration in `GlobalRequestHook`.
