# Tasks: Integrate Clash proxy rotation into HTTP request hook

## Phase 1: Proxy Manager Enhancements
- [X] Update `ProxyManager.load_proxies` in `src/proxy/manager.py` to use `logger.error` for missing/invalid config.
- [X] Add `is_enabled` property or similar check to `ProxyManager`.
- [X] Ensure `rotate_proxy` explicitly checks for valid initialization.

## Phase 2: Hook Verification & Integration
- [X] Verify that `GlobalRequestHook.patched_request` in `src/proxy/hook.py` correctly triggers rotation.
- [X] Add unit tests to verify error logging when config is missing.
- [X] Add unit tests to verify that rotation is skipped when config is missing.
